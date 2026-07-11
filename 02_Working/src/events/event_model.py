from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SEVERITY_LEVELS = {
    "low": 0.25,
    "medium": 0.5,
    "high": 0.8,
    "severe": 1.0,
}


@dataclass
class TrafficEvent:
    event_id: str
    event_type: str
    name: str
    start_time: float
    end_time: float
    severity: float
    target_edges: list[str] = field(default_factory=list)
    target_lanes: list[str] = field(default_factory=list)
    center_edge: str | None = None
    center_lane: str | None = None
    center_pos: float | None = None
    radius: float | None = None
    visual: dict[str, Any] = field(default_factory=dict)
    effects: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    status: str = "pending"
    warnings: list[str] = field(default_factory=list)
    auto_selected: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int = 1) -> "TrafficEvent":
        event_type = str(data.get("event_type") or data.get("type") or "").strip()
        event_id = str(data.get("event_id") or f"{event_type or 'event'}_{index:03d}")
        start_time = float(data.get("start_time", 0))
        end_time = float(data.get("end_time", start_time + 60))
        severity = _severity_value(data.get("severity", 0.5))
        effects = dict(data.get("effects") or data.get("parameters") or {})
        visual = dict(data.get("visual") or {})

        # Backward compatibility for the original three event formats.
        if event_type == "accident":
            effects.setdefault("speed_limit", float(data.get("speed_limit", 2.0)))
            effects.setdefault("enable_reroute", True)
            visual.setdefault("color", [220, 30, 30, 180])
            visual.setdefault("label", "TRAFFIC ACCIDENT")
        elif event_type == "road_closure":
            effects.setdefault("close_lanes", True)
            effects.setdefault("reduce_speed", True)
            effects.setdefault("speed_factor", 0.45)
            effects.setdefault("enable_reroute", True)
            visual.setdefault("color", [255, 165, 0, 180])
            visual.setdefault("label", "ROAD CLOSED")
        elif event_type == "large_event":
            effects.setdefault("max_spawn_count", int(data.get("vehicle_count", 250)))
            effects.setdefault("spawn_interval", 10)
            effects.setdefault("vehicle_type", data.get("vehicle_type", "event_vehicle"))
            effects.setdefault("origin_zone", data.get("target_zone", "event_zone"))
            effects.setdefault("destination_zone", "residential_zone")
            visual.setdefault("color", [180, 80, 255, 160])
            visual.setdefault("label", "LARGE EVENT")

        supplied_edges = list(data.get("target_edges") or data.get("affected_edges") or [])
        return cls(
            event_id=event_id,
            event_type=event_type,
            name=str(data.get("name") or data.get("description") or event_id),
            start_time=start_time,
            end_time=max(end_time, start_time + 1),
            severity=severity,
            target_edges=supplied_edges,
            target_lanes=list(data.get("target_lanes") or []),
            center_edge=data.get("center_edge"),
            center_lane=data.get("center_lane"),
            center_pos=float(data["center_pos"]) if data.get("center_pos") is not None else None,
            radius=float(data["radius"]) if data.get("radius") is not None else None,
            visual=visual,
            effects=effects,
            enabled=bool(data.get("enabled", True)),
            auto_selected=not bool(supplied_edges),
        )

    def state(self) -> dict[str, Any]:
        avoid_edges = list(self.target_edges) if self.effects.get("enable_reroute") else []
        return {
            "event_id": self.event_id,
            "type": self.event_type,
            "event_type": self.event_type,
            "name": self.name,
            "severity": self.severity,
            "affected_edges": list(self.target_edges),
            "avoid_edges": avoid_edges,
            "target_edges": list(self.target_edges),
            "target_lanes": list(self.target_lanes),
            "status": self.status,
        }


def _severity_value(value: Any) -> float:
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            value = SEVERITY_LEVELS.get(value.lower(), 0.5)
    return max(0.0, min(float(value), 1.0))
