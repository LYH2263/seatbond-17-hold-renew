"""持座时效生命周期：超时扫描释放、续期、取消。

扫描不做独立常驻超时台：在列表/座图/续期等读路径上顺带执行 expire_holds，
保证任何时刻判断占用都以最新的 expires_at 为准。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import (
    STATUS_HELD,
    STATUS_CANCELLED,
    STATUS_RELEASED,
    SeatHold,
)


class HoldLifecycleError(Exception):
    """续期/取消在业务规则上被拒绝，由 API 层映射为 409。"""


def expire_holds(
    db: Session,
    *,
    now: datetime | None = None,
    showtime_id: int | None = None,
    commit: bool = True,
) -> int:
    """把持有中且 expires_at 已到点的记录置为 released，返回释放条数。

    仅认 status == held，已释放/已取消不会被改动；判断一律用当前行上的
    expires_at，续期后按新时刻判断，不会按旧时刻提前释放。
    """
    now = now or datetime.utcnow()
    stmt = select(SeatHold).where(
        SeatHold.status == STATUS_HELD,
        SeatHold.expires_at.is_not(None),
        SeatHold.expires_at <= now,
    )
    if showtime_id is not None:
        stmt = stmt.where(SeatHold.showtime_id == showtime_id)
    count = 0
    for hold in db.scalars(stmt).all():
        hold.status = STATUS_RELEASED
        count += 1
    if count and commit:
        db.commit()
    return count


def renew_hold(
    db: Session,
    hold: SeatHold,
    *,
    now: datetime | None = None,
    ttl_seconds: int | None = None,
    max_renewals: int | None = None,
) -> SeatHold:
    """续期一条持座：校验状态/未过期/额度，后移 expires_at 并消耗一次额度。"""
    now = now or datetime.utcnow()
    ttl = settings.hold_ttl_seconds if ttl_seconds is None else ttl_seconds
    limit = settings.max_renewals if max_renewals is None else max_renewals

    if hold.status == STATUS_RELEASED:
        raise HoldLifecycleError("持座已释放，不可续期")
    if hold.status == STATUS_CANCELLED:
        raise HoldLifecycleError("持座已取消，不可续期")
    if hold.status != STATUS_HELD:
        raise HoldLifecycleError(f"当前状态 {hold.status} 不可续期")

    if hold.expires_at is not None and hold.expires_at <= now:
        # 已过点但扫描尚未跑到：先释放再拒绝，杜绝给死座续命
        hold.status = STATUS_RELEASED
        db.commit()
        raise HoldLifecycleError("持座已到期释放，不可续期")

    if hold.renewals_used >= limit:
        raise HoldLifecycleError(f"续期次数已用完（上限 {limit} 次）")

    hold.renewals_used += 1
    # 以“当前时刻与原到期的较晚者”为起点顺延，临近到期续也能拿到完整窗口
    base = now if hold.expires_at is None or now > hold.expires_at else hold.expires_at
    hold.expires_at = base + timedelta(seconds=ttl)
    db.commit()
    db.refresh(hold)
    return hold


def cancel_hold(db: Session, hold: SeatHold) -> SeatHold:
    """主动取消：只有持有中的记录可取消。"""
    if hold.status != STATUS_HELD:
        raise HoldLifecycleError("仅持有中的记录可取消")
    hold.status = STATUS_CANCELLED
    db.commit()
    db.refresh(hold)
    return hold
