package org.impulsegraph.geoloc;

import org.impulsegraph.api.ArgType;
import org.impulsegraph.api.ImpulseGraphQuery;
import org.impulsegraph.api.ImpulseGraphSnapshot;
import org.impulsegraph.api.ImpulseQueryBuilder;
import org.impulsegraph.api.bitset.ImpulseBitSet;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Engine-native Kleisli frontier geolocation query pipelines over an H3
 * multi-resolution LTL freight network {@link ImpulseGraphSnapshot}.
 *
 * <p>All spatial hierarchy transitions, hexagonal k-ring expansions, highway
 * corridor isochrones, and equipment qualification checks execute strictly via
 * {@code ImpulseVM} CSR traversals and off-heap {@link ImpulseBitSet} SIMD set
 * intersections.</p>
 */
public final class LtlGeolocEngine {

    public static final String DOMAIN_H3_RES4 = "H3_Res4";
    public static final String DOMAIN_H3_RES6 = "H3_Res6";
    public static final String DOMAIN_H3_RES8 = "H3_Res8";
    public static final String DOMAIN_TERMINAL = "Terminal";
    public static final String DOMAIN_RELAY_YARD = "RelayYard";
    public static final String DOMAIN_TRUCK = "Truck";
    public static final String DOMAIN_SHIPMENT = "Shipment";
    public static final String DOMAIN_SERVICE_CLASS = "ServiceClass";
    public static final String DOMAIN_LANDMARK = "Landmark";

    public static final String REL_RES4_TO_RES6 = "RES4_TO_RES6";
    public static final String REL_RES6_TO_RES4 = "RES6_TO_RES4";
    public static final String REL_RES6_TO_RES8 = "RES6_TO_RES8";
    public static final String REL_RES8_TO_RES6 = "RES8_TO_RES6";
    public static final String REL_HEX_ADJACENT_RES4 = "HEX_ADJACENT_RES4";
    public static final String REL_HEX_ADJACENT_RES6 = "HEX_ADJACENT_RES6";
    public static final String REL_HEX_ADJACENT_RES8 = "HEX_ADJACENT_RES8";
    public static final String REL_HIGHWAY_CORRIDOR_RES6 = "HIGHWAY_CORRIDOR_RES6";
    public static final String REL_TERMINAL_AT_RES6 = "TERMINAL_AT_RES6";
    public static final String REL_RES6_HAS_TERMINAL = "RES6_HAS_TERMINAL";
    public static final String REL_TERMINAL_SERVES_RES6 = "TERMINAL_SERVES_RES6";
    public static final String REL_RES6_HAS_RELAY_YARD = "RES6_HAS_RELAY_YARD";
    public static final String REL_RES6_HAS_TRUCK = "RES6_HAS_TRUCK";
    public static final String REL_RES8_HAS_TRUCK = "RES8_HAS_TRUCK";
    public static final String REL_SHIPMENT_ORIGIN_RES8 = "SHIPMENT_ORIGIN_RES8";
    public static final String REL_RES8_HAS_SHIPMENT = "RES8_HAS_SHIPMENT";
    public static final String REL_CAPABILITY_HAS_TRUCK = "CAPABILITY_HAS_TRUCK";
    public static final String REL_RES6_HAS_LANDMARK = "RES6_HAS_LANDMARK";
    public static final String REL_RES8_HAS_LANDMARK = "RES8_HAS_LANDMARK";

    private final ImpulseGraphSnapshot graph;

    private final Map<Integer, ImpulseGraphQuery<ImpulseBitSet>> kRingRes6Queries = new ConcurrentHashMap<>();
    private final Map<Integer, ImpulseGraphQuery<ImpulseBitSet>> kRingTruckQueries = new ConcurrentHashMap<>();
    private final Map<Integer, ImpulseGraphQuery<ImpulseBitSet>> corridorIsochroneQueries = new ConcurrentHashMap<>();
    private final Map<Integer, ImpulseGraphQuery<ImpulseBitSet>> stormRes4BufferQueries = new ConcurrentHashMap<>();
    private final Map<Long, ImpulseBitSet> capabilityTruckBitsets = new ConcurrentHashMap<>();

