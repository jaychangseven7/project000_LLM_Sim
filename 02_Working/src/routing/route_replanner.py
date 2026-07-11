from __future__ import annotations

from src.agents.intersection_agent import AgentDecision
from src.utils.logger import sim_time_to_hhmm


REROUTED_COLOR = (160, 32, 240, 255)


class RouteReplanner:
    def __init__(self, config: dict) -> None:
        routing_cfg = config["routing"]
        self.enabled = routing_cfg.get("use_rerouting", True)
        self.interval = int(routing_cfg.get("rerouting_interval", 300))
        self.max_per_interval = int(routing_cfg.get("max_rerouted_vehicles_per_interval", 80))
        self.last_time = -10**9
        self.rerouted: set[str] = set()

    def step(self, traci_conn, current_time: float, active_events: list[dict], decisions: list[AgentDecision]) -> int:
        if not self.enabled or current_time - self.last_time < self.interval:
            return 0

        avoid_edges: set[str] = set()
        source_agents = [decision for decision in decisions if decision.action == "reroute_guidance"]
        for decision in source_agents:
            avoid_edges.update(decision.avoid_edges)

        if not avoid_edges:
            return 0

        self.last_time = current_time
        candidates = self._vehicles_on_or_near_edges(traci_conn, avoid_edges)
        count = 0
        for vehicle_id in candidates:
            if count >= self.max_per_interval:
                break
            if vehicle_id in self.rerouted:
                continue
            try:
                traci_conn.vehicle.rerouteTraveltime(vehicle_id)
                traci_conn.vehicle.setColor(vehicle_id, REROUTED_COLOR)
            except Exception:
                continue
            self.rerouted.add(vehicle_id)
            count += 1

        source_ids = ",".join(decision.agent_id.replace("intersection_", "") for decision in source_agents[:3]) or "-"
        print(
            f"[RouteReplanner] time={sim_time_to_hhmm(current_time)} rerouted={count} "
            f"source_agents={len(source_agents)} source_sample={source_ids} "
            f"avoid_edges={len(avoid_edges)} max_per_interval={self.max_per_interval}",
            flush=True,
        )
        return count

    def _vehicles_on_or_near_edges(self, traci_conn, avoid_edges: set[str]) -> list[str]:
        candidates: list[str] = []
        for edge_id in avoid_edges:
            try:
                candidates.extend(traci_conn.edge.getLastStepVehicleIDs(edge_id))
            except Exception:
                continue
        if len(candidates) < self.max_per_interval:
            for vehicle_id in traci_conn.vehicle.getIDList():
                if vehicle_id not in candidates:
                    candidates.append(vehicle_id)
                if len(candidates) >= self.max_per_interval * 2:
                    break
        return candidates
