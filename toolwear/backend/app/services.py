"""领域服务：装刀/转机床、区段归属、负载累计与回溯、消耗区间估计。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .load_model import (DISCLAIMER, KIND_CUT, KIND_IDLE, KIND_PAUSE, MODEL_ID,
                         SegmentInput, compute_loads, consumption_band)
from .models import Assignment, Machine, Segment, Tool, ToolEdge


class DomainError(Exception):
    """业务规则冲突（如刀位被占用、刀具未装机）。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- 装刀 / 转机床

def current_assignment(session: Session, tool_id: int) -> Optional[Assignment]:
    return session.scalars(
        select(Assignment)
        .where(Assignment.tool_id == tool_id, Assignment.unmounted_at.is_(None))
        .order_by(Assignment.mounted_at.desc())
    ).first()


def mount_tool(session: Session, *, tool_id: int, machine_id: int, position: int,
               edge_id: Optional[int], at: Optional[datetime]) -> Assignment:
    tool = session.get(Tool, tool_id)
    machine = session.get(Machine, machine_id)
    if not tool or not machine:
        raise DomainError("刀具或机床不存在")
    if not 1 <= position <= machine.positions:
        raise DomainError(f"刀位号超出范围（1..{machine.positions}）")
    if current_assignment(session, tool_id):
        raise DomainError("刀具已装机，请先卸刀或转机床")

    occupant = session.scalars(
        select(Assignment).where(
            Assignment.machine_id == machine_id,
            Assignment.position == position,
            Assignment.unmounted_at.is_(None),
        )
    ).first()
    if occupant:
        raise DomainError(f"刀位 {machine.code}#{position} 已被占用")

    if edge_id is None:
        edge = next((e for e in tool.edges if e.status == "active"), None)
        if edge is None:
            raise DomainError("刀具没有可用刃口")
    else:
        edge = session.get(ToolEdge, edge_id)
        if edge is None or edge.tool_id != tool_id:
            raise DomainError("刃口不属于该刀具")

    assignment = Assignment(
        tool_id=tool_id, edge_id=edge.id, machine_id=machine_id,
        position=position, mounted_at=at or _now(),
    )
    session.add(assignment)
    session.flush()
    return assignment


def unmount_tool(session: Session, *, tool_id: int,
                 at: Optional[datetime]) -> Assignment:
    cur = current_assignment(session, tool_id)
    if not cur:
        raise DomainError("刀具未装机")
    cur.unmounted_at = at or _now()
    session.flush()
    return cur


def transfer_tool(session: Session, *, tool_id: int, machine_id: int, position: int,
                  edge_id: Optional[int], at: Optional[datetime]) -> Assignment:
    """转机床：结束当前装刀区间并在新机床开新区间。刀具身份与历史不变。"""
    when = at or _now()
    cur = current_assignment(session, tool_id)
    if not cur:
        raise DomainError("刀具未装机，无法转机床")
    cur.unmounted_at = when
    session.flush()
    return mount_tool(session, tool_id=tool_id, machine_id=machine_id,
                      position=position, edge_id=edge_id or cur.edge_id, at=when)


# ---------------------------------------------------------------- 区段归属

def ingest_segments(session: Session, payloads: list[dict]) -> dict:
    """批量接收区段。乱序到达不影响归属：归属只取决于发生时间与装刀区间。"""
    machines = {m.code: m for m in session.scalars(select(Machine)).all()}
    created: list[Segment] = []
    for p in payloads:
        machine = machines.get(p["machine_code"])
        if machine is None:
            raise DomainError(f"未知机床 {p['machine_code']}")
        if p["ended_at"] <= p["started_at"]:
            raise DomainError("区段结束时间必须晚于开始时间")
        seg = Segment(
            machine_id=machine.id, position=p["position"],
            started_at=p["started_at"], ended_at=p["ended_at"],
            kind=p["kind"], rpm=p.get("rpm"), feed=p.get("feed"),
            workpiece_material=p.get("workpiece_material"),
            interrupted=bool(p.get("interrupted", False)),
            operation_ref=p.get("operation_ref"),
        )
        session.add(seg)
        created.append(seg)
    session.flush()

    # 到达时未能归属任何装刀区间的区段：保留并显式报告，不静默丢弃
    unattributed = [s.id for s in created if _find_assignment(session, s) is None]
    return {"ingested": len(created), "segment_ids": [s.id for s in created],
            "unattributed_segment_ids": unattributed}


def _find_assignment(session: Session, seg: Segment) -> Optional[Assignment]:
    return session.scalars(
        select(Assignment).where(
            Assignment.machine_id == seg.machine_id,
            Assignment.position == seg.position,
            Assignment.mounted_at <= seg.started_at,
            (Assignment.unmounted_at.is_(None))
            | (Assignment.unmounted_at > seg.started_at),
        ).order_by(Assignment.mounted_at.desc())
    ).first()


