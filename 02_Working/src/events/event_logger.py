from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from src.utils.config_loader import resolve_path


EVENT_FIELDS = [
    "sim_time",
    "elapsed_time",
    "event_id",
    "event_type",
    "event_name",
    "status",
    "action",
    "target_edges",
    "target_lanes",
    "severity",
    "affected_vehicle_count",
    "note",
]

METRIC_FIELDS = [
    "sim_time",
    "elapsed_time",
    "active_events",
    "vehicle_count",
    "mean_speed",
    "mean_waiting_time",
    "halted_vehicle_count",
    "congested_edge_count",
    "mean_edge_occupancy",
]


class EventLogger:
    def __init__(
        self,
        output_dir: str | Path,
        interval: float = 5.0,
        enabled: bool = True,
        console_metrics: bool = False,
    ) -> None:
        self.enabled = enabled
        self.console_metrics = console_metrics
        self.output_dir = resolve_path(output_dir)
        self.interval = max(float(interval), 1.0)
        self.last_metric_time = -10**9
        self.event_log_path = self.output_dir / "event_log.csv"
        self.metrics_path = self.output_dir / "traffic_metrics.csv"
        self.summary_path = self.output_dir / "gui_demo_summary.json"
        self.summary: dict[str, Any] = {"events": [], "warnings": [], "runtime": {}}
        if enabled:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self._write_header(self.event_log_path, EVENT_FIELDS)
            self._write_header(self.metrics_path, METRIC_FIELDS)

    def event(
        self,
        sim_time: float,
        elapsed_time: float,
        event,
        action: str,
        affected_vehicle_count: int = 0,
        note: str = "",
    ) -> None:
        if not self.enabled:
            return
        self._append(
            self.event_log_path,
            EVENT_FIELDS,
            {
                "sim_time": f"{sim_time:.1f}",
                "elapsed_time": f"{elapsed_time:.1f}",
                "event_id": event.event_id,
                "event_type": event.event_type,
                "event_name": event.name,
                "status": event.status,
                "action": action,
                "target_edges": "|".join(event.target_edges),
                "target_lanes": "|".join(event.target_lanes),
                "severity": f"{event.severity:.3f}",
                "affected_vehicle_count": affected_vehicle_count,
                "note": note,
            },
        )

    def maybe_metrics(
        self,
        traci_conn,
        sim_time: float,
        elapsed_time: float,
        active_events: list,
        congestion_speed_threshold: float,
    ) -> None:
        if not self.enabled or elapsed_time - self.last_metric_time < self.interval:
            return
        self.last_metric_time = elapsed_time
        vehicle_ids = list(traci_conn.vehicle.getIDList())
        speeds: list[float] = []
        waiting: list[float] = []
        halted = 0
        road_ids: set[str] = set()
        for vehicle_id in vehicle_ids:
            try:
                speed = float(traci_conn.vehicle.getSpeed(vehicle_id))
                speeds.append(speed)
                waiting.append(float(traci_conn.vehicle.getWaitingTime(vehicle_id)))
                halted += int(speed < 0.1)
                edge_id = traci_conn.vehicle.getRoadID(vehicle_id)
                if edge_id and not edge_id.startswith(":"):
                    road_ids.add(edge_id)
            except Exception:
                continue

        occupancies: list[float] = []
        congested = 0
        for edge_id in road_ids:
            try:
                edge_speed = float(traci_conn.edge.getLastStepMeanSpeed(edge_id))
                occupancies.append(float(traci_conn.edge.getLastStepOccupancy(edge_id)))
                congested += int(0 <= edge_speed < congestion_speed_threshold)
            except Exception:
                continue

        row = {
            "sim_time": f"{sim_time:.1f}",
            "elapsed_time": f"{elapsed_time:.1f}",
            "active_events": "|".join(event.event_id for event in active_events),
            "vehicle_count": len(vehicle_ids),
            "mean_speed": f"{_mean(speeds):.3f}",
            "mean_waiting_time": f"{_mean(waiting):.3f}",
            "halted_vehicle_count": halted,
            "congested_edge_count": congested,
            "mean_edge_occupancy": f"{_mean(occupancies):.3f}",
        }
        self._append(self.metrics_path, METRIC_FIELDS, row)
        if self.console_metrics:
            print(
                f"[Metrics] elapsed={elapsed_time:.0f}s veh={len(vehicle_ids)} "
                f"mean_speed={_mean(speeds):.2f} halted={halted} congested_edges={congested}",
                flush=True,
            )

    def register_events(self, events: list) -> None:
        self.summary["events"] = [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "start_time": event.start_time,
                "end_time": event.end_time,
                "target_edges": event.target_edges,
                "target_lanes": event.target_lanes,
                "warnings": event.warnings,
            }
            for event in events
        ]
        self.summary["warnings"] = [warning for event in events for warning in event.warnings]
        self.flush_summary()

    def update_runtime(self, **values: Any) -> None:
        self.summary["runtime"].update(values)

    def flush_summary(self) -> None:
        if not self.enabled:
            return
        self.summary_path.write_text(
            json.dumps(self.summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _write_header(path: Path, fields: list[str]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            csv.DictWriter(handle, fieldnames=fields).writeheader()

    @staticmethod
    def _append(path: Path, fields: list[str], row: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8-sig", newline="") as handle:
            csv.DictWriter(handle, fieldnames=fields).writerow(row)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
