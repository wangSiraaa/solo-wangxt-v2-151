from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


# ----- requests -----------------------------------------------------------
class ToolCreate(BaseModel):
    tool_code: str
    edge_type: str
    material_grade: str


class MachineCreate(BaseModel):
    machine_code: str
    name: str
    positions: int = 8


class MountRequest(BaseModel):
    tool_code: str
    machine_code: str
    position: int = Field(ge=0)
    mounted_at: datetime | None = None
    note: str | None = None


class DismountRequest(BaseModel):
    machine_code: str
    position: int = Field(ge=0)
    dismounted_at: datetime | None = None


class EventIn(BaseModel):
    tool_code: str
    machine_code: str
    kind: str = Field(pattern="^(cutting|idle|pause)$")
    start_ts: datetime
    end_ts: datetime
    rpm: float | None = None
    feed_mm_rev: float | None = Field(default=None, ge=0)
    depth_mm: float | None = Field(default=None, ge=0)
    material: str | None = None
    interrupted: bool = False

    @field_validator("end_ts")
    @classmethod
    def _end_after_start(cls, v, info):
        start = info.data.get("start_ts")
        if start and v <= start:
            raise ValueError("end_ts must be after start_ts")
        return v


class EventBatch(BaseModel):
    events: list[EventIn]


# ----- responses ----------------------------------------------------------
class ToolOut(BaseModel):
    id: int
    tool_code: str
    edge_type: str
    material_grade: str
    accum_cutting_min: float
    accum_known_load: float
    accum_unknown_min: float
    current_machine: str | None = None
    current_position: int | None = None

    model_config = {"from_attributes": True}


class SegmentOut(BaseModel):
    id: int
    phase: str
    start_ts: datetime
    end_ts: datetime
    duration_min: float
    rpm: float | None
    feed_mm_rev: float | None
    depth_mm: float | None
    material: str | None
    interrupted: bool
    load: float | None          # null => unknown, never zero
    unknown_reason: str | None
    source_event_ids: list[int]

    model_config = {"from_attributes": True}


class LoadReport(BaseModel):
    tool_code: str
    edge_type: str
    material_grade: str
    cutting_load_known: float
    idle_load: float
    pause_load: float
    cutting_min: float
    unknown_min: float
    idle_min: float
    pause_min: float
    known_fraction_of_cutting_min: float
    total_accounted_load: float
    # Estimated fraction of demo "life budget" consumed by KNOWN cutting only.
    # NOT a failure countdown: unknown time is uncounted and the model is
    # unvalidated demo parameters.
    estimated_remaining_fraction: float
    by_material: dict[str, float]
    segments: list[SegmentOut]
    disclaimer: str


DISCLAIMER = (
    "Loads are computed from a fixed demo empirical model "
    "(see /api/model-params). They are estimates from process records, "
    "not connected to real machine signals and must not replace "
    "qualified tool-change / safety decisions. 'unknown' segments carry "
    "missing or conflicting conditions and are never counted as zero; "
    "remaining fraction is an estimate, not a failure countdown."
)
