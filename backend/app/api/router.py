from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.models import (
    STATUS_CANCELLED,
    STATUS_HELD,
    STATUS_RELEASED,
    ConflictLog,
    Hall,
    SeatHold,
    Showtime,
)
from app.schemas.schemas import (
    ConflictOut,
    HallOut,
    HoldOut,
    HoldRequest,
    SeatMapCell,
    SeatMapOut,
    ShowtimeOut,
)
from app.services.bond_engine import (
    HoldSpan,
    SeatCell,
    conflicts_with,
    find_bond_across_rows,
    find_contiguous_block,
)
from app.services.hold_lifecycle import (
    HoldLifecycleError,
    cancel_hold,
    expire_holds,
    renew_hold,
)

api_router = APIRouter()

VALID_STATUSES = {STATUS_HELD, STATUS_RELEASED, STATUS_CANCELLED}


def _aisles(hall: Hall) -> list[int]:
    if not hall.aisle_cols.strip():
        return []
    return [int(x) for x in hall.aisle_cols.split(",") if x.strip()]


def _hall_out(h: Hall) -> HallOut:
    return HallOut(id=h.id, name=h.name, rows=h.rows, cols=h.cols, aisle_cols=_aisles(h))


def _hold_out(h: SeatHold) -> HoldOut:
    return HoldOut(
        id=h.id,
        showtime_id=h.showtime_id,
        order_code=h.order_code,
        row=h.row,
        start_col=h.start_col,
        end_col=h.end_col,
        party_size=h.party_size,
        status=h.status,
        created_at=h.created_at,
        expires_at=h.expires_at,
        renewals_used=h.renewals_used,
        renewals_left=max(0, settings.max_renewals - h.renewals_used),
    )


@api_router.get("/health")
def health():
    return {"status": "ok"}


@api_router.get("/halls", response_model=list[HallOut])
def list_halls(db: Session = Depends(get_db)):
    return [_hall_out(h) for h in db.scalars(select(Hall).order_by(Hall.id)).all()]


@api_router.get("/showtimes", response_model=list[ShowtimeOut])
def list_showtimes(db: Session = Depends(get_db)):
    rows = db.scalars(select(Showtime).order_by(Showtime.start_at)).all()
    out = []
    for s in rows:
        hall = db.get(Hall, s.hall_id)
        out.append(
            ShowtimeOut(
                id=s.id,
                hall_id=s.hall_id,
                film_title=s.film_title,
                start_at=s.start_at,
                hall_name=hall.name if hall else None,
            )
        )
    return out


@api_router.get("/seatmap/{showtime_id}", response_model=SeatMapOut)
def seatmap(showtime_id: int, db: Session = Depends(get_db)):
    st = db.get(Showtime, showtime_id)
    if not st:
        raise HTTPException(404, "场次不存在")
    hall = db.get(Hall, st.hall_id)
    assert hall
    # 出图前先跑超时扫描：到期座位必须腾空，续过期的按新时刻仍占用
    expire_holds(db, showtime_id=showtime_id)
    aisles = set(_aisles(hall))
    holds = db.scalars(
        select(SeatHold).where(
            SeatHold.showtime_id == showtime_id, SeatHold.status == STATUS_HELD
        )
    ).all()
    occupied: set[tuple[int, int]] = set()
    for h in holds:
        for c in range(h.start_col, h.end_col + 1):
            occupied.add((h.row, c))
    cells: list[SeatMapCell] = []
    for r in range(1, hall.rows + 1):
        for c in range(1, hall.cols + 1):
            occ = (r, c) in occupied
            cells.append(
                SeatMapCell(
                    row=r,
                    col=c,
                    is_aisle=c in aisles,
                    occupied=occ,
                    heat=1.0 if occ else (0.15 if c in aisles else 0.0),
                )
            )
    return SeatMapOut(
        showtime_id=showtime_id,
        hall_name=hall.name,
        rows=hall.rows,
        cols=hall.cols,
        cells=cells,
    )


