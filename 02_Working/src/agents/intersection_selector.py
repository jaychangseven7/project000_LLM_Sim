from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from pathlib import Path
import xml.etree.ElementTree as ET

import sumolib

from src.utils.config_loader import resolve_path


@dataclass
class IntersectionCandidate:
    junction_id: str
    incoming_edges: list[str]
    outgoing_edges: list[str]
    x: float
    y: float
    score: float
    route_flow: int
    complexity: float
    degree: int
    lane_count: int
    major_edges: int


def select_intersections(net_file: str | Path, config: dict) -> list[IntersectionCandidate]:
    agents_cfg = config.get("agents", {})
    limit = int(agents_cfg.get("max_controlled_intersections", 10))
    net = sumolib.net.readNet(str(net_file), withInternal=False)
    route_flow = _load_route_flow(config)
    candidates = _build_candidates(net, route_flow, agents_cfg)

    manual_ids = [str(item) for item in agents_cfg.get("manual_intersection_ids", []) if str(item).strip()]
    if manual_ids:
        by_id = {candidate.junction_id: candidate for candidate in candidates}
        return [by_id[junction_id] for junction_id in manual_ids if junction_id in by_id][:limit]

    strategy = agents_cfg.get("selection_strategy", "hub_flow")
    if strategy == "legacy_length":
        candidates.sort(key=lambda item: sum(_edge_length(net, edge_id) for edge_id in item.incoming_edges), reverse=True)
    else:
        candidates.sort(key=lambda item: item.score, reverse=True)

    min_spacing = float(agents_cfg.get("min_agent_spacing_meters", 350))
    selected: list[IntersectionCandidate] = []
    for candidate in candidates:
        if all(hypot(candidate.x - other.x, candidate.y - other.y) >= min_spacing for other in selected):
            selected.append(candidate)
            if len(selected) >= limit:
                break

    if len(selected) < limit:
        selected_ids = {candidate.junction_id for candidate in selected}
        for candidate in candidates:
            if candidate.junction_id not in selected_ids:
                selected.append(candidate)
                selected_ids.add(candidate.junction_id)
                if len(selected) >= limit:
                    break

    return selected


def log_selected_intersections(candidates: list[IntersectionCandidate]) -> None:
    for idx, candidate in enumerate(candidates, start=1):
        print(
            "[IntersectionSelector] "
            f"#{idx:02d} junction={candidate.junction_id}, "
            f"score={candidate.score:.1f}, flow={candidate.route_flow}, "
            f"in/out={len(candidate.incoming_edges)}/{len(candidate.outgoing_edges)}, "
            f"lanes={candidate.lane_count}, major_edges={candidate.major_edges}",
            flush=True,
        )


def _build_candidates(net, route_flow: dict[str, int], agents_cfg: dict) -> list[IntersectionCandidate]:
    min_incoming = int(agents_cfg.get("min_incoming_edges", 2))
    min_outgoing = int(agents_cfg.get("min_outgoing_edges", 2))
    weights = agents_cfg.get("selection_weights", {})
    flow_weight = float(weights.get("route_flow", 0.45))
    complexity_weight = float(weights.get("complexity", 0.45))
    major_weight = float(weights.get("major_edges", 8.0))
    degree_weight = float(weights.get("degree", 2.0))

    candidates: list[IntersectionCandidate] = []
    for node in net.getNodes():
        incoming = _passenger_edges(node.getIncoming())
        outgoing = _passenger_edges(node.getOutgoing())
        if len(incoming) < min_incoming or len(outgoing) < min_outgoing:
            continue

        local_edges = incoming + outgoing
        degree = len(local_edges)
        lane_count = sum(len(edge.getLanes()) for edge in local_edges)
        major_edges = sum(1 for edge in local_edges if len(edge.getLanes()) >= 2 or edge.getSpeed() >= 13.9)
        edge_flow = sum(route_flow.get(edge.getID(), 0) for edge in local_edges)
        total_length = sum(edge.getLength() for edge in local_edges)
        complexity = degree * lane_count + major_edges * 4 + min(total_length / 100, 20)
        score = (
            edge_flow * flow_weight
            + complexity * complexity_weight
            + major_edges * major_weight
            + degree * degree_weight
        )
        x, y = node.getCoord()
        candidates.append(
            IntersectionCandidate(
                junction_id=node.getID(),
                incoming_edges=[edge.getID() for edge in incoming],
                outgoing_edges=[edge.getID() for edge in outgoing],
                x=x,
                y=y,
                score=score,
                route_flow=edge_flow,
                complexity=complexity,
                degree=degree,
                lane_count=lane_count,
                major_edges=major_edges,
            )
        )
    return candidates


def _load_route_flow(config: dict) -> dict[str, int]:
    route_file_value = config.get("map", {}).get("route_file")
    if not route_file_value:
        return {}

    route_file = resolve_path(route_file_value)
    if not route_file.exists():
        return {}

    flow: dict[str, int] = {}
    try:
        for _, elem in ET.iterparse(route_file, events=("end",)):
            if elem.tag == "route":
                for edge_id in (elem.get("edges") or "").split():
                    if not edge_id.startswith(":"):
                        flow[edge_id] = flow.get(edge_id, 0) + 1
            elem.clear()
    except ET.ParseError:
        return {}
    return flow


def _passenger_edges(edges) -> list:
    return [edge for edge in edges if not edge.getID().startswith(":") and edge.allows("passenger")]


def _edge_length(net, edge_id: str) -> float:
    try:
        return net.getEdge(edge_id).getLength()
    except Exception:
        return 0.0
