"""Segmentation + rollup logic bridging the DB and the NumPy model."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import model as m
from .schemas import DISCLAIMER
from .models import (Machine, MountHistory, ProcessEvent, Segment,
                     SegmentSource, Tool)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _naive_utc(dt: datetime) -> datetime:
    """Normalize to naive UTC for portable comparison/storage."""
    return _aware(dt).astimezone(timezone.utc).replace(tzinfo=None)


def get_or_404(db: Session, model, **kw):
    obj = db.scalar(select(model).filter_by(**kw))
    if obj is None:
        raise LookupError(f"{model.__name__} {kw} not found")
    return obj


# ----- mounts -------------------------------------------------------------
def mount_tool(db: Session, tool: Tool, machine: Machine, position: int,
               mounted_at: datetime, note: str | None) -> MountHistory:
    if position >= machine.positions:
        raise ValueError(
            f"position {position} exceeds machine {machine.machine_code} "
            f"capacity ({machine.positions} positions)")

    # A tool can only occupy one open mount; a position holds one tool.
    open_tool = db.scalar(select(MountHistory).where(
        MountHistory.tool_id == tool.id,
        MountHistory.dismounted_at.is_(None)))
    if open_tool is not None:
        raise ValueError(
            f"tool {tool.tool_code} is already mounted at "
            f"{open_tool.machine_id}#{open_tool.position}; dismount first")
    open_pos = db.scalar(select(MountHistory).where(
        MountHistory.machine_id == machine.id,
        MountHistory.position == position,
        MountHistory.dismounted_at.is_(None)))
    if open_pos is not None:
        raise ValueError(
            f"position {position} on {machine.machine_code} is occupied")

    row = MountHistory(tool_id=tool.id, machine_id=machine.id,
                       position=position, mounted_at=_naive_utc(mounted_at),
                       note=note)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def dismount(db: Session, machine: Machine, position: int,
             at: datetime) -> MountHistory:
    row = db.scalar(select(MountHistory).where(
        MountHistory.machine_id == machine.id,
        MountHistory.position == position,
        MountHistory.dismounted_at.is_(None)))
    if row is None:
        raise LookupError("no open mount at that position")
    row.dismounted_at = _naive_utc(at)
    db.commit()
    db.refresh(row)
    return row


def current_mount(db: Session, tool_id: int) -> MountHistory | None:
    return db.scalar(select(MountHistory).where(
        MountHistory.tool_id == tool_id,
        MountHistory.dismounted_at.is_(None)))


# ----- ingestion ----------------------------------------------------------
def ingest_events(db: Session, payloads) -> list[ProcessEvent]:
    rows: list[ProcessEvent] = []
    for ev in payloads:
        tool = get_or_404(db, Tool, tool_code=ev.tool_code)
        machine = get_or_404(db, Machine, machine_code=ev.machine_code)
        rows.append(ProcessEvent(
            tool_id=tool.id, machine_id=machine.id, kind=ev.kind,
            start_ts=_naive_utc(ev.start_ts), end_ts=_naive_utc(ev.end_ts),
            rpm=ev.rpm, feed_mm_rev=ev.feed_mm_rev, depth_mm=ev.depth_mm,
            material=ev.material, interrupted=ev.interrupted))
    db.add_all(rows)
    db.commit()
    for r in rows:
        db.refresh(r)
    return rows


def _mount_at(db: Session, tool_id: int, when: datetime) -> MountHistory | None:
    """The mount that covers `when`; falls back to an open mount."""
    when_naive = _aware(when).astimezone(timezone.utc).replace(tzinfo=None)
    return db.scalar(select(MountHistory).where(
        MountHistory.tool_id == tool_id,
        MountHistory.mounted_at <= when_naive,
        (MountHistory.dismounted_at.is_(None)) |
        (MountHistory.dismounted_at >= when_naive)))


def rebuild_segments(db: Session, tool_id: int) -> list[Segment]:
    """Recompute all segments for a tool from its raw events.

    Deterministic: safe to re-run after out-of-order events arrive.
    Previous segments and provenance links are replaced.
    """
    events = list(db.scalars(select(ProcessEvent).where(
        ProcessEvent.tool_id == tool_id).order_by(ProcessEvent.start_ts)))

    old = list(db.scalars(select(Segment).where(Segment.tool_id == tool_id)))
    if old:
        old_ids = [s.id for s in old]
        db.query(SegmentSource).filter(
            SegmentSource.segment_id.in_(old_ids)).delete(
            synchronize_session=False)
    for s in old:
        db.delete(s)
    db.flush()

    if not events:
        db.commit()
        return []

    t0 = min(_aware(e.start_ts) for e in events)

    def epoch(dt: datetime) -> float:
        return (_aware(dt) - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()

    mevents = [m.Event(
        kind=e.kind, start=epoch(e.start_ts), end=epoch(e.end_ts),
        rpm=e.rpm, feed_mm_rev=e.feed_mm_rev, depth_mm=e.depth_mm,
        material=e.material, interrupted=e.interrupted, id=e.id)
        for e in events]

    seg_rows: list[Segment] = []
    id_by_pk = {e.id: e for e in events}
    for seg in m.build_segments(mevents):
        start_dt = _naive_utc(
            datetime.fromtimestamp(seg.start, tz=timezone.utc))
        end_dt = _naive_utc(
            datetime.fromtimestamp(seg.end, tz=timezone.utc))
        mount = _mount_at(db, tool_id, start_dt)
        machine_id = mount.machine_id if mount else id_by_pk[
            seg.source_event_ids[0]].machine_id if seg.source_event_ids else None
        row = Segment(
            tool_id=tool_id, mount_id=mount.id if mount else None,
            machine_id=machine_id, phase=seg.phase.value,
            start_ts=start_dt, end_ts=end_dt, rpm=seg.rpm,
            feed_mm_rev=seg.feed_mm_rev, depth_mm=seg.depth_mm,
            material=seg.material, interrupted=seg.interrupted,
            load=seg.load, unknown_reason=seg.unknown_reason)
        db.add(row)
        db.flush()
        for eid in seg.source_event_ids:
            db.add(SegmentSource(segment_id=row.id, event_id=eid))
        seg_rows.append(row)

    _rollup_tool(db, tool_id)
    db.commit()
    for r in seg_rows:
        db.refresh(r)
    return seg_rows


def _rollup_tool(db: Session, tool_id: int) -> None:
    segs = list(db.scalars(select(Segment).where(Segment.tool_id == tool_id)))
    cutting = [s for s in segs if s.phase == "cutting"]
    known = [s for s in cutting if s.load is not None]
    unknown = [s for s in cutting if s.load is None]
    idle = [s for s in segs if s.phase == "idle"]

    loads = np.array([s.load for s in known], dtype=float)
    tool = db.get(Tool, tool_id)
    tool.accum_known_load = float(loads.sum()) if len(loads) else 0.0
    tool.accum_cutting_min = float(sum(
        (s.end_ts - s.start_ts).total_seconds() / 60.0 for s in cutting))
    tool.accum_unknown_min = float(sum(
        (s.end_ts - s.start_ts).total_seconds() / 60.0 for s in unknown))


# ----- reporting ----------------------------------------------------------
def load_report(db: Session, tool: Tool) -> dict:
    seg_rows = list(db.scalars(select(Segment).where(
        Segment.tool_id == tool.id).order_by(Segment.start_ts)))

    source_map: dict[int, list[int]] = defaultdict(list)
    if seg_rows:
        ids = [s.id for s in seg_rows]
        links = db.query(SegmentSource.segment_id,
                         SegmentSource.event_id).filter(
            SegmentSource.segment_id.in_(ids)).all()
        for sid, eid in links:
            source_map[sid].append(eid)

    segments = []
    by_material: dict[str, float] = defaultdict(float)
    cutting_load = idle_load = 0.0
    cut_min = unknown_min = idle_min = pause_min = 0.0
    known_cut_min = 0.0

    for s in seg_rows:
        dur = (s.end_ts - s.start_ts).total_seconds() / 60.0
        segments.append({
            "id": s.id, "phase": s.phase, "start_ts": s.start_ts,
            "end_ts": s.end_ts, "duration_min": dur, "rpm": s.rpm,
            "feed_mm_rev": s.feed_mm_rev, "depth_mm": s.depth_mm,
            "material": s.material, "interrupted": s.interrupted,
            "load": s.load, "unknown_reason": s.unknown_reason,
            "source_event_ids": sorted(source_map.get(s.id, [])),
        })
        if s.phase == "cutting":
            cut_min += dur
            if s.load is not None:
                cutting_load += s.load
                known_cut_min += dur
                by_material[s.material or "unknown"] += s.load
            else:
                unknown_min += dur
        elif s.phase == "idle":
            idle_min += dur
            idle_load += s.load or 0.0
        else:
            pause_min += dur

    return {
        "tool_code": tool.tool_code,
        "edge_type": tool.edge_type,
        "material_grade": tool.material_grade,
        "cutting_load_known": cutting_load,
        "idle_load": idle_load,
        "pause_load": 0.0,
        "cutting_min": cut_min,
        "unknown_min": unknown_min,
        "idle_min": idle_min,
        "pause_min": pause_min,
        "known_fraction_of_cutting_min":
            (known_cut_min / cut_min) if cut_min else 1.0,
        "total_accounted_load": cutting_load + idle_load,
        "estimated_remaining_fraction": max(0.0, 1.0 - cutting_load),
        "by_material": dict(by_material),
        "segments": segments,
        "disclaimer": DISCLAIMER,
    }