def tool_segments(session: Session, tool_id: int) -> list[tuple[Segment, Assignment]]:
    """刀具全部装刀区间覆盖到的区段，按发生时间排序（与到达顺序无关）。"""
    assignments = session.scalars(
        select(Assignment)
        .where(Assignment.tool_id == tool_id)
        .order_by(Assignment.mounted_at)
    ).all()
    if not assignments:
        return []
    pairs = []
    for a in assignments:
        q = select(Segment).where(
            Segment.machine_id == a.machine_id,
            Segment.position == a.position,
            Segment.started_at >= a.mounted_at,
        )
        if a.unmounted_at is not None:
            q = q.where(Segment.started_at < a.unmounted_at)
        for s in session.scalars(q):
            pairs.append((s, a))
    pairs.sort(key=lambda p: (p[0].started_at, p[0].id))
    return pairs


# ---------------------------------------------------------------- 负载报告与回溯

def tool_load_report(session: Session, tool_id: int) -> dict:
    """负载总量 + 逐区段明细，可回溯到工序号/机床/材料；未知负载单独列出。"""
    tool = session.get(Tool, tool_id)
    if tool is None:
        raise DomainError("刀具不存在")
    pairs = tool_segments(session, tool_id)
    machines = {m.id: m for m in session.scalars(select(Machine)).all()}

    inputs = [
        SegmentInput(kind=s.kind, started_at=s.started_at, ended_at=s.ended_at,
                     rpm=s.rpm, workpiece_material=s.workpiece_material,
                     interrupted=s.interrupted)
        for s, _ in pairs
    ]
    loads = compute_loads(inputs)

    total_known = 0.0
    per_material: dict[str, float] = defaultdict(float)
    per_operation: dict[str, float] = defaultdict(float)
    per_machine: dict[str, float] = defaultdict(float)
    per_edge: dict[int, float] = defaultdict(float)
    cut_min = idle_min = pause_min = 0.0
    unknown_segments: list[dict] = []
    details: list[dict] = []

    for (seg, assignment), load in zip(pairs, loads):
        minutes = (seg.ended_at - seg.started_at).total_seconds() / 60.0
        machine_code = machines[seg.machine_id].code
        entry = {
            "segment_id": seg.id,
            "arrival_order": seg.id,          # id 顺序即到达顺序（乱序可审计）
            "started_at": seg.started_at.isoformat(),
            "ended_at": seg.ended_at.isoformat(),
            "minutes": round(minutes, 4),
            "kind": seg.kind,
            "machine_code": machine_code,
            "position": seg.position,
            "rpm": seg.rpm,
            "workpiece_material": seg.workpiece_material,
            "interrupted": seg.interrupted,
            "operation_ref": seg.operation_ref,
            "edge_id": assignment.edge_id,
            "load": load,                      # None = 未知负载
        }
        if seg.kind == KIND_CUT:
            cut_min += minutes
            if load is None:
                unknown_segments.append(entry)
            else:
                total_known += load
                per_material[seg.workpiece_material] += load
                per_operation[seg.operation_ref or "(未标注工序)"] += load
                per_machine[machine_code] += load
                per_edge[assignment.edge_id] += load
        elif seg.kind == KIND_IDLE:
            idle_min += minutes
        else:
            pause_min += minutes
        details.append(entry)

    return {
        "tool_id": tool.id,
        "tool_code": tool.code,
        "model": MODEL_ID,
        "total_known_load": round(total_known, 6),
        "unknown_segment_count": len(unknown_segments),
        "unknown_segments": unknown_segments,
        "cut_minutes": round(cut_min, 4),
        "idle_minutes": round(idle_min, 4),
        "pause_minutes": round(pause_min, 4),
        "by_material": dict(sorted(per_material.items())),
        "by_operation": dict(sorted(per_operation.items())),
        "by_machine": dict(sorted(per_machine.items())),
        "by_edge": {str(k): v for k, v in sorted(per_edge.items())},
        "segments": details,
        "disclaimer": DISCLAIMER,
    }


def life_estimate(session: Session, tool_id: int) -> dict:
    """消耗区间估计：只给定性区间，不给精确失效倒计时。"""
    tool = session.get(Tool, tool_id)
    if tool is None:
        raise DomainError("刀具不存在")
    report = tool_load_report(session, tool_id)
    ratio = report["total_known_load"] / tool.nominal_capacity
    band, label = consumption_band(ratio)
    cut_segments = [s for s in report["segments"] if s["kind"] == KIND_CUT]
    unknown_share = (
        report["unknown_segment_count"] / len(cut_segments) if cut_segments else 0.0
    )
    return {
        "tool_id": tool.id,
        "tool_code": tool.code,
        "model": MODEL_ID,
        "consumption_ratio_known": round(ratio, 6),
        "band": band,
        "band_label": label,
        "unknown_segment_count": report["unknown_segment_count"],
        "unknown_share_of_cut_segments": round(unknown_share, 4),
        "estimate_incomplete": report["unknown_segment_count"] > 0,
        "note": "经验模型只给出消耗区间，不是剩余寿命倒计时；"
                "缺失工况的区段未计入已知负载。",
        "disclaimer": DISCLAIMER,
    }
