from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


SUPPORTED_EVENT_TYPES = {"accident", "road_closure", "large_event"}

KW_ACTIVITY = ("\u6d3b\u52a8", "\u4f53\u80b2", "\u6f14\u5531", "\u6563\u573a", "event")
KW_BUSINESS = ("\u5546\u52a1", "CBD", "\u4e0a\u73ed", "business")
KW_SCHOOL = ("\u5b66\u6821", "student", "school")
KW_SHOPPING = ("\u5546\u573a", "\u8d2d\u7269", "shopping")


@dataclass
class EventSpec:
    event_id: str
    type: str
    start_time: int
    end_time: int
    location: str = ""
    zone: str = ""
    affected_edges: list[str] = field(default_factory=list)
    severity: str = "medium"
    parameters: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventSpec":
        event_type = str(data.get("type", "")).strip()
        params = dict(data.get("parameters") or {})
        return cls(
            event_id=str(data.get("event_id") or f"{event_type}_generated"),
            type=event_type,
            start_time=_parse_time_value(data.get("start_time", 18 * 3600)),
            end_time=_parse_time_value(data.get("end_time", 19 * 3600)),
            location=str(data.get("location", "")),
            zone=str(data.get("zone") or data.get("target_zone") or ""),
            affected_edges=list(data.get("affected_edges") or []),
            severity=str(data.get("severity", "medium")),
            parameters=params,
            description=str(data.get("description", "")),
        )

    def to_event_config(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "event_id": self.event_id,
            "type": self.type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "affected_edges": self.affected_edges,
            "severity": self.severity,
            "description": self.description,
        }
        if self.type == "large_event":
            item["target_zone"] = self.zone or self.parameters.get("target_zone", "event_zone")
            item["vehicle_count"] = int(self.parameters.get("vehicle_count", 250))
            item["vehicle_type"] = self.parameters.get("vehicle_type", "event_vehicle")
        elif self.type == "accident":
            item["speed_limit"] = float(self.parameters.get("speed_limit", _speed_limit_for_severity(self.severity)))
        elif self.type == "road_closure":
            item["closure_mode"] = self.parameters.get("closure_mode", "all")
        return item


def normalize_event_specs(raw_events: list[dict[str, Any]], edge_sampler=None) -> list[EventSpec]:
    specs = []
    for idx, raw in enumerate(raw_events, start=1):
        spec = EventSpec.from_dict(raw)
        if spec.type not in SUPPORTED_EVENT_TYPES:
            continue
        if spec.end_time <= spec.start_time:
            spec.end_time = spec.start_time + 45 * 60
        if not spec.event_id or spec.event_id == f"{spec.type}_generated":
            spec.event_id = f"{spec.type}_{idx:02d}_{spec.start_time}"
        spec.affected_edges = _valid_edges(spec.affected_edges, edge_sampler)
        if not spec.affected_edges and spec.type in {"accident", "road_closure"} and edge_sampler is not None:
            spec.affected_edges = edge_sampler.main_edges(1)
        if not spec.zone:
            spec.zone = _zone_from_text(spec.location or spec.description)
        specs.append(spec)
    return specs


def specs_to_yaml_data(specs: list[EventSpec]) -> dict[str, list[dict[str, Any]]]:
    return {"events": [spec.to_event_config() for spec in specs]}


def _parse_time_value(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value)
    match = re.search(r"(\d{1,2})[:\uff1a](\d{1,2})", text)
    if match:
        return int(match.group(1)) * 3600 + int(match.group(2)) * 60
    match = re.search(r"(\d{1,2})\s*(?:\u70b9|\u65f6|h)", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1)) * 3600
    return 18 * 3600


def _speed_limit_for_severity(severity: str) -> float:
    return {"low": 6.0, "medium": 4.0, "high": 2.0, "severe": 1.5}.get(severity, 3.0)


def _valid_edges(edge_ids: list[str], edge_sampler) -> list[str]:
    if edge_sampler is None:
        return [str(edge_id) for edge_id in edge_ids]
    return [str(edge_id) for edge_id in edge_ids if str(edge_id) in edge_sampler.edge_by_id]


def _zone_from_text(text: str) -> str:
    if any(keyword in text for keyword in KW_ACTIVITY):
        return "event_zone"
    if any(keyword in text for keyword in KW_BUSINESS):
        return "business_zone"
    if any(keyword in text for keyword in KW_SCHOOL):
        return "school_zone"
    if any(keyword in text for keyword in KW_SHOPPING):
        return "shopping_zone"
    return "event_zone"
