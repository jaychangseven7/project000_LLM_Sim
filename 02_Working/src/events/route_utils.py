from __future__ import annotations

import math
from collections import Counter
import random
from typing import Any

from src.events.event_model import TrafficEvent


class RouteUtils:
    def __init__(self, edge_sampler, seed: int | None = None) -> None:
        self.edge_sampler = edge_sampler
        self.net = edge_sampler.net
        self.edge_by_id = edge_sampler.edge_by_id
        self.rng = random.Random(seed)
        self.edge_points = {
            edge.edge_id: (float(edge.x), float(edge.y))
            for edge in edge_sampler.edges
        }
        self._used_centers: list[tuple[float, float]] = []
        boundary = self.net.getBoundary()
        self._minimum_event_spacing = min(
            900.0,
            math.hypot(boundary[2] - boundary[0], boundary[3] - boundary[1]) / 7,
        )
        self._route_cache: dict[tuple[str, str], list[str]] = {}

    def prepare_event(self, event: TrafficEvent, index: int = 0) -> None:
        valid_edges = [edge for edge in event.target_edges if self.valid_edge(edge)]
        for edge in event.target_edges:
            if edge not in valid_edges:
                event.warnings.append(f"invalid edge skipped: {edge}")

        if not valid_edges:
            valid_edges = self._automatic_edges(event, index)
            event.warnings.append(f"auto-selected edges: {','.join(valid_edges)}")
        event.target_edges = valid_edges

        valid_lanes = [lane for lane in event.target_lanes if self.valid_lane(lane)]
        for lane in event.target_lanes:
            if lane not in valid_lanes:
                event.warnings.append(f"invalid lane skipped: {lane}")
        event.target_lanes = valid_lanes

        if event.event_type in {"construction", "road_closure"} and not event.target_lanes:
            event.target_lanes = self._safe_construction_lanes(valid_edges)
        if not event.center_edge and valid_edges:
            event.center_edge = valid_edges[0]
        if not event.center_lane and event.target_lanes:
            event.center_lane = event.target_lanes[0]
        if (
            event.center_edge
            and (
                not event.auto_selected
                or event.event_type not in {"construction", "road_closure", "accident", "weather"}
            )
        ):
            self._reserve_center(event.center_edge)

    def valid_edge(self, edge_id: str) -> bool:
        return bool(edge_id) and not edge_id.startswith(":") and edge_id in self.edge_by_id

    def valid_lane(self, lane_id: str) -> bool:
        try:
            return self.net.hasEdge(lane_id.rsplit("_", 1)[0]) and self.net.getLane(lane_id) is not None
        except Exception:
            return False

    def lanes_for_edges(self, edge_ids: list[str]) -> list[str]:
        lanes: list[str] = []
        for edge_id in edge_ids:
            edge = self.edge_by_id.get(edge_id)
            if edge is None:
                continue
            lanes.extend(lane.getID() for lane in edge.getLanes() if lane.allows("passenger"))
        return lanes

    def event_center(self, event: TrafficEvent) -> tuple[float, float]:
        edge = self.edge_by_id.get(event.center_edge or "")
        if edge is None and event.target_edges:
            edge = self.edge_by_id.get(event.target_edges[0])
        if edge is None:
            bounds = self.net.getBoundary()
            return ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
        shape = edge.getShape()
        if not shape:
            return (0.0, 0.0)
        position = event.center_pos
        if position is None:
            return shape[len(shape) // 2]
        return _point_at_distance(shape, max(0.0, position))

    def event_polygon(self, event: TrafficEvent) -> list[tuple[float, float]]:
        x, y = self.event_center(event)
        radius = float(event.radius or 120.0)
        points: list[tuple[float, float]] = []
        for index in range(40):
            angle = 2 * math.pi * index / 40
            points.append((x + radius * math.cos(angle), y + radius * math.sin(angle)))
        points.append(points[0])
        return points

    def refresh_live_targets(self, event: TrafficEvent, traci_conn) -> bool:
        """Retarget auto-selected road events to roads that currently contain vehicles."""
        if not event.auto_selected:
            return False
        counts: Counter[str] = Counter()
        try:
            for vehicle_id in traci_conn.vehicle.getIDList():
                edge_id = str(traci_conn.vehicle.getRoadID(vehicle_id))
                if self.valid_edge(edge_id):
                    counts[edge_id] += 1
        except Exception:
            return False
        if not counts:
            return False

        ranked = [edge_id for edge_id, _ in counts.most_common()]
        if event.event_type in {"construction", "road_closure"}:
            multi_lane_ranked = [
                edge_id
                for edge_id in ranked
                if len(
                    [
                        lane
                        for lane in self.edge_by_id[edge_id].getLanes()
                        if lane.allows("passenger")
                    ]
                )
                >= 2
            ]
            two_lane_ranked = [
                edge_id
                for edge_id in multi_lane_ranked
                if len(
                    [
                        lane
                        for lane in self.edge_by_id[edge_id].getLanes()
                        if lane.allows("passenger")
                    ]
                )
                == 2
            ]
            ranked = two_lane_ranked or multi_lane_ranked
            count = 2
        elif event.event_type == "weather":
            count = 12
        elif event.event_type == "rush_hour":
            count = 6
        elif event.event_type in {"concert", "large_event"}:
            count = 4
        else:
            count = 2
        if not ranked:
            return False

        # Randomize among many currently occupied local traffic clusters.
        # This keeps vehicles visible without returning to a fixed "busiest
        # road" location on every run.
        radius = float(event.radius or 120.0)
        ranked = sorted(
            ranked,
            key=lambda edge_id: self._local_vehicle_count(edge_id, counts, radius),
            reverse=True,
        )
        broad_pool_size = min(len(ranked), max(12, len(ranked) // 3))
        broad_pool = ranked[:broad_pool_size]
        spaced = [edge_id for edge_id in broad_pool if self._is_spatially_new(edge_id)]
        anchor_pool = spaced or broad_pool
        anchor = self._random_reachable_anchor(event, anchor_pool)
        eligible = (
            [
                edge.edge_id
                for edge in self.edge_sampler.edges
                if len(
                    [
                        lane
                        for lane in self.edge_by_id[edge.edge_id].getLanes()
                        if lane.allows("passenger")
                    ]
                )
                >= 2
            ]
            if event.event_type in {"construction", "road_closure"}
            else [edge.edge_id for edge in self.edge_sampler.edges]
        )
        event.target_edges = self._nearby_edges(
            anchor,
            eligible,
            min(count, len(eligible)),
            radius,
        )
        event.center_edge = anchor
        self._reserve_center(anchor)
        if event.event_type in {"construction", "road_closure"} or event.effects.get("partial_closure"):
            event.target_lanes = self._safe_construction_lanes(event.target_edges)
            event.center_lane = event.target_lanes[0] if event.target_lanes else None
        event.warnings.append(
            "live traffic selection: "
            + ",".join(f"{edge}({counts[edge]})" for edge in event.target_edges)
        )
        return True

    def ensure_construction_impact(self, event: TrafficEvent) -> bool:
        if event.event_type not in {"construction", "road_closure"}:
            return False
        if event.effects.get("_impact_expanded"):
            return False
        primary_edges = list(
            event.effects.get("_primary_edges") or event.target_edges[:2]
        )
        impact_edges, impact_factors = self._construction_impact_corridor(
            primary_edges,
            hops=max(1, int(event.effects.get("propagation_hops", 3))),
            maximum=max(2, int(event.effects.get("max_impact_edges", 20))),
            factors=list(
                event.effects.get(
                    "propagation_speed_factors",
                    [0.60, 0.70, 0.80, 0.90],
                )
            ),
        )
        event.effects["_primary_edges"] = primary_edges
        event.effects["_edge_speed_factors"] = impact_factors
        event.effects["_impact_expanded"] = True
        event.target_edges = impact_edges
        event.warnings.append(
            f"construction impact expanded from {len(primary_edges)} "
            f"to {len(impact_edges)} connected edges"
        )
        return len(impact_edges) > len(primary_edges)

    def _construction_impact_corridor(
        self,
        primary_edges: list[str],
        hops: int,
        maximum: int,
        factors: list[float],
    ) -> tuple[list[str], dict[str, float]]:
        selected = list(dict.fromkeys(primary_edges))
        factor_by_edge: dict[str, float] = {}
        frontier = list(primary_edges)
        visited = set(primary_edges)
        for hop in range(1, hops + 1):
            next_frontier: list[str] = []
            for edge_id in frontier:
                edge = self.edge_by_id.get(edge_id)
                if edge is None:
                    continue
                for incoming in edge.getIncoming():
                    incoming_id = incoming.getID()
                    if (
                        incoming_id in visited
                        or not self.valid_edge(incoming_id)
                        or not incoming.allows("passenger")
                    ):
                        continue
                    visited.add(incoming_id)
                    next_frontier.append(incoming_id)
                    selected.append(incoming_id)
                    factor_index = min(hop - 1, len(factors) - 1)
                    factor_by_edge[incoming_id] = max(
                        0.1, min(float(factors[factor_index]), 1.0)
                    )
                    if len(selected) >= maximum:
                        return selected, factor_by_edge
            frontier = next_frontier
            if not frontier:
                break
        return selected, factor_by_edge

    def _local_vehicle_count(
        self,
        anchor: str,
        counts: Counter[str],
        radius: float,
    ) -> int:
        center = self.edge_points.get(anchor)
        if center is None:
            return 0
        return sum(
            vehicle_count
            for edge_id, vehicle_count in counts.items()
            if edge_id in self.edge_points
            and math.dist(center, self.edge_points[edge_id]) <= max(radius, 80.0)
        )

    def edge_shape(self, edge_id: str) -> list[tuple[float, float]]:
        edge = self.edge_by_id.get(edge_id)
        return list(edge.getShape()) if edge is not None else []

    def find_route(
        self,
        traci_conn,
        origins: list[str],
        destinations: list[str],
        vehicle_type: str,
        attempt_offset: int = 0,
    ) -> list[str]:
        if not origins or not destinations:
            return []
        attempts = min(40, max(len(origins), len(destinations)) * 2)
        for attempt in range(attempts):
            origin = origins[(attempt + attempt_offset) % len(origins)]
            destination = destinations[(attempt * 3 + attempt_offset) % len(destinations)]
            if origin == destination:
                continue
            try:
                key = (origin, destination)
                if key not in self._route_cache:
                    path, _ = self.net.getOptimalPath(
                        self.edge_by_id[origin],
                        self.edge_by_id[destination],
                        fastest=True,
                        vClass="passenger",
                    )
                    self._route_cache[key] = [edge.getID() for edge in path] if path else []
                if self._route_cache[key]:
                    return list(self._route_cache[key])
            except Exception:
                continue
        return []

    def find_construction_route(
        self,
        event: TrafficEvent,
        attempt_offset: int = 0,
    ) -> list[str]:
        """Build a route that must traverse the real construction bottleneck."""
        primary = [
            edge_id
            for edge_id in event.effects.get("_primary_edges", event.target_edges[:1])
            if self.valid_edge(edge_id)
        ]
        impact_factors = event.effects.get("_edge_speed_factors", {})
        origins = sorted(
            [
                edge_id
                for edge_id in event.target_edges
                if edge_id not in primary and self.valid_edge(edge_id)
            ],
            key=lambda edge_id: float(impact_factors.get(edge_id, 0.0)),
            reverse=True,
        )
        if not primary or not origins:
            return []

        for attempt in range(min(60, len(origins) * len(primary) * 4)):
            via_id = primary[(attempt + attempt_offset) % len(primary)]
            origin_id = origins[(attempt * 3 + attempt_offset) % len(origins)]
            via_edge = self.edge_by_id[via_id]
            closed_lanes = set(event.target_lanes)
            destinations = list(
                dict.fromkeys(
                    connection.getToLane().getEdge().getID()
                    for lane in via_edge.getLanes()
                    if lane.getID() not in closed_lanes and lane.allows("passenger")
                    for connection in lane.getOutgoing()
                    if connection.getToLane().allows("passenger")
                    and self.valid_edge(connection.getToLane().getEdge().getID())
                )
            )
            if not destinations:
                continue
            destination_id = destinations[(attempt + attempt_offset) % len(destinations)]
            try:
                first_path, _ = self.net.getOptimalPath(
                    self.edge_by_id[origin_id],
                    via_edge,
                    fastest=True,
                    vClass="passenger",
                )
                second_path, _ = self.net.getOptimalPath(
                    via_edge,
                    self.edge_by_id[destination_id],
                    fastest=True,
                    vClass="passenger",
                )
                if not first_path or not second_path:
                    continue
                route = [edge.getID() for edge in first_path]
                second_ids = [edge.getID() for edge in second_path]
                route.extend(
                    second_ids[1:]
                    if second_ids and second_ids[0] == via_id
                    else second_ids
                )
                route = list(dict.fromkeys(route))
                if (
                    via_id in route
                    and len(route) >= 3
                    and self._route_respects_open_connections(route, closed_lanes)
                ):
                    return route
            except Exception:
                continue
        return []

    def _route_respects_open_connections(
        self,
        route: list[str],
        closed_lanes: set[str],
    ) -> bool:
        for from_id, to_id in zip(route, route[1:]):
            from_edge = self.edge_by_id.get(from_id)
            if from_edge is None:
                return False
            connected = any(
                connection.getToLane().getEdge().getID() == to_id
                and connection.getToLane().getID() not in closed_lanes
                and connection.getToLane().allows("passenger")
                for lane in from_edge.getLanes()
                if lane.getID() not in closed_lanes and lane.allows("passenger")
                for connection in lane.getOutgoing()
            )
            if not connected:
                return False
        return True

    def demand_endpoints(self, event: TrafficEvent, outbound: bool = False) -> tuple[list[str], list[str]]:
        effects = event.effects
        origins = [edge for edge in effects.get("origin_edges", []) if self.valid_edge(edge)]
        destinations = [edge for edge in effects.get("destination_edges", []) if self.valid_edge(edge)]
        origin_zone = effects.get("origin_zone")
        destination_zone = effects.get("destination_zone")

        if not origins:
            origins = list(self.edge_sampler.zones.get(origin_zone or "residential_zone", []))
        if not destinations:
            destinations = list(self.edge_sampler.zones.get(destination_zone or "business_zone", []))

        if event.event_type in {"concert", "large_event"}:
            venue = event.target_edges or self.edge_sampler.zones.get("event_zone", [])
            if outbound:
                origins, destinations = list(venue), list(self.edge_sampler.zones.get("residential_zone", []))
            else:
                origins, destinations = list(self.edge_sampler.zones.get("residential_zone", [])), list(venue)
        elif event.event_type == "rush_hour" and event.effects.get("peak_type") == "evening":
            origins = list(event.target_edges)
            destinations = list(self.edge_sampler.zones.get("residential_zone", []))
        elif event.event_type == "rush_hour":
            origins = list(self.edge_sampler.zones.get("residential_zone", []))
            destinations = list(event.target_edges)
        elif effects.get("peak_type") == "evening":
            origins, destinations = destinations, origins
        return origins, destinations

    def _automatic_edges(self, event: TrafficEvent, index: int) -> list[str]:
        if event.event_type in {"construction", "road_closure"}:
            multi_lane_candidates = [
                edge.edge_id for edge in self.edge_sampler.edges
                if len([lane for lane in self.edge_by_id[edge.edge_id].getLanes() if lane.allows("passenger")]) >= 2
            ]
            two_lane_candidates = [
                edge_id
                for edge_id in multi_lane_candidates
                if len(
                    [
                        lane
                        for lane in self.edge_by_id[edge_id].getLanes()
                        if lane.allows("passenger")
                    ]
                )
                == 2
            ]
            candidates = two_lane_candidates or multi_lane_candidates
            count = 2
        elif event.event_type == "weather":
            candidates = [edge.edge_id for edge in self.edge_sampler.edges]
            count = 12
        elif event.event_type in {"concert", "large_event"}:
            candidates = [edge.edge_id for edge in self.edge_sampler.edges]
            count = 4
        else:
            candidates = [edge.edge_id for edge in self.edge_sampler.edges]
            count = 6
        if not candidates:
            candidates = [edge.edge_id for edge in self.edge_sampler.edges]
        candidates = [
            edge_id
            for edge_id in candidates
            if self.edge_points.get(edge_id) is not None
            and self.edge_by_id[edge_id].getLength() >= 20
        ] or candidates
        spaced = [edge_id for edge_id in candidates if self._is_spatially_new(edge_id)]
        anchor = self._random_reachable_anchor(event, spaced or candidates)
        return self._nearby_edges(
            anchor,
            candidates,
            min(count, len(candidates)),
            float(event.radius or 120.0),
        )

    def _random_reachable_anchor(
        self,
        event: TrafficEvent,
        candidates: list[str],
    ) -> str:
        shuffled = list(candidates)
        self.rng.shuffle(shuffled)
        if event.event_type not in {"rush_hour", "concert", "large_event"}:
            return shuffled[0]

        residential = list(self.edge_sampler.zones.get("residential_zone", []))
        if not residential:
            return shuffled[0]
        probes = self.rng.sample(residential, min(32, len(residential)))
        for anchor in shuffled[: min(300, len(shuffled))]:
            inbound = any(
                self.edge_sampler.is_reachable(origin, anchor)
                for origin in probes
                if origin != anchor
            )
            if not inbound:
                continue
            needs_outbound = (
                event.event_type in {"concert", "large_event"}
                or event.effects.get("peak_type") == "evening"
            )
            if not needs_outbound or any(
                self.edge_sampler.is_reachable(anchor, destination)
                for destination in probes
                if destination != anchor
            ):
                return anchor
        return shuffled[0]

    def _nearby_edges(
        self,
        anchor: str,
        candidates: list[str],
        count: int,
        radius: float,
    ) -> list[str]:
        center = self.edge_points.get(anchor)
        if center is None:
            return [anchor]
        ordered = sorted(
            set(candidates),
            key=lambda edge_id: math.dist(center, self.edge_points.get(edge_id, center)),
        )
        local = [
            edge_id
            for edge_id in ordered
            if math.dist(center, self.edge_points.get(edge_id, center)) <= max(radius, 80.0)
        ]
        selected = (local + [edge for edge in ordered if edge not in local])[:count]
        if anchor in selected:
            selected.remove(anchor)
        return [anchor, *selected][:count]

    def _is_spatially_new(self, edge_id: str) -> bool:
        point = self.edge_points.get(edge_id)
        if point is None:
            return False
        return all(
            math.dist(point, previous) >= self._minimum_event_spacing
            for previous in self._used_centers
        )

    def _reserve_center(self, edge_id: str) -> None:
        point = self.edge_points.get(edge_id)
        if point is not None:
            self._used_centers.append(point)

    def _safe_construction_lanes(self, edge_ids: list[str]) -> list[str]:
        for edge_id in edge_ids:
            edge = self.edge_by_id.get(edge_id)
            if edge is None:
                continue
            passenger_lanes = [
                lane for lane in edge.getLanes() if lane.allows("passenger")
            ]
            if len(passenger_lanes) >= 2:
                # Close the lane with the fewest turn connections so at least
                # one legal through/turn path remains on the open lanes.
                candidates = sorted(
                    passenger_lanes,
                    key=lambda lane: len(lane.getOutgoing()),
                )
                for lane in candidates:
                    remaining_connections = sum(
                        len(other.getOutgoing())
                        for other in passenger_lanes
                        if other.getID() != lane.getID()
                    )
                    if remaining_connections > 0:
                        return [lane.getID()]
        return []

    def _weighted_sample(
        self,
        candidates: list[str],
        counts: Counter[str],
        sample_size: int,
    ) -> list[str]:
        remaining = list(candidates)
        selected: list[str] = []
        while remaining and len(selected) < sample_size:
            weights = [max(1, counts[edge_id]) for edge_id in remaining]
            chosen = self.rng.choices(remaining, weights=weights, k=1)[0]
            selected.append(chosen)
            remaining.remove(chosen)
        return selected


def _point_at_distance(shape: list[tuple[float, float]], distance: float) -> tuple[float, float]:
    remaining = distance
    for start, end in zip(shape, shape[1:]):
        segment = math.dist(start, end)
        if remaining <= segment and segment > 0:
            ratio = remaining / segment
            return (start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio)
        remaining -= segment
    return shape[-1]
