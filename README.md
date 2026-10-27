# Impulse Geoloc — Uber H3 Multi-Resolution LTL Freight Showcase

`impulse-geoloc` is a geolocation and transportation showcase built on the **Impulse Graph Engine** (`impulse-graph`) C-ABI Binary Snapshot Specification v0.9.0. It demonstrates how pre-compiling **Uber's H3 discrete global grid system** across multiple resolutions (`Res 4`, `Res 6`, `Res 8`) alongside Less-Than-Truckload (LTL) freight assets and real-world highway landmarks (**369 US Waffle House restaurants** and **117 OPIS commercial truck stops** falling inside our active Interstate corridors) converts runtime geospatial queries into pure CSR graph traversals (`ImpulseVM`) and off-heap bitset intersections.

---

## 1. Architectural Overview: Why Pre-Compile H3 into `.imps`?

Conventional geolocation pipelines evaluate spatial queries at runtime using trigonometric distance formulas (Haversine), R-tree bounding-box scans, or repeated `h3.grid_disk` / `h3.cell_to_children` allocations followed by relational joins against asset tables.

By compiling H3 cell hierarchies and hexagonal neighbor rings into an immutable `.imps` binary snapshot:
1. **Multi-Resolution Spatial Containment (`cellToChildren` / `cellToParent`)** becomes a 1-hop cross-domain CSR transition (`RES4_TO_RES6`, `RES6_TO_RES8`, `RES8_TO_RES6`).
2. **Radial Proximity Search (`gridDisk(cell, k)`)** becomes a $k$-iteration CSR loop (`repeat(k, s -> s.out("HEX_ADJACENT_RES6"))`) over reflexive 1-disk edges (`OP_LOOP_DECR` + `OP_CSR_WALK`).
3. **Highway-Constrained Isochrones** are represented directly as a routable subgraph (`HIGHWAY_CORRIDOR_RES6`) over `H3_Res6` cells along US Interstate corridors (`I-10`, `I-20`, `I-30`, `I-35`, `I-40`, `I-55`, `I-65`, `I-70`, `I-75`, `I-85`).
4. **Multi-Criteria Asset Filtering** (e.g., Hazmat-certified trucks with $\ge 4$ pallet spaces and $\ge 2$ hours of DOT Hours-of-Service, or FEMA "Waffle House Index" storm impact zones) executes via word-aligned off-heap bitset intersections (`OffHeapBitSet.and(...)`).

---

## 2. Multi-Domain Graph Schema & Empirical `.imps` Footprint

In accordance with **Per-Domain Dense ID Independence**, each node domain occupies its own 0-indexed dense ID space $0 \dots N_d - 1$:

| Domain ID | Domain Name | Cardinality | H3 Edge / Role | SoA Attributes |
| :---: | :--- | ---: | :--- | :--- |
| `0` | `H3_Res4` | `475` | $\sim 22.6\ \text{km}$ edge — Regional Weather & Tariff Zones | `lat: f32`, `lon: f32`, `waffle_index_status: i32` |
| `1` | `H3_Res6` | `3,982` | $\sim 3.23\ \text{km}$ edge — Metro P&D & Highway Exit Cells | `lat: f32`, `lon: f32`, `is_corridor: i32` |
| `2` | `H3_Res8` | `37,696` | $\sim 461\ \text{m}$ edge — Dock, Truck GPS & POI Cells | `lat: f32`, `lon: f32` |
| `3` | `Terminal` | `12` | LTL Breakbulk Hubs (`DAL-01`, `MEM-01`, `ATL-01`, `ORD-01`, etc.) | `dock_doors: i32`, `is_breakbulk: i32` |
| `4` | `RelayYard` | `54` | Interstate Meet-and-Turn Trailer Drop Yards | `lot_capacity: i32` |
| `5` | `Truck` | `1,500` | Active LTL Fleet Tractors (ELD/GPS Telemetry) | `avail_pallets: i32`, `hos_mins: i32`, `hazmat: i32`, `speed_mph: f32` |
| `6` | `Shipment` | `3,000` | LTL Bills of Lading (BOL) Awaiting Pickup/Delivery | `pallets: i32`, `weight_lbs: i32`, `hazmat: i32`, `priority: i32` |
| `7` | `ServiceClass` | `4` | Dispatch Qualifications (`CAP_HAZMAT`, `CAP_PALLET_4PLUS`, `CAP_HOS_2HR_PLUS`, `CAP_LIFTGATE`) | `code: i32` |
| `8` | `Landmark` | `486` | **369 Real US Waffle Houses** (`WH-xxxx`) + **117 OPIS Truck Stops** (`TS-OPIS-xxxx`) | `is_waffle_house: i32`, `waffle_status: i32` |
| `9` | `Corridor_Res6` | `3,982` | Highway-Constrained `H3_Res6` Routing Domain | — |

