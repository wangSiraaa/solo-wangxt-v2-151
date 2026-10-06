"""服务层测试：乱序归属、转机床保留身份、负载回溯、消耗区间。"""
import random
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Machine, Tool, ToolEdge
from app.services import (DomainError, current_assignment, ingest_segments,
                          life_estimate, mount_tool, tool_load_report,
                          transfer_tool, unmount_tool)

T0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)


def _machine(db, code="MC-01", positions=4):
    m = Machine(code=code, name=code, positions=positions)
    db.add(m)
    db.flush()
    return m


def _tool(db, code="T-1", edges=2, capacity=100.0):
    t = Tool(code=code, material="coated_carbide", edge_count=edges,
             nominal_capacity=capacity)
    db.add(t)
    db.flush()
    for i in range(1, edges + 1):
        db.add(ToolEdge(tool_id=t.id, edge_index=i))
    db.flush()
    return t


def _cut(code, pos, start, minutes, rpm=8000.0, material="steel", op="OP-1"):
    return dict(machine_code=code, position=pos, started_at=start,
                ended_at=start + timedelta(minutes=minutes), kind="cut",
                rpm=rpm, workpiece_material=material, operation_ref=op)


def test_out_of_order_ingest_same_accumulation(db):
    """乱序上报与顺序上报的累计结果一致，且逐段可回溯。"""
    m = _machine(db)
    t = _tool(db)
    mount_tool(db, tool_id=t.id, machine_id=m.id, position=1, edge_id=None, at=T0)

    ordered = [_cut("MC-01", 1, T0 + timedelta(minutes=10 * i), 5,
                    op=f"OP-{i}") for i in range(6)]
    shuffled = ordered.copy()
    random.Random(7).shuffle(shuffled)

    ingest_segments(db, ordered)
    total_ordered = tool_load_report(db, t.id)["total_known_load"]
    db.rollback()

    m2 = _machine(db)
    t2 = _tool(db)
    mount_tool(db, tool_id=t2.id, machine_id=m2.id, position=1, edge_id=None, at=T0)
    ingest_segments(db, shuffled)
    report = tool_load_report(db, t2.id)
    assert report["total_known_load"] == pytest.approx(total_ordered)
    assert len(report["segments"]) == 6
    # 区段按发生时间排序输出，与到达顺序无关
    starts = [s["started_at"] for s in report["segments"]]
    assert starts == sorted(starts)


def test_unattributed_segments_reported_not_dropped(db):
    """装刀区间之外的区段：保留并显式报告，不静默计入。"""
    m = _machine(db)
    t = _tool(db)
    mount_tool(db, tool_id=t.id, machine_id=m.id, position=1, edge_id=None,
               at=T0 + timedelta(hours=1))
    res = ingest_segments(db, [_cut("MC-01", 1, T0, 10)])  # 装刀前发生
    assert res["unattributed_segment_ids"], "装刀前的区段应被标记为未归属"
    assert tool_load_report(db, t.id)["total_known_load"] == 0.0


def test_transfer_keeps_identity_and_history(db):
    """转机床：同一 tool_id，历史跨两台机床，总量 = 各机床之和。"""
    m1 = _machine(db, "MC-01")
    m2 = _machine(db, "MC-02")
    t = _tool(db)
    mount_tool(db, tool_id=t.id, machine_id=m1.id, position=1, edge_id=None, at=T0)
    ingest_segments(db, [_cut("MC-01", 1, T0 + timedelta(minutes=5), 10,
                              material="aluminum", op="OP-A")])
    transfer_tool(db, tool_id=t.id, machine_id=m2.id, position=2, edge_id=None,
                  at=T0 + timedelta(hours=1))
    ingest_segments(db, [_cut("MC-02", 2, T0 + timedelta(hours=1, minutes=5), 10,
                              material="titanium", op="OP-B")])

    report = tool_load_report(db, t.id)
    assert report["tool_id"] == t.id
    assert set(report["by_machine"]) == {"MC-01", "MC-02"}
    assert report["total_known_load"] == pytest.approx(
        sum(report["by_machine"].values()))
    assert set(report["by_material"]) == {"aluminum", "titanium"}
    # 当前位置已在新机床
    cur = current_assignment(db, t.id)
    assert cur.machine_id == m2.id and cur.position == 2


def test_traceability_sum_of_segments_equals_total(db):
    """负载总量可回溯：逐段已知负载之和 == 总量；工序分组覆盖全部区段。"""
    m = _machine(db)
    t = _tool(db)
    mount_tool(db, tool_id=t.id, machine_id=m.id, position=1, edge_id=None, at=T0)
    ingest_segments(db, [
        _cut("MC-01", 1, T0 + timedelta(minutes=0), 4, op="OP-1"),
        _cut("MC-01", 1, T0 + timedelta(minutes=4), 4, op="OP-1"),
        _cut("MC-01", 1, T0 + timedelta(minutes=8), 4, op="OP-2"),
        dict(machine_code="MC-01", position=1,
             started_at=T0 + timedelta(minutes=12),
             ended_at=T0 + timedelta(minutes=16), kind="cut",
             operation_ref="OP-3"),  # 缺工况 -> 未知
    ])
    report = tool_load_report(db, t.id)
    seg_sum = sum(s["load"] for s in report["segments"] if s["load"] is not None)
    assert report["total_known_load"] == pytest.approx(seg_sum)
    assert report["total_known_load"] == pytest.approx(
        sum(report["by_operation"].values()))
    assert report["unknown_segment_count"] == 1
    unknown = report["unknown_segments"][0]
    assert unknown["operation_ref"] == "OP-3" and unknown["load"] is None


def test_life_estimate_is_band_not_countdown(db):
    """消耗估计只给区间与说明，不包含任何倒计时/精确失效时间字段。"""
    m = _machine(db)
    t = _tool(db)
    mount_tool(db, tool_id=t.id, machine_id=m.id, position=1, edge_id=None, at=T0)
    ingest_segments(db, [_cut("MC-01", 1, T0 + timedelta(minutes=1), 10)])
    est = life_estimate(db, t.id)
    assert est["band"] in {"low", "medium", "high", "beyond", "unknown"}
    assert "不是剩余寿命倒计时" in est["note"]
    forbidden = [k for k in est if any(
        w in k.lower() for w in ("countdown", "minutes_remaining", "time_to_fail",
                                 "ttl", "deadline"))]
    assert not forbidden
    assert est["disclaimer"]


def test_mount_conflicts(db):
    m = _machine(db)
    t1, t2 = _tool(db, "T-1"), _tool(db, "T-2")
    mount_tool(db, tool_id=t1.id, machine_id=m.id, position=1, edge_id=None, at=T0)
    with pytest.raises(DomainError):
        mount_tool(db, tool_id=t1.id, machine_id=m.id, position=2, edge_id=None,
                   at=T0)  # 已装机
    with pytest.raises(DomainError):
        mount_tool(db, tool_id=t2.id, machine_id=m.id, position=1, edge_id=None,
                   at=T0)  # 刀位被占用
    unmount_tool(db, tool_id=t1.id, at=T0 + timedelta(hours=1))
    mount_tool(db, tool_id=t2.id, machine_id=m.id, position=1, edge_id=None,
               at=T0 + timedelta(hours=1))  # 卸刀后可装
