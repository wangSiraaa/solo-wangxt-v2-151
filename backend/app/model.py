"""Empirical tool-wear model (DEMO PARAMETERS ONLY).

This is a deliberately small, transparent model used for estimating tool
consumption from process records.  It is *not* calibrated against any real
machine tool and **must not** be used to make safety-critical tool-change
decisions.

Idea (Taylor-inspired, simplified)
----------------------------------
For every cutting segment we compute a dimensionless *wear load*

    rate = k0 * k_mat * (rpm / v_ref) ** n_v
                   * (feed / f_ref) ** n_f
                   * (depth / d_ref) ** n_d
    load = rate * duration_min

Interrupted cuts multiply the rate by an impact factor k_int.  Paused and
idling time is computed separately and never silently folded into cutting
load.  Segments whose cutting conditions are unknown keep load = NULL
("unknown"), they are never treated as zero.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# Demo parameters -- do not "tune" these against production without validation
# ---------------------------------------------------------------------------
DEMO_PARAMS = {
    "k0_per_min": 0.012,       # base wear fraction per reference minute
    "v_ref_rpm": 2000.0,
    "feed_ref_mm_per_rev": 0.20,
    "depth_ref_mm": 2.0,
    "n_v": 1.30,
    "n_f": 0.60,
    "n_d": 0.45,
    "k_interrupted": 1.80,     # impact multiplier for interrupted cutting
}

# Material machinability factors (demo): harder / stringier materials wear
# the edge faster.  Only these keys are known.
MATERIAL_FACTORS: dict[str, float] = {
    "steel_45": 1.00,
    "stainless_304": 1.55,
    "aluminum_6061": 0.45,
    "cast_iron_ht250": 1.20,
}

IDLE_LOAD_PER_MIN = 0.0005    # spindle on, no cutting: separately reported
PAUSE_LOAD_PER_MIN = 0.0      # machine paused: zero wear by definition


class Phase(str, Enum):
    CUTTING = "cutting"
    IDLE = "idle"
    PAUSE = "pause"


@dataclass
class Event:
    """One raw interval from a process record."""

    kind: str                      # 'cutting' | 'idle' | 'pause'
    start: float                   # epoch seconds (float on purpose)
    end: float
    rpm: Optional[float] = None
    feed_mm_rev: Optional[float] = None
    depth_mm: Optional[float] = None
    material: Optional[str] = None
    interrupted: bool = False
    id: Optional[int] = None       # database id, for provenance


@dataclass
class Segment:
    """One homogeneous slice of the timeline.

    load is None  -> unknown load (missing/ambiguous cutting conditions)
    load is float -> modelled load; 0.0 for pause, small for idle
    """

    start: float
    end: float
    phase: Phase
    rpm: Optional[float] = None
    feed_mm_rev: Optional[float] = None
    depth_mm: Optional[float] = None
    material: Optional[str] = None
    interrupted: bool = False
    load: Optional[float] = None
    unknown_reason: Optional[str] = None
    source_event_ids: list[int] = field(default_factory=list)

    @property
    def duration_min(self) -> float:
        return (self.end - self.start) / 60.0


def _cutting_rate(rpm: float, feed: float, depth: float, material: str,
                  interrupted: bool, params: dict) -> float:
    p = params
    rate = (
        p["k0_per_min"]
        * MATERIAL_FACTORS[material]
        * (rpm / p["v_ref_rpm"]) ** p["n_v"]
        * (feed / p["feed_ref_mm_per_rev"]) ** p["n_f"]
        * (depth / p["depth_ref_mm"]) ** p["n_d"]
    )
    if interrupted:
        rate *= p["k_interrupted"]
    return rate


def _known_consistent(vals: list) -> tuple[bool, Optional[str]]:
    """All values present, not NaN, and mutually equal (within tolerance)."""
    clean = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(clean) != len(vals):
        return False, "missing_condition"
    if len(set(clean)) > 1:
        nums = [float(v) for v in clean]
        if max(nums) - min(nums) > 1e-9:
            return False, "conflicting_active_events"
    return True, None


def build_segments(events: list[Event],
                   params: Optional[dict] = None) -> list[Segment]:
    """Slice the timeline at every event boundary (sweep line).

    Works even if events arrive out of order or overlap.  Each elementary
    interval is classified from the events that are active on it:

    * >= 1 active cutting event -> cutting; conditions must be present and
      consistent, otherwise the slice stays UNKNOWN (never zero).
    * only idle active           -> idle load (reported separately)
    * pause active (no cutting)  -> pause, zero load
    * nothing active             -> omitted (machine off / not recorded)
    """
    p = params or DEMO_PARAMS
    if not events:
        return []

    for ev in events:
        if ev.end < ev.start:
            raise ValueError(f"event {ev.id} ends before it starts")

    bounds = sorted({b for ev in events for b in (ev.start, ev.end)})
    segments: list[Segment] = []

    for t0, t1 in zip(bounds[:-1], bounds[1:]):
        if t1 <= t0:
            continue
        mid = (t0 + t1) / 2.0
        active = [e for e in events if e.start <= t0 < e.end]
        if not active:
            continue
        ids = sorted(e.id for e in active if e.id is not None)

        cutting = [e for e in active if e.kind == "cutting"]
        if cutting:
            rpms = [e.rpm for e in cutting]
            feeds = [e.feed_mm_rev for e in cutting]
            depths = [e.depth_mm for e in cutting]
            mats = [e.material for e in cutting]
            inter = any(e.interrupted for e in cutting)

            rpm = rpms[0] if rpms else None
            feed = feeds[0] if feeds else None
            depth = depths[0] if depths else None
            material = mats[0] if mats else None

            seg = Segment(t0, t1, Phase.CUTTING, rpm=rpm, feed_mm_rev=feed,
                          depth_mm=depth, material=material,
                          interrupted=inter, source_event_ids=ids)

            checks = [
                _known_consistent(rpms),
                _known_consistent(feeds),
                _known_consistent(depths),
            ]
            mat_ok = all(m is not None for m in mats) and len(set(mats)) == 1
            if not mat_ok:
                checks.append((False, "material_unknown_or_conflicting"))

            failed = [reason for ok, reason in checks if not ok]
            if failed:
                seg.load = None
                seg.unknown_reason = failed[0]
            elif material not in MATERIAL_FACTORS:
                seg.load = None
                seg.unknown_reason = "unsupported_material"
            else:
                seg.load = (_cutting_rate(rpm, feed, depth, material,
                                          inter, p) * seg.duration_min)
            segments.append(seg)
            continue

        if any(e.kind == "pause" for e in active):
            segments.append(Segment(t0, t1, Phase.PAUSE, load=0.0,
                                    source_event_ids=ids))
        else:
            seg = Segment(t0, t1, Phase.IDLE,
                          load=IDLE_LOAD_PER_MIN * (t1 - t0) / 60.0,
                          source_event_ids=ids)
            segments.append(seg)

    return segments


@dataclass
class LoadSummary:
    cutting_load: float          # sum over known cutting segments only
    unknown_load: None           # always None: signals "not countable"
    idle_load: float
    pause_load: float
    cutting_min: float
    idle_min: float
    pause_min: float
    unknown_min: float
    known_fraction_of_cutting_min: float
    by_material: dict[str, float]
    by_phase: dict[str, float]
    segments: list[Segment]

    @property
    def total_accounted_load(self) -> float:
        """Known load only. Unknown cutting is excluded, not zeroed."""
        return self.cutting_load + self.idle_load

    def remaining_life_fraction(self) -> Optional[float]:
        """1 - known cutting load.  An ESTIMATE with unknown time excluded."""
        return max(0.0, 1.0 - self.cutting_load)


def summarize(segments: list[Segment]) -> LoadSummary:
    cut = [s for s in segments if s.phase == Phase.CUTTING]
    known = [s for s in cut if s.load is not None]
    unknown = [s for s in cut if s.load is None]
    idle = [s for s in segments if s.phase == Phase.IDLE]
    pause = [s for s in segments if s.phase == Phase.PAUSE]

    cutting_min = sum(s.duration_min for s in cut)
    known_min = sum(s.duration_min for s in known)

    by_material: dict[str, float] = {}
    for s in known:
        by_material[s.material] = by_material.get(s.material, 0.0) + s.load

    return LoadSummary(
        cutting_load=float(np.sum([s.load for s in known]) ) if known else 0.0,
        unknown_load=None,
        idle_load=float(np.sum([s.load for s in idle])) if idle else 0.0,
        pause_load=0.0,
        cutting_min=cutting_min,
        idle_min=sum(s.duration_min for s in idle),
        pause_min=sum(s.duration_min for s in pause),
        unknown_min=sum(s.duration_min for s in unknown),
        known_fraction_of_cutting_min=(known_min / cutting_min) if cutting_min else 1.0,
        by_material=by_material,
        by_phase={
            "cutting_known": float(np.sum([s.load for s in known])) if known else 0.0,
            "idle": float(np.sum([s.load for s in idle])) if idle else 0.0,
            "pause": 0.0,
        },
        segments=segments,
    )


# Vectorized aggregation used by the API for multi-tool reporting:
# loads array in, per-tool sums via np.add.at.
def aggregate_by_tool(tool_ids: np.ndarray, loads: np.ndarray) -> dict[int, float]:
    uniq, inverse = np.unique(tool_ids, return_inverse=True)
    out = np.zeros(len(uniq))
    np.add.at(out, inverse, np.nan_to_num(loads, nan=0.0))
    return {int(t): float(v) for t, v in zip(uniq, out)}