@api_router.get("/holds", response_model=list[HoldOut])
def list_holds(
    status: str | None = Query(default=None, description="held / released / cancelled"),
    db: Session = Depends(get_db),
):
    # 读列表即触发超时扫描，保证状态与到期时刻一致
    expire_holds(db)
    stmt = select(SeatHold).order_by(SeatHold.id.desc())
    if status is not None:
        if status not in VALID_STATUSES:
            raise HTTPException(400, f"未知状态：{status}")
        stmt = stmt.where(SeatHold.status == status)
    return [_hold_out(h) for h in db.scalars(stmt).all()]


@api_router.get("/conflicts", response_model=list[ConflictOut])
def list_conflicts(db: Session = Depends(get_db)):
    return db.scalars(select(ConflictLog).order_by(ConflictLog.id.desc())).all()


@api_router.post("/holds", response_model=HoldOut)
def create_hold(body: HoldRequest, db: Session = Depends(get_db)):
    st = db.get(Showtime, body.showtime_id)
    if not st:
        raise HTTPException(404, "场次不存在")
    hall = db.get(Hall, st.hall_id)
    assert hall
    aisles = set(_aisles(hall))
    # 分配前先清到期持座，释放出的座位可立即被再次选中
    expire_holds(db, showtime_id=body.showtime_id)
    existing = db.scalars(
        select(SeatHold).where(
            SeatHold.showtime_id == body.showtime_id, SeatHold.status == STATUS_HELD
        )
    ).all()
    holds = [HoldSpan(row=h.row, start_col=h.start_col, end_col=h.end_col) for h in existing]
    seats_by_row: dict[int, list[SeatCell]] = {}
    for r in range(1, hall.rows + 1):
        seats_by_row[r] = [
            SeatCell(row=r, col=c, is_aisle=c in aisles) for c in range(1, hall.cols + 1)
        ]

    block = None
    if body.preferred_row:
        block = find_contiguous_block(
            seats_by_row.get(body.preferred_row, []), holds, body.preferred_row, body.party_size
        )
    if block is None:
        block = find_bond_across_rows(seats_by_row, holds, body.party_size)
    if block is None:
        db.add(
            ConflictLog(
                showtime_id=body.showtime_id,
                party_size=body.party_size,
                reason=f"无足够连续空座（人数 {body.party_size}）",
            )
        )
        db.commit()
        raise HTTPException(409, "无足够连续空座")

    hits = conflicts_with(holds, block)
    if hits:
        db.add(
            ConflictLog(
                showtime_id=body.showtime_id,
                party_size=body.party_size,
                reason=f"与既有持座重叠：第{hits[0].row}排 {hits[0].start_col}-{hits[0].end_col}",
            )
        )
        db.commit()
        raise HTTPException(409, "与既有持座冲突")

    code = f"SB-{int(datetime.utcnow().timestamp()) % 100000:05d}"
    hold = SeatHold(
        showtime_id=body.showtime_id,
        order_code=code,
        row=block.row,
        start_col=block.start_col,
        end_col=block.end_col,
        party_size=body.party_size,
        expires_at=datetime.utcnow() + timedelta(seconds=settings.hold_ttl_seconds),
        renewals_used=0,
    )
    db.add(hold)
    db.commit()
    db.refresh(hold)
    return _hold_out(hold)


@api_router.post("/holds/{hold_id}/renew", response_model=HoldOut)
def renew(hold_id: int, db: Session = Depends(get_db)):
    hold = db.get(SeatHold, hold_id)
    if not hold:
        raise HTTPException(404, "持座记录不存在")
    try:
        # 内部按当前 expires_at 判断是否已到期，续期成功后以新时刻为准
        renew_hold(db, hold)
    except HoldLifecycleError as e:
        raise HTTPException(409, str(e)) from e
    return _hold_out(hold)


@api_router.post("/holds/{hold_id}/cancel", response_model=HoldOut)
def cancel(hold_id: int, db: Session = Depends(get_db)):
    hold = db.get(SeatHold, hold_id)
    if not hold:
        raise HTTPException(404, "持座记录不存在")
    try:
        cancel_hold(db, hold)
    except HoldLifecycleError as e:
        raise HTTPException(409, str(e)) from e
    return _hold_out(hold)