### Empirical `.imps` Snapshot Sizes (Spec v0.9.0)

| Snapshot File | Contents | Total Nodes | Total Edges | File Size (Bytes) | Per-H3-Cell Cost |
| :--- | :--- | ---: | ---: | ---: | ---: |
| `datasets/h3_grid_only.imps` | **Pure H3 Grid Only** (`H3_Res4`, `H3_Res6`, `H3_Res8` + 7 hierarchy/1-ring relations + string hash indexes + `lat`/`lon`) | `42,153` | `301,609` | **`3,117,076` B (`2.97 MiB`)** | **`73.95 B / cell`** |
| `datasets/us_ltl_h3.imps` | **Full LTL + H3 + Real Landmarks** (10 domains, 22 relations, SoA attributes) | `51,191` | `322,349` | **`4,087,828` B (`3.90 MiB`)** | — |

---

## 3. Showcase Geolocation Queries (`LtlGeolocEngine.java`)

All 4 scenarios are implemented in [`LtlGeolocEngine`](src/main/java/org/impulsegraph/geoloc/LtlGeolocEngine.java) using the Java 25 `DomainView` and `Traversal` APIs:

1. **Scenario 1 — Dynamic P&D Pickup Dispatch via $k$-Ring Expansion (`H3_Res6` $\to$ `Truck` $\cap$ `ServiceClass`)**:
   - Expands $k = 3$ hexagonal rings (`37` `H3_Res6` cells) around `DAL-01` via `.repeat(3, s -> s.out("HEX_ADJACENT_RES6")).out("RES6_HAS_TRUCK").toBitSet()` (`97` trucks) and intersects with `CAP_HAZMAT`, `CAP_PALLET_4PLUS`, and `CAP_HOS_2HR_PLUS` bitsets to isolate **`27` qualified trucks**.
2. **Scenario 2 — Linehaul Meet-and-Turn Relay Isochrone Intersection (`DAL-01` $\leftrightarrow$ `MEM-01`)**:
   - Propagates `62`-hop highway corridor frontiers along `HIGHWAY_CORRIDOR_RES6` from Dallas (`DAL-01`) and Memphis (`MEM-01`), intersects the two `ImpulseBitSet` frontiers (`7` overlapping `H3_Res6` cells near Little Rock along I-30/I-40), and projects via `.out("RES6_HAS_RELAY_YARD")` to **`3` Meet-and-Turn Relay Yards**.
3. **Scenario 3 — Multi-Resolution Weather & FEMA "Waffle House Index" Drilldown (`Res 4` $\to$ `Res 6` $\to$ `Res 8` $\to$ Assets)**:
   - Expands 3 seed `H3_Res4` storm cells by a 1-ring buffer (`21` `H3_Res4` cells), drills down `.out("RES4_TO_RES6")` (`358` cells) $\to$ `.out("RES6_TO_RES8")` (`4,938` cells), and resolves **`327` impacted trucks**, **`649` shipments**, and **`84` real Waffle Houses & commercial truck stops** inside the storm cone.
