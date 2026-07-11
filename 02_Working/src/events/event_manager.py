from __future__ import annotations

import math
import secrets
from collections import defaultdict
from typing import Any

from src.events.event_effects import EventEffects
from src.events.event_logger import EventLogger
from src.events.event_model import TrafficEvent
from src.events.event_validator import validate_event_time
from src.events.event_visualizer import EventVisualizer
from src.events.route_utils import RouteUtils
from src.map.edge_sampler import EdgeSampler
from src.utils.config_loader import load_yaml


SUPPORTED_EVENT_TYPES = {
    "concert",
    "construction",
    "weather",
    "rush_hour",
    "accident",
    "road_closure",
    "large_event",
}


class EventManager:
    def __init__(self, config: dict, edge_sampler: EdgeSampler) -> None:
        runtime_cfg = config.get("events", {})
        self.enabled = bool(runtime_cfg.get("use_events", True))
        self.edge_sampler = edge_sampler
        self.events: list[TrafficEvent] = []
        self._traci_conn = None
        self._last_sim_time = 0.0
        self._last_elapsed_time = 0.0
        self._simulation_begin = float(config.get("simulation", {}).get("begin_time", 0))

        event_file = runtime_cfg.get("event_file")
        event_cfg = load_yaml(event_file) if self.enabled and event_file else {}
        global_cfg = dict(event_cfg.get("global") or {})
        global_cfg.update({key: value for key, value in runtime_cfg.items() if key != "event_file"})
        metrics_cfg = dict(event_cfg.get("metrics") or {})
        configured_seed = global_cfg.get("location_seed")
        self.location_seed_configured = configured_seed is not None
        self.location_seed = (
            int(configured_seed) if configured_seed is not None else secrets.randbits(32)
        )
        self.route_utils = RouteUtils(edge_sampler, seed=self.location_seed)
        self.time_mode = str(global_cfg.get("time_mode", "absolute")).lower()
        self.restore_on_end = bool(global_cfg.get("restore_on_end", True))
        self.auto_correct_event_times = bool(
            global_cfg.get("auto_correct_unrealistic_times", True)
        )
        self.congestion_threshold = float(
            metrics_cfg.get("phenomenon_speed_threshold", 8.0)
        )
        self.phenomenon_interval = max(
            1.0, float(metrics_cfg.get("phenomenon_check_interval", 5.0))
        )
        self.minimum_phenomenon_vehicles = max(
            1, int(metrics_cfg.get("phenomenon_min_vehicles", 3))
        )
        self._last_phenomenon_check: dict[str, float] = defaultdict(lambda: -1e9)
        self._reported_phenomena: dict[str, set[str]] = defaultdict(set)
        self._pending_reroutes: dict[str, int] = defaultdict(int)
        self._event_phases: dict[str, str] = {}

        self.effects = EventEffects(self.route_utils)
        self.visualizer = EventVisualizer(self.route_utils, global_cfg)
        self.logger = EventLogger(
            global_cfg.get("output_dir", "../03_Outputs/events"),
            interval=float(metrics_cfg.get("interval", 5)),
            enabled=bool(global_cfg.get("enable_metrics_logging", True)),
            console_metrics=bool(metrics_cfg.get("console_metrics", False)),
        )
        self.logger.update_runtime(location_seed=self.location_seed)

        if self.enabled:
            self._load_events(event_cfg.get("events", []))
        self.logger.register_events(self.events)
        print(
            f"[EventManager] loaded={len(self.events)} time_mode={self.time_mode} "
            f"location_seed={self.location_seed}",
            flush=True,
        )
        seed_note = (
            "已指定固定种子，可复现本次位置。"
            if self.location_seed_configured
            else "未指定 --event-seed，下一次运行将重新选择全路网事件位置。"
        )
        print(f"[事件选址] 本次随机种子={self.location_seed}；{seed_note}", flush=True)

    def step(self, traci_conn, current_time: float) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        self._traci_conn = traci_conn
        self._last_sim_time = float(current_time)
        elapsed_time = max(0.0, float(current_time) - self._simulation_begin)
        self._last_elapsed_time = elapsed_time
        schedule_time = elapsed_time if self.time_mode == "relative" else float(current_time)
        self.visualizer.step(traci_conn, elapsed_time)

        active: list[TrafficEvent] = []
        for event in self.events:
            if not event.enabled or event.status == "ended":
                continue
            if event.status == "pending" and schedule_time >= event.start_time:
                self._start_event(traci_conn, event, current_time, elapsed_time)
            if event.status == "active" and schedule_time >= event.end_time:
                self._end_event(traci_conn, event, current_time, elapsed_time)
                continue
            if event.status != "active":
                continue

            result = self.effects.update(traci_conn, event, schedule_time)
            self._report_phase_change(event, result, current_time, elapsed_time)
            self.visualizer.update(
                traci_conn,
                event,
                result.get("vehicle_ids", []),
                elapsed_time,
            )
            self._pending_reroutes[event.event_id] += int(result.get("rerouted", 0))
            self._maybe_report_phenomenon(
                traci_conn,
                event,
                result,
                current_time,
                elapsed_time,
            )
            active.append(event)

        self.logger.maybe_metrics(
            traci_conn,
            current_time,
            elapsed_time,
            active,
            self.congestion_threshold,
        )
        states: list[dict[str, Any]] = []
        for event in active:
            state = event.state()
            state["center"] = self.route_utils.event_center(event)
            state["radius"] = float(event.radius or 120.0)
            states.append(state)
        return states

    def _report_phase_change(
        self,
        event: TrafficEvent,
        result: dict[str, Any],
        sim_time: float,
        elapsed_time: float,
    ) -> None:
        if event.event_type not in {"concert", "large_event"}:
            return
        phase = str(result.get("phase", "inbound"))
        previous = self._event_phases.get(event.event_id)
        self._event_phases[event.event_id] = phase
        if previous == phase:
            return
        description = (
            "演出结束，散场车流开始从场馆向外疏散"
            if phase == "outbound"
            else "观众进场车流开始向场馆汇集"
        )
        self.logger.event(
            sim_time,
            elapsed_time,
            event,
            "phase_change",
            note=description,
        )
        print(
            f"[事件阶段] 时刻={_clock_time(sim_time)} "
            f"事件={_event_display_name(event)} 阶段={description}",
            flush=True,
        )

    def _maybe_report_phenomenon(
        self,
        traci_conn,
        event: TrafficEvent,
        result: dict[str, Any],
        sim_time: float,
        elapsed_time: float,
    ) -> None:
        if (
            elapsed_time - self._last_phenomenon_check[event.event_id]
            < self.phenomenon_interval
        ):
            return
        self._last_phenomenon_check[event.event_id] = elapsed_time
        stats = _event_traffic_stats(traci_conn, result.get("vehicle_ids", []))
        rerouted = self._pending_reroutes.pop(event.event_id, 0)
        kind = _detect_phenomenon(
            event,
            stats,
            rerouted,
            self.congestion_threshold,
            self.minimum_phenomenon_vehicles,
        )
        if kind is None or kind in self._reported_phenomena[event.event_id]:
            return

        self._reported_phenomena[event.event_id].add(kind)
        description = _phenomenon_text(event, kind, stats, rerouted)
        self.logger.event(
            sim_time,
            elapsed_time,
            event,
            "traffic_phenomenon",
            int(stats["vehicle_count"]),
            description,
        )
        print(
            f"[交通现象] 时刻={_clock_time(sim_time)} "
            f"原因={_event_display_name(event)} "
            f"位置={_location_text(event)} "
            f"现象={description}",
            flush=True,
        )

    def get_active_event_context(self, vehicle_id: str) -> dict[str, list[dict[str, Any]]]:
        active = [event for event in self.events if event.status == "active"]
        vehicle_position: tuple[float, float] | None = None
        road_id = ""
        if self._traci_conn is not None:
            try:
                vehicle_position = tuple(self._traci_conn.vehicle.getPosition(vehicle_id))
                road_id = str(self._traci_conn.vehicle.getRoadID(vehicle_id))
            except Exception:
                pass

        context = []
        for event in active:
            center = self.route_utils.event_center(event)
            distance = math.dist(vehicle_position, center) if vehicle_position else None
            if road_id in event.target_edges:
                distance = 0.0
            context.append(
                {
                    "event_type": event.event_type,
                    "name": event.name,
                    "distance": round(distance, 1) if distance is not None else None,
                    "target_edges": list(event.target_edges),
                    "severity": event.severity,
                    "suggestion": _suggestion(event.event_type),
                }
            )
        return {"active_events": context}

    def close(self, traci_conn=None) -> None:
        connection = traci_conn or self._traci_conn
        if connection is not None and self.restore_on_end:
            for event in self.events:
                if event.status == "active":
                    self.effects.deactivate(connection, event)
                    self.visualizer.end(connection, event)
                    event.status = "ended"
        self.logger.update_runtime(
            final_sim_time=self._last_sim_time,
            elapsed_time=self._last_elapsed_time,
            event_statuses={event.event_id: event.status for event in self.events},
            spawned_vehicles=dict(self.effects.spawned_count),
            spawned_vehicle_phases={
                f"{event_id}:{phase}": count
                for (event_id, phase), count in self.effects.phase_spawned_count.items()
            },
            gui_available=self.visualizer.gui_available,
        )
        self.logger.flush_summary()

    def _load_events(self, raw_events: list[dict[str, Any]]) -> None:
        for index, raw in enumerate(raw_events, start=1):
            event = TrafficEvent.from_dict(raw, index=index)
            if event.event_type not in SUPPORTED_EVENT_TYPES:
                print(
                    f"[EventManager][warning] unsupported event type skipped: {event.event_type}",
                    flush=True,
                )
                continue
            timing_warnings = validate_event_time(
                event,
                self._simulation_begin,
                self.time_mode,
                auto_correct=self.auto_correct_event_times,
            )
            event.warnings.extend(timing_warnings)
            for warning in timing_warnings:
                print(
                    f"[合理性校验] 事件={_event_display_name(event)} {warning}",
                    flush=True,
                )
            self.route_utils.prepare_event(event, index=index - 1)
            for warning in event.warnings:
                print(f"[EventManager][warning] event={event.event_id} {warning}", flush=True)
            self.events.append(event)

    def _start_event(
        self,
        traci_conn,
        event: TrafficEvent,
        sim_time: float,
        elapsed_time: float,
    ) -> None:
        event.status = "active"
        if self.route_utils.refresh_live_targets(event, traci_conn):
            print(
                f"[事件选址] 事件={_event_display_name(event)} "
                f"本次位置={_location_text(event)}",
                flush=True,
            )
            self.logger.register_events(self.events)
        if self.route_utils.ensure_construction_impact(event):
            print(
                f"[影响扩散] 事件={_event_display_name(event)} "
                f"施工核心道路={len(event.effects.get('_primary_edges', []))}条，"
                f"上游及替代通道共={len(event.target_edges)}条",
                flush=True,
            )
            self.logger.register_events(self.events)
        result = self.effects.activate(
            traci_conn,
            event,
            elapsed_time if self.time_mode == "relative" else sim_time,
        )
        self.visualizer.start(traci_conn, event, elapsed_time)
        note = f"changed_lanes={result['changed_lanes']} closed_lanes={result['closed_lanes']}"
        self.logger.event(sim_time, elapsed_time, event, "start", note=note)
        print(
            f"[事件启动] 时刻={_clock_time(sim_time)} "
            f"事件={_event_display_name(event)} 位置={_location_text(event)}",
            flush=True,
        )

    def _end_event(
        self,
        traci_conn,
        event: TrafficEvent,
        sim_time: float,
        elapsed_time: float,
    ) -> None:
        affected_count = len(self.effects.event_vehicles.get(event.event_id, []))
        result = self.effects.deactivate(traci_conn, event) if self.restore_on_end else {}
        self.visualizer.end(traci_conn, event)
        event.status = "ended"
        note = " ".join(f"{key}={value}" for key, value in result.items()) or "restore_disabled"
        self.logger.event(sim_time, elapsed_time, event, "end", affected_count, note)
        print(
            f"[事件结束] 时刻={_clock_time(sim_time)} "
            f"事件={_event_display_name(event)} 恢复结果={_restore_text(result)}",
            flush=True,
        )


