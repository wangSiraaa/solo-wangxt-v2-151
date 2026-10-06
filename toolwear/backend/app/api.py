"""REST API 路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .load_model import (DISCLAIMER, INTERRUPTED_FACTOR, MATERIAL_FACTORS,
                         MODEL_ID, REF_RPM, SPEED_EXPONENT)
from .models import Assignment, Machine, Tool
from .schemas import (MountIn, SegmentBatchIn, ToolCreate, TransferIn,
                      UnmountIn)
from .seed import seed_demo
from .services import (DomainError, current_assignment, ingest_segments,
                       life_estimate, mount_tool, tool_load_report,
                       transfer_tool, unmount_tool)

router = APIRouter(prefix="/api")


def _err(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _machine_by_code(db: Session, code: str) -> Machine:
    m = db.scalars(select(Machine).where(Machine.code == code)).first()
    if m is None:
        raise HTTPException(status_code=404, detail=f"未知机床 {code}")
    return m


def _tool_or_404(db: Session, tool_id: int) -> Tool:
    t = db.get(Tool, tool_id)
    if t is None:
        raise HTTPException(status_code=404, detail="刀具不存在")
    return t


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/model-info")
def model_info():
    """模型参数与免责声明（前端横幅展示）。"""
    return {
        "model": MODEL_ID,
        "params": {
            "ref_rpm": REF_RPM,
            "speed_exponent": SPEED_EXPONENT,
            "interrupted_factor": INTERRUPTED_FACTOR,
            "material_factors": MATERIAL_FACTORS,
        },
        "disclaimer": DISCLAIMER,
    }


@router.get("/machines")
def list_machines(db: Session = Depends(get_db)):
    """机床刀位看板：每个刀位当前装的是哪把刀。"""
    machines = db.scalars(select(Machine).order_by(Machine.code)).all()
    current = db.scalars(
        select(Assignment).where(Assignment.unmounted_at.is_(None))
    ).all()
    by_slot = {(a.machine_id, a.position): a for a in current}
    tools = {t.id: t for t in db.scalars(select(Tool)).all()}
    out = []
    for m in machines:
        slots = []
        for pos in range(1, m.positions + 1):
            a = by_slot.get((m.id, pos))
            slots.append({
                "position": pos,
                "tool": ({"id": a.tool_id, "code": tools[a.tool_id].code,
                          "edge_id": a.edge_id}
                         if a and a.tool_id in tools else None),
            })
        out.append({"id": m.id, "code": m.code, "name": m.name, "slots": slots})
    return out


@router.post("/tools", status_code=201)
def create_tool(payload: ToolCreate, db: Session = Depends(get_db)):
    if db.scalars(select(Tool).where(Tool.code == payload.code)).first():
        raise HTTPException(status_code=409, detail="刀具编号已存在")
    from .models import ToolEdge
    tool = Tool(code=payload.code, material=payload.material,
                edge_count=payload.edge_count,
                nominal_capacity=payload.nominal_capacity)
    db.add(tool)
    db.flush()
    for i in range(1, payload.edge_count + 1):
        db.add(ToolEdge(tool_id=tool.id, edge_index=i))
    db.commit()
    return {"id": tool.id, "code": tool.code}


@router.get("/tools")
def list_tools(db: Session = Depends(get_db)):
    """刀具列表：当前位置 + 已知负载 + 消耗区间（非倒计时）。"""
    tools = db.scalars(select(Tool).order_by(Tool.code)).all()
    machines = {m.id: m for m in db.scalars(select(Machine)).all()}
    out = []
    for t in tools:
        cur = current_assignment(db, t.id)
        est = life_estimate(db, t.id)
        out.append({
            "id": t.id,
            "code": t.code,
            "material": t.material,
            "edge_count": t.edge_count,
            "location": ({"machine_code": machines[cur.machine_id].code,
                          "position": cur.position} if cur else None),
            "total_known_load": est["consumption_ratio_known"] * t.nominal_capacity,
            "band": est["band"],
            "band_label": est["band_label"],
            "unknown_segment_count": est["unknown_segment_count"],
        })
    return out


@router.get("/tools/{tool_id}")
def tool_detail(tool_id: int, db: Session = Depends(get_db)):
    t = _tool_or_404(db, tool_id)
    machines = {m.id: m for m in db.scalars(select(Machine)).all()}
    history = db.scalars(
        select(Assignment).where(Assignment.tool_id == tool_id)
        .order_by(Assignment.mounted_at)
    ).all()
    return {
        "id": t.id,
        "code": t.code,
        "material": t.material,
        "nominal_capacity": t.nominal_capacity,
        "edges": [{"id": e.id, "edge_index": e.edge_index, "status": e.status}
                  for e in t.edges],
        "assignments": [{
            "id": a.id,
            "machine_code": machines[a.machine_id].code,
            "position": a.position,
            "edge_id": a.edge_id,
            "mounted_at": a.mounted_at.isoformat(),
            "unmounted_at": a.unmounted_at.isoformat() if a.unmounted_at else None,
        } for a in history],
    }


@router.post("/tools/{tool_id}/mount", status_code=201)
def mount(tool_id: int, payload: MountIn, db: Session = Depends(get_db)):
    _tool_or_404(db, tool_id)
    machine = _machine_by_code(db, payload.machine_code)
    try:
        a = mount_tool(db, tool_id=tool_id, machine_id=machine.id,
                       position=payload.position, edge_id=payload.edge_id,
                       at=payload.at)
        db.commit()
    except DomainError as exc:
        db.rollback()
        raise _err(exc)
    return {"assignment_id": a.id}


@router.post("/tools/{tool_id}/unmount")
def unmount(tool_id: int, payload: UnmountIn, db: Session = Depends(get_db)):
    _tool_or_404(db, tool_id)
    try:
        a = unmount_tool(db, tool_id=tool_id, at=payload.at)
        db.commit()
    except DomainError as exc:
        db.rollback()
        raise _err(exc)
    return {"assignment_id": a.id, "unmounted_at": a.unmounted_at.isoformat()}


@router.post("/tools/{tool_id}/transfer", status_code=201)
def transfer(tool_id: int, payload: TransferIn, db: Session = Depends(get_db)):
    """转机床：刀具身份与全部历史保留。"""
    _tool_or_404(db, tool_id)
    machine = _machine_by_code(db, payload.machine_code)
    try:
        a = transfer_tool(db, tool_id=tool_id, machine_id=machine.id,
                          position=payload.position, edge_id=payload.edge_id,
                          at=payload.at)
        db.commit()
    except DomainError as exc:
        db.rollback()
        raise _err(exc)
    return {"assignment_id": a.id, "note": "刀具身份与历史保留，仅变更装刀区间"}


@router.post("/segments:batch", status_code=201)
def ingest(payload: SegmentBatchIn, db: Session = Depends(get_db)):
    """批量接收区段；乱序到达不影响按发生时间的归属与累计。"""
    try:
        result = ingest_segments(db, [s.model_dump() for s in payload.segments])
        db.commit()
    except DomainError as exc:
        db.rollback()
        raise _err(exc)
    return result


@router.get("/tools/{tool_id}/load")
def load_report(tool_id: int, db: Session = Depends(get_db)):
    """负载报告：总量可回溯到每个区段与工序号；未知负载单独列出。"""
    _tool_or_404(db, tool_id)
    return tool_load_report(db, tool_id)


@router.get("/tools/{tool_id}/life-estimate")
def estimate(tool_id: int, db: Session = Depends(get_db)):
    """消耗区间估计（定性区间，非失效倒计时）。"""
    _tool_or_404(db, tool_id)
    return life_estimate(db, tool_id)


@router.post("/seed/demo")
def seed(db: Session = Depends(get_db)):
    """重建演示案例（清空现有数据）。"""
    return seed_demo(db)
