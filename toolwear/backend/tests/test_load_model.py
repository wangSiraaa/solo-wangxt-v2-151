"""负载模型单元测试：公式、未知负载、空转/暂停、断续系数。"""
from datetime import datetime, timedelta, timezone

from app.load_model import (INTERRUPTED_FACTOR, MATERIAL_FACTORS, REF_RPM,
                            SPEED_EXPONENT, SegmentInput, compute_loads,
                            summarize)

T0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)


def seg(kind="cut", minutes=10, rpm=8000.0, material="steel",
        interrupted=False, offset=0):
    start = T0 + timedelta(minutes=offset)
    return SegmentInput(kind=kind, started_at=start,
                        ended_at=start + timedelta(minutes=minutes),
                        rpm=rpm, workpiece_material=material,
                        interrupted=interrupted)


def test_known_cut_load_matches_formula():
    (load,) = compute_loads([seg(minutes=10, rpm=9000.0, material="steel")])
    expected = 10 * (9000.0 / REF_RPM) ** SPEED_EXPONENT * MATERIAL_FACTORS["steel"]
    assert abs(load - expected) < 1e-9


def test_idle_and_pause_have_zero_load():
    loads = compute_loads([seg(kind="idle"), seg(kind="pause")])
    assert loads == [0.0, 0.0]


def test_missing_rpm_or_material_is_unknown_not_zero():
    loads = compute_loads([
        seg(rpm=None),                    # 缺转速
        seg(material=None),               # 缺材料
        seg(rpm=None, material=None),     # 都缺
    ])
    assert loads == [None, None, None]
    summary = summarize(loads)
    assert summary["total_known_load"] == 0.0
    assert summary["unknown_count"] == 3


def test_interrupted_cut_has_impact_factor():
    (smooth,) = compute_loads([seg(interrupted=False)])
    (rough,) = compute_loads([seg(interrupted=True)])
    assert abs(rough - smooth * INTERRUPTED_FACTOR) < 1e-9


def test_material_change_requires_separate_segments_and_accumulates():
    """转速/材料变化切段累计：总分段负载 = 各段之和（且不等于按单一工况估算）。"""
    segments = [
        seg(minutes=5, rpm=10000.0, material="aluminum", offset=0),
        seg(minutes=5, rpm=7500.0, material="steel", offset=5),
        seg(minutes=5, rpm=6000.0, material="titanium", offset=10),
    ]
    loads = compute_loads(segments)
    total = sum(loads)
    expected = sum(
        5 * (rpm / REF_RPM) ** SPEED_EXPONENT * MATERIAL_FACTORS[mat]
        for rpm, mat in [(10000.0, "aluminum"), (7500.0, "steel"),
                         (6000.0, "titanium")]
    )
    assert abs(total - expected) < 1e-9
    # 若错误地按平均工况一段估算，结果会不同
    naive = 15 * (8166.7 / REF_RPM) ** SPEED_EXPONENT * 1.0
    assert abs(total - naive) > 1e-3


def test_unknown_segments_excluded_from_total():
    loads = compute_loads([seg(minutes=4), seg(rpm=None), seg(minutes=6, offset=10)])
    summary = summarize(loads)
    assert summary["unknown_count"] == 1
    assert abs(summary["total_known_load"] - (loads[0] + loads[2])) < 1e-9
