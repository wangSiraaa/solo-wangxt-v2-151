"""Unit tests for the NumPy empirical model."""
import pytest

from app import model as m


def _cut(start, end, **kw):
    base = dict(rpm=2000, feed_mm_rev=0.2, depth_mm=2.0,
                material="steel_45", interrupted=False, id=1)
    base.update(kw)
    return m.Event("cutting", start, end, **base)


def test_basic_cutting_load_is_positive_and_traceable():
    segs = m.build_segments([_cut(0, 60)])  # 1 minute
    assert len(segs) == 1
    assert segs[0].phase == m.Phase.CUTTING
    # reference conditions -> k0 per minute
    assert segs[0].load == pytest.approx(0.012, rel=1e-9)
    assert segs[0].source_event_ids == [1]


def test_idle_and_pause_are_separate_from_cutting():
    events = [
        m.Event("cutting", 0, 60, rpm=2000, feed_mm_rev=0.2, depth_mm=2.0,
                material="steel_45", id=1),
        m.Event("idle", 60, 120, id=2),
        m.Event("pause", 120, 180, id=3),
    ]
    segs = m.build_segments(events)
    phases = [s.phase for s in segs]
    assert phases == [m.Phase.CUTTING, m.Phase.IDLE, m.Phase.PAUSE]
    assert segs[1].load == pytest.approx(m.IDLE_LOAD_PER_MIN)
    assert segs[2].load == 0.0
    summary = m.summarize(segs)
    assert summary.cutting_load == pytest.approx(0.012)
    assert summary.idle_load == pytest.approx(m.IDLE_LOAD_PER_MIN)
    assert summary.pause_load == 0.0


def test_rpm_change_splits_into_two_segments():
    events = [
        _cut(0, 60, rpm=2000, id=1),
        _cut(60, 120, rpm=3000, id=2),
    ]
    segs = m.build_segments(events)
    assert [s.rpm for s in segs] == [2000, 3000]
    # higher rpm must accumulate more load
    assert segs[1].load > segs[0].load
    assert (segs[1].load / segs[0].load) == pytest.approx(1.5 ** 1.30, rel=1e-6)


def test_material_change_splits_and_both_materials_counted():
    events = [
        _cut(0, 60, material="steel_45", id=1),
        _cut(60, 120, material="stainless_304", id=2),
    ]
    segs = m.build_segments(events)
    assert [s.material for s in segs] == ["steel_45", "stainless_304"]
    summary = m.summarize(segs)
    assert set(summary.by_material) == {"steel_45", "stainless_304"}
    assert summary.by_material["stainless_304"] == pytest.approx(
        summary.by_material["steel_45"] * 1.55)


def test_interrupted_cut_gets_impact_factor():
    normal = m.build_segments([_cut(0, 60, id=1)])[0]
    interrupted = m.build_segments([_cut(0, 60, interrupted=True, id=1)])[0]
    assert interrupted.load == pytest.approx(
        normal.load * 1.80, rel=1e-9)


def test_out_of_order_events_produce_same_segments():
    ordered = [_cut(0, 60, material="steel_45", id=1),
               _cut(60, 120, material="aluminum_6061", id=2),
               m.Event("pause", 120, 180, id=3)]
    shuffled = [ordered[2], ordered[0], ordered[1]]
    a = m.build_segments(ordered)
    b = m.build_segments(shuffled)
    assert [(s.start, s.end, s.phase.value, s.load) for s in a] == \
           [(s.start, s.end, s.phase.value, s.load) for s in b]


def test_missing_conditions_are_unknown_not_zero():
    events = [m.Event("cutting", 0, 120, rpm=2000, material="steel_45",
                      id=1)]  # feed/depth missing
    segs = m.build_segments(events)
    assert segs[0].load is None
    assert segs[0].unknown_reason == "missing_condition"
    summary = m.summarize(segs)
    assert summary.cutting_load == 0.0          # nothing countable
    assert summary.unknown_min == pytest.approx(2.0)
    assert summary.known_fraction_of_cutting_min == 0.0


def test_partial_missing_data_keeps_known_and_unknown_apart():
    # rpm changes mid-event, second half lacks conditions
    events = [
        _cut(0, 60, rpm=2000, feed_mm_rev=0.2, depth_mm=2.0,
             material="steel_45", id=1),
        m.Event("cutting", 60, 120, rpm=2500, material="steel_45", id=2),
    ]
    segs = m.build_segments(events)
    assert segs[0].load is not None
    assert segs[1].load is None
    summary = m.summarize(segs)
    assert summary.unknown_min == pytest.approx(1.0)
    assert summary.known_fraction_of_cutting_min == pytest.approx(0.5)
    # remaining life is based only on known load — not a precise ETA
    assert summary.remaining_life_fraction() == pytest.approx(1 - 0.012)


def test_unsupported_material_is_unknown():
    segs = m.build_segments([_cut(0, 60, material="unobtainium", id=1)])
    assert segs[0].load is None
    assert segs[0].unknown_reason == "unsupported_material"


def test_overlapping_conflicting_cutting_events_are_unknown():
    events = [
        _cut(0, 60, rpm=2000, id=1),
        _cut(0, 60, rpm=3000, id=2),
    ]
    segs = m.build_segments(events)
    assert segs[0].load is None
    assert segs[0].unknown_reason in {"conflicting_active_events",
                                      "material_unknown_or_conflicting"}
    assert segs[0].source_event_ids == [1, 2]  # provenance preserved


def test_bad_event_raises():
    with pytest.raises(ValueError):
        m.build_segments([m.Event("cutting", 100, 0, rpm=1, material="x")])


def test_aggregate_by_tool():
    import numpy as np
    out = m.aggregate_by_tool(
        np.array([1, 2, 1]), np.array([0.1, 0.5, 0.2]))
    assert out == {1: pytest.approx(0.3), 2: pytest.approx(0.5)}
