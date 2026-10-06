"""SQLAlchemy 持久层模型（PostgreSQL）。

- Tool / ToolEdge：刀具身份与刃口；刀具转机床时身份与全部历史保留。
- Assignment：一次装刀区间 [mounted_at, unmounted_at)，记录机床、刀位与所用刃口。
- Segment：工序记录区段（切削/空转/暂停），id 顺序即到达顺序，
  乱序到达不影响按发生时间的归属与累计。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Integer,
                        String)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Machine(Base):
    __tablename__ = "machines"

    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, nullable=False)
    name = Column(String(128), nullable=False)
    positions = Column(Integer, nullable=False, default=4)  # 刀位数


class Tool(Base):
    __tablename__ = "tools"

    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, nullable=False)
    material = Column(String(64), nullable=False)          # 刀具材质，如 coated_carbide
    edge_count = Column(Integer, nullable=False, default=1)
    nominal_capacity = Column(Float, nullable=False, default=100.0)  # 经验负载上限（演示值）
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    edges = relationship("ToolEdge", back_populates="tool",
                         order_by="ToolEdge.edge_index", cascade="all, delete-orphan")
    assignments = relationship("Assignment", back_populates="tool")


class ToolEdge(Base):
    __tablename__ = "tool_edges"

    id = Column(Integer, primary_key=True)
    tool_id = Column(ForeignKey("tools.id"), nullable=False)
    edge_index = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="active")  # active / retired

    tool = relationship("Tool", back_populates="edges")


class Assignment(Base):
    """装刀区间：刀具在某机床某刀位、使用某刃口的在岗时间段。"""

    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True)
    tool_id = Column(ForeignKey("tools.id"), nullable=False)
    edge_id = Column(ForeignKey("tool_edges.id"), nullable=False)
    machine_id = Column(ForeignKey("machines.id"), nullable=False)
    position = Column(Integer, nullable=False)             # 刀位号
    mounted_at = Column(DateTime(timezone=True), nullable=False)
    unmounted_at = Column(DateTime(timezone=True), nullable=True)  # NULL = 当前在岗

    tool = relationship("Tool", back_populates="assignments")
    edge = relationship("ToolEdge")
    machine = relationship("Machine")


class Segment(Base):
    """工序记录区段。

    kind: cut(切削) / idle(空转) / pause(暂停)。
    rpm 或 workpiece_material 缺失时，切削段负载记为未知（不计零）。
    received_at 记录到达时刻，乱序事件按 started_at 归属，与到达顺序无关。
    """

    __tablename__ = "segments"

    id = Column(Integer, primary_key=True)                 # id 顺序 = 到达顺序
    machine_id = Column(ForeignKey("machines.id"), nullable=False)
    position = Column(Integer, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=False)
    kind = Column(String(8), nullable=False)
    rpm = Column(Float, nullable=True)
    feed = Column(Float, nullable=True)
    workpiece_material = Column(String(32), nullable=True)
    interrupted = Column(Boolean, nullable=False, default=False)  # 断续切削
    operation_ref = Column(String(64), nullable=True)      # 工序号：负载回溯的锚点
    received_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    machine = relationship("Machine")
