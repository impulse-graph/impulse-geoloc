#!/usr/bin/env python3
"""
Cross-Language Verification & Benchmark Harness (`impulse-geoloc`)
Comparing Python (`h3-py` + `NetworkX`) against Java 25 (`ImpulseVM` FFM Off-Heap).

1. Loads the TSV topology into `NetworkX` and `h3-py` structures (measuring Python load time).
2. Executes all 4 LTL freight geolocation scenarios across 200 measured iterations in Python.
3. Invokes the Java 25 `LtlGeolocShowcaseRunner` via Maven to execute 500 warmup + 2,000
   measured iterations on the `.imps` binary snapshot.
4. Verifies 100.00% exact set identity across all 4 scenarios between Python (`h3-py` + `NetworkX`)
   and Java 25 (`ImpulseVM`).
5. Outputs a Markdown empirical performance comparison table and writes `datasets/benchmark_comparison.json`.
"""

import json
import subprocess
import time
from pathlib import Path

import h3
import networkx as nx


REPO_ROOT = Path(__file__).resolve().parent.parent
DATASETS_DIR = REPO_ROOT / "datasets"
PYTHON_WARMUP_ITERS = 25
PYTHON_BENCH_ITERS = 200


def read_tsv_nodes(path: Path) -> list[str]:
    nodes: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            if idx == 0:
                continue
            parts = line.rstrip("\n").split("\t")
            if parts and parts[0]:
                nodes.append(parts[0])
    return nodes


