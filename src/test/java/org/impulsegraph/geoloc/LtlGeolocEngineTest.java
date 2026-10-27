package org.impulsegraph.geoloc;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.impulsegraph.api.bitset.ImpulseBitSet;
import org.impulsegraph.storage.csr.BinarySnapshotLoader;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.lang.foreign.Arena;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * JUnit 5 verification suite validating 100% set identity between the Java 25
 * {@code ImpulseVM} geolocation engine ({@link LtlGeolocEngine}) and the
 * Python {@code h3-py} / {@code networkx} ground-truth oracle
 * ({@code datasets/query_ground_truth.json}).
 */
class LtlGeolocEngineTest {

    private static Arena sharedArena;
    private static BinarySnapshotLoader.LoadedSnapshot loadedSnapshot;
    private static LtlGeolocEngine engine;
    private static JsonObject groundTruth;

    @BeforeAll
    static void setUpAll() throws Exception {
        Path snapshotPath = Paths.get("datasets/us_ltl_h3.imps");
        Path groundTruthPath = Paths.get("datasets/query_ground_truth.json");

        assertTrue(Files.exists(snapshotPath),
                "Snapshot file missing at " + snapshotPath.toAbsolutePath()
                        + "; run scripts/generate_h3_ltl_dataset.py first.");
        assertTrue(Files.exists(groundTruthPath),
                "Ground truth file missing at " + groundTruthPath.toAbsolutePath()
                        + "; run scripts/generate_h3_ltl_dataset.py first.");

        groundTruth = JsonParser.parseString(Files.readString(groundTruthPath, StandardCharsets.UTF_8))
                .getAsJsonObject();

        sharedArena = Arena.ofShared();
        loadedSnapshot = BinarySnapshotLoader.loadSnapshot(snapshotPath, sharedArena);
        engine = new LtlGeolocEngine(loadedSnapshot.getGraph());
    }

    @AfterAll
    static void tearDownAll() {
        if (sharedArena != null) {
            sharedArena.close();
        }
    }

    @Test
    @DisplayName("Scenario 1: Dynamic P&D Pickup Dispatch via H3 k-Ring Expansion & Capability Bitset Intersection")
    void testScenario1PdDispatchKRing() {
        JsonObject s1 = groundTruth.getAsJsonObject("scenario1_pd_dispatch");
        long originRes6Id = s1.get("origin_res6_dense_id").getAsLong();
        int kRings = s1.get("k_rings").getAsInt();
        long[] serviceClassIds = toLongArray(s1.getAsJsonArray("required_service_class_ids"));

        List<Long> expectedKRingRes6Ids = toLongList(s1.getAsJsonArray("expected_k_ring_res6_ids"));
        List<Long> expectedAllTruckIds = toLongList(s1.getAsJsonArray("expected_all_truck_ids_in_kring"));
        List<Long> expectedQualifiedTruckIds = toLongList(s1.getAsJsonArray("expected_qualified_truck_ids"));

        LtlGeolocEngine.PdDispatchResult result = engine.findDispatchableTrucksInKRing(
                originRes6Id, kRings, serviceClassIds);

        assertEquals(expectedKRingRes6Ids, LtlGeolocEngine.toSortedIdList(result.kRingRes6Cells()),
                "H3_Res6 k-ring expansion mismatch against h3.grid_disk oracle");
        assertEquals(expectedAllTruckIds, LtlGeolocEngine.toSortedIdList(result.allTrucksInKRing()),
                "Candidate trucks in k-ring mismatch");
        assertEquals(expectedQualifiedTruckIds, LtlGeolocEngine.toSortedIdList(result.qualifiedTrucks()),
                "Qualified trucks after ServiceClass SIMD bitset intersection mismatch");
    }

    @Test
    @DisplayName("Scenario 2: Linehaul Meet-and-Turn Relay Yard Corridor Isochrone Intersection")
    void testScenario2MeetAndTurnRelayYards() {
        JsonObject s2 = groundTruth.getAsJsonObject("scenario2_relay_yards");
        long originRes6Id = s2.get("origin_res6_dense_id").getAsLong();
        long destRes6Id = s2.get("dest_res6_dense_id").getAsLong();
        int corridorHops = s2.get("corridor_hops").getAsInt();

        List<Long> expectedOverlapRes6Ids = toLongList(s2.getAsJsonArray("expected_overlap_res6_ids"));
        List<Long> expectedRelayYardIds = toLongList(s2.getAsJsonArray("expected_relay_yard_ids"));

        LtlGeolocEngine.RelayMeetResult result = engine.findMeetAndTurnRelayYards(
                originRes6Id, destRes6Id, corridorHops);

        assertEquals(expectedOverlapRes6Ids, LtlGeolocEngine.toSortedIdList(result.overlappingCorridorCells()),
                "Overlapping H3_Res6 corridor isochrone cells mismatch");
        assertEquals(expectedRelayYardIds, LtlGeolocEngine.toSortedIdList(result.relayYards()),
                "Eligible Meet-and-Turn RelayYard IDs mismatch");
    }

