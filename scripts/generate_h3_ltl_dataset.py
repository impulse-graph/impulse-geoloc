#!/usr/bin/env python3
"""
Deterministic US Interstate LTL Freight & H3 Multi-Resolution Dataset Generator
for the Impulse Graph Engine (`impulse-geoloc`) showcase.

Ingests real public GIS datasets (1,939 Waffle House locations and 2,631 OPIS
Interstate Truck Stops / Travel Plazas), generates multi-resolution H3 (Res 4,
Res 6, Res 8) and LTL freight TSV tables, invokes the official `impulse build`
and `impulse snapshot validate` CLI tools, and exports exact ground-truth
verification sets to `datasets/query_ground_truth.json`.
"""

import argparse
import csv
import json
import os
import random
import re
import subprocess
import urllib.request
from pathlib import Path

import h3
import networkx as nx


REPO_ROOT = Path(__file__).resolve().parent.parent
DATASETS_DIR = REPO_ROOT / "datasets"
RAW_DIR = DATASETS_DIR / "raw"
IMPULSE_CLI = Path("/Users/jesse/impulse/impulse-graph-tooling/target/release/impulse")

WAFFLE_HOUSE_CSV_URL = (
    "https://raw.githubusercontent.com/beaswift/heatmapping_location_data/"
    "d026e2c72285c737292721786c36e6ab4f2da22e/csv_folder/Waffle%20House.csv"
)
OPIS_TRUCK_STOPS_CSV_URL = (
    "https://raw.githubusercontent.com/rohitrajput-42/geospat/"
    "710c666481737b848979d329d07cfcc44b1a573b/fuel_with_coords.csv"
)


TERMINALS = [
    ("DAL-01", "Dallas, TX", 32.7767, -96.7970, 180, 1),
    ("MEM-01", "Memphis, TN", 35.1495, -90.0490, 220, 1),
    ("ATL-01", "Atlanta, GA", 33.7490, -84.3880, 195, 1),
    ("ORD-01", "Chicago, IL", 41.8781, -87.6298, 240, 1),
    ("IND-01", "Indianapolis, IN", 39.7684, -86.1581, 165, 1),
    ("STL-01", "St. Louis, MO", 38.6270, -90.1994, 150, 1),
    ("BNA-01", "Nashville, TN", 36.1627, -86.7816, 140, 1),
    ("LIT-01", "Little Rock, AR", 34.7465, -92.2896, 120, 1),
    ("OKC-01", "Oklahoma City, OK", 35.4676, -97.5164, 115, 1),
    ("MCI-01", "Kansas City, MO", 39.0997, -94.5786, 155, 1),
    ("CLT-01", "Charlotte, NC", 35.2271, -80.8431, 160, 1),
    ("HOU-01", "Houston, TX", 29.7604, -95.3698, 175, 1),
]

CORRIDOR_PAIRS = [
    # I-40
    ("OKC-01", "LIT-01"),
    ("LIT-01", "MEM-01"),
    ("MEM-01", "BNA-01"),
    ("BNA-01", "CLT-01"),
    # I-20 / I-30
    ("DAL-01", "LIT-01"),
    ("DAL-01", "ATL-01"),
    # I-35
    ("HOU-01", "DAL-01"),
    ("DAL-01", "OKC-01"),
    ("OKC-01", "MCI-01"),
    # I-55
    ("MEM-01", "STL-01"),
    ("STL-01", "ORD-01"),
    # I-65 / I-24 / I-75
    ("ATL-01", "BNA-01"),
    ("BNA-01", "IND-01"),
    ("IND-01", "ORD-01"),
    # I-70
    ("MCI-01", "STL-01"),
    ("STL-01", "IND-01"),
    # I-85
    ("ATL-01", "CLT-01"),
]

SERVICE_CLASSES = [
    ("CAP_HAZMAT", 0),
    ("CAP_PALLET_4PLUS", 1),
    ("CAP_HOS_2HR_PLUS", 2),
    ("CAP_LIFTGATE", 3),
]


