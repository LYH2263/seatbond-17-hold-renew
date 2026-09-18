import os

# Point the app at a throwaway SQLite file before any `app.*` import reads
# settings, so importing app.main never touches a real Postgres. The API
# tests themselves override get_db with their own in-memory engine.
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/seatbond_test_lifespan.db")
os.environ.setdefault("SEED_ON_EMPTY", "false")
