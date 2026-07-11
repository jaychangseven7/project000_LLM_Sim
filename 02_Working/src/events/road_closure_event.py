from __future__ import annotations

from src.utils.logger import sim_time_to_hhmm


class RoadClosureEvent:
    def __init__(self, event_cfg: dict, default_edge: str | None = None) -> None:
        self.cfg = event_cfg
        self.event_id = event_cfg["event_id"]
        self.start_time = int(event_cfg["start_time"])
        self.end_time = int(event_cfg["end_time"])
        self.severity = event_cfg.get("severity", "medium")
        self.description = event_cfg.get("description", "")
        self.affected_edges = list(event_cfg.get("affected_edges") or ([default_edge] if default_edge else []))
        self.active = False
        self.completed = False
        self.original_allowed: dict[str, list[str]] = {}
        self.original_lane_allowed: dict[str, list[str]] = {}

    def maybe_activate(self, traci_conn, current_time: float) -> dict | None:
        if self.completed:
            return self.state() if self.active else None
        if not self.active and current_time >= self.start_time:
            self.active = True
            changed = 0
            for edge_id in self.affected_edges:
                changed += self._close_edge(traci_conn, edge_id)
            print(
                f"[Event] time={sim_time_to_hhmm(current_time)} id={self.event_id} type=road_closure "
                f"severity={self.severity} affected_edges={changed} "
                f"duration={(self.end_time - self.start_time) // 60}min desc={self.description}",
                flush=True,
            )
            print(f"[Event] affected_edge_sample={','.join(self.affected_edges[:5]) or '-'}", flush=True)
        if self.active and current_time >= self.end_time:
            restored = 0
            for edge_id, allowed in self.original_allowed.items():
                try:
                    traci_conn.edge.setAllowed(edge_id, allowed)
                    restored += 1
                except Exception:
                    pass
            for lane_id, allowed in self.original_lane_allowed.items():
                try:
                    traci_conn.lane.setAllowed(lane_id, allowed)
                    restored += 1
                except Exception:
                    pass
            self.active = False
            self.completed = True
            print(
                f"[Event] time={sim_time_to_hhmm(current_time)} id={self.event_id} type=road_closure ended "
                f"restored_edges={restored}",
                flush=True,
            )
            return None
        return self.state() if self.active else None

    def state(self) -> dict:
        return {"event_id": self.event_id, "type": "road_closure", "affected_edges": self.affected_edges}

    def _close_edge(self, traci_conn, edge_id: str) -> int:
        try:
            self.original_allowed[edge_id] = traci_conn.edge.getAllowed(edge_id)
            traci_conn.edge.setAllowed(edge_id, [])
            return 1
        except Exception:
            pass

        changed = 0
        try:
            lane_count = traci_conn.edge.getLaneNumber(edge_id)
        except Exception:
            lane_count = 0
        for lane_idx in range(lane_count):
            lane_id = f"{edge_id}_{lane_idx}"
            try:
                self.original_lane_allowed[lane_id] = traci_conn.lane.getAllowed(lane_id)
                traci_conn.lane.setAllowed(lane_id, [])
                changed += 1
            except Exception:
                continue
        return changed
