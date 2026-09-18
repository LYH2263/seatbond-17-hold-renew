from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text

from app.api.router import api_router
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.services.seed import seed_if_empty


def _ensure_hold_columns() -> None:
    """幂等补齐 seat_holds 新增的时效列，兼容已存在的旧库（无独立迁移工具）。"""
    inspector = inspect(engine)
    if "seat_holds" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("seat_holds")}
    ddl = []
    if "expires_at" not in existing:
        ddl.append("ALTER TABLE seat_holds ADD COLUMN expires_at TIMESTAMP")
    if "renewals_used" not in existing:
        ddl.append("ALTER TABLE seat_holds ADD COLUMN renewals_used INTEGER DEFAULT 0")
    if not ddl:
        return
    with engine.begin() as conn:
        for stmt in ddl:
            conn.execute(text(stmt))
        # 旧库把 (showtime,row,start,end) 设成唯一，会挡住释放后同段重订；
        # 占用冲突改由应用层仅对 held 记录判定，故移除该约束（仅 PG 显式约束）
        if conn.dialect.name != "sqlite":
            conn.execute(text("ALTER TABLE seat_holds DROP CONSTRAINT IF EXISTS uq_hold_span"))
        # 老持座没有到期时刻：直接置为已释放，避免它们永久占用座位图
        conn.execute(
            text(
                "UPDATE seat_holds SET status = 'released' "
                "WHERE expires_at IS NULL AND status = 'held'"
            )
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _ensure_hold_columns()
    if settings.seed_on_empty:
        db = SessionLocal()
        try:
            seed_if_empty(db)
        finally:
            db.close()
    yield


app = FastAPI(title="SeatBond", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")
