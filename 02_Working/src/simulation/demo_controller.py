from __future__ import annotations

from src.agents.agent_manager import AgentManager
from src.events.event_manager import EventManager
from src.routing.route_replanner import RouteReplanner
from src.utils.logger import DemoLogger, sim_time_to_hhmm


class DemoController:
    def __init__(self, config: dict, net_file: str, edge_sampler, logger: DemoLogger) -> None:
        self.config = config
        self.logger = logger
        self.event_manager = EventManager(config, edge_sampler)
        self.agent_manager = AgentManager(net_file, config)
        self.replanner = RouteReplanner(config)
        self.logged_stages: set[str] = set()

    def step(self, traci_conn, current_time: float) -> None:
        self._log_stage(current_time)
        active_events = self.event_manager.step(traci_conn, current_time)
        decisions = self.agent_manager.step(traci_conn, current_time, active_events)
        self.replanner.step(traci_conn, current_time, active_events, decisions)

    def close(self, traci_conn) -> None:
        self.event_manager.close(traci_conn)

    def _log_stage(self, current_time: float) -> None:
        stages = [
            ("morning_start", 6 * 3600 + 30 * 60, "[Demo] 06:30 清晨交通流开始，车辆逐渐进入路网。"),
            ("morning_peak", 7 * 3600 + 30 * 60, "[Demo] 07:30 早高峰开始，通勤车辆集中进入路网。"),
            ("agent_start", 8 * 3600, "[Demo] 08:00 路口 Agent 开始监测拥堵并给出绕行建议。"),
            ("evening_peak", 17 * 3600 + 30 * 60, "[Demo] 17:30 晚高峰开始，返程车辆集中出发。"),
            ("event_hint", 17 * 3600 + 45 * 60, "[Demo] 17:45 事故事件进入演示窗口，请观察局部排队和紫色绕行车辆。"),
            ("large_event_hint", 20 * 3600, "[Demo] 20:00 大型活动散场演示开始。"),
        ]
        for key, start, message in stages:
            if key not in self.logged_stages and current_time >= start:
                self.logged_stages.add(key)
                self.logger.line(message)
                if key in {"event_hint", "large_event_hint"} and self.config["demo"].get("pause_on_event", True):
                    self.logger.sleep_for_explain(float(self.config["demo"].get("event_pause_seconds", 2)))
