from __future__ import annotations

import unittest

from src.events.event_effects import EventEffects
from src.events.event_manager import _detect_phenomenon
from src.events.event_model import TrafficEvent
from src.events.event_validator import validate_event_time
from src.events.event_visualizer import EventVisualizer
from src.events.route_utils import RouteUtils
from src.map.edge_sampler import EdgeSampler
from src.agents.intersection_agent import IntersectionAgent


class FakeLane:
    def __init__(self) -> None:
        self.speeds = {"edge_0": 10.0, "edge_1": 10.0}
        self.allowed = {"edge_0": [], "edge_1": []}
        self.disallowed = {"edge_0": [], "edge_1": []}

    def getMaxSpeed(self, lane_id):
        return self.speeds[lane_id]

    def setMaxSpeed(self, lane_id, value):
        self.speeds[lane_id] = value

    def getAllowed(self, lane_id):
        return self.allowed[lane_id]

    def getDisallowed(self, lane_id):
        return self.disallowed[lane_id]

    def setAllowed(self, lane_id, value):
        self.allowed[lane_id] = list(value)
        self.disallowed[lane_id] = []

    def setDisallowed(self, lane_id, value):
        self.disallowed[lane_id] = list(value)
        self.allowed[lane_id] = []


class FakeVehicle:
    def getIDList(self):
        return []


class FakeEdge:
    def getLastStepVehicleIDs(self, edge_id):
        return []


class FakeTraCI:
    def __init__(self) -> None:
        self.lane = FakeLane()
        self.vehicle = FakeVehicle()
        self.edge = FakeEdge()


class StubRoutes:
    def lanes_for_edges(self, edge_ids):
        return ["edge_0", "edge_1"]

    def _safe_construction_lanes(self, edge_ids):
        return ["edge_0"]


class EventEffectsTests(unittest.TestCase):
    def test_overlapping_speed_effects_restore_in_stack_order(self) -> None:
        traci = FakeTraCI()
        effects = EventEffects(StubRoutes())
        first = TrafficEvent.from_dict(
            {
                "event_id": "rain",
                "event_type": "weather",
                "start_time": 0,
                "end_time": 10,
                "target_edges": ["edge"],
                "effects": {"reduce_speed": True, "speed_factor": 0.8},
            }
        )
        second = TrafficEvent.from_dict(
            {
                "event_id": "typhoon",
                "event_type": "weather",
                "start_time": 1,
                "end_time": 9,
                "target_edges": ["edge"],
                "effects": {"reduce_speed": True, "speed_factor": 0.5},
            }
        )
        effects.activate(traci, first, 0)
        self.assertEqual(traci.lane.speeds["edge_0"], 8.0)
        effects.activate(traci, second, 1)
        self.assertEqual(traci.lane.speeds["edge_0"], 5.0)
        effects.deactivate(traci, second)
        self.assertEqual(traci.lane.speeds["edge_0"], 8.0)
        effects.deactivate(traci, first)
        self.assertEqual(traci.lane.speeds["edge_0"], 10.0)

    def test_unrealistic_concert_and_peak_times_are_corrected(self) -> None:
        morning = TrafficEvent.from_dict(
            {
                "event_type": "rush_hour",
                "start_time": 30,
                "end_time": 180,
                "effects": {"peak_type": "morning"},
            }
        )
        concert = TrafficEvent.from_dict(
            {
                "event_type": "concert",
                "start_time": 120,
                "end_time": 320,
            }
        )
        self.assertTrue(validate_event_time(morning, 23400, "relative"))
        self.assertTrue(validate_event_time(concert, 23400, "relative"))
        self.assertEqual(morning.start_time, 1800)
        self.assertEqual(concert.start_time, 41400)

    def test_agent_monitors_nearby_event_then_reroutes_on_measured_queue(self) -> None:
        agent = IntersectionAgent(
            junction_id="junction",
            incoming_edges=["edge_a"],
            queue_threshold=15,
            speed_threshold=4.0,
        )
        observation = {
            "time": 0,
            "incoming_edges": ["edge_a"],
            "active_events": [
                {
                    "event_id": "construction",
                    "event_type": "construction",
                    "affected_edges": ["edge_a"],
                }
            ],
            "queue_length": 0,
            "vehicle_count": 4,
            "mean_speed": 10.0,
            "waiting_time": 0.0,
            "congestion_index": 0.0,
            "congested_edges": [],
        }
        self.assertEqual(agent.decide(observation).action, "event_monitoring")
        observation["queue_length"] = 6
        observation["congestion_index"] = 0.6
        self.assertEqual(agent.decide(observation).action, "reroute_guidance")

    def test_lane_closure_restores_permissions(self) -> None:
        traci = FakeTraCI()
        effects = EventEffects(StubRoutes())
        event = TrafficEvent.from_dict(
            {
                "event_id": "work",
                "event_type": "construction",
                "start_time": 0,
                "end_time": 10,
                "target_edges": ["edge"],
                "target_lanes": ["edge_0"],
                "effects": {"close_lanes": True},
            }
        )
        effects.activate(traci, event, 0)
        self.assertEqual(traci.lane.disallowed["edge_0"], ["passenger"])
        effects.deactivate(traci, event)
        self.assertEqual(traci.lane.disallowed["edge_0"], [])


class RouteAndVisualizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sampler = EdgeSampler("data/maps/demo_city/demo_city.net.xml")

    def test_invalid_edges_are_replaced_with_non_internal_edges(self) -> None:
        routes = RouteUtils(self.sampler)
        event = TrafficEvent.from_dict(
            {
                "event_type": "construction",
                "start_time": 0,
                "end_time": 10,
                "target_edges": [":missing", "missing"],
            }
        )
        routes.prepare_event(event)
        self.assertTrue(event.target_edges)
        self.assertTrue(all(not edge.startswith(":") for edge in event.target_edges))
        self.assertTrue(all(routes.valid_edge(edge) for edge in event.target_edges))

    def test_event_location_seed_is_reproducible_but_not_fixed(self) -> None:
        def selected_edges(seed: int) -> list[str]:
            routes = RouteUtils(self.sampler, seed=seed)
            event = TrafficEvent.from_dict(
                {"event_type": "concert", "start_time": 0, "end_time": 10}
            )
            routes.prepare_event(event)
            return event.target_edges

        self.assertEqual(selected_edges(12345), selected_edges(12345))
        self.assertNotEqual(selected_edges(12345), selected_edges(54321))

    def test_construction_impact_expands_to_connected_upstream_edges(self) -> None:
        routes = RouteUtils(self.sampler, seed=21)
        primary = next(
            [edge.edge_id]
            for edge in self.sampler.edges
            if routes.edge_by_id[edge.edge_id].getIncoming()
        )
        expanded, factors = routes._construction_impact_corridor(
            primary,
            hops=3,
            maximum=20,
            factors=[0.6, 0.7, 0.8],
        )
        self.assertEqual(expanded[0], primary[0])
        self.assertGreater(len(expanded), 1)
        self.assertTrue(factors)
        self.assertTrue(all(0.6 <= factor <= 0.8 for factor in factors.values()))

    def test_construction_demand_route_crosses_real_bottleneck(self) -> None:
        routes = RouteUtils(self.sampler, seed=31)
        event = TrafficEvent.from_dict(
            {
                "event_type": "construction",
                "start_time": 0,
                "end_time": 100,
                "effects": {
                    "propagation_hops": 4,
                    "max_impact_edges": 24,
                },
            }
        )
        routes.prepare_event(event)
        routes.ensure_construction_impact(event)
        route = []
        for offset in range(60):
            route = routes.find_construction_route(event, offset)
            if route:
                break
        self.assertTrue(route)
        self.assertTrue(
            any(edge_id in route for edge_id in event.effects["_primary_edges"])
        )

    def test_missing_gui_api_degrades_without_raising(self) -> None:
        routes = RouteUtils(self.sampler)
        event = TrafficEvent.from_dict(
            {"event_type": "weather", "start_time": 0, "end_time": 10}
        )
        routes.prepare_event(event)
        visualizer = EventVisualizer(routes, {"enable_gui_visualization": True})

        class BrokenPoi:
            def add(self, *args, **kwargs):
                raise RuntimeError("GUI domain unavailable")

        class BrokenTraci:
            poi = BrokenPoi()

        visualizer.start(BrokenTraci(), event, 0)
        self.assertFalse(visualizer.gui_available)

    def test_sumo_invalid_position_sentinel_is_rejected(self) -> None:
        routes = RouteUtils(self.sampler)
        visualizer = EventVisualizer(routes, {"enable_gui_visualization": True})
        self.assertFalse(visualizer._valid_position(-1073741824.0, -1073741824.0))
        center_x, center_y = visualizer.global_center
        self.assertTrue(visualizer._valid_position(center_x, center_y))

    def test_visualizer_draws_only_unfilled_ring_and_never_uses_camera(self) -> None:
        routes = RouteUtils(self.sampler, seed=7)
        event = TrafficEvent.from_dict(
            {"event_type": "rush_hour", "start_time": 0, "end_time": 10}
        )
        routes.prepare_event(event)

        class RecordingDomain:
            def __init__(self, ids=()):
                self.ids = list(ids)
                self.added = []
                self.removed = []

            def add(self, *args, **kwargs):
                self.added.append((args, kwargs))
                self.ids.append(args[0])

            def getIDList(self):
                return list(self.ids)

            def remove(self, object_id):
                self.removed.append(object_id)

        class RingOnlyTraci:
            polygon = RecordingDomain(["agent_box_old", "ordinary_polygon"])
            poi = RecordingDomain(["agent_marker_old", "ordinary_poi"])

        traci = RingOnlyTraci()
        visualizer = EventVisualizer(routes, {"enable_gui_visualization": True})
        visualizer.step(traci, 0)
        visualizer.start(traci, event, 0)

        self.assertEqual(len(traci.polygon.added), 1)
        _, kwargs = traci.polygon.added[0]
        self.assertFalse(kwargs["fill"])
        self.assertEqual(kwargs["lineWidth"], 2)
        self.assertNotIn("agent_box_old", traci.polygon.removed)
        self.assertNotIn("agent_marker_old", traci.poi.removed)
        self.assertNotIn("ordinary_polygon", traci.polygon.removed)
        self.assertNotIn("ordinary_poi", traci.poi.removed)

    def test_spawn_activity_is_not_reported_until_congestion_exists(self) -> None:
        event = TrafficEvent.from_dict(
            {"event_type": "rush_hour", "start_time": 0, "end_time": 10}
        )
        free_flow = {
            "vehicle_count": 8.0,
            "mean_speed": 13.0,
            "mean_waiting": 0.0,
            "halted": 0.0,
        }
        congested = {
            "vehicle_count": 8.0,
            "mean_speed": 5.0,
            "mean_waiting": 0.0,
            "halted": 0.0,
        }
        self.assertIsNone(_detect_phenomenon(event, free_flow, 0, 8.0, 3))
        self.assertEqual(
            _detect_phenomenon(event, congested, 0, 8.0, 3),
            "congestion",
        )

    def test_empty_event_ring_is_hidden_and_reappears_at_vehicle(self) -> None:
        routes = RouteUtils(self.sampler, seed=17)
        event = TrafficEvent.from_dict(
            {
                "event_id": "visible_event",
                "event_type": "weather",
                "start_time": 0,
                "end_time": 10,
                "radius": 100,
            }
        )
        routes.prepare_event(event)

        class PolygonDomain:
            def __init__(self):
                self.added = []
                self.removed = []
                self.shapes = {}

            def add(self, object_id, shape, *args, **kwargs):
                self.added.append(object_id)
                self.shapes[object_id] = shape

            def remove(self, object_id):
                self.removed.append(object_id)
                self.shapes.pop(object_id, None)

            def setShape(self, object_id, shape):
                self.shapes[object_id] = shape

        class VehicleDomain:
            def getPosition(self, vehicle_id):
                return (3000.0, 2000.0)

        class Traci:
            polygon = PolygonDomain()
            vehicle = VehicleDomain()

        traci = Traci()
        visualizer = EventVisualizer(routes, {"enable_gui_visualization": True})
        visualizer.start(traci, event, 0)
        visualizer.update(traci, event, [], 1)
        self.assertIn("event_area_visible_event", traci.polygon.removed)

        visualizer.update(traci, event, ["vehicle_1"], 2)
        shape = traci.polygon.shapes["event_area_visible_event"]
        center_x = sum(point[0] for point in shape[:-1]) / 40
        center_y = sum(point[1] for point in shape[:-1]) / 40
        self.assertAlmostEqual(center_x, 3000.0)
        self.assertAlmostEqual(center_y, 2000.0)


if __name__ == "__main__":
    unittest.main()