    @Test
    @DisplayName("Scenario 3: Multi-Resolution Severe Weather Geofence Drilldown (Res4 -> Res6 -> Res8 -> Assets)")
    void testScenario3WeatherHazardMultiResolutionDrilldown() {
        JsonObject s3 = groundTruth.getAsJsonObject("scenario3_weather_drilldown");
        long[] stormRes4Ids = toLongArray(s3.getAsJsonArray("storm_res4_dense_ids"));
        int bufferRings = s3.get("buffer_rings").getAsInt();

        List<Long> expectedRes4Ids = toLongList(s3.getAsJsonArray("expected_res4_zone_ids"));
        List<Long> expectedRes6Ids = toLongList(s3.getAsJsonArray("expected_res6_ids"));
        List<Long> expectedRes8Ids = toLongList(s3.getAsJsonArray("expected_res8_ids"));
        List<Long> expectedTruckIds = toLongList(s3.getAsJsonArray("expected_impacted_truck_ids"));
        List<Long> expectedShipmentIds = toLongList(s3.getAsJsonArray("expected_impacted_shipment_ids"));
        List<Long> expectedLandmarkIds = toLongList(s3.getAsJsonArray("expected_impacted_landmark_ids"));

        LtlGeolocEngine.WeatherImpactResult result = engine.drilldownWeatherHazardImpact(
                stormRes4Ids, bufferRings);

        assertEquals(expectedRes4Ids, LtlGeolocEngine.toSortedIdList(result.impactedRes4()),
                "Impacted H3_Res4 weather zone cells mismatch");
        assertEquals(expectedRes6Ids, LtlGeolocEngine.toSortedIdList(result.impactedRes6()),
                "Impacted H3_Res6 cells mismatch");
        assertEquals(expectedRes8Ids, LtlGeolocEngine.toSortedIdList(result.impactedRes8()),
                "Impacted H3_Res8 cells mismatch");
        assertEquals(expectedTruckIds, LtlGeolocEngine.toSortedIdList(result.impactedTrucks()),
                "Impacted Truck IDs mismatch");
        assertEquals(expectedShipmentIds, LtlGeolocEngine.toSortedIdList(result.impactedShipments()),
                "Impacted Shipment IDs mismatch");
        assertEquals(expectedLandmarkIds, LtlGeolocEngine.toSortedIdList(result.impactedLandmarks()),
                "Impacted Landmark (Waffle House / Travel Plaza) IDs mismatch");
    }

    @Test
    @DisplayName("Scenario 4: Transitive Highway Corridor Network Reachability (Fixed-Point Loop)")
    void testScenario4TransitiveCorridorReachability() {
        JsonObject s4 = groundTruth.getAsJsonObject("scenario4_corridor_reachability");
        long startRes6Id = s4.get("start_res6_dense_id").getAsLong();
        List<Long> expectedReachableIds = toLongList(s4.getAsJsonArray("expected_reachable_corridor_res6_ids"));
        long expectedCount = s4.get("expected_reachable_count").getAsLong();

        ImpulseBitSet reachable = engine.findReachableCorridorNetwork(startRes6Id);

        assertEquals(expectedCount, reachable.cardinality(),
                "Reachable H3_Res6 corridor cell count mismatch");
        assertEquals(expectedReachableIds, LtlGeolocEngine.toSortedIdList(reachable),
                "Reachable H3_Res6 corridor cell IDs mismatch");
    }

    @Test
    @DisplayName("Verify ImpAsm Bytecode Disassembly Generation across all 4 Geolocation Pipelines")
    void testImpAsmDisassemblyGeneration() {
        String asm1 = engine.disassembleScenario1KRingDispatch(0L, 3);
        assertNotNull(asm1);
        assertTrue(asm1.contains("OP_CSR_WALK"), "Scenario 1 ImpAsm should contain OP_CSR_WALK");
        assertTrue(asm1.contains("OP_LOOP_DECR"), "Scenario 1 ImpAsm should contain OP_LOOP_DECR");

        String asm2 = engine.disassembleScenario2CorridorIsochrone(0L, 6);
        assertNotNull(asm2);
        assertTrue(asm2.contains("OP_CSR_WALK"), "Scenario 2 ImpAsm should contain OP_CSR_WALK");

        ImpulseBitSet seedSet = engine.graph().domain(LtlGeolocEngine.DOMAIN_H3_RES4).from(0L).toBitSet();
        String asm3 = engine.disassembleScenario3WeatherDrilldownToTrucks(seedSet);
        assertNotNull(asm3);
        assertTrue(asm3.contains("OP_CSR_WALK"), "Scenario 3 ImpAsm should contain OP_CSR_WALK");

        String asm4 = engine.disassembleScenario4CorridorReachability(0L);
        assertNotNull(asm4);
        assertTrue(asm4.contains("OP_STABLE_CHECK"), "Scenario 4 ImpAsm should contain OP_STABLE_CHECK");
    }

    private static long[] toLongArray(JsonArray array) {
        long[] out = new long[array.size()];
        for (int i = 0; i < array.size(); i++) {
            out[i] = array.get(i).getAsLong();
        }
        return out;
    }

    private static List<Long> toLongList(JsonArray array) {
        List<Long> out = new ArrayList<>(array.size());
        for (JsonElement el : array) {
            out.add(el.getAsLong());
        }
        return out;
    }
}
