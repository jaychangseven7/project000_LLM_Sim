from __future__ import annotations

from collections.abc import Callable

from src.events.accident_event import AccidentEvent
from src.events.large_event import LargeEvent
from src.events.road_closure_event import RoadClosureEvent


EventFactory = Callable[[dict, object, dict], object]


def _accident_factory(event_cfg: dict, edge_sampler, config: dict):
    default_edge = edge_sampler.main_edges(1)[0] if edge_sampler is not None else None
    return AccidentEvent(event_cfg, default_edge=default_edge)


def _road_closure_factory(event_cfg: dict, edge_sampler, config: dict):
    default_edge = edge_sampler.main_edges(1)[0] if edge_sampler is not None else None
    return RoadClosureEvent(event_cfg, default_edge=default_edge)


def _large_event_factory(event_cfg: dict, edge_sampler, config: dict):
    return LargeEvent(event_cfg, edge_sampler=edge_sampler, seed=config.get("demo", {}).get("random_seed", 42))


EVENT_REGISTRY: dict[str, EventFactory] = {
    "accident": _accident_factory,
    "road_closure": _road_closure_factory,
    "large_event": _large_event_factory,
}


def create_event(event_cfg: dict, edge_sampler, config: dict):
    event_type = event_cfg.get("type")
    factory = EVENT_REGISTRY.get(event_type)
    if factory is None:
        print(f"[Scenario] Unsupported event type skipped: {event_type}", flush=True)
        return None
    return factory(event_cfg, edge_sampler, config)
