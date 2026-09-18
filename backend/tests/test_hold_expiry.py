"""Hold lifetime tests: renewal moves expiry (and the sweep respects the new
moment), quota is enforced and visible, released/cancelled holds reject renewal,
and the minimal timeout loop (create -> sweep -> released -> seat freed) works.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.models import Hall, Showtime
from app.services import hold_service

T0 = datetime(2026, 9, 18, 12, 0, 0)


class FakeClock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    clock = FakeClock(T0)
    monkeypatch.setattr(hold_service, "utcnow", clock)
    monkeypatch.setattr(settings, "hold_ttl_minutes", 30)
    monkeypatch.setattr(settings, "renew_extension_minutes", 15)
    monkeypatch.setattr(settings, "max_renewals", 2)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    session = testing_session()
    hall = Hall(name="测试厅", rows=4, cols=8, aisle_cols="")
    session.add(hall)
    session.flush()
    showtime = Showtime(
        hall_id=hall.id, film_title="测试片", start_at=T0 + timedelta(hours=3)
    )
    session.add(showtime)
    session.commit()

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, clock=clock, session=session, showtime_id=showtime.id
        )

    session.close()
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def _create_hold(env, party_size=2):
    resp = env.client.post("/api/holds", json={"showtime_id": env.showtime_id, "party_size": party_size})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _get_hold(env, hold_id):
    resp = env.client.get("/api/holds")
    assert resp.status_code == 200
    return next(h for h in resp.json() if h["id"] == hold_id)


def _occupied(env, row, col):
    resp = env.client.get(f"/api/seatmap/{env.showtime_id}")
    assert resp.status_code == 200
    cell = next(c for c in resp.json()["cells"] if c["row"] == row and c["col"] == col)
    return cell["occupied"]


def test_create_writes_expiry_and_shows_full_quota(env):
    hold = _create_hold(env)
    assert hold["status"] == "held"
    assert hold["expires_at"] == (T0 + timedelta(minutes=30)).isoformat()
    assert hold["renew_count"] == 0
    assert hold["renewals_remaining"] == 2
    listed = _get_hold(env, hold["id"])
    assert listed["renewals_remaining"] == 2


def test_renew_extends_expiry_and_sweep_respects_new_moment(env):
    hold = _create_hold(env)
    row, col = hold["row"], hold["start_col"]
    old_expiry = datetime.fromisoformat(hold["expires_at"])

    renewed = env.client.post(f"/api/holds/{hold['id']}/renew")
    assert renewed.status_code == 200, renewed.text
    body = renewed.json()
    new_expiry = datetime.fromisoformat(body["expires_at"])
    assert new_expiry == old_expiry + timedelta(minutes=15)  # 到期后移
    assert body["renew_count"] == 1
    assert body["renewals_remaining"] == 1

    # Past the ORIGINAL expiry but before the renewed one: sweep must not release.
    env.clock.now = old_expiry + timedelta(minutes=1)
    sweep = env.client.post("/api/holds/sweep")
    assert sweep.status_code == 200
    assert sweep.json()["released"] == 0
    assert _get_hold(env, hold["id"])["status"] == "held"
    assert _occupied(env, row, col) is True  # 座位图在续期后保持占用

    # Past the renewed expiry: now the sweep releases it.
    env.clock.now = new_expiry + timedelta(seconds=1)
    sweep = env.client.post("/api/holds/sweep")
    assert sweep.json()["released"] == 1
    assert _get_hold(env, hold["id"])["status"] == "released"
    assert _occupied(env, row, col) is False


def test_renew_beyond_quota_rejected(env):
    hold = _create_hold(env)
    for expected_remaining in (1, 0):
        resp = env.client.post(f"/api/holds/{hold['id']}/renew")
        assert resp.status_code == 200, resp.text
        assert resp.json()["renewals_remaining"] == expected_remaining

    resp = env.client.post(f"/api/holds/{hold['id']}/renew")
    assert resp.status_code == 409
    assert "续期次数已用完" in resp.json()["detail"]

    listed = _get_hold(env, hold["id"])
    assert listed["renew_count"] == 2
    assert listed["renewals_remaining"] == 0
    assert listed["status"] == "held"


def test_released_hold_cannot_renew(env):
    hold = _create_hold(env)
    env.clock.advance(minutes=31)  # past expiry; renew itself must sweep first
    resp = env.client.post(f"/api/holds/{hold['id']}/renew")
    assert resp.status_code == 409
    assert "已释放" in resp.json()["detail"]
    assert _get_hold(env, hold["id"])["status"] == "released"


def test_cancelled_hold_cannot_renew(env):
    hold = _create_hold(env)
    cancelled = env.client.post(f"/api/holds/{hold['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    resp = env.client.post(f"/api/holds/{hold['id']}/renew")
    assert resp.status_code == 409
    assert "已取消" in resp.json()["detail"]


def test_sweep_releases_frees_seatmap_and_status_filter(env):
    hold = _create_hold(env)
    row, start, end = hold["row"], hold["start_col"], hold["end_col"]
    assert all(_occupied(env, row, c) for c in range(start, end + 1))

    env.clock.advance(minutes=31)
    sweep = env.client.post("/api/holds/sweep")
    assert sweep.json() == {"released": 1, "released_ids": [hold["id"]]}

    held = env.client.get("/api/holds", params={"status": "held"})
    assert all(h["id"] != hold["id"] for h in held.json())
    released = env.client.get("/api/holds", params={"status": "released"})
    assert [h["id"] for h in released.json()] == [hold["id"]]
    bad = env.client.get("/api/holds", params={"status": "bogus"})
    assert bad.status_code == 400

    assert all(not _occupied(env, row, c) for c in range(start, end + 1))

    # Released span is bookable again (closed loop).
    rebooked = _create_hold(env)
    assert (rebooked["row"], rebooked["start_col"], rebooked["end_col"]) == (row, start, end)


def test_renewals_remaining_counts_down_in_list(env):
    hold = _create_hold(env)
    assert _get_hold(env, hold["id"])["renewals_remaining"] == 2
    env.client.post(f"/api/holds/{hold['id']}/renew")
    listed = _get_hold(env, hold["id"])
    assert listed["renew_count"] == 1
    assert listed["renewals_remaining"] == 1