    private final ImpulseGraphQuery<ImpulseBitSet> capabilityTrucksQuery;
    private final ImpulseGraphQuery<ImpulseBitSet> relayYardFromRes6Query;
    private final ImpulseGraphQuery<ImpulseBitSet> res4ToRes6Query;
    private final ImpulseGraphQuery<ImpulseBitSet> res4ToRes8Query;
    private final ImpulseGraphQuery<ImpulseBitSet> res4ToTrucksQuery;
    private final ImpulseGraphQuery<ImpulseBitSet> res4ToShipmentsQuery;
    private final ImpulseGraphQuery<ImpulseBitSet> res4ToLandmarksQuery;
    private final ImpulseGraphQuery<ImpulseBitSet> corridorReachableQuery;

    public LtlGeolocEngine(ImpulseGraphSnapshot graph) {
        this.graph = Objects.requireNonNull(graph, "graph must not be null");

        this.capabilityTrucksQuery = new ImpulseQueryBuilder<ImpulseBitSet>()
                .input(DOMAIN_SERVICE_CLASS, ArgType.ROARING_BITSET)
                .walkEdge(REL_CAPABILITY_HAS_TRUCK)
                .collectRoaringBitset();

        this.relayYardFromRes6Query = new ImpulseQueryBuilder<ImpulseBitSet>()
                .input(DOMAIN_H3_RES6, ArgType.ROARING_BITSET)
                .walkEdge(REL_RES6_HAS_RELAY_YARD)
                .collectRoaringBitset();

        this.res4ToRes6Query = new ImpulseQueryBuilder<ImpulseBitSet>()
                .input(DOMAIN_H3_RES4, ArgType.ROARING_BITSET)
                .walkEdge(REL_RES4_TO_RES6)
                .collectRoaringBitset();

        this.res4ToRes8Query = new ImpulseQueryBuilder<ImpulseBitSet>()
                .input(DOMAIN_H3_RES4, ArgType.ROARING_BITSET)
                .walkEdge(REL_RES4_TO_RES6)
                .walkEdge(REL_RES6_TO_RES8)
                .collectRoaringBitset();

        this.res4ToTrucksQuery = new ImpulseQueryBuilder<ImpulseBitSet>()
                .input(DOMAIN_H3_RES4, ArgType.ROARING_BITSET)
                .walkEdge(REL_RES4_TO_RES6)
                .walkEdge(REL_RES6_TO_RES8)
                .walkEdge(REL_RES8_HAS_TRUCK)
                .collectRoaringBitset();

        this.res4ToShipmentsQuery = new ImpulseQueryBuilder<ImpulseBitSet>()
                .input(DOMAIN_H3_RES4, ArgType.ROARING_BITSET)
                .walkEdge(REL_RES4_TO_RES6)
                .walkEdge(REL_RES6_TO_RES8)
                .walkEdge(REL_RES8_HAS_SHIPMENT)
                .collectRoaringBitset();

        this.res4ToLandmarksQuery = new ImpulseQueryBuilder<ImpulseBitSet>()
                .input(DOMAIN_H3_RES4, ArgType.ROARING_BITSET)
                .walkEdge(REL_RES4_TO_RES6)
                .walkEdge(REL_RES6_TO_RES8)
                .walkEdge(REL_RES8_HAS_LANDMARK)
                .collectRoaringBitset();

        this.corridorReachableQuery = new ImpulseQueryBuilder<ImpulseBitSet>()
                .input(DOMAIN_H3_RES6, ArgType.ROARING_BITSET)
                .repeatUntilStable(b -> b.walkEdge(REL_HIGHWAY_CORRIDOR_RES6))
                .collectRoaringBitset();
    }

    public ImpulseGraphSnapshot graph() {
        return graph;
    }

    public record PdDispatchResult(
            ImpulseBitSet kRingRes6Cells,
            ImpulseBitSet allTrucksInKRing,
            ImpulseBitSet qualifiedTrucks) {
    }

    public record RelayMeetResult(
            ImpulseBitSet overlappingCorridorCells,
            ImpulseBitSet relayYards) {
    }

