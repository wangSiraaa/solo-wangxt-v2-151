"""Seed demo data: two machines, three tools, three showcase scenarios.

Run with the app environment active:
    python -m app.seed
Idempotent enough for a throwaway demo database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .database import SessionLocal, init_db
from . import service
from .models import Machine, Tool
from .schemas import EventIn

T0 = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)


def _ev(tool, machine, kind, s, e, **kw):
    return EventIn(
        tool_code=tool, machine_code=machine, kind=kind,
        start_ts=T0 + timedelta(minutes=s), end_ts=T0 + timedelta(minutes=e),
        **kw)


def seed() -> None:
    init_db()
    db = SessionLocal()

    def get_or_make(model, defaults: dict, **key):
        row = db.scalar(select(model).filter_by(**key))
        if row is None:
            row = model(**key, **defaults)
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    mca = get_or_make(Machine, {"name": "CNC Lathe A", "positions": 4},
                      machine_code="MC-A")
    mcb = get_or_make(Machine, {"name": "Machining Center B", "positions": 6},
                      machine_code="MC-B")
    t1 = get_or_make(Tool, {"edge_type": "carbide_insert_p2t",
                            "material_grade": "P25-TiN"}, tool_code="T-100")
    t2 = get_or_make(Tool, {"edge_type": "end_mill_4f_d10",
                            "material_grade": "K20"}, tool_code="T-200")
    t3 = get_or_make(Tool, {"edge_type": "carbide_insert_p2t",
                            "material_grade": "P25"}, tool_code="T-300")

    # Case 1: interrupted cut + idle + pause, rpm/material transitions
    service.mount_tool(db, t1, mca, 0, T0 - timedelta(minutes=10),
                       "roughing blade, case 1")
    service.ingest_events(db, [
        _ev("T-100", "MC-A", "cutting", 0, 3, rpm=2000, feed_mm_rev=0.2,
            depth_mm=2.0, material="steel_45"),
        # interrupted facing of stainless: impact factor applies
        _ev("T-100", "MC-A", "cutting", 3, 6, rpm=2400, feed_mm_rev=0.15,
            depth_mm=1.5, material="stainless_304", interrupted=True),
        _ev("T-100", "MC-A", "idle", 6, 9),
        _ev("T-100", "MC-A", "pause", 9, 12),
        # missing material -> unknown load, NOT zero
        _ev("T-100", "MC-A", "cutting", 12, 15, rpm=2000, feed_mm_rev=0.2,
            depth_mm=2.0),
    ])
    service.rebuild_segments(db, t1.id)

    # Case 2: events ingested OUT OF ORDER (late + early + a gap fill)
    service.mount_tool(db, t2, mcb, 2, T0 - timedelta(minutes=10),
                       "case 2: unordered records")
    service.ingest_events(db, [
        _ev("T-200", "MC-B", "cutting", 20, 24, rpm=3000, feed_mm_rev=0.12,
            depth_mm=1.0, material="aluminum_6061"),  # arrives first
        _ev("T-200", "MC-B", "idle", 24, 26),
    ])
    service.ingest_events(db, [
        _ev("T-200", "MC-B", "cutting", 0, 20, rpm=2200, feed_mm_rev=0.18,
            depth_mm=1.2, material="cast_iron_ht250"),  # late arrival
    ])
    service.rebuild_segments(db, t2.id)

    # Case 3: same tool, different materials, then TRANSFER to another machine
    service.mount_tool(db, t3, mca, 1, T0 - timedelta(minutes=10),
                       "case 3: starts on lathe")
    service.ingest_events(db, [
        _ev("T-300", "MC-A", "cutting", 0, 8, rpm=2000, feed_mm_rev=0.2,
            depth_mm=2.0, material="steel_45"),
        _ev("T-300", "MC-A", "cutting", 8, 14, rpm=2000, feed_mm_rev=0.2,
            depth_mm=2.0, material="cast_iron_ht250"),
    ])
    service.dismount(db, mca, 1, T0 + timedelta(minutes=30))
    service.mount_tool(db, t3, mcb, 3, T0 + timedelta(minutes=40),
                       "transferred: identity and history retained")
    service.ingest_events(db, [
        _ev("T-300", "MC-B", "cutting", 45, 52, rpm=2600, feed_mm_rev=0.14,
            depth_mm=1.5, material="aluminum_6061"),
    ])
    service.rebuild_segments(db, t3.id)

    db.close()
    print("Seeded T-100 (interrupted/idle/pause/unknown), "
          "T-200 (out-of-order), T-300 (multi-material + transfer).")


if __name__ == "__main__":
    seed()
