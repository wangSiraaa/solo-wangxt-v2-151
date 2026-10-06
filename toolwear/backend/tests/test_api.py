"""API 端到端测试：演示案例种子 + 关键端点。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_seed_and_reports(db):
    r = client.post("/api/seed/demo")
    assert r.status_code == 200

    tools = client.get("/api/tools").json()
    assert {t["code"] for t in tools} == {"T-1001", "T-1002", "T-1003"}
    by_code = {t["code"]: t for t in tools}

    # 案例 3：T-1003 已转到 MC-02，且有一段未知负载
    assert by_code["T-1003"]["location"] == {"machine_code": "MC-02", "position": 1}
    assert by_code["T-1003"]["unknown_segment_count"] == 1

    t3 = next(t for t in tools if t["code"] == "T-1003")
    report = client.get(f"/api/tools/{t3['id']}/load").json()
    # 三种材料 + 两台机床均可回溯
    assert set(report["by_material"]) == {"aluminum", "steel", "titanium"}
    assert set(report["by_machine"]) == {"MC-01", "MC-02"}
    seg_sum = sum(s["load"] for s in report["segments"] if s["load"] is not None)
    assert report["total_known_load"] == pytest.approx(seg_sum, abs=1e-5)
    # 空转/暂停时长与切削时长分开统计
    assert report["idle_minutes"] > 0 and report["pause_minutes"] > 0
    assert report["cut_minutes"] > 0

    est = client.get(f"/api/tools/{t3['id']}/life-estimate").json()
    assert est["estimate_incomplete"] is True
    assert est["band"] in {"low", "medium", "high", "beyond"}

    # 案例 1：断续切削的段数与空转间隔
    t1 = next(t for t in tools if t["code"] == "T-1001")
    r1 = client.get(f"/api/tools/{t1['id']}/load").json()
    cuts = [s for s in r1["segments"] if s["kind"] == "cut"]
    assert len(cuts) == 12 and all(s["interrupted"] for s in cuts)

    # 案例 2：乱序上报后仍按发生时间排序
    t2 = next(t for t in tools if t["code"] == "T-1002")
    r2 = client.get(f"/api/tools/{t2['id']}/load").json()
    starts = [s["started_at"] for s in r2["segments"]]
    assert starts == sorted(starts)
    arrivals = [s["arrival_order"] for s in r2["segments"]]
    assert arrivals != sorted(arrivals), "到达顺序应与发生顺序不同（乱序案例）"


def test_machine_board(db):
    client.post("/api/seed/demo")
    board = client.get("/api/machines").json()
    assert len(board) == 2
    mc1 = next(m for m in board if m["code"] == "MC-01")
    occupied = {s["position"]: s["tool"]["code"] for s in mc1["slots"] if s["tool"]}
    assert occupied == {1: "T-1001", 2: "T-1002"}  # T-1003 已转到 MC-02
import pytest
