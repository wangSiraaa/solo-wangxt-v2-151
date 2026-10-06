"""End-to-end API tests: ingestion out of order, provenance, tool transfer."""
from datetime import datetime, timedelta, timezone

BASE = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)


def _ev(tool, machine, kind, start, end_min, **kw):
    d = {"tool_code": tool, "machine_code": machine, "kind": kind,
         "start_ts": (BASE + timedelta(minutes=start)).isoformat(),
         "end_ts": (BASE + timedelta(minutes=end_min)).isoformat()}
    d.update(kw)
    return d


def _mount(client, tool, machine, pos, **kw):
    body = {"tool_code": tool, "machine_code": machine, "position": pos,
            "mounted_at": (BASE - timedelta(minutes=1)).isoformat()}
    body.update(kw)
    return client.post("/api/mounts", json=body)


def _setup(client):
    client.post("/api/machines", json={"machine_code": "MC-A",
                                       "name": "Lathe A", "positions": 4})
    client.post("/api/machines", json={"machine_code": "MC-B",
                                       "name": "Mill B", "positions": 6})
    client.post("/api/tools", json={"tool_code": "T-001",
                                    "edge_type": "carbide_insert_p2t",
                                    "material_grade": "P25-TiN"})
    client.post("/api/tools", json={"tool_code": "T-002",
                                    "edge_type": "end_mill_4f",
                                    "material_grade": "K20"})


def test_positions_view_and_mount_conflicts(client):
    _setup(client)
    r = _mount(client, "T-001", "MC-A", 0)
    assert r.status_code == 201

    # same position on same machine -> 409
    r = _mount(client, "T-002", "MC-A", 0)
    assert r.status_code == 409

    # same tool already mounted elsewhere -> 409
    r = _mount(client, "T-001", "MC-B", 1)
    assert r.status_code == 409

    slots = client.get("/api/machines/MC-A/positions").json()
    assert slots[0]["occupied"] is True
    assert slots[0]["tool_code"] == "T-001"
    assert slots[1]["tool_code"] is None


def test_interrupted_cut_case_report(client):
    _setup(client)
    _mount(client, "T-001", "MC-A", 0)
    events = [
        _ev("T-001", "MC-A", "cutting", 0, 2, rpm=2000,
            feed_mm_rev=0.2, depth_mm=2.0, material="steel_45"),
        # interrupted cut with 2 entries/exit per pass; load carries k=1.8
        _ev("T-001", "MC-A", "cutting", 2, 4, rpm=2400,
            feed_mm_rev=0.15, depth_mm=1.5, material="stainless_304",
            interrupted=True),
        _ev("T-001", "MC-A", "idle", 4, 6),
        _ev("T-001", "MC-A", "pause", 6, 8),
    ]
    r = client.post("/api/events", json={"events": events})
    assert r.status_code == 201

    rep = client.get("/api/tools/T-001/report").json()
    assert rep["cutting_min"] == 4.0
    assert rep["idle_min"] == 2.0
    assert rep["pause_min"] == 2.0
    assert rep["unknown_min"] == 0.0
    assert rep["cutting_load_known"] > 0
    assert rep["idle_load"] > 0
    assert rep["pause_load"] == 0.0
    # interrupted stainless segment should dominate
    segs = rep["segments"]
    cut_segs = [s for s in segs if s["phase"] == "cutting"]
    assert cut_segs[1]["interrupted"] is True
    assert cut_segs[1]["material"] == "stainless_304"
    assert cut_segs[1]["load"] > cut_segs[0]["load"]
    assert "demo empirical model" in rep["disclaimer"]


def test_out_of_order_ingestion_is_stable(client):
    _setup(client)
    _mount(client, "T-002", "MC-B", 3)
    later = [
        _ev("T-002", "MC-B", "cutting", 10, 12, rpm=2000,
            feed_mm_rev=0.2, depth_mm=2.0, material="steel_45"),
        _ev("T-002", "MC-B", "pause", 12, 14),
    ]
    earlier = [
        _ev("T-002", "MC-B", "cutting", 0, 10, rpm=2000,
            feed_mm_rev=0.2, depth_mm=2.0, material="aluminum_6061"),
    ]
    # late arrivals first
    client.post("/api/events", json={"events": later})
    client.post("/api/events", json={"events": earlier})

    rep = client.get("/api/tools/T-002/report").json()
    phases = [s["phase"] for s in rep["segments"]]
    assert phases == ["cutting", "cutting", "pause"]
    materials = [s["material"] for s in rep["segments"][:2]]
    assert materials == ["aluminum_6061", "steel_45"]
    assert rep["cutting_min"] == 12.0