def read_tsv_edges(path: Path) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    with open(path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            if idx == 0:
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                edges.append((parts[0], parts[1]))
    return edges


def measure_python_scenario(fn, warmup: int = PYTHON_WARMUP_ITERS, iters: int = PYTHON_BENCH_ITERS) -> dict:
    for _ in range(warmup):
        fn()
    samples_ns: list[int] = []
    for _ in range(iters):
        t0 = time.perf_counter_ns()
        fn()
        samples_ns.append(time.perf_counter_ns() - t0)
    samples_ns.sort()
    avg_us = (sum(samples_ns) / len(samples_ns)) / 1_000.0
    p50_us = samples_ns[len(samples_ns) // 2] / 1_000.0
    p99_us = samples_ns[int(len(samples_ns) * 0.99)] / 1_000.0
    qps = 1_000_000.0 / avg_us
    return {
        "avg_latency_us": avg_us,
        "p50_latency_us": p50_us,
        "p99_latency_us": p99_us,
        "qps": qps,
    }


def main() -> None:
    gt_path = DATASETS_DIR / "query_ground_truth.json"
    with open(gt_path, "r", encoding="utf-8") as f:
        gt = json.load(f)

    # 1. Measure Python + NetworkX Cold-Start Load Time
    t_load0 = time.perf_counter_ns()
    res4_keys = read_tsv_nodes(DATASETS_DIR / "h3_res4_nodes.tsv")
    res6_keys = read_tsv_nodes(DATASETS_DIR / "h3_res6_nodes.tsv")
    res8_keys = read_tsv_nodes(DATASETS_DIR / "h3_res8_nodes.tsv")
    relay_keys = read_tsv_nodes(DATASETS_DIR / "relay_yards.tsv")
    truck_keys = read_tsv_nodes(DATASETS_DIR / "trucks.tsv")
    shipment_keys = read_tsv_nodes(DATASETS_DIR / "shipments.tsv")
    service_keys = read_tsv_nodes(DATASETS_DIR / "service_classes.tsv")
    landmark_keys = read_tsv_nodes(DATASETS_DIR / "landmarks.tsv")

    res4_to_id = {k: i for i, k in enumerate(res4_keys)}
    res6_to_id = {k: i for i, k in enumerate(res6_keys)}
    res8_to_id = {k: i for i, k in enumerate(res8_keys)}
    relay_to_id = {k: i for i, k in enumerate(relay_keys)}
    truck_to_id = {k: i for i, k in enumerate(truck_keys)}
    shipment_to_id = {k: i for i, k in enumerate(shipment_keys)}
    service_to_id = {k: i for i, k in enumerate(service_keys)}
    landmark_to_id = {k: i for i, k in enumerate(landmark_keys)}

    corridor_nx = nx.Graph()
    for u_str, v_str in read_tsv_edges(DATASETS_DIR / "highway_corridor_res6.tsv"):
        corridor_nx.add_edge(res6_to_id[u_str], res6_to_id[v_str])

    res6_trucks: dict[int, list[int]] = {}
    for c6_str, trk_str in read_tsv_edges(DATASETS_DIR / "res6_has_truck.tsv"):
        res6_trucks.setdefault(res6_to_id[c6_str], []).append(truck_to_id[trk_str])

    cap_trucks: dict[int, set[int]] = {}
    for sc_str, trk_str in read_tsv_edges(DATASETS_DIR / "capability_has_truck.tsv"):
        cap_trucks.setdefault(service_to_id[sc_str], set()).add(truck_to_id[trk_str])

    res6_relays: dict[int, list[int]] = {}
    for c6_str, ry_str in read_tsv_edges(DATASETS_DIR / "res6_has_relay_yard.tsv"):
        res6_relays.setdefault(res6_to_id[c6_str], []).append(relay_to_id[ry_str])

    res8_trucks: dict[int, list[int]] = {}
    for c8_str, trk_str in read_tsv_edges(DATASETS_DIR / "res8_has_truck.tsv"):
        res8_trucks.setdefault(res8_to_id[c8_str], []).append(truck_to_id[trk_str])

    res8_shipments: dict[int, list[int]] = {}
    for c8_str, bol_str in read_tsv_edges(DATASETS_DIR / "res8_has_shipment.tsv"):
        res8_shipments.setdefault(res8_to_id[c8_str], []).append(shipment_to_id[bol_str])

    res8_landmarks: dict[int, list[int]] = {}
    for c8_str, lm_str in read_tsv_edges(DATASETS_DIR / "res8_has_landmark.tsv"):
        res8_landmarks.setdefault(res8_to_id[c8_str], []).append(landmark_to_id[lm_str])

    python_load_us = (time.perf_counter_ns() - t_load0) / 1_000.0

    # 2. Define the 4 Python (`h3-py` + `NetworkX`) query pipelines
    s1_cfg = gt["scenario1_pd_dispatch"]
    s1_origin_key = s1_cfg["origin_res6_key"]
    s1_k_rings = s1_cfg["k_rings"]
    s1_req_caps = s1_cfg["required_service_class_ids"]

    def run_python_s1():
        kring_c6_ids = [
            res6_to_id[c6]
            for c6 in h3.grid_disk(s1_origin_key, s1_k_rings)
            if c6 in res6_to_id
        ]
        all_trks = set()
        for c6_id in kring_c6_ids:
            for tid in res6_trucks.get(c6_id, ()):
                all_trks.add(tid)
        qual_trks = set(all_trks)
        for cap_id in s1_req_caps:
            qual_trks &= cap_trucks.get(cap_id, set())
        return sorted(kring_c6_ids), sorted(all_trks), sorted(qual_trks)

    s2_cfg = gt["scenario2_relay_yards"]
    s2_orig_id = s2_cfg["origin_res6_dense_id"]
    s2_dest_id = s2_cfg["dest_res6_dense_id"]
    s2_hops = s2_cfg["corridor_hops"]

    def run_python_s2():
        reach_a = set(nx.single_source_shortest_path_length(corridor_nx, s2_orig_id, cutoff=s2_hops).keys())
        reach_b = set(nx.single_source_shortest_path_length(corridor_nx, s2_dest_id, cutoff=s2_hops).keys())
        overlap_c6 = sorted(reach_a & reach_b)
        relays = set()
        for c6_id in overlap_c6:
            for ry_id in res6_relays.get(c6_id, ()):
                relays.add(ry_id)
        return overlap_c6, sorted(relays)

    s3_cfg = gt["scenario3_weather_drilldown"]
    s3_storm_keys = s3_cfg["storm_res4_keys"]
    s3_buffer_rings = s3_cfg["buffer_rings"]

    def run_python_s3():
        zone_res4_keys = set()
        for c4 in s3_storm_keys:
            for nb4 in h3.grid_disk(c4, s3_buffer_rings):
                if nb4 in res4_to_id:
                    zone_res4_keys.add(nb4)
        zone_res4_ids = sorted(res4_to_id[c4] for c4 in zone_res4_keys)

        zone_res6_keys = []
        for c4 in zone_res4_keys:
            for c6 in h3.cell_to_children(c4, 6):
                if c6 in res6_to_id:
                    zone_res6_keys.append(c6)
        zone_res6_ids = sorted(res6_to_id[c6] for c6 in zone_res6_keys)

        zone_res8_ids = []
        for c6 in zone_res6_keys:
            for c8 in h3.cell_to_children(c6, 8):
                c8_id = res8_to_id.get(c8)
                if c8_id is not None:
                    zone_res8_ids.append(c8_id)
        zone_res8_ids.sort()

        imp_trucks = set()
        imp_shipments = set()
        imp_landmarks = set()
        for c8_id in zone_res8_ids:
            for tid in res8_trucks.get(c8_id, ()):
                imp_trucks.add(tid)
            for bid in res8_shipments.get(c8_id, ()):
                imp_shipments.add(bid)
            for lid in res8_landmarks.get(c8_id, ()):
                imp_landmarks.add(lid)

        return (
            zone_res4_ids,
            zone_res6_ids,
            zone_res8_ids,
            sorted(imp_trucks),
            sorted(imp_shipments),
            sorted(imp_landmarks),
        )

    s4_cfg = gt["scenario4_corridor_reachability"]
    s4_start_id = s4_cfg["start_res6_dense_id"]

    def run_python_s4():
        comp = nx.node_connected_component(corridor_nx, s4_start_id)
        return sorted(comp)

    # Execute & Benchmark Python Scenarios
    py_s1_kring, py_s1_all, py_s1_qual = run_python_s1()
    py_s2_overlap, py_s2_relays = run_python_s2()
    py_s3_r4, py_s3_r6, py_s3_r8, py_s3_trk, py_s3_shp, py_s3_lm = run_python_s3()
    py_s4_reach = run_python_s4()

    py_b1 = measure_python_scenario(run_python_s1)
    py_b2 = measure_python_scenario(run_python_s2)
    py_b3 = measure_python_scenario(run_python_s3)
    py_b4 = measure_python_scenario(run_python_s4)

    # 3. Invoke Java 25 Showcase Runner via Maven
    java_json_path = DATASETS_DIR / "java_benchmark_results.json"
    subprocess.run(
        [
            "mvn",
            "-q",
            "compile",
            "exec:exec",
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )

    with open(java_json_path, "r", encoding="utf-8") as f:
        java_res = json.load(f)

    # 4. Verify 100.00% Set Identity across Python (`h3-py` + `NetworkX`), Oracle, and Java 25 (`ImpulseVM`)
    jv_s1 = java_res["scenario1_pd_dispatch"]
    jv_s2 = java_res["scenario2_relay_yards"]
    jv_s3 = java_res["scenario3_weather_drilldown"]
    jv_s4 = java_res["scenario4_corridor_reachability"]

    assert py_s1_kring == jv_s1["k_ring_res6_ids"] == s1_cfg["expected_k_ring_res6_ids"], "S1 k-ring mismatch"
    assert py_s1_all == jv_s1["all_truck_ids_in_kring"] == s1_cfg["expected_all_truck_ids_in_kring"], "S1 all trucks mismatch"
    assert py_s1_qual == jv_s1["qualified_truck_ids"] == s1_cfg["expected_qualified_truck_ids"], "S1 qualified trucks mismatch"

    assert py_s2_overlap == jv_s2["overlap_res6_ids"] == s2_cfg["expected_overlap_res6_ids"], "S2 overlap Res6 mismatch"
    assert py_s2_relays == jv_s2["relay_yard_ids"] == s2_cfg["expected_relay_yard_ids"], "S2 relay yards mismatch"

    assert py_s3_r4 == jv_s3["res4_zone_ids"] == s3_cfg["expected_res4_zone_ids"], "S3 Res4 zone mismatch"
    assert py_s3_r6 == jv_s3["res6_ids"] == s3_cfg["expected_res6_ids"], "S3 Res6 mismatch"
    assert py_s3_r8 == jv_s3["res8_ids"] == s3_cfg["expected_res8_ids"], "S3 Res8 mismatch"
    assert py_s3_trk == jv_s3["impacted_truck_ids"] == s3_cfg["expected_impacted_truck_ids"], "S3 impacted trucks mismatch"
    assert py_s3_shp == jv_s3["impacted_shipment_ids"] == s3_cfg["expected_impacted_shipment_ids"], "S3 impacted shipments mismatch"
    assert py_s3_lm == jv_s3["impacted_landmark_ids"] == s3_cfg["expected_impacted_landmark_ids"], "S3 impacted landmarks mismatch"

    assert py_s4_reach == jv_s4["reachable_corridor_res6_ids"] == s4_cfg["expected_reachable_corridor_res6_ids"], "S4 corridor reachability mismatch"

    # 5. Build & Export Combined Comparison Report
    java_load_us = java_res["cold_start_load_us"]
    load_speedup = python_load_us / java_load_us

    scenarios_summary = [
        ("1. Dynamic P&D k-Ring Dispatch (k=3 + 3 Caps)", py_b1, jv_s1, f"{len(py_s1_qual)} trucks ({len(py_s1_kring)} cells)"),
        ("2. Meet-and-Turn Corridor Isochrone (62 hops)", py_b2, jv_s2, f"{len(py_s2_relays)} yards ({len(py_s2_overlap)} cells)"),
        ("3. Multi-Res Weather Drilldown (Res4->6->8->POI)", py_b3, jv_s3, f"{len(py_s3_trk)} trk / {len(py_s3_shp)} bol / {len(py_s3_lm)} poi"),
        ("4. Transitive Corridor Reachability (Fixed-Point)", py_b4, jv_s4, f"{len(py_s4_reach)} Res6 corridor cells"),
    ]

    comparison_out = {
        "cold_start": {
            "python_load_us": round(python_load_us, 2),
            "java_mmap_load_us": round(java_load_us, 2),
            "speedup_x": round(load_speedup, 2),
        },
        "scenarios": {},
    }

    print("\n### Empirical Cross-Language Verification & Benchmark Summary")
    print(
        f"- **Cold-Start Graph Load**: Python (`TSV` + `NetworkX` build) = `{python_load_us:,.2f} us` "
        f"vs. Java 25 (`ImpulseGraphSnapshot` `.imps` mmap) = `{java_load_us:,.2f} us` (**`{load_speedup:,.1f}x` faster**)"
    )
    print("\n| Scenario | Python `h3-py` + `NetworkX` Avg (us) | Java 25 `ImpulseVM` Avg (us) | Java 25 p50 (us) | Java 25 p99 (us) | Java 25 QPS | Speedup | Set Parity (100% Match) |")
    print("| :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- |")

    for title, py_m, jv_m, match_desc in scenarios_summary:
        speedup = py_m["avg_latency_us"] / jv_m["avg_latency_us"]
        comparison_out["scenarios"][title] = {
            "python_avg_us": round(py_m["avg_latency_us"], 2),
            "python_p50_us": round(py_m["p50_latency_us"], 2),
            "python_p99_us": round(py_m["p99_latency_us"], 2),
            "python_qps": round(py_m["qps"], 1),
            "java_avg_us": round(jv_m["avg_latency_us"], 2),
            "java_p50_us": round(jv_m["p50_latency_us"], 2),
            "java_p99_us": round(jv_m["p99_latency_us"], 2),
            "java_qps": round(jv_m["qps"], 1),
            "speedup_x": round(speedup, 2),
            "verified_output": match_desc,
        }
        print(
            f"| **{title}** | `{py_m['avg_latency_us']:,.2f}` | `{jv_m['avg_latency_us']:,.2f}` | "
            f"`{jv_m['p50_latency_us']:,.2f}` | `{jv_m['p99_latency_us']:,.2f}` | "
            f"`{jv_m['qps']:,.0f}/s` | **`{speedup:,.1f}x`** | `PASS` ({match_desc}) |"
        )

    comp_path = DATASETS_DIR / "benchmark_comparison.json"
    with open(comp_path, "w", encoding="utf-8") as f:
        json.dump(comparison_out, f, indent=2)


if __name__ == "__main__":
    main()
