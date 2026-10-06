"""演示数据：断续切削、事件乱序、同刀不同材料（含转机床与缺失工况）。"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Assignment, Machine, Segment, Tool, ToolEdge
from .services import ingest_segments, mount_tool, transfer_tool


def reset_all(session: Session) -> None:
    for table in (Segment, Assignment, ToolEdge, Tool, Machine):
        session.execute(delete(table))
    session.flush()


def _mk_tool(session: Session, code: str, material: str, edges: int,
             capacity: float = 100.0) -> Tool:
    tool = Tool(code=code, material=material, edge_count=edges,
                nominal_capacity=capacity)
    session.add(tool)
    session.flush()
    for i in range(1, edges + 1):
        session.add(ToolEdge(tool_id=tool.id, edge_index=i))
    session.flush()
    return tool


def seed_demo(session: Session) -> dict:
    """重建全部演示案例。返回摘要。"""
    reset_all(session)

    m1 = Machine(code="MC-01", name="立式加工中心 1", positions=4)
    m2 = Machine(code="MC-02", name="立式加工中心 2", positions=4)
    session.add_all([m1, m2])
    session.flush()

    base = datetime.now(timezone.utc) - timedelta(days=2)

    # ---- 案例 1：断续切削 ------------------------------------------------
    # T-1001 在 MC-01#1 加工钢件，12 段短切削（入刀/出刀冲击），段间空转。
    t1 = _mk_tool(session, "T-1001", "coated_carbide", edges=4)
    mount_tool(session, tool_id=t1.id, machine_id=m1.id, position=1,
               edge_id=None, at=base)
    segs: list[dict] = []
    cursor = base + timedelta(minutes=5)
    for i in range(12):
        start = cursor
        end = start + timedelta(minutes=3)
        segs.append(dict(machine_code="MC-01", position=1, started_at=start,
                         ended_at=end, kind="cut", rpm=9000.0, feed=1200.0,
                         workpiece_material="steel", interrupted=True,
                         operation_ref=f"OP-10/{i + 1:02d}"))
        cursor = end
        if i < 11:  # 段间空转 2 分钟（不计负载但计时长）
            segs.append(dict(machine_code="MC-01", position=1, started_at=cursor,
                             ended_at=cursor + timedelta(minutes=2), kind="idle",
                             operation_ref=f"OP-10/{i + 1:02d}"))
            cursor += timedelta(minutes=2)
    ingest_segments(session, segs)

    # ---- 案例 2：事件乱序 ------------------------------------------------
    # T-1002 在 MC-01#2。区段按打乱顺序上报，系统按发生时间归属累计。
    t2 = _mk_tool(session, "T-1002", "coated_carbide", edges=2)
    mount_tool(session, tool_id=t2.id, machine_id=m1.id, position=2,
               edge_id=None, at=base)
    ordered: list[dict] = []
    cursor = base + timedelta(minutes=10)
    for i in range(6):
        start = cursor
        end = start + timedelta(minutes=5)
        ordered.append(dict(machine_code="MC-01", position=2, started_at=start,
                            ended_at=end, kind="cut", rpm=8000.0, feed=1000.0,
                            workpiece_material="aluminum",
                            operation_ref=f"OP-20/{i + 1:02d}"))
        cursor = end + timedelta(minutes=1)
        ordered.append(dict(machine_code="MC-01", position=2,
                            started_at=end, ended_at=cursor, kind="pause",
                            operation_ref=f"OP-20/{i + 1:02d}"))
    shuffled = ordered.copy()
    random.Random(20261006).shuffle(shuffled)   # 固定种子：可复现的乱序
    ingest_segments(session, shuffled)

    # ---- 案例 3：同刀不同材料 + 转机床 + 缺失工况 -------------------------
    # T-1003 先在 MC-01#3 加工铝、钢，再转到 MC-02#1 加工钛；
    # 其中一段切削缺少转速与材料 -> 负载未知（不计零）。
    t3 = _mk_tool(session, "T-1003", "ceramic", edges=6, capacity=120.0)
    mount_tool(session, tool_id=t3.id, machine_id=m1.id, position=3,
               edge_id=None, at=base)
    t_al = base + timedelta(minutes=15)
    segs3 = [
        dict(machine_code="MC-01", position=3, started_at=t_al,
             ended_at=t_al + timedelta(minutes=10), kind="cut", rpm=10000.0,
             feed=1500.0, workpiece_material="aluminum", operation_ref="OP-30/铝"),
        dict(machine_code="MC-01", position=3,
             started_at=t_al + timedelta(minutes=10),
             ended_at=t_al + timedelta(minutes=12), kind="idle",
             operation_ref="OP-30/铝"),
        dict(machine_code="MC-01", position=3,
             started_at=t_al + timedelta(minutes=12),
             ended_at=t_al + timedelta(minutes=20), kind="cut", rpm=7500.0,
             feed=900.0, workpiece_material="steel", operation_ref="OP-31/钢"),
    ]
    ingest_segments(session, segs3)

    transfer_at = t_al + timedelta(minutes=25)
    transfer_tool(session, tool_id=t3.id, machine_id=m2.id, position=1,
                  edge_id=None, at=transfer_at)

    t_ti = transfer_at + timedelta(minutes=5)
    segs3b = [
        dict(machine_code="MC-02", position=1, started_at=t_ti,
             ended_at=t_ti + timedelta(minutes=6), kind="cut", rpm=6000.0,
             feed=600.0, workpiece_material="titanium", operation_ref="OP-32/钛"),
        # 缺失工况（无转速、无材料）的切削段：负载未知
        dict(machine_code="MC-02", position=1,
             started_at=t_ti + timedelta(minutes=6),
             ended_at=t_ti + timedelta(minutes=11), kind="cut",
             operation_ref="OP-33/工况缺失"),
        dict(machine_code="MC-02", position=1,
             started_at=t_ti + timedelta(minutes=11),
             ended_at=t_ti + timedelta(minutes=14), kind="pause",
             operation_ref="OP-33/工况缺失"),
    ]
    ingest_segments(session, segs3b)

    session.commit()
    return {
        "machines": ["MC-01", "MC-02"],
        "tools": ["T-1001", "T-1002", "T-1003"],
        "cases": [
            "断续切削：T-1001 十二段短切削+段间空转",
            "事件乱序：T-1002 区段乱序上报，按发生时间累计",
            "同刀不同材料：T-1003 铝/钢/钛 + 转机床 MC-01→MC-02 + 一段未知负载",
        ],
    }


def is_seeded(session: Session) -> bool:
    return session.scalars(select(Tool.id).limit(1)).first() is not None
