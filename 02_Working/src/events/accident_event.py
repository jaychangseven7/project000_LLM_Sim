from __future__ import annotations

from src.utils.logger import sim_time_to_hhmm


class AccidentEvent:
    def __init__(self, event_cfg: dict, default_edge: str | None = None) -> None:
        self.cfg = event_cfg
        self.event_id = event_cfg["event_id"]
        self.start_time = int(event_cfg["start_time"])
        self.end_time = int(event_cfg["end_time"])
        self.speed_limit = float(event_cfg.get("speed_limit", 2.0))
        self.severity = event_cfg.get("severity", "medium")
        self.description = event_cfg.get("description", "")
        self.affected_edges = list(event_cfg.get("affected_edges") or ([default_edge] if default_edge else []))
        self.active = False
        self.completed = False
        self.original_speeds: dict[str, float] = {}
        self.original_lane_speeds: dict[str, float] = {}

    def maybe_activate(self, traci_conn, current_time: float) -> dict | None:
        if self.completed:
            return self.state() if self.active else None
        if not self.active and current_time >= self.start_time:
            self.active = True
            changed = 0
            for edge_id in self.affected_edges:
                changed += self._set_edge_speed(traci_conn, edge_id)
            print(
                f"[Event] time={sim_time_to_hhmm(current_time)} id={self.event_id} type=accident "
                f"severity={self.severity} speed_limit={self.speed_limit} affected_edges={changed} "
                f"duration={(self.end_time - self.start_time) // 60}min desc={self.description}",
                flush=True,
            )
            print(f"[Event] affected_edge_sample={','.join(self.affected_edges[:5]) or '-'}", flush=True)
        if self.active and current_time >= self.end_time:
            restored = 0
            for edge_id, speed in self.original_speeds.items():
                try:
                    traci_conn.edge.setMaxSpeed(edge_id, speed)
                    restored += 1
                except Exception:
                    pass
            for lane_id, speed in self.original_lane_speeds.items():
                try:
                    traci_conn.lane.setMaxSpeed(lane_id, speed)
                    restored += 1
                except Exception:
                    pass
            self.active = False
            self.completed = True
            print(
                f"[Event] time={sim_time_to_hhmm(current_time)} id={self.event_id} type=accident ended "
                f"restored_edges={restored}",
                flush=True,
            )
            return None
        return self.state() if self.active else None

    def state(self) -> dict:
        return {"event_id": self.event_id, "type": "accident", "affected_edges": self.affected_edges}

    def _set_edge_speed(self, traci_conn, edge_id: str) -> int:
        try:
            self.original_speeds[edge_id] = traci_conn.edge.getMaxSpeed(edge_id)
            traci_conn.edge.setMaxSpeed(edge_id, self.speed_limit)
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
                self.original_lane_speeds[lane_id] = traci_conn.lane.getMaxSpeed(lane_id)
                traci_conn.lane.setMaxSpeed(lane_id, self.speed_limit)
                changed += 1
            except Exception:
                continue
        return changed
