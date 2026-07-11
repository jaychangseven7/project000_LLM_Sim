from __future__ import annotations

import math
from typing import Any


DEFAULT_COLORS = {
    "concert": (180, 80, 255, 255),
    "large_event": (180, 80, 255, 255),
    "construction": (255, 165, 0, 255),
    "road_closure": (255, 165, 0, 255),
    "accident": (230, 30, 30, 255),
    "weather": (20, 80, 210, 255),
    "rush_hour": (240, 60, 40, 255),
}


class EventVisualizer:
    """Draw only a thin, unfilled event-area ring.

    Camera movement, POIs, road overlays, vehicle highlights, and tracking
    markers are deliberately excluded so the user retains full camera control.
    """

    def __init__(self, route_utils, config: dict[str, Any]) -> None:
        self.route_utils = route_utils
        self.enabled = bool(config.get("enable_gui_visualization", True))
        self.bounds = tuple(float(value) for value in route_utils.net.getBoundary())
        self.global_center = (
            (self.bounds[0] + self.bounds[2]) / 2,
            (self.bounds[1] + self.bounds[3]) / 2,
        )
        self.gui_available = True
        self._legacy_markers_removed = False
        self._centers: dict[str, tuple[float, float]] = {}
        self._visible_events: set[str] = set()

    def start(self, traci_conn, event, elapsed_time: float) -> None:
        if not self.enabled or not self.gui_available:
            return
        center = self.route_utils.event_center(event)
        self._centers[event.event_id] = center
        try:
            self._add_ring(traci_conn, event, center)
        except Exception as exc:
            self.gui_available = False
            print(f"[EventVisualizer][warning] GUI event ring unavailable: {exc}", flush=True)

    def update(self, traci_conn, event, vehicle_ids: list[str], elapsed_time: float) -> None:
        if not self.enabled or not self.gui_available:
            return
        positions: list[tuple[float, float]] = []
        for vehicle_id in vehicle_ids:
            try:
                x, y = traci_conn.vehicle.getPosition(vehicle_id)
                point = (float(x), float(y))
                if self._valid_position(*point):
                    positions.append(point)
            except Exception:
                continue

        if not positions:
            self._remove_ring(traci_conn, event)
            return

        radius = float(event.radius or 120.0)
        center = self._centers.get(event.event_id)
        if center is None or not any(math.dist(center, point) <= radius for point in positions):
            # Choose the affected vehicle surrounded by the largest local
            # cluster. The ring moves, but the GUI camera never does.
            center = max(
                positions,
                key=lambda candidate: sum(
                    math.dist(candidate, other) <= radius for other in positions
                ),
            )
            self._centers[event.event_id] = center
            shape = _circle(center, radius)
            try:
                if event.event_id in self._visible_events:
                    traci_conn.polygon.setShape(self._polygon_id(event), shape)
                else:
                    self._add_ring(traci_conn, event, center)
            except Exception as exc:
                self.gui_available = False
                print(f"[EventVisualizer][warning] GUI event ring update unavailable: {exc}", flush=True)
        elif event.event_id not in self._visible_events:
            try:
                self._add_ring(traci_conn, event, center)
            except Exception as exc:
                self.gui_available = False
                print(f"[EventVisualizer][warning] GUI event ring update unavailable: {exc}", flush=True)

    def focus_on_vehicles(
        self,
        traci_conn,
        event,
        vehicle_ids: list[str],
        elapsed_time: float,
    ) -> bool:
        # Compatibility no-op: camera and vehicle following are user-controlled.
        return False

    def end(self, traci_conn, event) -> None:
        if not self.enabled or not self.gui_available:
            return
        try:
            traci_conn.polygon.remove(self._polygon_id(event))
        except Exception:
            pass
        self._visible_events.discard(event.event_id)
        self._centers.pop(event.event_id, None)

    def step(self, traci_conn, elapsed_time: float) -> None:
        if not self.enabled or self._legacy_markers_removed:
            return
        # Keep IntersectionAgent POIs/diamonds visible. Remove only overlays
        # left by older event-demo versions.
        try:
            legacy_polygon_prefixes = (
                "event_vehicle_marker_",
                "event_edge_",
            )
            for polygon_id in list(traci_conn.polygon.getIDList()):
                if str(polygon_id).startswith(legacy_polygon_prefixes):
                    traci_conn.polygon.remove(polygon_id)
            self._legacy_markers_removed = True
        except Exception as exc:
            self._legacy_markers_removed = True
            print(f"[EventVisualizer][warning] legacy marker cleanup unavailable: {exc}", flush=True)

    def _add_ring(
        self,
        traci_conn,
        event,
        center: tuple[float, float],
    ) -> None:
        color = _color(
            event.visual.get("color"),
            DEFAULT_COLORS.get(event.event_type, (255, 0, 255, 255)),
        )
        outline = (color[0], color[1], color[2], 255)
        traci_conn.polygon.add(
            self._polygon_id(event),
            _circle(center, float(event.radius or 120.0)),
            outline,
            fill=False,
            polygonType="traffic_event_area",
            layer=int(event.visual.get("area_layer", 200)),
            lineWidth=float(event.visual.get("line_width", 2)),
        )
        self._visible_events.add(event.event_id)

    def _remove_ring(self, traci_conn, event) -> None:
        if event.event_id not in self._visible_events:
            return
        try:
            traci_conn.polygon.remove(self._polygon_id(event))
        except Exception:
            pass
        self._visible_events.discard(event.event_id)

    def _valid_position(self, x: float, y: float) -> bool:
        if not (math.isfinite(x) and math.isfinite(y)):
            return False
        margin = 200.0
        return (
            self.bounds[0] - margin <= x <= self.bounds[2] + margin
            and self.bounds[1] - margin <= y <= self.bounds[3] + margin
        )

    @staticmethod
    def _polygon_id(event) -> str:
        return f"event_area_{event.event_id}"


def _color(value: Any, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return default
    values = list(value[:4])
    if len(values) == 3:
        values.append(255)
    return tuple(max(0, min(int(channel), 255)) for channel in values)


def _circle(
    center: tuple[float, float],
    radius: float,
) -> list[tuple[float, float]]:
    x, y = center
    points = [
        (
            x + radius * math.cos(2 * math.pi * index / 40),
            y + radius * math.sin(2 * math.pi * index / 40),
        )
        for index in range(40)
    ]
    points.append(points[0])
    return points
