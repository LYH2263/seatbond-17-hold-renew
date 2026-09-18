from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models.models import Hall, SeatHold, Showtime
from app.services.hold_lifecycle import expire_holds


def _make_showtime(client) -> int:
    db = SessionLocal()
    try:
        hall = Hall(name="测试厅", rows=5, cols=10, aisle_cols="")
        db.add(hall)
        db.flush()
        st = Showtime(
            hall_id=hall.id,
            film_title="测试片",
            start_at=datetime.utcnow() + timedelta(hours=1),
        )
        db.add(st)
        db.commit()
        return st.id
    finally:
        db.close()


def _create_hold(client, sid, party=2):
    r = client.post("/api/holds", json={"showtime_id": sid, "party_size": party})
    assert r.status_code == 200, r.text
    return r.json()


def _expired_hold(sid, party=2, ttl=120) -> SeatHold:
    db = SessionLocal()
    try:
        hold = SeatHold(
            showtime_id=sid,
            order_code=f"SB-X{sid}-{party}",
            row=1,
            start_col=1,
            end_col=party,
            party_size=party,
            expires_at=datetime.utcnow() - timedelta(seconds=10),
            renewals_used=0,
        )
        db.add(hold)
        db.commit()
        db.refresh(hold)
        return hold
    finally:
        db.close()


def _occupied_count(client, sid) -> int:
    r = client.get(f"/api/seatmap/{sid}")
    assert r.status_code == 200, r.text
    return sum(1 for c in r.json()["cells"] if c["occupied"])


def _scan_at(moment: datetime) -> None:
    """用指定时刻跑一次超时扫描（不依赖真实睡眠）。"""
    db = SessionLocal()
    try:
        expire_holds(db, now=moment)
    finally:
        db.close()


def _get_status(hold_id: int) -> str:
    db = SessionLocal()
    try:
        db.expire_all()
        return db.get(SeatHold, hold_id).status
    finally:
        db.close()


# —— 创建即写到期 ——

def test_create_hold_writes_expiry_and_quota(client):
    sid = _make_showtime(client)
    hold = _create_hold(client, sid)
    assert hold["status"] == "held"
    assert hold["expires_at"] is not None
    # 创建时间 ~120s 后到期
    exp = datetime.fromisoformat(hold["expires_at"])
    assert timedelta(seconds=110) < exp - datetime.utcnow() < timedelta(seconds=130)
    # 续期额度在列表/详情可见
    assert hold["renewals_used"] == 0
    assert hold["renewals_left"] == 2


# —— 续期成功：到期后移，旧时刻扫描不误释，新时刻后释放，座位先占用后腾空 ——

def test_renew_extends_and_scan_uses_new_expiry(client):
    sid = _make_showtime(client)
    hold = _create_hold(client, sid, party=3)
    old_exp = datetime.fromisoformat(hold["expires_at"])
    assert _occupied_count(client, sid) == 3

    r = client.post(f"/api/holds/{hold['id']}/renew")
    assert r.status_code == 200, r.text
    renewed = r.json()
    new_exp = datetime.fromisoformat(renewed["expires_at"])
    assert new_exp > old_exp
    assert renewed["renewals_used"] == 1
    assert renewed["renewals_left"] == 1
    # 续期后座位图保持占用
    assert _occupied_count(client, sid) == 3

    # 越过旧到期时刻、但未到新时刻：扫描不得提前释放
    _scan_at(old_exp + timedelta(seconds=1))
    assert _get_status(hold["id"]) == "held"
    assert _occupied_count(client, sid) == 3

    # 越过新到期时刻：扫描释放，座位图腾空，同段可重新持座
    _scan_at(new_exp + timedelta(seconds=1))
    assert _get_status(hold["id"]) == "released"
    assert _occupied_count(client, sid) == 0

    rows = client.get("/api/holds", params={"status": "released"}).json()
    assert any(h["id"] == hold["id"] for h in rows)
    held_rows = client.get("/api/holds", params={"status": "held"}).json()
    assert all(h["id"] != hold["id"] for h in held_rows)


# —— 超过允许次数拒绝 ——

def test_renew_over_limit_rejected(client):
    sid = _make_showtime(client)
    hold = _create_hold(client, sid)
    last_exp = hold["expires_at"]
    for i in (1, 2):
        r = client.post(f"/api/holds/{hold['id']}/renew")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["renewals_used"] == i
        assert body["renewals_left"] == 2 - i
        last_exp = body["expires_at"]

    r = client.post(f"/api/holds/{hold['id']}/renew")
    assert r.status_code == 409, r.text
    # 被拒绝后状态与到期时刻不变，座位仍占用
    assert _get_status(hold["id"]) == "held"
    db = SessionLocal()
    try:
        db.expire_all()
        fresh = db.get(SeatHold, hold["id"])
        assert fresh.renewals_used == 2
        assert fresh.expires_at == datetime.fromisoformat(last_exp)
    finally:
        db.close()
    assert _occupied_count(client, sid) == hold["party_size"]


# —— 已释放 / 已取消不可续 ——

def test_released_hold_cannot_renew(client):
    sid = _make_showtime(client)
    hold = _expired_hold(sid)
    # 座图/列表读路径触发扫描后即释放
    assert _occupied_count(client, sid) == 0

    r = client.post(f"/api/holds/{hold.id}/renew")
    assert r.status_code == 409
    assert _get_status(hold.id) == "released"


def test_cancelled_hold_cannot_renew(client):
    sid = _make_showtime(client)
    hold = _create_hold(client, sid)

    r = client.post(f"/api/holds/{hold['id']}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    assert _occupied_count(client, sid) == 0

    r = client.post(f"/api/holds/{hold['id']}/renew")
    assert r.status_code == 409
    assert _get_status(hold["id"]) == "cancelled"


# —— 续期额度在列表上正确展示 ——

def test_quota_visible_in_list(client):
    sid = _make_showtime(client)
    h1 = _create_hold(client, sid)
    client.post(f"/api/holds/{h1['id']}/renew")
    h2 = _create_hold(client, sid)

    rows = client.get("/api/holds").json()
    by_id = {h["id"]: h for h in rows}
    assert by_id[h1["id"]]["renewals_used"] == 1
    assert by_id[h1["id"]]["renewals_left"] == 1
    assert by_id[h2["id"]]["renewals_used"] == 0
    assert by_id[h2["id"]]["renewals_left"] == 2


def test_list_status_filter(client):
    sid = _make_showtime(client)
    hold = _create_hold(client, sid)
    client.post(f"/api/holds/{hold['id']}/cancel")
    _create_hold(client, sid)

    cancelled = client.get("/api/holds", params={"status": "cancelled"}).json()
    assert {h["id"] for h in cancelled} == {hold["id"]}
    held = client.get("/api/holds", params={"status": "held"}).json()
    assert len(held) == 1

    r = client.get("/api/holds", params={"status": "nope"})
    assert r.status_code == 400
