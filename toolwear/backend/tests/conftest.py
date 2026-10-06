import os

os.environ["DATABASE_URL"] = "postgresql+psycopg2://postgres@localhost:5432/toolwear_test"

import psycopg2
import pytest
from sqlalchemy import delete

from app.db import SessionLocal, engine
from app.models import Assignment, Base, Machine, Segment, Tool, ToolEdge


def _ensure_test_db():
    conn = psycopg2.connect(host="localhost", port=5432, user="postgres",
                            dbname="postgres")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname='toolwear_test'")
    if not cur.fetchone():
        cur.execute("CREATE DATABASE toolwear_test")
    conn.close()


_ensure_test_db()


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    session = SessionLocal()
    for table in (Segment, Assignment, ToolEdge, Tool, Machine):
        session.execute(delete(table))
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
