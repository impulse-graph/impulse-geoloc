package org.impulsegraph.geoloc;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.impulsegraph.api.ImpulseGraphSnapshot;
import org.impulsegraph.api.bitset.ImpulseBitSet;
import org.impulsegraph.storage.csr.BinarySnapshotLoader;

import java.lang.foreign.Arena;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * CLI Showcase & Microbenchmark Runner for the H3 Multi-Resolution LTL Freight
 * Geolocation Engine ({@code impulse-geoloc}).
 */
public final class LtlGeolocShowcaseRunner {

    private static final int WARMUP_ITERATIONS = 2_000;
    private static final int BENCHMARK_ITERATIONS = 2_000;

    private LtlGeolocShowcaseRunner() {
    }

    public static void main(String[] args) throws Exception {
        Path snapshotPath = Paths.get("datasets/us_ltl_h3.imps");
        Path groundTruthPath = Paths.get("datasets/query_ground_truth.json");
        Path jsonOutputPath = Paths.get("datasets/java_benchmark_results.json");

        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--snapshot" -> {
                    if (i + 1 < args.length) {
                        snapshotPath = Paths.get(args[++i]);
                    }
                }
                case "--ground-truth" -> {
                    if (i + 1 < args.length) {
                        groundTruthPath = Paths.get(args[++i]);
                    }
                }
                case "--json" -> {
                    if (i + 1 < args.length) {
                        jsonOutputPath = Paths.get(args[++i]);
                    }
                }
                default -> {
                    if (!args[i].startsWith("--") && i == 0) {
                        snapshotPath = Paths.get(args[i]);
                    }
                }
            }
        }

        if (!Files.exists(snapshotPath)) {
            System.err.println("Error: Snapshot file not found at " + snapshotPath.toAbsolutePath());
            System.exit(1);
        }
        if (!Files.exists(groundTruthPath)) {
            System.err.println("Error: Ground truth file not found at " + groundTruthPath.toAbsolutePath());
            System.exit(1);
        }

        JsonObject gtRoot = JsonParser.parseString(Files.readString(groundTruthPath, StandardCharsets.UTF_8))
                .getAsJsonObject();

        try (Arena arena = Arena.ofShared()) {
            long loadStartNs = System.nanoTime();
            BinarySnapshotLoader.LoadedSnapshot loadedSnapshot = BinarySnapshotLoader.loadSnapshot(snapshotPath, arena);
            long loadElapsedNs = System.nanoTime() - loadStartNs;
            double coldStartUs = loadElapsedNs / 1_000.0;

            ImpulseGraphSnapshot graph = loadedSnapshot.getGraph();
            LtlGeolocEngine engine = new LtlGeolocEngine(graph);

            System.out.println("=========================================================================");
            System.out.println("  IMPULSE GRAPH ENGINE (v0.9.0) — H3 LTL FREIGHT GEOLOCATION SHOWCASE    ");
            System.out.println("=========================================================================");
            System.out.printf("Snapshot File       : %s (%d bytes)%n",
                    snapshotPath, Files.size(snapshotPath));
            System.out.printf("Magic / Spec Ver    : 0x%X / v0.%d%n",
                    loadedSnapshot.magic(), loadedSnapshot.version());
            System.out.printf("SHA-256 Checksum    : %s%n", loadedSnapshot.getSha256Checksum());
            System.out.printf("Cold-Start mmap     : %.2f us%n", coldStartUs);
            System.out.printf("Relation Count      : %d%n", graph.getRelationCount());
            System.out.println("-------------------------------------------------------------------------");

            // Extract Scenario 1 parameters
            JsonObject s1 = gtRoot.getAsJsonObject("scenario1_pd_dispatch");
            long s1OriginRes6 = s1.get("origin_res6_dense_id").getAsLong();
            String s1OriginKey = s1.has("origin_res6_key") ? s1.get("origin_res6_key").getAsString() : String.valueOf(s1OriginRes6);
            int s1KRings = s1.get("k_rings").getAsInt();
            long[] s1ServiceClasses = toLongArray(s1.getAsJsonArray("required_service_class_ids"));

            LtlGeolocEngine.PdDispatchResult r1 = engine.findDispatchableTrucksInKRing(
                    s1OriginRes6, s1KRings, s1ServiceClasses);
            List<Long> r1KRingIds = LtlGeolocEngine.toSortedIdList(r1.kRingRes6Cells());
            List<Long> r1AllTruckIds = LtlGeolocEngine.toSortedIdList(r1.allTrucksInKRing());
            List<Long> r1QualTruckIds = LtlGeolocEngine.toSortedIdList(r1.qualifiedTrucks());

            System.out.printf("[Scenario 1] P&D Dispatch k-Ring (origin=%s [id=%d], k=%d, caps=%s)%n",
                    s1OriginKey, s1OriginRes6, s1KRings, Arrays.toString(s1ServiceClasses));
            System.out.printf("  -> H3_Res6 cells in %d-ring : %d%n", s1KRings, r1KRingIds.size());
            System.out.printf("  -> Candidate trucks in ring : %d%n", r1AllTruckIds.size());
            System.out.printf("  -> Qualified trucks matched : %d %s%n", r1QualTruckIds.size(), r1QualTruckIds);
            System.out.println(engine.disassembleScenario1KRingDispatch(s1OriginRes6, s1KRings));

            // Extract Scenario 2 parameters
            JsonObject s2 = gtRoot.getAsJsonObject("scenario2_relay_yards");
            long s2OriginRes6 = s2.get("origin_res6_dense_id").getAsLong();
            long s2DestRes6 = s2.get("dest_res6_dense_id").getAsLong();
            String s2OriginKey = s2.has("origin_terminal_key") ? s2.get("origin_terminal_key").getAsString() : String.valueOf(s2OriginRes6);
            String s2DestKey = s2.has("dest_terminal_key") ? s2.get("dest_terminal_key").getAsString() : String.valueOf(s2DestRes6);
            int s2Hops = s2.get("corridor_hops").getAsInt();

            LtlGeolocEngine.RelayMeetResult r2 = engine.findMeetAndTurnRelayYards(s2OriginRes6, s2DestRes6, s2Hops);
            List<Long> r2OverlapRes6Ids = LtlGeolocEngine.toSortedIdList(r2.overlappingCorridorCells());
            List<Long> r2RelayYardIds = LtlGeolocEngine.toSortedIdList(r2.relayYards());

            System.out.printf("[Scenario 2] Linehaul Meet-and-Turn Relay (%s [res6=%d] <-> %s [res6=%d], hops=%d)%n",
                    s2OriginKey, s2OriginRes6, s2DestKey, s2DestRes6, s2Hops);
            System.out.printf("  -> Overlapping H3_Res6 corridor cells : %d%n", r2OverlapRes6Ids.size());
            System.out.printf("  -> Eligible RelayYard IDs             : %d %s%n", r2RelayYardIds.size(), r2RelayYardIds);
            System.out.println(engine.disassembleScenario2CorridorIsochrone(s2OriginRes6, s2Hops));

            // Extract Scenario 3 parameters
            JsonObject s3 = gtRoot.getAsJsonObject("scenario3_weather_drilldown");
            long[] s3StormRes4Ids = toLongArray(s3.getAsJsonArray("storm_res4_dense_ids"));
            int s3BufferRings = s3.get("buffer_rings").getAsInt();

            LtlGeolocEngine.WeatherImpactResult r3 = engine.drilldownWeatherHazardImpact(s3StormRes4Ids, s3BufferRings);
            List<Long> r3Res4Ids = LtlGeolocEngine.toSortedIdList(r3.impactedRes4());
            List<Long> r3Res6Ids = LtlGeolocEngine.toSortedIdList(r3.impactedRes6());
            List<Long> r3Res8Ids = LtlGeolocEngine.toSortedIdList(r3.impactedRes8());
            List<Long> r3TruckIds = LtlGeolocEngine.toSortedIdList(r3.impactedTrucks());
            List<Long> r3ShipmentIds = LtlGeolocEngine.toSortedIdList(r3.impactedShipments());
            List<Long> r3LandmarkIds = LtlGeolocEngine.toSortedIdList(r3.impactedLandmarks());

            System.out.printf("[Scenario 3] Weather Hazard Multi-Resolution Drilldown (seeds=%s, bufferRings=%d)%n",
                    Arrays.toString(s3StormRes4Ids), s3BufferRings);
            System.out.printf("  -> Impacted H3_Res4 cells : %d%n", r3Res4Ids.size());
            System.out.printf("  -> Impacted H3_Res6 cells : %d%n", r3Res6Ids.size());
            System.out.printf("  -> Impacted H3_Res8 cells : %d%n", r3Res8Ids.size());
            System.out.printf("  -> Impacted Trucks        : %d%n", r3TruckIds.size());
            System.out.printf("  -> Impacted Shipments     : %d%n", r3ShipmentIds.size());
            System.out.printf("  -> Impacted Landmarks     : %d%n", r3LandmarkIds.size());
            System.out.println(engine.disassembleScenario3WeatherDrilldownToTrucks(r3.impactedRes4()));

            // Extract Scenario 4 parameters
            JsonObject s4 = gtRoot.getAsJsonObject("scenario4_corridor_reachability");
            long s4StartRes6 = s4.get("start_res6_dense_id").getAsLong();
            String s4StartKey = s4.has("start_res6_key") ? s4.get("start_res6_key").getAsString() : String.valueOf(s4StartRes6);

            ImpulseBitSet r4BitSet = engine.findReachableCorridorNetwork(s4StartRes6);
            List<Long> r4ReachableIds = LtlGeolocEngine.toSortedIdList(r4BitSet);

            System.out.printf("[Scenario 4] Transitive Highway Corridor Reachability (start=%s [id=%d])%n",
                    s4StartKey, s4StartRes6);
            System.out.printf("  -> Connected H3_Res6 corridor cells : %d%n", r4ReachableIds.size());
            System.out.println(engine.disassembleScenario4CorridorReachability(s4StartRes6));

            // Warmup & Benchmark (500 warmup iterations, 2,000 measured iterations)
            System.out.printf("Running JIT warmup (%d iterations) and latency benchmark (%d iterations)...%n",
                    WARMUP_ITERATIONS, BENCHMARK_ITERATIONS);

            BenchStats b1 = benchmarkScenario(
                    () -> engine.findQualifiedTrucksInKRing(s1OriginRes6, s1KRings, s1ServiceClasses));
            BenchStats b2 = benchmarkScenario(
                    () -> engine.findMeetAndTurnRelayYards(s2OriginRes6, s2DestRes6, s2Hops));
            BenchStats b3 = benchmarkScenario(
                    () -> engine.drilldownWeatherHazardImpact(s3StormRes4Ids, s3BufferRings));
            BenchStats b4 = benchmarkScenario(
                    () -> engine.findReachableCorridorNetwork(s4StartRes6));

            System.out.println("-------------------------------------------------------------------------");
            System.out.printf("%-42s | %10s | %10s | %10s | %12s%n",
                    "Scenario", "Avg (us)", "p50 (us)", "p99 (us)", "Throughput");
            System.out.println("-------------------------------------------------------------------------");
            printBenchRow("1. P&D k-Ring Truck Dispatch", b1);
            printBenchRow("2. Meet-and-Turn Corridor Isochrone", b2);
            printBenchRow("3. Multi-Res Weather Hazard Drilldown", b3);
            printBenchRow("4. Fixed-Point Corridor Reachability", b4);
            System.out.println("=========================================================================");

            if (jsonOutputPath != null) {
                Map<String, Object> out = new LinkedHashMap<>();
                out.put("snapshot_path", snapshotPath.toString());
                out.put("snapshot_size_bytes", Files.size(snapshotPath));
                out.put("cold_start_load_us", coldStartUs);
                out.put("sha256", loadedSnapshot.getSha256Checksum());
                out.put("warmup_iterations", WARMUP_ITERATIONS);
                out.put("benchmark_iterations", BENCHMARK_ITERATIONS);

                Map<String, Object> m1 = new LinkedHashMap<>();
                m1.put("avg_latency_us", b1.avgUs());
                m1.put("p50_latency_us", b1.p50Us());
                m1.put("p99_latency_us", b1.p99Us());
                m1.put("qps", b1.qps());
                m1.put("k_ring_res6_ids", r1KRingIds);
                m1.put("all_truck_ids_in_kring", r1AllTruckIds);
                m1.put("qualified_truck_ids", r1QualTruckIds);
                out.put("scenario1_pd_dispatch", m1);

                Map<String, Object> m2 = new LinkedHashMap<>();
                m2.put("avg_latency_us", b2.avgUs());
                m2.put("p50_latency_us", b2.p50Us());
                m2.put("p99_latency_us", b2.p99Us());
                m2.put("qps", b2.qps());
                m2.put("overlap_res6_ids", r2OverlapRes6Ids);
                m2.put("relay_yard_ids", r2RelayYardIds);
                out.put("scenario2_relay_yards", m2);

                Map<String, Object> m3 = new LinkedHashMap<>();
                m3.put("avg_latency_us", b3.avgUs());
                m3.put("p50_latency_us", b3.p50Us());
                m3.put("p99_latency_us", b3.p99Us());
                m3.put("qps", b3.qps());
                m3.put("res4_zone_ids", r3Res4Ids);
                m3.put("res6_ids", r3Res6Ids);
                m3.put("res8_ids", r3Res8Ids);
                m3.put("impacted_truck_ids", r3TruckIds);
                m3.put("impacted_shipment_ids", r3ShipmentIds);
                m3.put("impacted_landmark_ids", r3LandmarkIds);
                out.put("scenario3_weather_drilldown", m3);

                Map<String, Object> m4 = new LinkedHashMap<>();
                m4.put("avg_latency_us", b4.avgUs());
                m4.put("p50_latency_us", b4.p50Us());
                m4.put("p99_latency_us", b4.p99Us());
                m4.put("qps", b4.qps());
                m4.put("reachable_corridor_res6_ids", r4ReachableIds);
                m4.put("reachable_count", r4ReachableIds.size());
                out.put("scenario4_corridor_reachability", m4);

                Gson gson = new GsonBuilder().setPrettyPrinting().create();
                if (jsonOutputPath.getParent() != null) {
                    Files.createDirectories(jsonOutputPath.getParent());
                }
                Files.writeString(jsonOutputPath, gson.toJson(out), StandardCharsets.UTF_8);
                System.out.println("Benchmark JSON written to: " + jsonOutputPath.toAbsolutePath());
            }
        }
    }

    private record BenchStats(double avgUs, double p50Us, double p99Us, double qps) {
    }

    private static BenchStats benchmarkScenario(Runnable task) {
        for (int i = 0; i < WARMUP_ITERATIONS; i++) {
            task.run();
        }
        long[] samplesNs = new long[BENCHMARK_ITERATIONS];
        long totalNs = 0L;
        for (int i = 0; i < BENCHMARK_ITERATIONS; i++) {
            long t0 = System.nanoTime();
            task.run();
            long dt = System.nanoTime() - t0;
            samplesNs[i] = dt;
            totalNs += dt;
        }
        Arrays.sort(samplesNs);
        double avgUs = (totalNs / (double) BENCHMARK_ITERATIONS) / 1_000.0;
        double p50Us = samplesNs[BENCHMARK_ITERATIONS / 2] / 1_000.0;
        double p99Us = samplesNs[(int) (BENCHMARK_ITERATIONS * 0.99)] / 1_000.0;
        double qps = 1_000_000.0 / avgUs;
        return new BenchStats(avgUs, p50Us, p99Us, qps);
    }

    private static void printBenchRow(String label, BenchStats stats) {
        System.out.printf("%-42s | %10.2f | %10.2f | %10.2f | %10.0f/s%n",
                label, stats.avgUs(), stats.p50Us(), stats.p99Us(), stats.qps());
    }

    private static long[] toLongArray(JsonArray array) {
        long[] out = new long[array.size()];
        for (int i = 0; i < array.size(); i++) {
            JsonElement el = array.get(i);
            out[i] = el.getAsLong();
        }
        return out;
    }
}