4. **Scenario 4 — Transitive Highway Corridor Connectivity (`repeatUntilStable`)**:
   - Executes delta-frontier fixed-point reachability (`.repeatUntilStable(s -> s.out("HIGHWAY_CORRIDOR_RES6"))`) across the Interstate corridor network (`OP_STABLE_CHECK` + `OP_SET_DIFFERENCE` + `OP_SET_UNION`), verifying connectivity across all **`1,265` corridor `H3_Res6` cells**.

---

## 4. Empirical Benchmark & Multi-Scale Fleet Verification Results

Measured on local hardware via [`scripts/verify_and_benchmark.py`](scripts/verify_and_benchmark.py) (200 iterations for Python `h3-py` `4.5.0` + `NetworkX` `3.6.1` vs 2,000 warmup + 2,000 measured iterations for Java 25 `ImpulseVM`) across three fleet scales generated via `scripts/generate_h3_ltl_dataset.py --trucks <N> --shipments <M>`:

### 4.1 Baseline Regional Fleet (`1,500` Trucks / `3,000` Shipments — `3.90 MiB` `.imps`)

| Scenario | Python (`h3-py` + `NetworkX`) Avg ($\mu\text{s}$) | Java 25 (`ImpulseVM`) Avg ($\mu\text{s}$) | Java 25 $P_{50}$ ($\mu\text{s}$) | Java 25 $P_{99}$ ($\mu\text{s}$) | Java 25 Throughput (QPS) | Relative Speedup | Result Parity |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1. Dynamic P&D Dispatch ($k=3$ ring)** | $32.47\ \mu\text{s}$ | **$6.88\ \mu\text{s}$** | **$5.83\ \mu\text{s}$** | $23.29\ \mu\text{s}$ | $145,369\ \text{q/s}$ | **$4.7\times$** | **100.00% Match (`27` trucks)** |
| **2. Linehaul Relay Isochrone (`62` hops)** | $152.60\ \mu\text{s}$ | **$47.53\ \mu\text{s}$** | **$40.79\ \mu\text{s}$** | $191.17\ \mu\text{s}$ | $21,041\ \text{q/s}$ | **$3.2\times$** | **100.00% Match (`3` yards)** |
| **3. Multi-Res Weather & WHI Drilldown** | $8,727.20\ \mu\text{s}$ | **$263.88\ \mu\text{s}$** | **$255.71\ \mu\text{s}$** | $420.17\ \mu\text{s}$ | $3,790\ \text{q/s}$ | **$33.1\times$** | **100.00% Match (`327` trk / `649` bol / `84` poi)** |
| **4. Corridor Transitive Closure** | $294.40\ \mu\text{s}$ | **$74.20\ \mu\text{s}$** | **$72.50\ \mu\text{s}$** | $96.75\ \mu\text{s}$ | $13,478\ \text{q/s}$ | **$4.0\times$** | **100.00% Match (`1,265` cells)** |

### 4.2 Enterprise Scaling: `1.5k` $\to$ `20k` $\to$ `50k` Trucks (`100k` Shipments)