def test_same_tool_different_materials(client):
    _setup(client)
    _mount(client, "T-001", "MC-A", 0)
    events = [
        _ev("T-001", "MC-A", "cutting", 0, 5, rpm=2000,
            feed_mm_rev=0.2, depth_mm=2.0, material="steel_45"),
        _ev("T-001", "MC-A", "cutting", 5, 10, rpm=2000,
            feed_mm_rev=0.2, depth_mm=2.0, material="cast_iron_ht250"),
    ]
    client.post("/api/events", json={"events": events})
    rep = client.get("/api/tools/T-001/report").json()
    assert set(rep["by_material"]) == {"steel_45", "cast_iron_ht250"}
    # total load traceable: sum of per-material loads equals cutting total
    assert sum(rep["by_material"].values()) == rep["cutting_load_known"]


def test_missing_conditions_segments_unknown_not_zero(client):
    _setup(client)
    _mount(client, "T-001", "MC-A", 0)
    events = [
        # no feed/depth/material for first half -> unknown
        _ev("T-001", "MC-A", "cutting", 0, 3, rpm=2000),
        _ev("T-001", "MC-A", "cutting", 3, 6, rpm=2000,
            feed_mm_rev=0.2, depth_mm=2.0, material="steel_45"),
    ]
    client.post("/api/events", json={"events": events})
    rep = client.get("/api/tools/T-001/report").json()
    assert rep["unknown_min"] == 3.0
    assert rep["known_fraction_of_cutting_min"] == 0.5
    unk = [s for s in rep["segments"] if s["load"] is None]
    assert unk and unk[0]["unknown_reason"]

    # tool list must surface unknown minutes, not hide them as zero
    tools = {t["tool_code"]: t for t in client.get("/api/tools").json()}
    assert tools["T-001"]["accum_unknown_min"] == 3.0
    assert tools["T-001"]["accum_known_load"] > 0


def test_tool_keeps_identity_across_machines(client):
    _setup(client)
    # first life on MC-A
    _mount(client, "T-001", "MC-A", 1)
    client.post("/api/events", json={"events": [
        _ev("T-001", "MC-A", "cutting", 0, 5, rpm=2000,
            feed_mm_rev=0.2, depth_mm=2.0, material="steel_45")]})

    client.post("/api/dismounts",
                json={"machine_code": "MC-A", "position": 1})
    # transfer to MC-B next day
    at = (BASE + timedelta(days=1)).isoformat()
    r = client.post("/api/mounts", json={"tool_code": "T-001",
                                         "machine_code": "MC-B", "position": 2,
                                         "mounted_at": at,
                                         "note": "transferred to mill"})
    assert r.status_code == 201
    client.post("/api/events", json={"events": [
        _ev("T-001", "MC-B", "cutting", 1440 + 0, 1440 + 5, rpm=2000,
            feed_mm_rev=0.2, depth_mm=2.0, material="aluminum_6061")]})

    rep = client.get("/api/tools/T-001/report").json()
    assert rep["cutting_min"] == 10.0  # history from both machines kept
    assert len(rep["by_material"]) == 2

    hist = client.get("/api/tools/T-001/mount-history").json()
    assert [h["machine_code"] for h in hist] == ["MC-A", "MC-B"]
    assert hist[1]["note"] == "transferred to mill"

    # segments remember which machine/mount they belong to
    machines = {s["machine_code"] if "machine_code" in s else None
                for s in client.get("/api/tools/T-001/events").json()}
    assert machines == {"MC-A", "MC-B"}


def test_load_backtrace_to_source_events(client):
    _setup(client)
    _mount(client, "T-001", "MC-A", 0)
    resp = client.post("/api/events", json={"events": [
        _ev("T-001", "MC-A", "cutting", 0, 4, rpm=2000,
            feed_mm_rev=0.2, depth_mm=2.0, material="steel_45"),
        _ev("T-001", "MC-A", "idle", 2, 6),   # overlaps -> extra slice
    ]}).json()
    eid = resp["event_ids"]
    rep = client.get("/api/tools/T-001/report").json()
    all_sources = {x for s in rep["segments"] for x in s["source_event_ids"]}
    assert set(all_sources) == set(eid)
