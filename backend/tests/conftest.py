import os

# 必须在导入 app.* 之前设置：用文件型 SQLite，便于 TestClient 跨线程访问
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/seatbond_pytest.db")
os.environ.setdefault("SEED_ON_EMPTY", "false")
os.environ.setdefault("HOLD_TTL_SECONDS", "120")
os.environ.setdefault("MAX_RENEWALS", "2")

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app


@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    with TestClient(app) as c:
        yield c
