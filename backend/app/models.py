"""ORM models.

Tool identity is global: a Tool row is created once and follows the physical
cutter across machines via MountHistory rows.  Segments reference BOTH the
tool and the mount (machine + position) in which the wear occurred, so every
load figure is traceable back to the raw process events.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[int] = mapped_column(primary_key=True)
    tool_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    edge_type: Mapped[str] = mapped_column(String(64))   # e.g. carbide_insert_p2t
    material_grade: Mapped[str] = mapped_column(String(64))
    # Lifetime accumulated cutting minutes / modelled load are DERIVED from
    # segments; these columns cache the latest rollup.
    accum_cutting_min: Mapped[float] = mapped_column(Float, default=0.0)
    accum_known_load: Mapped[float] = mapped_column(Float, default=0.0)
    accum_unknown_min: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_utcnow)

    mounts: Mapped[list["MountHistory"]] = relationship(
        back_populates="tool", cascade="all, delete-orphan")


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    positions: Mapped[int] = mapped_column(Integer, default=8)  # 刀位数


class MountHistory(Base):
    """A tool occupying a machine position for a period.

    The same tool id may appear in many rows on different machines — that is
    how identity survives a transfer.  An open mount has dismounted_at NULL.
    """
    __tablename__ = "mount_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    tool_id: Mapped[int] = mapped_column(ForeignKey("tools.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)               # 刀位
    mounted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dismounted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    tool: Mapped[Tool] = relationship(back_populates="mounts")
    machine: Mapped[Machine] = relationship()


class ProcessEvent(Base):
    """Raw interval ingested from a process record (may arrive out of order)."""
    __tablename__ = "process_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    tool_id: Mapped[int] = mapped_column(ForeignKey("tools.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # cutting | idle | pause
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    feed_mm_rev: Mapped[float | None] = mapped_column(Float, nullable=True)
    depth_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    material: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=_utcnow)


class Segment(Base):
    """Computed homogeneous slice.  load NULL = unknown, NOT zero."""
    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    tool_id: Mapped[int] = mapped_column(ForeignKey("tools.id"), index=True)
    mount_id: Mapped[int | None] = mapped_column(
        ForeignKey("mount_history.id"), index=True, nullable=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    phase: Mapped[str] = mapped_column(String(16))
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    feed_mm_rev: Mapped[float | None] = mapped_column(Float, nullable=True)
    depth_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    material: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False)
    load: Mapped[float | None] = mapped_column(Float, nullable=True)
    unknown_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SegmentSource(Base):
    """Many-to-many provenance: segment -> raw events it was derived from."""
    __tablename__ = "segment_sources"
    __table_args__ = (
        UniqueConstraint("segment_id", "event_id", name="uq_segment_event"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("process_events.id"))
