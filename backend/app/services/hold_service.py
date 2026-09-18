"""Hold lifetime: timeout sweep, renewal with quota, cancellation.

The clock (`utcnow`) is module-level so tests can monkeypatch it; every
endpoint reads the current time through it, and the sweep always compares
against the hold's *current* expires_at — after a renewal the new later
expiry is what keeps the seat from being released early.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import HOLD_CANCELLED, HOLD_HELD, HOLD_RELEASED, SeatHold


class HoldStateError(Exception):
    """Raised when a lifecycle action is not allowed for the hold's state."""


def utcnow() -> datetime:
    return datetime.utcnow()


def renewals_remaining(hold: SeatHold) -> int:
    return max(0, settings.max_renewals - hold.renew_count)


def sweep_expired(db: Session, now: datetime | None = None) -> list[SeatHold]:
    """Release every held hold whose expires_at has passed. Commits if anything changed."""
    now = now or utcnow()
    stale = db.scalars(
        select(SeatHold).where(SeatHold.status == HOLD_HELD, SeatHold.expires_at <= now)
    ).all()
    for hold in stale:
        hold.status = HOLD_RELEASED
    if stale:
        db.commit()
    return list(stale)


def renew_hold(hold: SeatHold, now: datetime | None = None) -> SeatHold:
    """Extend expires_at by the configured step, consuming one renewal quota."""
    now = now or utcnow()
    if hold.status == HOLD_RELEASED:
        raise HoldStateError("已释放的持座不可续期")
    if hold.status == HOLD_CANCELLED:
        raise HoldStateError("已取消的持座不可续期")
    if hold.status != HOLD_HELD:
        raise HoldStateError(f"当前状态（{hold.status}）不可续期")
    if hold.expires_at <= now:
        raise HoldStateError("持座已过期，不可续期")
    if renewals_remaining(hold) <= 0:
        raise HoldStateError("续期次数已用完")
    hold.expires_at = hold.expires_at + timedelta(minutes=settings.renew_extension_minutes)
    hold.renew_count += 1
    return hold


def cancel_hold(hold: SeatHold, now: datetime | None = None) -> SeatHold:
    now = now or utcnow()
    if hold.status != HOLD_HELD:
        raise HoldStateError(f"当前状态（{hold.status}）不可取消")
    if hold.expires_at <= now:
        raise HoldStateError("持座已过期，不可取消")
    hold.status = HOLD_CANCELLED
    return hold
