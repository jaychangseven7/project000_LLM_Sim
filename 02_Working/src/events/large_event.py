from __future__ import annotations

import random

from src.utils.logger import sim_time_to_hhmm


class LargeEvent:
    def __init__(self, event_cfg: dict, edge_sampler, seed: int = 42) -> None:
        self.cfg = event_cfg
        self.edge_sampler = edge_sampler
        self.rng = random.Random(seed)
        self.event_id = event_cfg["event_id"]
        self.start_time = int(event_cfg["start_time"])
        self.end_time = int(event_cfg["end_time"])
        self.target_zone = event_cfg.get("target_zone", "event_zone")
        self.vehicle_count = int(event_cfg.get("vehicle_count", 250))
        self.vehicle_type = event_cfg.get("vehicle_type", "event_vehicle")
        self.severity = event_cfg.get("severity", "medium")
        self.description = event_cfg.get("description", "")
        self.injected = False
        self.completed = False
        self.injected_count = 0

    def maybe_activate(self, traci_conn, current_time: float) -> dict | None:
        if self.completed:
            return None
        if not self.injected and current_time >= self.start_time:
            self.injected = True
            self.injected_count = self._inject_vehicles(traci_conn, current_time)
            print(
                f"[Event] time={sim_time_to_hhmm(current_time)} id={self.event_id} type=large_event "
                f"zone={self.target_zone} requested_vehicles={self.vehicle_count} "
                f"injected_vehicles={self.injected_count} duration={(self.end_time - self.start_time) // 60}min "
                f"desc={self.description}",
                flush=True,
            )
        if self.injected and current_time <= self.end_time:
            return {
                "event_id": self.event_id,
                "type": "large_event",
                "affected_edges": self.edge_sampler.zones.get(self.target_zone, [])[:5],
            }
        if self.injected and current_time > self.end_time:
            print(
                f"[Event] time={sim_time_to_hhmm(current_time)} id={self.event_id} type=large_event ended",
                flush=True,
            )
            self.injected = False
            self.completed = True
        return None

    def _inject_vehicles(self, traci_conn, current_time: float) -> int:
        injected = 0
        for idx in range(self.vehicle_count):
            from_edge, to_edge = self.edge_sampler.sample_reachable_pair(self.target_zone, "residential_zone")
            route_id = f"{self.event_id}_route_{idx:03d}"
            vehicle_id = f"{self.event_id}_vehicle_{idx:03d}"
            depart = current_time + self.rng.randint(0, 45 * 60)
            try:
                route = traci_conn.simulation.findRoute(from_edge, to_edge, self.vehicle_type)
                if not route.edges:
                    continue
                traci_conn.route.add(route_id, list(route.edges))
                traci_conn.vehicle.add(vehicle_id, route_id, typeID=self.vehicle_type, depart=str(depart))
                injected += 1
            except Exception:
                continue
        return injected