def write_tsv(path: Path, headers: list[str], rows: list[tuple]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("\t".join(headers) + "\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")


def sanitize_token(text: str, max_len: int = 24) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", text.strip()).strip("_")
    return cleaned[:max_len] if cleaned else "POI"


def ensure_raw_gis_csvs() -> tuple[Path, Path]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    wh_csv = RAW_DIR / "waffle_houses.csv"
    ts_csv = RAW_DIR / "truck_stops.csv"

    for url, target in [(WAFFLE_HOUSE_CSV_URL, wh_csv), (OPIS_TRUCK_STOPS_CSV_URL, ts_csv)]:
        if not target.exists() or target.stat().st_size < 1024:
            try:
                urllib.request.urlretrieve(url, target)
            except Exception as exc:
                print(f"Warning: could not fetch {url} ({exc}); using fallback if needed.")
    return wh_csv, ts_csv


def load_real_landmarks_in_res6(
    all_res6_set: set[str],
    storm_res4_set: set[str],
    storm_buffer_res4_set: set[str],
    rng: random.Random,
) -> list[tuple[str, int, int, str, str]]:
    """
    Returns list of (landmark_id, is_waffle_house, waffle_status, c6, c8)
    filtered to the active H3_Res6 domain.
    """
    wh_csv, ts_csv = ensure_raw_gis_csvs()
    landmarks: list[tuple[str, int, int, str, str]] = []
    seen_ids: set[str] = set()

    if wh_csv.exists() and wh_csv.stat().st_size >= 1024:
        with open(wh_csv, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader):
                try:
                    lat = float(row["latitude"])
                    lon = float(row["longitude"])
                except (KeyError, ValueError, TypeError):
                    continue
                c6 = h3.latlng_to_cell(lat, lon, 6)
                if c6 not in all_res6_set:
                    continue
                c8 = h3.latlng_to_cell(lat, lon, 8)
                c4 = h3.cell_to_parent(c6, 4)

                addr = row.get("Address", "")
                loc_name = row.get("Location Name", "").replace("Waffle House-", "")
                m = re.search(r"WH\s*#\s*(\d+)", addr)
                unit_num = f"{int(m.group(1)):04d}" if m else f"X{idx + 1:04d}"
                city_tok = sanitize_token(loc_name, 18)
                lm_id = f"WH-{unit_num}_{city_tok}"
                if lm_id in seen_ids:
                    lm_id = f"{lm_id}_{idx}"
                seen_ids.add(lm_id)

                if c4 in storm_res4_set:
                    w_status = 2 if rng.random() < 0.7 else 1
                elif c4 in storm_buffer_res4_set:
                    w_status = 1 if rng.random() < 0.6 else 0
                else:
                    w_status = 0

                landmarks.append((lm_id, 1, w_status, c6, c8))

    if ts_csv.exists() and ts_csv.stat().st_size >= 1024:
        seen_opis: set[str] = set()
        with open(ts_csv, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader):
                try:
                    lat = float(row["Latitude"])
                    lon = float(row["Longitude"])
                except (KeyError, ValueError, TypeError):
                    continue
                opis_raw = row.get("OPIS Truckstop ID", "").strip()
                if not opis_raw or opis_raw in seen_opis:
                    continue
                seen_opis.add(opis_raw)

                c6 = h3.latlng_to_cell(lat, lon, 6)
                if c6 not in all_res6_set:
                    continue
                c8 = h3.latlng_to_cell(lat, lon, 8)
                c4 = h3.cell_to_parent(c6, 4)

                name_tok = sanitize_token(row.get("Truckstop Name", "TravelPlaza"), 16)
                state_tok = sanitize_token(row.get("State", "US"), 4)
                lm_id = f"TS-OPIS-{int(opis_raw):04d}_{name_tok}_{state_tok}"
                if lm_id in seen_ids:
                    lm_id = f"{lm_id}_{idx}"
                seen_ids.add(lm_id)

                if c4 in storm_res4_set:
                    w_status = 2 if rng.random() < 0.5 else 1
                elif c4 in storm_buffer_res4_set:
                    w_status = 1 if rng.random() < 0.4 else 0
                else:
                    w_status = 0

                landmarks.append((lm_id, 0, w_status, c6, c8))

    return landmarks


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate H3 LTL Freight dataset and .imps snapshot.")
    parser.add_argument("--trucks", type=int, default=1500, help="Number of trucks (default: 1500)")
    parser.add_argument("--shipments", type=int, default=3000, help="Number of shipments (default: 3000)")
    args = parser.parse_args()

    rng = random.Random(42)
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Resolve terminal H3 Res 6 cells and interstate highway corridors
    term_res6: dict[str, str] = {}
    for tid, _city, lat, lon, _doors, _bb in TERMINALS:
        term_res6[tid] = h3.latlng_to_cell(lat, lon, 6)

    corridor_paths: dict[tuple[str, str], list[str]] = {}
    corridor_res6_set: set[str] = set()
    for src_t, dst_t in CORRIDOR_PAIRS:
        path_cells = list(h3.grid_path_cells(term_res6[src_t], term_res6[dst_t]))
        corridor_paths[(src_t, dst_t)] = path_cells
        corridor_res6_set.update(path_cells)

    for tid, c6 in term_res6.items():
        corridor_res6_set.update(h3.grid_disk(c6, 1))

    # Build full H3_Res6 set: 1-ring buffer around all corridors + 4-ring P&D zone around terminals
    all_res6_set: set[str] = set()
    for c6 in corridor_res6_set:
        all_res6_set.update(h3.grid_disk(c6, 1))
    for tid, c6 in term_res6.items():
        all_res6_set.update(h3.grid_disk(c6, 4))

    res6_nodes = sorted(all_res6_set)
    res6_to_dense = {c6: idx for idx, c6 in enumerate(res6_nodes)}

    # Build H3_Res4 set: parents of all H3_Res6 cells + 1-ring around corridor Res4 cells
    all_res4_set: set[str] = {h3.cell_to_parent(c6, 4) for c6 in res6_nodes}
    for c6 in corridor_res6_set:
        p4 = h3.cell_to_parent(c6, 4)
        all_res4_set.update(h3.grid_disk(p4, 1))

    res4_nodes = sorted(all_res4_set)
    res4_to_dense = {c4: idx for idx, c4 in enumerate(res4_nodes)}

    # Storm alert cells at Res 4 (Mid-South I-40 corridor: MEM-01, LIT-01, BNA-01 parent Res 4 cells)
    storm_res4_keys = sorted(
        {
            h3.cell_to_parent(term_res6["MEM-01"], 4),
            h3.cell_to_parent(term_res6["LIT-01"], 4),
            h3.cell_to_parent(term_res6["BNA-01"], 4),
        }
    )
    storm_res4_set = set(storm_res4_keys)
    storm_buffer_res4_set: set[str] = set()
    for c4 in storm_res4_keys:
        for nb4 in h3.grid_disk(c4, 1):
            if nb4 in res4_to_dense:
                storm_buffer_res4_set.add(nb4)

    # Load real Waffle House & OPIS Truck Stop landmarks in active H3_Res6
    real_landmarks = load_real_landmarks_in_res6(
        all_res6_set, storm_res4_set, storm_buffer_res4_set, rng
    )

    # Build H3_Res8 set: full 49 children in 2-ring around terminals, 7-cell center cluster elsewhere,
    # plus exact H3_Res8 cells of all real Waffle Houses and OPIS Truck Stops
    term_2ring_res6: set[str] = set()
    for _tid, c6 in term_res6.items():
        term_2ring_res6.update(h3.grid_disk(c6, 2))

    all_res8_set: set[str] = set()
    for c6 in res6_nodes:
        if c6 in term_2ring_res6:
            all_res8_set.update(h3.cell_to_children(c6, 8))
        else:
            cc8 = h3.cell_to_center_child(c6, 8)
            all_res8_set.update(h3.grid_disk(cc8, 1))

    for _lm_id, _is_wh, _w_status, _c6, c8 in real_landmarks:
        all_res8_set.add(c8)

    for c8 in all_res8_set:
        all_res6_set.add(h3.cell_to_parent(c8, 6))
    res6_nodes = sorted(all_res6_set)
    res6_to_dense = {c6: idx for idx, c6 in enumerate(res6_nodes)}

    res8_nodes = sorted(all_res8_set)
    res8_to_dense = {c8: idx for idx, c8 in enumerate(res8_nodes)}

    # Group Res8 by parent Res6
    res6_to_res8_children: dict[str, list[str]] = {c6: [] for c6 in res6_nodes}
    for c8 in res8_nodes:
        p6 = h3.cell_to_parent(c8, 6)
        if p6 in res6_to_res8_children:
            res6_to_res8_children[p6].append(c8)

    # 2. Write H3 domain TSV files
    res4_rows = []
    for c4 in res4_nodes:
        lat, lon = h3.cell_to_latlng(c4)
        if c4 in storm_res4_set:
            w_status = 2
        elif c4 in storm_buffer_res4_set:
            w_status = 1
        else:
            w_status = 0
        res4_rows.append((c4, f"{lat:.6f}", f"{lon:.6f}", w_status))
    write_tsv(DATASETS_DIR / "h3_res4_nodes.tsv", ["id", "lat", "lon", "waffle_index_status"], res4_rows)

    res6_rows = []
    for c6 in res6_nodes:
        lat, lon = h3.cell_to_latlng(c6)
        is_corr = 1 if c6 in corridor_res6_set else 0
        res6_rows.append((c6, f"{lat:.6f}", f"{lon:.6f}", is_corr))
    write_tsv(DATASETS_DIR / "h3_res6_nodes.tsv", ["id", "lat", "lon", "is_corridor"], res6_rows)

    res8_rows = []
    for c8 in res8_nodes:
        lat, lon = h3.cell_to_latlng(c8)
        res8_rows.append((c8, f"{lat:.6f}", f"{lon:.6f}"))
    write_tsv(DATASETS_DIR / "h3_res8_nodes.tsv", ["id", "lat", "lon"], res8_rows)

    # 3. Build H3 hierarchy & adjacency edges
    edges_res4_to_res6 = []
    edges_res6_to_res4 = []
    for c6 in res6_nodes:
        p4 = h3.cell_to_parent(c6, 4)
        if p4 in res4_to_dense:
            edges_res4_to_res6.append((p4, c6))
            edges_res6_to_res4.append((c6, p4))

    edges_res6_to_res8 = []
    edges_res8_to_res6 = []
    for c8 in res8_nodes:
        p6 = h3.cell_to_parent(c8, 6)
        if p6 in res6_to_dense:
            edges_res6_to_res8.append((p6, c8))
            edges_res8_to_res6.append((c8, p6))

    edges_hex_adj_res4 = []
    for c4 in res4_nodes:
        for nb4 in sorted(h3.grid_disk(c4, 1)):
            if nb4 in res4_to_dense:
                edges_hex_adj_res4.append((c4, nb4))

    edges_hex_adj_res6 = []
    for c6 in res6_nodes:
        for nb6 in sorted(h3.grid_disk(c6, 1)):
            if nb6 in res6_to_dense:
                edges_hex_adj_res6.append((c6, nb6))

    edges_hex_adj_res8 = []
    for c8 in res8_nodes:
        for nb8 in sorted(h3.grid_disk(c8, 1)):
            if nb8 in res8_to_dense:
                edges_hex_adj_res8.append((c8, nb8))

    edges_corridor_res6 = []
    corridor_nx = nx.Graph()
    for c6 in sorted(corridor_res6_set):
        for nb6 in sorted(h3.grid_disk(c6, 1)):
            if nb6 in corridor_res6_set:
                edges_corridor_res6.append((c6, nb6))
                corridor_nx.add_edge(c6, nb6)

    write_tsv(DATASETS_DIR / "res4_to_res6.tsv", ["src", "tgt"], edges_res4_to_res6)
    write_tsv(DATASETS_DIR / "res6_to_res4.tsv", ["src", "tgt"], edges_res6_to_res4)
    write_tsv(DATASETS_DIR / "res6_to_res8.tsv", ["src", "tgt"], edges_res6_to_res8)
    write_tsv(DATASETS_DIR / "res8_to_res6.tsv", ["src", "tgt"], edges_res8_to_res6)
    write_tsv(DATASETS_DIR / "hex_adjacent_res4.tsv", ["src", "tgt"], edges_hex_adj_res4)
    write_tsv(DATASETS_DIR / "hex_adjacent_res6.tsv", ["src", "tgt"], edges_hex_adj_res6)
    write_tsv(DATASETS_DIR / "hex_adjacent_res8.tsv", ["src", "tgt"], edges_hex_adj_res8)
    write_tsv(DATASETS_DIR / "highway_corridor_res6.tsv", ["src", "tgt"], edges_corridor_res6)

    # 4. Write Terminals (Domain 3) and Terminal Relations
    terminal_rows = []
    edges_terminal_at_res6 = []
    edges_res6_has_terminal = []
    edges_terminal_serves_res6 = []
    term_to_dense = {}

    for idx, (tid, _city, _lat, _lon, doors, is_bb) in enumerate(TERMINALS):
        term_to_dense[tid] = idx
        terminal_rows.append((tid, doors, is_bb))
        c6 = term_res6[tid]
        edges_terminal_at_res6.append((tid, c6))
        edges_res6_has_terminal.append((c6, tid))
        for serv_c6 in sorted(h3.grid_disk(c6, 3)):
            if serv_c6 in res6_to_dense:
                edges_terminal_serves_res6.append((tid, serv_c6))

    write_tsv(DATASETS_DIR / "terminals.tsv", ["id", "dock_doors", "is_breakbulk"], terminal_rows)
    write_tsv(DATASETS_DIR / "terminal_at_res6.tsv", ["src", "tgt"], edges_terminal_at_res6)
    write_tsv(DATASETS_DIR / "res6_has_terminal.tsv", ["src", "tgt"], edges_res6_has_terminal)
    write_tsv(DATASETS_DIR / "terminal_serves_res6.tsv", ["src", "tgt"], edges_terminal_serves_res6)

    # 5. Compute DAL-01 <-> MEM-01 bidirectional corridor overlap and place Relay Yards (Domain 4)
    dal_c6 = term_res6["DAL-01"]
    mem_c6 = term_res6["MEM-01"]
    corridor_hops = 62
    dal_reach_c6 = set(nx.single_source_shortest_path_length(corridor_nx, dal_c6, cutoff=corridor_hops).keys())
    mem_reach_c6 = set(nx.single_source_shortest_path_length(corridor_nx, mem_c6, cutoff=corridor_hops).keys())
    dal_mem_overlap_c6 = sorted(dal_reach_c6 & mem_reach_c6)

    relay_candidate_cells: list[str] = []
    for c6 in dal_mem_overlap_c6[::3]:
        if c6 not in relay_candidate_cells:
            relay_candidate_cells.append(c6)

    for _pair, path_cells in corridor_paths.items():
        n = len(path_cells)
        for frac in (n // 3, n // 2, (2 * n) // 3):
            c6 = path_cells[frac]
            if c6 not in relay_candidate_cells:
                relay_candidate_cells.append(c6)

    relay_rows = []
    edges_res6_has_relay = []
    edges_relay_at_res6 = []
    relay_to_dense = {}
    for idx, c6 in enumerate(relay_candidate_cells):
        ry_id = f"RY-{idx + 1:04d}"
        relay_to_dense[ry_id] = idx
        lot_cap = rng.randint(45, 120)
        relay_rows.append((ry_id, lot_cap))
        edges_res6_has_relay.append((c6, ry_id))
        edges_relay_at_res6.append((ry_id, c6))

    write_tsv(DATASETS_DIR / "relay_yards.tsv", ["id", "lot_capacity"], relay_rows)
    write_tsv(DATASETS_DIR / "res6_has_relay_yard.tsv", ["src", "tgt"], edges_res6_has_relay)
    write_tsv(DATASETS_DIR / "relay_yard_at_res6.tsv", ["src", "tgt"], edges_relay_at_res6)

    # 6. Write ServiceClass (Domain 7)
    service_class_rows = [(sc_name, sc_code) for sc_name, sc_code in SERVICE_CLASSES]
    write_tsv(DATASETS_DIR / "service_classes.tsv", ["id", "code"], service_class_rows)

    # 7. Place 1,500 Trucks (Domain 5) at H3_Res8 cells (and parent H3_Res6 cells)
    terminal_3ring_res8: list[str] = []
    for _tid, c6 in term_res6.items():
        for sc6 in sorted(h3.grid_disk(c6, 3)):
            terminal_3ring_res8.extend(res6_to_res8_children.get(sc6, []))
    terminal_3ring_res8 = sorted(set(terminal_3ring_res8))

    truck_rows = []
    edges_res6_has_truck = []
    edges_res8_has_truck = []
    edges_truck_at_res8 = []
    edges_cap_has_truck = []

    num_trucks = args.trucks
    trk_pad = max(4, len(str(num_trucks)))
    terminal_truck_cutoff = int(num_trucks * 0.6)
    for idx in range(num_trucks):
        trk_id = f"TRK-{idx + 1:0{trk_pad}d}"
        if idx < terminal_truck_cutoff:
            c8 = rng.choice(terminal_3ring_res8)
        else:
            c8 = rng.choice(res8_nodes)
        c6 = h3.cell_to_parent(c8, 6)

        avail_pallets = rng.randint(1, 12)
        hos_mins = rng.randint(45, 660)
        hazmat = 1 if rng.random() < 0.35 else 0
        speed_mph = round(rng.uniform(48.0, 66.0), 1)
        has_liftgate = 1 if rng.random() < 0.50 else 0

        truck_rows.append((trk_id, avail_pallets, hos_mins, hazmat, f"{speed_mph:.1f}"))
        edges_res8_has_truck.append((c8, trk_id))
        edges_res6_has_truck.append((c6, trk_id))
        edges_truck_at_res8.append((trk_id, c8))

        if hazmat == 1:
            edges_cap_has_truck.append(("CAP_HAZMAT", trk_id))
        if avail_pallets >= 4:
            edges_cap_has_truck.append(("CAP_PALLET_4PLUS", trk_id))
        if hos_mins >= 120:
            edges_cap_has_truck.append(("CAP_HOS_2HR_PLUS", trk_id))
        if has_liftgate == 1:
            edges_cap_has_truck.append(("CAP_LIFTGATE", trk_id))

    write_tsv(
        DATASETS_DIR / "trucks.tsv",
        ["id", "avail_pallets", "hos_mins", "hazmat", "speed_mph"],
        truck_rows,
    )
    write_tsv(DATASETS_DIR / "res6_has_truck.tsv", ["src", "tgt"], edges_res6_has_truck)
    write_tsv(DATASETS_DIR / "res8_has_truck.tsv", ["src", "tgt"], edges_res8_has_truck)
    write_tsv(DATASETS_DIR / "truck_at_res8.tsv", ["src", "tgt"], edges_truck_at_res8)
    write_tsv(DATASETS_DIR / "capability_has_truck.tsv", ["src", "tgt"], edges_cap_has_truck)

    # 8. Place 3,000 Shipments (Domain 6) at H3_Res8 cells
    shipment_rows = []
    edges_shipment_origin_res8 = []
    edges_res8_has_shipment = []

    num_shipments = args.shipments
    bol_pad = max(5, len(str(num_shipments)))
    terminal_shipment_cutoff = int(num_shipments * 0.6)
    for idx in range(num_shipments):
        bol_id = f"BOL-{idx + 1:0{bol_pad}d}"
        if idx < terminal_shipment_cutoff:
            c8 = rng.choice(terminal_3ring_res8)
        else:
            c8 = rng.choice(res8_nodes)

        pallets = rng.randint(1, 10)
        weight_lbs = rng.randint(450, 18500)
        hazmat = 1 if rng.random() < 0.18 else 0
        priority = rng.choice([1, 2, 3])

        shipment_rows.append((bol_id, pallets, weight_lbs, hazmat, priority))
        edges_shipment_origin_res8.append((bol_id, c8))
        edges_res8_has_shipment.append((c8, bol_id))

    write_tsv(
        DATASETS_DIR / "shipments.tsv",
        ["id", "pallets", "weight_lbs", "hazmat", "priority"],
        shipment_rows,
    )
    write_tsv(DATASETS_DIR / "shipment_origin_res8.tsv", ["src", "tgt"], edges_shipment_origin_res8)
    write_tsv(DATASETS_DIR / "res8_has_shipment.tsv", ["src", "tgt"], edges_res8_has_shipment)

    # 9. Write Landmarks (Domain 8: Real Waffle Houses & OPIS Truck Stops, with fallback if offline)
    landmark_rows = []
    edges_res6_has_landmark = []
    edges_res8_has_landmark = []
    edges_landmark_at_res8 = []
    lm_to_dense: dict[str, int] = {}

    if real_landmarks:
        for idx, (lm_id, is_wh, w_status, c6, c8) in enumerate(real_landmarks):
            lm_to_dense[lm_id] = idx
            landmark_rows.append((lm_id, is_wh, w_status))
            edges_res6_has_landmark.append((c6, lm_id))
            edges_res8_has_landmark.append((c8, lm_id))
            edges_landmark_at_res8.append((lm_id, c8))
    else:
        corridor_res8_pool: list[str] = []
        for c6 in sorted(corridor_res6_set):
            corridor_res8_pool.extend(res6_to_res8_children.get(c6, []))
        for idx in range(400):
            is_wh = 1 if idx < 250 else 0
            lm_id = f"WH-{idx + 1:04d}" if is_wh else f"TP-{idx + 1:04d}"
            lm_to_dense[lm_id] = idx
            c8 = rng.choice(corridor_res8_pool)
            c6 = h3.cell_to_parent(c8, 6)
            c4 = h3.cell_to_parent(c6, 4)
            w_status = 2 if c4 in storm_res4_set else (1 if c4 in storm_buffer_res4_set else 0)
            landmark_rows.append((lm_id, is_wh, w_status))
            edges_res6_has_landmark.append((c6, lm_id))
            edges_res8_has_landmark.append((c8, lm_id))
            edges_landmark_at_res8.append((lm_id, c8))

    write_tsv(
        DATASETS_DIR / "landmarks.tsv",
        ["id", "is_waffle_house", "waffle_status"],
        landmark_rows,
    )
    write_tsv(DATASETS_DIR / "res6_has_landmark.tsv", ["src", "tgt"], edges_res6_has_landmark)
    write_tsv(DATASETS_DIR / "res8_has_landmark.tsv", ["src", "tgt"], edges_res8_has_landmark)
    write_tsv(DATASETS_DIR / "landmark_at_res8.tsv", ["src", "tgt"], edges_landmark_at_res8)

    # 10. Write manifest.json (Full US LTL + H3 Multi-Resolution Graph)
    manifest_full = {
        "version": "0.9.0",
        "domains": [
            {
                "id": 0,
                "name": "H3_Res4",
                "key_type": "string",
                "file": "h3_res4_nodes.tsv",
                "id_column": "id",
                "attributes": [
                    {"name": "lat", "column": "lat", "type": "float32"},
                    {"name": "lon", "column": "lon", "type": "float32"},
                    {"name": "waffle_index_status", "column": "waffle_index_status", "type": "int32"},
                ],
            },
            {
                "id": 1,
                "name": "H3_Res6",
                "key_type": "string",
                "file": "h3_res6_nodes.tsv",
                "id_column": "id",
                "attributes": [
                    {"name": "lat", "column": "lat", "type": "float32"},
                    {"name": "lon", "column": "lon", "type": "float32"},
                    {"name": "is_corridor", "column": "is_corridor", "type": "int32"},
                ],
            },
            {
                "id": 2,
                "name": "H3_Res8",
                "key_type": "string",
                "file": "h3_res8_nodes.tsv",
                "id_column": "id",
                "attributes": [
                    {"name": "lat", "column": "lat", "type": "float32"},
                    {"name": "lon", "column": "lon", "type": "float32"},
                ],
            },
            {
                "id": 3,
                "name": "Terminal",
                "key_type": "string",
                "file": "terminals.tsv",
                "id_column": "id",
                "attributes": [
                    {"name": "dock_doors", "column": "dock_doors", "type": "int32"},
                    {"name": "is_breakbulk", "column": "is_breakbulk", "type": "int32"},
                ],
            },
            {
                "id": 4,
                "name": "RelayYard",
                "key_type": "string",
                "file": "relay_yards.tsv",
                "id_column": "id",
                "attributes": [
                    {"name": "lot_capacity", "column": "lot_capacity", "type": "int32"},
                ],
            },
            {
                "id": 5,
                "name": "Truck",
                "key_type": "string",
                "file": "trucks.tsv",
                "id_column": "id",
                "attributes": [
                    {"name": "avail_pallets", "column": "avail_pallets", "type": "int32"},
                    {"name": "hos_mins", "column": "hos_mins", "type": "int32"},
                    {"name": "hazmat", "column": "hazmat", "type": "int32"},
                    {"name": "speed_mph", "column": "speed_mph", "type": "float32"},
                ],
            },
            {
                "id": 6,
                "name": "Shipment",
                "key_type": "string",
                "file": "shipments.tsv",
                "id_column": "id",
                "attributes": [
                    {"name": "pallets", "column": "pallets", "type": "int32"},
                    {"name": "weight_lbs", "column": "weight_lbs", "type": "int32"},
                    {"name": "hazmat", "column": "hazmat", "type": "int32"},
                    {"name": "priority", "column": "priority", "type": "int32"},
                ],
            },
            {
                "id": 7,
                "name": "ServiceClass",
                "key_type": "string",
                "file": "service_classes.tsv",
                "id_column": "id",
                "attributes": [
                    {"name": "code", "column": "code", "type": "int32"},
                ],
            },
            {
                "id": 8,
                "name": "Landmark",
                "key_type": "string",
                "file": "landmarks.tsv",
                "id_column": "id",
                "attributes": [
                    {"name": "is_waffle_house", "column": "is_waffle_house", "type": "int32"},
                    {"name": "waffle_status", "column": "waffle_status", "type": "int32"},
                ],
            },
            {
                "id": 9,
                "name": "Corridor_Res6",
                "key_type": "string",
                "file": "h3_res6_nodes.tsv",
                "id_column": "id",
            },
        ],
        "relations": [
            {"name": "HEX_ADJACENT_RES4", "src_domain": 0, "tgt_domain": 0, "file": "hex_adjacent_res4.tsv"},
            {"name": "RES4_TO_RES6", "src_domain": 0, "tgt_domain": 1, "file": "res4_to_res6.tsv"},
            {"name": "RES6_TO_RES4", "src_domain": 1, "tgt_domain": 0, "file": "res6_to_res4.tsv"},
            {"name": "HEX_ADJACENT_RES6", "src_domain": 1, "tgt_domain": 1, "file": "hex_adjacent_res6.tsv"},
            {"name": "RES6_TO_RES8", "src_domain": 1, "tgt_domain": 2, "file": "res6_to_res8.tsv"},
            {"name": "RES6_HAS_TERMINAL", "src_domain": 1, "tgt_domain": 3, "file": "res6_has_terminal.tsv"},
            {"name": "RES6_HAS_RELAY_YARD", "src_domain": 1, "tgt_domain": 4, "file": "res6_has_relay_yard.tsv"},
            {"name": "RES6_HAS_TRUCK", "src_domain": 1, "tgt_domain": 5, "file": "res6_has_truck.tsv"},
            {"name": "RES6_HAS_LANDMARK", "src_domain": 1, "tgt_domain": 8, "file": "res6_has_landmark.tsv"},
            {"name": "RES8_TO_RES6", "src_domain": 2, "tgt_domain": 1, "file": "res8_to_res6.tsv"},
            {"name": "HEX_ADJACENT_RES8", "src_domain": 2, "tgt_domain": 2, "file": "hex_adjacent_res8.tsv"},
            {"name": "RES8_HAS_TRUCK", "src_domain": 2, "tgt_domain": 5, "file": "res8_has_truck.tsv"},
            {"name": "RES8_HAS_SHIPMENT", "src_domain": 2, "tgt_domain": 6, "file": "res8_has_shipment.tsv"},
            {"name": "RES8_HAS_LANDMARK", "src_domain": 2, "tgt_domain": 8, "file": "res8_has_landmark.tsv"},
            {"name": "TERMINAL_AT_RES6", "src_domain": 3, "tgt_domain": 1, "file": "terminal_at_res6.tsv"},
            {"name": "TERMINAL_SERVES_RES6", "src_domain": 3, "tgt_domain": 9, "file": "terminal_serves_res6.tsv"},
            {"name": "RELAY_YARD_AT_RES6", "src_domain": 4, "tgt_domain": 1, "file": "relay_yard_at_res6.tsv"},
            {"name": "TRUCK_AT_RES8", "src_domain": 5, "tgt_domain": 2, "file": "truck_at_res8.tsv"},
            {"name": "SHIPMENT_ORIGIN_RES8", "src_domain": 6, "tgt_domain": 2, "file": "shipment_origin_res8.tsv"},
            {"name": "CAPABILITY_HAS_TRUCK", "src_domain": 7, "tgt_domain": 5, "file": "capability_has_truck.tsv"},
            {"name": "LANDMARK_AT_RES8", "src_domain": 8, "tgt_domain": 2, "file": "landmark_at_res8.tsv"},
            {"name": "HIGHWAY_CORRIDOR_RES6", "src_domain": 9, "tgt_domain": 9, "file": "highway_corridor_res6.tsv"},
        ],
    }

    manifest_path = DATASETS_DIR / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_full, f, indent=2)

    # 11. Write manifest_h3_only.json (Pure H3 Multi-Resolution Grid Only)
    manifest_h3_only = {
        "version": "0.9.0",
        "domains": manifest_full["domains"][:3],
        "relations": [
            {"name": "HEX_ADJACENT_RES4", "src_domain": 0, "tgt_domain": 0, "file": "hex_adjacent_res4.tsv"},
            {"name": "RES4_TO_RES6", "src_domain": 0, "tgt_domain": 1, "file": "res4_to_res6.tsv"},
            {"name": "RES6_TO_RES4", "src_domain": 1, "tgt_domain": 0, "file": "res6_to_res4.tsv"},
            {"name": "HEX_ADJACENT_RES6", "src_domain": 1, "tgt_domain": 1, "file": "hex_adjacent_res6.tsv"},
            {"name": "RES6_TO_RES8", "src_domain": 1, "tgt_domain": 2, "file": "res6_to_res8.tsv"},
            {"name": "RES8_TO_RES6", "src_domain": 2, "tgt_domain": 1, "file": "res8_to_res6.tsv"},
            {"name": "HEX_ADJACENT_RES8", "src_domain": 2, "tgt_domain": 2, "file": "hex_adjacent_res8.tsv"},
        ],
    }

    manifest_h3_path = DATASETS_DIR / "manifest_h3_only.json"
    with open(manifest_h3_path, "w", encoding="utf-8") as f:
        json.dump(manifest_h3_only, f, indent=2)

    # 12. Compute Ground-Truth Oracle for the 4 Showcase Scenarios
    origin_res6_key = term_res6["DAL-01"]
    origin_res6_dense_id = res6_to_dense[origin_res6_key]
    k_rings = 3
    k_ring_res6_keys = sorted(c6 for c6 in h3.grid_disk(origin_res6_key, k_rings) if c6 in res6_to_dense)
    expected_k_ring_res6_ids = sorted(res6_to_dense[c6] for c6 in k_ring_res6_keys)
    k_ring_res6_key_set = set(k_ring_res6_keys)

    trk_to_dense = {f"TRK-{i + 1:0{trk_pad}d}": i for i in range(num_trucks)}
    bol_to_dense = {f"BOL-{i + 1:0{bol_pad}d}": i for i in range(num_shipments)}

    all_trucks_in_kring: set[int] = set()
    for c6, trk_id in edges_res6_has_truck:
        if c6 in k_ring_res6_key_set:
            all_trucks_in_kring.add(trk_to_dense[trk_id])

    cap_truck_sets: dict[int, set[int]] = {0: set(), 1: set(), 2: set(), 3: set()}
    sc_name_to_id = {name: code for name, code in SERVICE_CLASSES}
    for sc_name, trk_id in edges_cap_has_truck:
        cap_truck_sets[sc_name_to_id[sc_name]].add(trk_to_dense[trk_id])

    req_service_classes = [0, 1, 2]
    qualified_trucks = set(all_trucks_in_kring)
    for sc_id in req_service_classes:
        qualified_trucks &= cap_truck_sets[sc_id]

    expected_all_truck_ids_in_kring = sorted(all_trucks_in_kring)
    expected_qualified_truck_ids = sorted(qualified_trucks)
    expected_qualified_truck_keys = [f"TRK-{tid + 1:0{trk_pad}d}" for tid in expected_qualified_truck_ids]

    # Scenario 2: Interstate Linehaul Relay & Meet-and-Turn Corridor Search (DAL-01 <-> MEM-01)
    expected_overlap_res6_ids = sorted(res6_to_dense[c6] for c6 in dal_mem_overlap_c6)
    overlap_c6_set = set(dal_mem_overlap_c6)
    expected_relay_yard_ids = sorted(
        relay_to_dense[ry_id] for c6, ry_id in edges_res6_has_relay if c6 in overlap_c6_set
    )
    expected_relay_yard_keys = [f"RY-{ry_id + 1:04d}" for ry_id in expected_relay_yard_ids]

    # Scenario 3: Multi-Resolution Hierarchical Weather / Storm Disruption & Waffle House Index Drill-Down
    storm_res4_dense_ids = sorted(res4_to_dense[c4] for c4 in storm_res4_keys)
    expected_res4_zone_ids = sorted(res4_to_dense[c4] for c4 in storm_buffer_res4_set)
    expected_res6_keys_s3 = sorted(
        c6 for p4, c6 in edges_res4_to_res6 if p4 in storm_buffer_res4_set
    )
    expected_res6_ids_s3 = sorted(res6_to_dense[c6] for c6 in expected_res6_keys_s3)
    res6_s3_set = set(expected_res6_keys_s3)

    expected_res8_keys_s3 = sorted(
        c8 for p6, c8 in edges_res6_to_res8 if p6 in res6_s3_set
    )
    expected_res8_ids_s3 = sorted(res8_to_dense[c8] for c8 in expected_res8_keys_s3)
    res8_s3_set = set(expected_res8_keys_s3)

    expected_impacted_truck_ids = sorted(
        trk_to_dense[trk_id] for c8, trk_id in edges_res8_has_truck if c8 in res8_s3_set
    )
    expected_impacted_shipment_ids = sorted(
        bol_to_dense[bol_id] for c8, bol_id in edges_res8_has_shipment if c8 in res8_s3_set
    )
    expected_impacted_landmark_ids = sorted(
        lm_to_dense[lm_id] for c8, lm_id in edges_res8_has_landmark if c8 in res8_s3_set
    )

    # Scenario 4: Connected Corridor Reachability Fixed-Point Traversal
    reachable_corridor_c6 = nx.node_connected_component(corridor_nx, origin_res6_key)
    expected_reachable_corridor_res6_ids = sorted(res6_to_dense[c6] for c6 in reachable_corridor_c6)

    relation_ids = {rel["name"]: idx for idx, rel in enumerate(manifest_full["relations"])}
    domain_ids = {dom["name"]: dom["id"] for dom in manifest_full["domains"]}

    ground_truth = {
        "domain_ids": domain_ids,
        "relation_ids": relation_ids,
        "scenario1_pd_dispatch": {
            "origin_res6_dense_id": origin_res6_dense_id,
            "origin_res6_key": origin_res6_key,
            "k_rings": k_rings,
            "required_service_class_ids": req_service_classes,
            "expected_k_ring_res6_ids": expected_k_ring_res6_ids,
            "expected_all_truck_ids_in_kring": expected_all_truck_ids_in_kring,
            "expected_qualified_truck_ids": expected_qualified_truck_ids,
            "expected_qualified_truck_keys": expected_qualified_truck_keys,
        },
        "scenario2_relay_yards": {
            "origin_terminal_dense_id": term_to_dense["DAL-01"],
            "origin_terminal_key": "DAL-01",
            "origin_res6_dense_id": res6_to_dense[dal_c6],
            "dest_terminal_dense_id": term_to_dense["MEM-01"],
            "dest_terminal_key": "MEM-01",
            "dest_res6_dense_id": res6_to_dense[mem_c6],
            "corridor_hops": corridor_hops,
            "expected_overlap_res6_ids": expected_overlap_res6_ids,
            "expected_relay_yard_ids": expected_relay_yard_ids,
            "expected_relay_yard_keys": expected_relay_yard_keys,
        },
        "scenario3_weather_drilldown": {
            "storm_res4_dense_ids": storm_res4_dense_ids,
            "storm_res4_keys": storm_res4_keys,
            "buffer_rings": 1,
            "expected_res4_zone_ids": expected_res4_zone_ids,
            "expected_res6_ids": expected_res6_ids_s3,
            "expected_res8_ids": expected_res8_ids_s3,
            "expected_impacted_truck_ids": expected_impacted_truck_ids,
            "expected_impacted_shipment_ids": expected_impacted_shipment_ids,
            "expected_impacted_landmark_ids": expected_impacted_landmark_ids,
        },
        "scenario4_corridor_reachability": {
            "start_res6_dense_id": origin_res6_dense_id,
            "start_res6_key": origin_res6_key,
            "expected_reachable_corridor_res6_ids": expected_reachable_corridor_res6_ids,
            "expected_reachable_count": len(expected_reachable_corridor_res6_ids),
        },
    }

    gt_path = DATASETS_DIR / "query_ground_truth.json"
    with open(gt_path, "w", encoding="utf-8") as f:
        json.dump(ground_truth, f, indent=2)

    # 13. Build & Validate both snapshots via official `impulse` CLI
    us_ltl_imps = DATASETS_DIR / "us_ltl_h3.imps"
    h3_only_imps = DATASETS_DIR / "h3_grid_only.imps"

    for m_path, out_imps in [(manifest_path, us_ltl_imps), (manifest_h3_path, h3_only_imps)]:
        subprocess.run(
            [str(IMPULSE_CLI), "build", "--manifest", str(m_path), "--output", str(out_imps)],
            check=True,
        )
        subprocess.run(
            [str(IMPULSE_CLI), "snapshot", "validate", "--strict-alignment", str(out_imps)],
            check=True,
        )

    wh_count = sum(1 for _, is_wh, _ in landmark_rows if is_wh == 1)
    ts_count = sum(1 for _, is_wh, _ in landmark_rows if is_wh == 0)

    print("\n=== Dataset & Snapshot Generation Summary ===")
    print(f"H3_Res4 nodes:       {len(res4_nodes):,}")
    print(f"H3_Res6 nodes:       {len(res6_nodes):,} (corridor: {len(corridor_res6_set):,})")
    print(f"H3_Res8 nodes:       {len(res8_nodes):,}")
    print(f"Terminal nodes:      {len(terminal_rows):,}")
    print(f"RelayYard nodes:     {len(relay_rows):,}")
    print(f"Truck nodes:         {len(truck_rows):,}")
    print(f"Shipment nodes:      {len(shipment_rows):,}")
    print(f"ServiceClass nodes:  {len(service_class_rows):,}")
    print(f"Landmark nodes:      {len(landmark_rows):,} ({wh_count} real Waffle Houses, {ts_count} real OPIS Truck Stops)")
    print(f"us_ltl_h3.imps:      {os.path.getsize(us_ltl_imps):,} bytes")
    print(f"h3_grid_only.imps:   {os.path.getsize(h3_only_imps):,} bytes")
    print(f"S1 qualified trucks: {len(expected_qualified_truck_ids)} (of {len(expected_all_truck_ids_in_kring)} in 3-ring)")
    print(f"S2 relay yards:      {len(expected_relay_yard_ids)} (in {len(expected_overlap_res6_ids)} overlap Res6 cells)")
    print(
        f"S3 storm impact:     {len(expected_res4_zone_ids)} Res4 -> {len(expected_res6_ids_s3)} Res6 -> "
        f"{len(expected_res8_ids_s3)} Res8 -> {len(expected_impacted_truck_ids)} trucks, "
        f"{len(expected_impacted_shipment_ids)} shipments, {len(expected_impacted_landmark_ids)} landmarks"
    )
    print(f"S4 reachable Res6:   {len(expected_reachable_corridor_res6_ids):,}")


if __name__ == "__main__":
    main()
