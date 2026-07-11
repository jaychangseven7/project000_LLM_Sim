from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import sumolib


@dataclass
class EdgeInfo:
    edge_id: str
    x: float
    y: float
    length: float
    speed: float
    outgoing: int


class EdgeSampler:
    def __init__(self, net_file: str | Path, seed: int = 42) -> None:
        self.rng = random.Random(seed)
        self.net = sumolib.net.readNet(str(net_file), withInternal=False)
        self.edges = self._load_passenger_edges()
        self.edge_by_id = {edge.getID(): edge for edge in self.net.getEdges() if not edge.getID().startswith(":")}
        if len(self.edges) < 10:
            raise RuntimeError("可用 passenger 道路太少，请换一个更密集的城市地图区域。")
        self.zones = self._build_zones()

    def _load_passenger_edges(self) -> list[EdgeInfo]:
        edges: list[EdgeInfo] = []
        for edge in self.net.getEdges():
            if edge.getID().startswith(":"):
                continue
            if not edge.allows("passenger"):
                continue
            shape = edge.getShape()
            if not shape:
                continue
            x = sum(p[0] for p in shape) / len(shape)
            y = sum(p[1] for p in shape) / len(shape)
            edges.append(
                EdgeInfo(
                    edge_id=edge.getID(),
                    x=x,
                    y=y,
                    length=edge.getLength(),
                    speed=edge.getSpeed(),
                    outgoing=len(edge.getOutgoing()),
                )
            )
        return edges

    def _build_zones(self) -> dict[str, list[str]]:
        by_x = sorted(self.edges, key=lambda e: e.x)
        by_y = sorted(self.edges, key=lambda e: e.y)
        n = len(self.edges)
        residential = by_x[: max(8, n // 4)]
        business = by_x[(n * 3) // 5 :]
        school = by_y[: max(8, n // 4)]
        shopping = by_y[(n * 2) // 5 : (n * 4) // 5]
        central = sorted(self.edges, key=lambda e: e.length * max(1, e.outgoing), reverse=True)[: max(8, n // 5)]
        return {
            "residential_zone": [e.edge_id for e in residential],
            "business_zone": [e.edge_id for e in business],
            "school_zone": [e.edge_id for e in school],
            "shopping_zone": [e.edge_id for e in shopping],
            "event_zone": [e.edge_id for e in central],
        }

    def sample_edge(self, zone: str) -> str:
        candidates = self.zones.get(zone) or [e.edge_id for e in self.edges]
        return self.rng.choice(candidates)

    def sample_reachable_pair(self, origin_zone: str, target_zone: str, attempts: int = 80) -> tuple[str, str]:
        origin_candidates = self.zones.get(origin_zone) or [e.edge_id for e in self.edges]
        target_candidates = self.zones.get(target_zone) or [e.edge_id for e in self.edges]
        for _ in range(attempts):
            from_edge = self.rng.choice(origin_candidates)
            to_edge = self.rng.choice(target_candidates)
            if from_edge == to_edge:
                continue
            if self.is_reachable(from_edge, to_edge):
                return from_edge, to_edge
        return self.rng.choice(origin_candidates), self.rng.choice(target_candidates)

    def is_reachable(self, from_edge: str, to_edge: str) -> bool:
        try:
            origin = self.edge_by_id[from_edge]
            target = self.edge_by_id[to_edge]
            path, _ = self.net.getOptimalPath(origin, target)
            return bool(path)
        except Exception:
            return False

    def main_edges(self, count: int = 10) -> list[str]:
        ranked = sorted(self.edges, key=lambda e: e.length * e.speed * max(1, e.outgoing), reverse=True)
        return [e.edge_id for e in ranked[:count]]