def _event_traffic_stats(traci_conn, vehicle_ids: list[str]) -> dict[str, float]:
    speeds: list[float] = []
    waiting: list[float] = []
    halted = 0
    occupied_roads: set[str] = set()
    for vehicle_id in vehicle_ids:
        try:
            speed = float(traci_conn.vehicle.getSpeed(vehicle_id))
            speeds.append(speed)
            waiting.append(float(traci_conn.vehicle.getWaitingTime(vehicle_id)))
            halted += int(speed < 0.1)
            road_id = str(traci_conn.vehicle.getRoadID(vehicle_id))
            if road_id and not road_id.startswith(":"):
                occupied_roads.add(road_id)
        except Exception:
            continue
    return {
        "vehicle_count": float(len(speeds)),
        "mean_speed": sum(speeds) / len(speeds) if speeds else 0.0,
        "mean_waiting": sum(waiting) / len(waiting) if waiting else 0.0,
        "halted": float(halted),
        "road_count": float(len(occupied_roads)),
    }


def _detect_phenomenon(
    event: TrafficEvent,
    stats: dict[str, float],
    rerouted: int,
    speed_threshold: float,
    minimum_vehicles: int,
) -> str | None:
    vehicle_count = int(stats["vehicle_count"])
    if vehicle_count >= minimum_vehicles and (
        stats["halted"] >= 2 or stats["mean_waiting"] >= 3.0
    ):
        return "queue"
    if event.event_type in {"construction", "road_closure", "accident"} and rerouted > 0:
        return "reroute"
    if vehicle_count >= minimum_vehicles and stats["mean_speed"] <= speed_threshold:
        return "congestion"
    if (
        event.event_type == "weather"
        and vehicle_count >= 1
        and stats["mean_speed"] <= max(speed_threshold, 10.0)
    ):
        return "slowdown"
    return None


