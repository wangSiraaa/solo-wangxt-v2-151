"""API 请求/响应模式（Pydantic）。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ToolCreate(BaseModel):
    code: str
    material: str = "coated_carbide"
    edge_count: int = Field(default=1, ge=1, le=64)
    nominal_capacity: float = Field(default=100.0, gt=0)


class MountIn(BaseModel):
    machine_code: str
    position: int = Field(ge=1)
    edge_id: Optional[int] = None       # 缺省用第一个 active 刃口
    at: Optional[datetime] = None       # 缺省为服务器当前时间


class TransferIn(BaseModel):
    machine_code: str
    position: int = Field(ge=1)
    edge_id: Optional[int] = None       # 缺省沿用当前刃口
    at: Optional[datetime] = None


class UnmountIn(BaseModel):
    at: Optional[datetime] = None


class SegmentIn(BaseModel):
    machine_code: str
    position: int = Field(ge=1)
    started_at: datetime
    ended_at: datetime
    kind: Literal["cut", "idle", "pause"]
    rpm: Optional[float] = None
    feed: Optional[float] = None
    workpiece_material: Optional[str] = None
    interrupted: bool = False
    operation_ref: Optional[str] = None


class SegmentBatchIn(BaseModel):
    segments: list[SegmentIn]
