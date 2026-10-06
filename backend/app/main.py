from __future__ import annotations

from datetime import datetime, timezone

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import model
from .database import get_db, init_db
from .models import Machine, MountHistory, ProcessEvent, Segment, Tool
from .schemas import (DismountRequest, EventBatch, LoadReport, MachineCreate,
                      MountRequest, ToolCreate, ToolOut)
from . import service

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Tool Consumption Estimator (DEMO)",
    description="Estimate tool load from process records with a fixed "
                "empirical model. Not connected to real machines; not for "
                "safety-critical tool-change decisions.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"], allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "demo": True}


@app.get("/api/model-params")
def model_params() -> dict:
    return {
        "params": model.DEMO_PARAMS,
        "material_factors": model.MATERIAL_FACTORS,
        "idle_load_per_min": model.IDLE_LOAD_PER_MIN,
        "pause_load_per_min": model.PAUSE_LOAD_PER_MIN,
        "note": "Fixed demonstration parameters. Do not use for real "
                "tool-change decisions.",
    }


# ----- tools / machines ---------------------------------------------------
@app.post("/api/tools", response_model=ToolOut, status_code=201)
def create_tool(body: ToolCreate, db: Session = Depends(get_db)) -> Tool:
    if db.scalar(select(Tool).where(Tool.tool_code == body.tool_code)):
        raise HTTPException(409, "tool_code already exists")
    t = Tool(tool_code=body.tool_code, edge_type=body.edge_type,
             material_grade=body.material_grade)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@app.get("/api/tools", response_model=list[ToolOut])
def list_tools(db: Session = Depends(get_db)) -> list[Tool]:
    tools = list(db.scalars(select(Tool).order_by(Tool.id)))
    for t in tools:
        mh = service.current_mount(db, t.id)
        if mh:
            t.current_machine = db.get(Machine, mh.machine_id).machine_code
            t.current_position = mh.position
        else:
            t.current_machine = None
            t.current_position = None
    return tools


@app.get("/api/tools/{tool_code}/mount-history")
def mount_history(tool_code: str, db: Session = Depends(get_db)) -> list[dict]:
    """Identity trail: every machine/position this physical tool has been on."""
    tool = service.get_or_404(db, Tool, tool_code=tool_code)
    rows = db.scalars(select(MountHistory).where(
        MountHistory.tool_id == tool.id).order_by(
        MountHistory.mounted_at)).all()
    return [{
        "machine_code": db.get(Machine, r.machine_id).machine_code,
        "position": r.position,
        "mounted_at": r.mounted_at,
        "dismounted_at": r.dismounted_at,
        "note": r.note,
    } for r in rows]


@app.post("/api/machines", status_code=201)
def create_machine(body: MachineCreate, db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(Machine).where(
            Machine.machine_code == body.machine_code)):
        raise HTTPException(409, "machine_code already exists")
    m = Machine(machine_code=body.machine_code, name=body.name,
                positions=body.positions)
    db.add(m)
    db.commit()
    return {"id": m.id, "machine_code": m.machine_code}


@app.get("/api/machines")
def list_machines(db: Session = Depends(get_db)) -> list[dict]:
    return [{"id": x.id, "machine_code": x.machine_code, "name": x.name,
             "positions": x.positions}
            for x in db.scalars(select(Machine).order_by(Machine.id))]


@app.post("/api/mounts", status_code=201)
def mount(body: MountRequest, db: Session = Depends(get_db)) -> dict:
    tool = service.get_or_404(db, Tool, tool_code=body.tool_code)
    machine = service.get_or_404(db, Machine, machine_code=body.machine_code)
    try:
        row = service.mount_tool(
            db, tool, machine, body.position,
            body.mounted_at or datetime.now(timezone.utc), body.note)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"mount_id": row.id, "position": row.position,
            "machine_code": machine.machine_code, "tool_code": tool.tool_code}


@app.post("/api/dismounts")
def dismount(body: DismountRequest, db: Session = Depends(get_db)) -> dict:
    machine = service.get_or_404(db, Machine, machine_code=body.machine_code)
    try:
        row = service.dismount(
            db, machine, body.position,
            body.dismounted_at or datetime.now(timezone.utc))
    except LookupError as e:
        raise HTTPException(404, str(e))
    return {"dismounted": True, "tool_id": row.tool_id,
            "dismounted_at": row.dismounted_at}


@app.get("/api/machines/{machine_code}/positions")
def positions(machine_code: str, db: Session = Depends(get_db)) -> list[dict]:
    """刀位视图: every slot of a machine with the tool currently in it."""
    machine = service.get_or_404(db, Machine, machine_code=machine_code)
    open_mounts = {r.position: r for r in db.scalars(select(MountHistory).where(
        MountHistory.machine_id == machine.id,
        MountHistory.dismounted_at.is_(None))).all()}
    out = []
    for pos in range(machine.positions):
        r = open_mounts.get(pos)
        tool = db.get(Tool, r.tool_id) if r else None
        out.append({"position": pos, "occupied": r is not None,
                    "tool_code": tool.tool_code if tool else None,
                    "edge_type": tool.edge_type if tool else None,
                    "accum_known_load": tool.accum_known_load if tool else None,
                    "accum_unknown_min": tool.accum_unknown_min if tool else None})
    return out


# ----- events + segmentation ---------------------------------------------
@app.post("/api/events", status_code=201)
def add_events(body: EventBatch, db: Session = Depends(get_db)) -> dict:
    try:
        rows = service.ingest_events(db, body.events)
    except LookupError as e:
        raise HTTPException(404, str(e))
    # Rebuild per affected tool; ingestion order does not matter.
    tool_ids = {r.tool_id for r in rows}
    for tid in tool_ids:
        service.rebuild_segments(db, tid)
    return {"ingested": len(rows), "event_ids": [r.id for r in rows],
            "rebuilt_tools": sorted(tool_ids)}


@app.get("/api/tools/{tool_code}/events")
def list_events(tool_code: str, db: Session = Depends(get_db)) -> list[dict]:
    tool = service.get_or_404(db, Tool, tool_code=tool_code)
    rows = db.scalars(select(ProcessEvent).where(
        ProcessEvent.tool_id == tool.id).order_by(
        ProcessEvent.start_ts)).all()
    machine_cache = {}
    out = []
    for r in rows:
        mc = machine_cache.setdefault(r.machine_id,
                                      db.get(Machine, r.machine_id).machine_code)
        out.append({"id": r.id, "machine_code": mc, "kind": r.kind,
                    "start_ts": r.start_ts, "end_ts": r.end_ts,
                    "rpm": r.rpm, "feed_mm_rev": r.feed_mm_rev,
                    "depth_mm": r.depth_mm, "material": r.material,
                    "interrupted": r.interrupted})
    return out


@app.get("/api/tools/{tool_code}/report", response_model=LoadReport)
def tool_report(tool_code: str, db: Session = Depends(get_db)) -> dict:
    tool = service.get_or_404(db, Tool, tool_code=tool_code)
    service.rebuild_segments(db, tool.id)  # idempotent; tolerates late events
    db.refresh(tool)
    return service.load_report(db, tool)
