from __future__ import annotations

from collections import defaultdict
from typing import Any


EVENT_VEHICLE_COLORS = {
    "concert": (180, 80, 255, 255),
    "large_event": (180, 80, 255, 255),
    "construction": (255, 165, 0, 255),
    "road_closure": (255, 165, 0, 255),
    "accident": (230, 30, 30, 255),
    "weather": (20, 80, 210, 255),
    "rush_hour": (240, 60, 40, 255),
}


class EventEffects:
    """Applies composable TraCI effects and restores the original state safely."""

    def __init__(self, route_utils) -> None:
        self.route_utils = route_utils
        self.base_lane_speed: dict[str, float] = {}
        self.speed_modifiers: dict[str, dict[str, tuple[float, float | None]]] = defaultdict(dict)
        self.base_lane_permissions: dict[str, tuple[list[str], list[str]]] = {}
        self.lane_closures: dict[str, set[str]] = defaultdict(set)
        self.base_vehicle_color: dict[str, tuple[int, int, int, int]] = {}
        self.vehicle_colors: dict[str, dict[str, tuple[float, tuple[int, int, int, int]]]] = defaultdict(dict)
        self.base_vehicle_min_gap: dict[str, float] = {}
        self.vehicle_min_gaps: dict[str, dict[str, float]] = defaultdict(dict)
        self.event_vehicles: dict[str, set[str]] = defaultdict(set)
        self.spawned_vehicle_ids: dict[str, set[str]] = defaultdict(set)
        self.event_rerouted: dict[str, set[str]] = defaultdict(set)
        self.spawned_count: dict[str, int] = defaultdict(int)
        self.phase_spawned_count: dict[tuple[str, str], int] = defaultdict(int)
        self.next_spawn_time: dict[str, float] = {}
        self.next_reroute_time: dict[str, float] = {}
        self.warned_no_route: set[str] = set()
        self.spawn_serial = 0

    def activate(self, traci_conn, event, elapsed_time: float) -> dict[str, int]:
        changed_lanes = self._apply_lane_effects(traci_conn, event)
        closed_lanes = self._apply_closures(traci_conn, event)
        self.next_spawn_time[event.event_id] = elapsed_time
        self.next_reroute_time[event.event_id] = elapsed_time
        return {"changed_lanes": changed_lanes, "closed_lanes": closed_lanes}

    def update(self, traci_conn, event, elapsed_time: float) -> dict[str, Any]:
        vehicle_ids = self._vehicles_on_edges(traci_conn, event.target_edges)
        self._apply_vehicle_effects(traci_conn, event, vehicle_ids)
        spawned_vehicle_ids = self._maybe_spawn(traci_conn, event, elapsed_time)
        rerouted = self._maybe_reroute(traci_conn, event, vehicle_ids, elapsed_time)
        return {
            "affected_vehicle_count": len(vehicle_ids),
            "vehicle_ids": vehicle_ids,
            "spawned": len(spawned_vehicle_ids),
            "spawned_vehicle_ids": spawned_vehicle_ids,
            "rerouted": rerouted,
            "phase": self._event_phase(event, elapsed_time),
        }

    def deactivate(self, traci_conn, event) -> dict[str, int]:
        restored_lanes = 0
        restored_permissions = 0
        restored_vehicles = 0
        try:
            live_vehicles = set(traci_conn.vehicle.getIDList())
        except Exception:
            live_vehicles = set()

        for lane_id in list(self.speed_modifiers):
            modifiers = self.speed_modifiers[lane_id]
            if event.event_id not in modifiers:
                continue
            del modifiers[event.event_id]
            restored_lanes += int(self._refresh_lane_speed(traci_conn, lane_id))
            if not modifiers:
                self.speed_modifiers.pop(lane_id, None)
                self.base_lane_speed.pop(lane_id, None)

        for lane_id in list(self.lane_closures):
            closures = self.lane_closures[lane_id]
            if event.event_id not in closures:
                continue
            closures.discard(event.event_id)
            if not closures:
                restored_permissions += int(self._restore_lane_permissions(traci_conn, lane_id))
                self.lane_closures.pop(lane_id, None)
                self.base_lane_permissions.pop(lane_id, None)

        for vehicle_id in list(self.vehicle_colors):
            modifiers = self.vehicle_colors[vehicle_id]
            if event.event_id in modifiers:
                del modifiers[event.event_id]
                if vehicle_id in live_vehicles:
                    restored_vehicles += int(self._refresh_vehicle_color(traci_conn, vehicle_id))
            if not modifiers:
                self.vehicle_colors.pop(vehicle_id, None)
                self.base_vehicle_color.pop(vehicle_id, None)

        for vehicle_id in list(self.vehicle_min_gaps):
            modifiers = self.vehicle_min_gaps[vehicle_id]
            if event.event_id in modifiers:
                del modifiers[event.event_id]
                if vehicle_id in live_vehicles:
                    self._refresh_vehicle_min_gap(traci_conn, vehicle_id)
            if not modifiers:
                self.vehicle_min_gaps.pop(vehicle_id, None)
                self.base_vehicle_min_gap.pop(vehicle_id, None)

        self.event_vehicles.pop(event.event_id, None)
        self.spawned_vehicle_ids.pop(event.event_id, None)
        self.event_rerouted.pop(event.event_id, None)
        self.next_spawn_time.pop(event.event_id, None)
        self.next_reroute_time.pop(event.event_id, None)
        return {
            "restored_lanes": restored_lanes,
            "restored_permissions": restored_permissions,
            "restored_vehicles": restored_vehicles,
        }

    def restore_all(self, traci_conn, events: list) -> None:
        for event in events:
            if event.status == "active":
                self.deactivate(traci_conn, event)

    def _apply_lane_effects(self, traci_conn, event) -> int:
        effects = event.effects
        factor: float | None = None
        absolute_limit: float | None = None
        if "speed_limit" in effects:
            absolute_limit = max(float(effects["speed_limit"]), 0.1)
        elif effects.get("reduce_speed") or event.event_type in {
            "concert",
            "large_event",
            "construction",
            "weather",
        }:
            factor = max(0.1, min(float(effects.get("speed_factor", 0.75)), 1.0))
        if factor is None and absolute_limit is None:
            return 0

        changed = 0
        edge_factors = event.effects.get("_edge_speed_factors", {})
        for edge_id in event.target_edges:
            edge_factor = float(edge_factors.get(edge_id, factor or 1.0))
            for lane_id in self.route_utils.lanes_for_edges([edge_id]):
                try:
                    self.base_lane_speed.setdefault(
                        lane_id,
                        float(traci_conn.lane.getMaxSpeed(lane_id)),
                    )
                    self.speed_modifiers[lane_id][event.event_id] = (
                        edge_factor,
                        absolute_limit,
                    )
                    changed += int(self._refresh_lane_speed(traci_conn, lane_id))
                except Exception as exc:
                    print(
                        f"[EventEffects][warning] cannot change speed lane={lane_id}: {exc}",
                        flush=True,
                    )
        return changed

    def _apply_closures(self, traci_conn, event) -> int:
        effects = event.effects
        should_close = bool(effects.get("close_lanes") or effects.get("partial_closure"))
        if not should_close:
            return 0
        lanes = list(event.target_lanes)
        if not lanes and effects.get("partial_closure"):
            lanes = self.route_utils._safe_construction_lanes(event.target_edges)
            event.target_lanes = lanes
        if str(effects.get("closure_mode", "")).lower() == "capacity_drop":
            changed = 0
            closure_speed = max(0.1, float(effects.get("closure_speed", 0.5)))
            for lane_id in lanes:
                try:
                    self.base_lane_speed.setdefault(
                        lane_id,
                        float(traci_conn.lane.getMaxSpeed(lane_id)),
                    )
                    self.speed_modifiers[lane_id][event.event_id] = (
                        1.0,
                        closure_speed,
                    )
                    changed += int(self._refresh_lane_speed(traci_conn, lane_id))
                except Exception as exc:
                    print(
                        f"[EventEffects][warning] cannot apply capacity drop lane={lane_id}: {exc}",
                        flush=True,
                    )
            return changed
        changed = 0
        for lane_id in lanes:
            try:
                if lane_id not in self.base_lane_permissions:
                    self.base_lane_permissions[lane_id] = (
                        list(traci_conn.lane.getAllowed(lane_id)),
                        list(traci_conn.lane.getDisallowed(lane_id)),
                    )
                self.lane_closures[lane_id].add(event.event_id)
                traci_conn.lane.setDisallowed(lane_id, ["passenger"])
                changed += 1
            except Exception as exc:
                print(f"[EventEffects][warning] cannot close lane={lane_id}: {exc}", flush=True)
        return changed

    def _refresh_lane_speed(self, traci_conn, lane_id: str) -> bool:
        base = self.base_lane_speed.get(lane_id)
        if base is None:
            return False
        modifiers = self.speed_modifiers.get(lane_id, {})
        target = base
        for factor, absolute in modifiers.values():
            target = min(target, base * factor)
            if absolute is not None:
                target = min(target, absolute)
        try:
            traci_conn.lane.setMaxSpeed(lane_id, target)
            return True
        except Exception:
            return False

    def _restore_lane_permissions(self, traci_conn, lane_id: str) -> bool:
        allowed, disallowed = self.base_lane_permissions.get(lane_id, ([], []))
        try:
            if allowed:
                traci_conn.lane.setAllowed(lane_id, allowed)
            else:
                traci_conn.lane.setDisallowed(lane_id, disallowed)
            return True
        except Exception:
            return False

    def _apply_vehicle_effects(self, traci_conn, event, vehicle_ids: list[str]) -> None:
        color = _color(event.effects.get("vehicle_color"), EVENT_VEHICLE_COLORS.get(event.event_type, (255, 255, 255, 255)))
        headway_factor = 1.0 + event.severity if event.effects.get("increase_headway") else 1.0
        for vehicle_id in vehicle_ids:
            try:
                self.base_vehicle_color.setdefault(vehicle_id, tuple(traci_conn.vehicle.getColor(vehicle_id)))
                self.vehicle_colors[vehicle_id][event.event_id] = (event.severity, color)
                self._refresh_vehicle_color(traci_conn, vehicle_id)
                if headway_factor > 1:
                    self.base_vehicle_min_gap.setdefault(vehicle_id, float(traci_conn.vehicle.getMinGap(vehicle_id)))
                    self.vehicle_min_gaps[vehicle_id][event.event_id] = headway_factor
                    self._refresh_vehicle_min_gap(traci_conn, vehicle_id)
                self.event_vehicles[event.event_id].add(vehicle_id)
            except Exception:
                continue

    def _refresh_vehicle_color(self, traci_conn, vehicle_id: str) -> bool:
        modifiers = self.vehicle_colors.get(vehicle_id, {})
        if modifiers:
            color = max(modifiers.values(), key=lambda item: item[0])[1]
        else:
            color = self.base_vehicle_color.get(vehicle_id)
        if color is None:
            return False
        try:
            traci_conn.vehicle.setColor(vehicle_id, color)
            return True
        except Exception:
            return False

    def _refresh_vehicle_min_gap(self, traci_conn, vehicle_id: str) -> bool:
        base = self.base_vehicle_min_gap.get(vehicle_id)
        if base is None:
            return False
        factors = self.vehicle_min_gaps.get(vehicle_id, {})
        target = base * max(factors.values(), default=1.0)
        try:
            traci_conn.vehicle.setMinGap(vehicle_id, target)
            return True
        except Exception:
            return False

    def _maybe_spawn(self, traci_conn, event, elapsed_time: float) -> list[str]:
        construction_demand = (
            event.event_type in {"construction", "road_closure"}
            and event.effects.get("inject_approach_demand")
        )
        if (
            event.event_type not in {"rush_hour", "concert", "large_event"}
            and not construction_demand
        ):
            return []
        maximum = int(event.effects.get("max_spawn_count", event.effects.get("vehicle_count", 0)))
        if maximum <= 0 or self.spawned_count[event.event_id] >= maximum:
            return []
        interval = max(float(event.effects.get("spawn_interval", 5)), 1.0)
        if elapsed_time + 1e-6 < self.next_spawn_time.get(event.event_id, elapsed_time):
            return []
        self.next_spawn_time[event.event_id] = elapsed_time + interval
        phase = self._event_phase(event, elapsed_time)
        outbound = phase == "outbound"
        phase_maximum = int(
            event.effects.get(
                f"{phase}_max_count",
                maximum,
            )
        )
        phase_key = (event.event_id, phase)
        if self.phase_spawned_count[phase_key] >= phase_maximum:
            return []
        batch = min(
            int(event.effects.get("spawn_batch", 1)),
            maximum - self.spawned_count[event.event_id],
            phase_maximum - self.phase_spawned_count[phase_key],
        )
        spawned_vehicle_ids: list[str] = []
        origins, destinations = self.route_utils.demand_endpoints(event, outbound=outbound)
        vehicle_type = str(event.effects.get("vehicle_type", "event_vehicle"))
        runtime_vehicle_type = vehicle_type
        try:
            known_types = set(traci_conn.vehicletype.getIDList())
            if vehicle_type not in known_types:
                runtime_vehicle_type = "DEFAULT_VEHTYPE"
                warning_key = f"_warned_vtype_{vehicle_type}"
                if not event.effects.get(warning_key):
                    event.effects[warning_key] = True
                    print(
                        f"[EventEffects][warning] vehicle type '{vehicle_type}' is not loaded yet; "
                        "using DEFAULT_VEHTYPE while preserving event color",
                        flush=True,
                    )
        except Exception:
            runtime_vehicle_type = "DEFAULT_VEHTYPE"
        prefix = str(event.effects.get("vehicle_prefix") or event.event_id)
        for _ in range(batch):
            self.spawn_serial += 1
            if construction_demand:
                route = self.route_utils.find_construction_route(
                    event,
                    attempt_offset=self.spawn_serial,
                )
            else:
                route = self.route_utils.find_route(
                    traci_conn,
                    origins,
                    destinations,
                    runtime_vehicle_type,
                    attempt_offset=self.spawn_serial,
                )
            if not route:
                if event.event_id not in self.warned_no_route:
                    self.warned_no_route.add(event.event_id)
                    print(
                        f"[EventEffects][warning] no route found for event={event.event_id}; "
                        "further identical warnings are suppressed",
                        flush=True,
                    )
                continue
            route_id = f"{prefix}_route_{self.spawn_serial:06d}"
            vehicle_id = f"{prefix}_{self.spawn_serial:06d}"
            try:
                traci_conn.route.add(route_id, route)
                traci_conn.vehicle.add(
                    vehicle_id,
                    route_id,
                    typeID=runtime_vehicle_type,
                    depart="now",
                    departLane="best",
                    departSpeed="max",
                )
                traci_conn.vehicle.setColor(
                    vehicle_id,
                    _color(event.effects.get("vehicle_color"), EVENT_VEHICLE_COLORS.get(event.event_type, (255, 255, 255, 255))),
                )
                self.spawned_count[event.event_id] += 1
                self.phase_spawned_count[phase_key] += 1
                self.spawned_vehicle_ids[event.event_id].add(vehicle_id)
                self.event_vehicles[event.event_id].add(vehicle_id)
                spawned_vehicle_ids.append(vehicle_id)
            except Exception as exc:
                print(f"[EventEffects][warning] spawn failed event={event.event_id}: {exc}", flush=True)
        return spawned_vehicle_ids

    @staticmethod
    def _event_phase(event, elapsed_time: float) -> str:
        if event.event_type not in {"concert", "large_event"}:
            return "active"
        phase_ratio = max(
            0.05,
            min(float(event.effects.get("outbound_phase_ratio", 0.5)), 0.95),
        )
        outbound_start = event.start_time + (
            event.end_time - event.start_time
        ) * phase_ratio
        return "outbound" if elapsed_time >= outbound_start else "inbound"

    def _maybe_reroute(
        self,
        traci_conn,
        event,
        vehicle_ids: list[str],
        elapsed_time: float,
    ) -> int:
        if not event.effects.get("enable_reroute"):
            return 0
        if elapsed_time < self.next_reroute_time.get(event.event_id, elapsed_time):
            return 0
        self.next_reroute_time[event.event_id] = elapsed_time + float(event.effects.get("reroute_interval", 15))
        count = 0
        maximum = int(event.effects.get("max_reroute_per_interval", 30))
        for vehicle_id in vehicle_ids:
            if vehicle_id in self.event_rerouted[event.event_id] or count >= maximum:
                continue
            try:
                traci_conn.vehicle.rerouteTraveltime(vehicle_id)
                self.event_rerouted[event.event_id].add(vehicle_id)
                count += 1
            except Exception:
                continue
        return count

    @staticmethod
    def _vehicles_on_edges(traci_conn, edge_ids: list[str]) -> list[str]:
        vehicles: list[str] = []
        seen: set[str] = set()
        for edge_id in edge_ids:
            try:
                for vehicle_id in traci_conn.edge.getLastStepVehicleIDs(edge_id):
                    if vehicle_id not in seen:
                        seen.add(vehicle_id)
                        vehicles.append(vehicle_id)
            except Exception:
                continue
        return vehicles


def _color(value: Any, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return default
    values = list(value[:4])
    if len(values) == 3:
        values.append(255)
    return tuple(max(0, min(int(channel), 255)) for channel in values)