def _phenomenon_text(
    event: TrafficEvent,
    kind: str,
    stats: dict[str, float],
    rerouted: int,
) -> str:
    count = int(stats["vehicle_count"])
    speed = stats["mean_speed"]
    halted = int(stats["halted"])
    if kind == "queue":
        if event.event_type in {"construction", "road_closure"}:
            roads = int(stats.get("road_count", 0))
            return (
                f"施工排队已扩散到{roads}条相连道路，"
                f"区域内{count}辆车受影响，其中{halted}辆停车"
            )
        return f"区域内{count}辆车受影响，其中{halted}辆停车，出现排队拥堵"
    if kind == "reroute":
        return f"道路受限后有{rerouted}辆车触发绕行，车流开始转移"
    if kind == "slowdown":
        return f"恶劣天气使区域内车辆平均速度降至{speed:.1f}米/秒"
    if event.event_type in {"construction", "road_closure"}:
        roads = int(stats.get("road_count", 0))
        return (
            f"施工瓶颈已向上游及替代通道扩散，"
            f"{roads}条道路上的{count}辆车平均速度降至{speed:.1f}米/秒"
        )
    return f"区域内{count}辆车平均速度降至{speed:.1f}米/秒，形成交通拥堵"


def _suggestion(event_type: str) -> str:
    if event_type in {"construction", "road_closure", "accident"}:
        return "avoid affected roads, reroute early, and reduce speed"
    if event_type == "weather":
        return "reduce speed and increase following distance"
    if event_type in {"concert", "large_event"}:
        return "expect venue traffic and use an alternate approach"
    return "expect higher demand and allow additional travel time"


def _clock_time(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _event_display_name(event: TrafficEvent) -> str:
    if event.event_type == "rush_hour":
        return "晚高峰" if event.effects.get("peak_type") == "evening" else "早高峰"
    if event.event_type == "weather":
        return {
            "rain": "暴雨天气",
            "snow": "降雪天气",
            "typhoon": "台风天气",
        }.get(str(event.effects.get("weather_type")), "恶劣天气")
    return {
        "concert": "演唱会",
        "large_event": "大型活动",
        "construction": "道路施工",
        "road_closure": "道路封闭",
        "accident": "交通事故",
    }.get(event.event_type, event.name)


def _location_text(event: TrafficEvent) -> str:
    edges = ",".join(event.target_edges[:4])
    lanes = ",".join(event.target_lanes[:2])
    if lanes:
        return f"道路[{edges}] 车道[{lanes}]"
    return f"道路[{edges}]"


def _restore_text(result: dict[str, int]) -> str:
    return (
        f"车道速度{result.get('restored_lanes', 0)}项，"
        f"车道权限{result.get('restored_permissions', 0)}项，"
        f"车辆状态{result.get('restored_vehicles', 0)}辆"
    )