| Metric / Scenario | `1.5k` Trucks / `3k` BOLs | `20k` Trucks / `40k` BOLs | `50k` Trucks / `100k` BOLs |
| :--- | :--- | :--- | :--- |
| **`.imps` Snapshot Size** | `4,087,828 B` (`3.90 MiB`) | `7,127,060 B` (`6.80 MiB`) | **`12,140,564 B` (`11.58 MiB`)** |
| **Cold-Start Load (Python vs `.imps` mmap)** | `27.90 ms` vs `29.32 ms` (`1.0x`) | `102.53 ms` vs `28.20 ms` (**`3.6x` faster**) | `249.13 ms` vs `28.42 ms` (**`8.8x` faster**) |
| **1. Dynamic P&D Dispatch ($k=3$ + 3 Caps)** | Py `32.47 µs` vs Java **`6.88 µs`** (`p50: 5.83 µs`, **`4.7x` faster**, `27` trucks) | Py `173.03 µs` vs Java **`7.90 µs`** (`p50: 7.00 µs`, **`21.9x` faster**, `278` trucks) | Py `524.97 µs` vs Java **`13.56 µs`** (`p50: 11.50 µs`, **`38.7x` faster**, `694` trucks) |
| **2. Linehaul Relay Isochrone (`62` hops)** | Py `152.60 µs` vs Java **`47.53 µs`** (`p50: 40.79 µs`, **`3.2x` faster**) | Py `147.35 µs` vs Java **`33.85 µs`** (`p50: 30.33 µs`, **`4.4x` faster**) | Py `149.35 µs` vs Java **`42.85 µs`** (`p50: 35.67 µs`, **`3.5x` faster**, $O(1)$ in fleet size) |
| **3. Multi-Res Weather Drilldown (`Res4->6->8->POI`)** | Py `8,727.20 µs` vs Java **`263.88 µs`** (`p50: 255.71 µs`, **`33.1x` faster**) | Py `10,701.47 µs` vs Java **`296.99 µs`** (`p50: 291.75 µs`, **`36.0x` faster**) | Py `12,939.75 µs` vs Java **`305.96 µs`** (`p50: 292.04 µs`, **`42.3x` faster**, `10,092` trk / `20,222` bol) |
| **4. Corridor Transitive Closure (Fixed-Point)** | Py `294.40 µs` vs Java **`74.20 µs`** (`p50: 72.50 µs`, **`4.0x` faster**) | Py `293.34 µs` vs Java **`73.72 µs`** (`p50: 72.83 µs`, **`4.0x` faster**) | Py `286.53 µs` vs Java **`75.37 µs`** (`p50: 72.67 µs`, **`3.8x` faster**, `1,265` cells) |

---

## 5. Building & Running the Showcase

### Step 1: Generate H3 + LTL Dataset & Compile `.imps` Snapshots
Uses `h3-py` to generate TSV tables and `manifest.json`, and invokes `impulse build` and `impulse snapshot validate` from `impulse-graph-tooling`:

```bash
/Users/jesse/impulse/venv/bin/python3 scripts/generate_h3_ltl_dataset.py
```

### Step 2: Inspect & Validate `.imps` Snapshots via CLI
```bash
/Users/jesse/impulse/impulse-graph-tooling/target/release/impulse inspect datasets/us_ltl_h3.imps
/Users/jesse/impulse/impulse-graph-tooling/target/release/impulse snapshot validate --strict-alignment datasets/us_ltl_h3.imps
/Users/jesse/impulse/impulse-graph-tooling/target/release/impulse stats datasets/us_ltl_h3.imps
```

### Step 3: Run Java 25 JUnit 5 Verification Suite
```bash
mvn test
```

### Step 4: Run Showcase Disassembly & Empirical Benchmark Suite
```bash
mvn exec:java
/Users/jesse/impulse/venv/bin/python3 scripts/verify_and_benchmark.py
```

---

## 6. Repository Sitemap

```
impulse-geoloc/
├── scripts/
│   ├── generate_h3_ltl_dataset.py       # H3 multi-res + Waffle House/Truck Stop dataset builder (via impulse build)
│   └── verify_and_benchmark.py          # 3rd-party h3-py / NetworkX parity verifier & benchmark harness
├── src/
│   ├── main/java/org/impulsegraph/geoloc/
│   │   ├── LtlGeolocEngine.java         # Fluent DomainView / Traversal Kleisli geolocation pipelines
│   │   └── LtlGeolocShowcaseRunner.java # CLI showcase, ImpAsm disassembler & microbenchmark runner
│   └── test/java/org/impulsegraph/geoloc/
│       └── LtlGeolocEngineTest.java     # JUnit 5 ground-truth parity verification suite
├── datasets/                            # Local generated TSVs, raw POI CSVs & .imps snapshots (gitignored)
├── pom.xml                              # Java 25 Maven configuration
└── README.md
```