    public record WeatherImpactResult(
            ImpulseBitSet impactedRes4,
            ImpulseBitSet impactedRes6,
            ImpulseBitSet impactedRes8,
            ImpulseBitSet impactedTrucks,
            ImpulseBitSet impactedShipments,
            ImpulseBitSet impactedLandmarks) {
    }

    // -------------------------------------------------------------------------
    // Scenario 1: Dynamic P&D Pickup Dispatch via H3 k-Ring Expansion
    // -------------------------------------------------------------------------

    public ImpulseBitSet expandKRingRes6(long originRes6Id, int kRings) {
        ImpulseGraphQuery<ImpulseBitSet> query = kRingRes6Queries.computeIfAbsent(kRings, k -> {
            ImpulseQueryBuilder<ImpulseBitSet> b = new ImpulseQueryBuilder<ImpulseBitSet>()
                    .input(DOMAIN_H3_RES6, ArgType.ROARING_BITSET);
            if (k > 0) {
                b.repeat(k, s -> s.walkEdge(REL_HEX_ADJACENT_RES6));
            }
            return b.collectRoaringBitset();
        });
        return query.execute(graph, originRes6Id);
    }

    public ImpulseBitSet findAllTrucksInKRing(long originRes6Id, int kRings) {
        ImpulseGraphQuery<ImpulseBitSet> query = kRingTruckQueries.computeIfAbsent(kRings, k -> {
            ImpulseQueryBuilder<ImpulseBitSet> b = new ImpulseQueryBuilder<ImpulseBitSet>()
                    .input(DOMAIN_H3_RES6, ArgType.ROARING_BITSET);
            if (k > 0) {
                b.repeat(k, s -> s.walkEdge(REL_HEX_ADJACENT_RES6));
            }
            return b.walkEdge(REL_RES6_HAS_TRUCK).collectRoaringBitset();
        });
        return query.execute(graph, originRes6Id);
    }

    public ImpulseBitSet findQualifiedTrucksInKRing(long originRes6Id, int kRings, long... serviceClassIds) {
        ImpulseBitSet candidateTrucks = findAllTrucksInKRing(originRes6Id, kRings);
        if (serviceClassIds != null) {
            for (long capId : serviceClassIds) {
                ImpulseBitSet capTrucks = capabilityTruckBitsets.computeIfAbsent(
                        capId, id -> capabilityTrucksQuery.execute(graph, id));
                candidateTrucks.and(capTrucks);
            }
        }
        return candidateTrucks;
    }

    public PdDispatchResult findDispatchableTrucksInKRing(long originRes6Id, int kRings, long... serviceClassIds) {
        ImpulseBitSet kRingCells = expandKRingRes6(originRes6Id, kRings);
        ImpulseBitSet allTrucks = findAllTrucksInKRing(originRes6Id, kRings);
        ImpulseBitSet qualifiedTrucks = findQualifiedTrucksInKRing(originRes6Id, kRings, serviceClassIds);
        return new PdDispatchResult(kRingCells, allTrucks, qualifiedTrucks);
    }

    // -------------------------------------------------------------------------
    // Scenario 2: Linehaul Meet-and-Turn (Relay) Corridor Isochrone Intersection
    // -------------------------------------------------------------------------

    public RelayMeetResult findMeetAndTurnRelayYards(long originRes6Id, long destRes6Id, int corridorHops) {
        ImpulseGraphQuery<ImpulseBitSet> isochroneQuery = corridorIsochroneQueries.computeIfAbsent(corridorHops, k ->
                new ImpulseQueryBuilder<ImpulseBitSet>()
                        .input(DOMAIN_H3_RES6, ArgType.ROARING_BITSET)
                        .repeat(k, s -> s.walkEdge(REL_HIGHWAY_CORRIDOR_RES6))
                        .collectRoaringBitset());

        ImpulseBitSet originIsochrone = isochroneQuery.execute(graph, originRes6Id);
        ImpulseBitSet destIsochrone = isochroneQuery.execute(graph, destRes6Id);

        originIsochrone.and(destIsochrone);

        ImpulseBitSet relayYards = relayYardFromRes6Query.execute(graph, originIsochrone);

        return new RelayMeetResult(originIsochrone, relayYards);
    }

    // -------------------------------------------------------------------------
    // Scenario 3: Multi-Resolution Severe Weather Geofence Drilldown
    // -------------------------------------------------------------------------

    public WeatherImpactResult drilldownWeatherHazardImpact(long[] stormRes4Ids, int bufferRings) {
        ImpulseGraphQuery<ImpulseBitSet> res4ZoneQuery = stormRes4BufferQueries.computeIfAbsent(bufferRings, k -> {
            ImpulseQueryBuilder<ImpulseBitSet> b = new ImpulseQueryBuilder<ImpulseBitSet>()
                    .input(DOMAIN_H3_RES4, ArgType.ROARING_BITSET);
            if (k > 0) {
                b.repeat(k, s -> s.walkEdge(REL_HEX_ADJACENT_RES4));
            }
            return b.collectRoaringBitset();
        });

        ImpulseBitSet impactedRes4 = res4ZoneQuery.execute(graph, stormRes4Ids);
        ImpulseBitSet impactedRes6 = res4ToRes6Query.execute(graph, impactedRes4);
        ImpulseBitSet impactedRes8 = res4ToRes8Query.execute(graph, impactedRes4);
        ImpulseBitSet impactedTrucks = res4ToTrucksQuery.execute(graph, impactedRes4);
        ImpulseBitSet impactedShipments = res4ToShipmentsQuery.execute(graph, impactedRes4);
        ImpulseBitSet impactedLandmarks = res4ToLandmarksQuery.execute(graph, impactedRes4);

        return new WeatherImpactResult(
                impactedRes4,
                impactedRes6,
                impactedRes8,
                impactedTrucks,
                impactedShipments,
                impactedLandmarks);
    }

    // -------------------------------------------------------------------------
    // Scenario 4: Transitive Freight Network Reachability (Fixed-Point Loop)
    // -------------------------------------------------------------------------

    public ImpulseBitSet findReachableCorridorNetwork(long startRes6Id) {
        return corridorReachableQuery.execute(graph, startRes6Id);
    }

    public ImpulseBitSet findReachableHighwayCorridorCells(long startRes6Id) {
        return findReachableCorridorNetwork(startRes6Id);
    }

    // -------------------------------------------------------------------------
    // ImpAsm Bytecode Disassembly & BitSet Utility Helpers
    // -------------------------------------------------------------------------

    public String disassembleScenario1KRingDispatch(long originRes6Id, int kRings) {
        return graph.domain(DOMAIN_H3_RES6)
                .from(originRes6Id)
                .repeat(kRings, s -> s.out(REL_HEX_ADJACENT_RES6))
                .out(REL_RES6_HAS_TRUCK)
                .toImpAsm();
    }

    public String disassembleScenario2CorridorIsochrone(long originRes6Id, int corridorHops) {
        return graph.domain(DOMAIN_H3_RES6)
                .from(originRes6Id)
                .repeat(corridorHops, s -> s.out(REL_HIGHWAY_CORRIDOR_RES6))
                .toImpAsm();
    }

    public String disassembleScenario3WeatherDrilldownToTrucks(ImpulseBitSet impactedRes4) {
        return graph.domain(DOMAIN_H3_RES4)
                .from(impactedRes4)
                .out(REL_RES4_TO_RES6)
                .out(REL_RES6_TO_RES8)
                .out(REL_RES8_HAS_TRUCK)
                .toImpAsm();
    }

    public String disassembleScenario4CorridorReachability(long startRes6Id) {
        return graph.domain(DOMAIN_H3_RES6)
                .from(startRes6Id)
                .repeatUntilStable(s -> s.out(REL_HIGHWAY_CORRIDOR_RES6))
                .toImpAsm();
    }

    public static List<Long> toSortedIdList(ImpulseBitSet bitSet) {
        if (bitSet == null) {
            return List.of();
        }
        List<Long> ids = new ArrayList<>();
        for (int i = bitSet.nextSetBit(0); i >= 0; i = bitSet.nextSetBit(i + 1)) {
            ids.add((long) i);
        }
        return Collections.unmodifiableList(ids);
    }
}
