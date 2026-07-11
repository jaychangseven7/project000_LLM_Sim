from __future__ import annotations

from dataclasses import dataclass, field

from src.agents.neural_policy import NeuralHubPolicy
from src.utils.logger import sim_time_to_hhmm


@dataclass
class AgentDecision:
    agent_id: str
    time: float
    action: str
    avoid_edges: list[str]
    reason: str
    confidence: float = 1.0
    model_name: str = "rule_fallback"
    score: float = 0.0
    metrics: dict[str, float | int | str] = field(default_factory=dict)


class IntersectionAgent:
    def __init__(
        self,
        junction_id: str,
        incoming_edges: list[str],
        queue_threshold: int,
        speed_threshold: float,
        static_metrics: dict | None = None,
        policy_model: str = "rule_fallback",
        policy: NeuralHubPolicy | None = None,
    ) -> None:
        self.junction_id = junction_id
        self.incoming_edges = incoming_edges
        self.queue_threshold = queue_threshold
        self.speed_threshold = speed_threshold
        self.static_metrics = static_metrics or {}
        self.policy_model = policy_model
        self.policy = policy

    def observe(self, traci_conn, current_time: float, active_events: list[dict]) -> dict:
        vehicle_count = 0
        queue_length = 0
        waiting_time = 0.0
        weighted_speed = 0.0
        congested_edges: list[str] = []
        edge_features: list[list[float]] = []

        for edge_id in self.incoming_edges:
            try:
                count = traci_conn.edge.getLastStepVehicleNumber(edge_id)
                speed = traci_conn.edge.getLastStepMeanSpeed(edge_id)
                waiting = traci_conn.edge.getWaitingTime(edge_id)
                halted = traci_conn.edge.getLastStepHaltingNumber(edge_id)
            except Exception:
                continue
            vehicle_count += count
            queue_length += halted
            waiting_time += waiting
            weighted_speed += speed * max(count, 1)
            edge_features.append(
                [
                    min(float(count) / 30.0, 3.0),
                    min(float(halted) / max(self.queue_threshold, 1), 3.0),
                    max(0.0, min(float(speed) / 15.0, 2.0)),
                    min(float(waiting) / 300.0, 3.0),
                ]
            )
            if halted >= max(4, self.queue_threshold // 3) or speed < self.speed_threshold:
                congested_edges.append(edge_id)

        denom = max(vehicle_count, len(self.incoming_edges), 1)
        mean_speed = weighted_speed / denom
        congestion_index = self._congestion_index(queue_length, mean_speed, waiting_time)
        return {
            "time": current_time,
            "junction_id": self.junction_id,
            "incoming_edges": self.incoming_edges,
            "vehicle_count": vehicle_count,
            "mean_speed": mean_speed,
            "queue_length": queue_length,
            "waiting_time": waiting_time,
            "active_events": active_events,
            "congested_edges": congested_edges,
            "edge_features": edge_features,
            "static_metrics": self.static_metrics,
            "congestion_index": congestion_index,
        }

    def decide(self, observation: dict) -> AgentDecision:
        nearby_events = observation.get("active_events", [])
        event_edges = {
            edge_id
            for event in nearby_events
            for edge_id in event.get("affected_edges", event.get("target_edges", []))
        }
        event_incoming = [
            edge_id for edge_id in self.incoming_edges if edge_id in event_edges
        ]
        event_requires_action = bool(nearby_events) and (
            observation["queue_length"] >= max(3, self.queue_threshold // 3)
            or (
                observation["vehicle_count"] >= 3
                and observation["mean_speed"] < self.speed_threshold * 1.5
            )
        )
        if event_requires_action:
            avoid_edges = (
                observation["congested_edges"]
                or event_incoming
                or self.incoming_edges[:1]
            )
            return AgentDecision(
                agent_id=f"intersection_{self.junction_id}",
                time=observation["time"],
                action="reroute_guidance",
                avoid_edges=avoid_edges,
                reason="nearby event has produced a measured queue or low-speed state",
                confidence=1.0,
                model_name="event_aware_rule",
                score=min(1.0, 0.5 + observation["congestion_index"]),
                metrics=self._decision_metrics(observation),
            )

        if self.policy_model == "neural_hub" and self.policy is not None:
            result = self.policy.infer(observation)
            avoid_edges = observation["congested_edges"] or observation["incoming_edges"][:1]
            if nearby_events and result.action == "no_action":
                return AgentDecision(
                    agent_id=f"intersection_{self.junction_id}",
                    time=observation["time"],
                    action="event_monitoring",
                    avoid_edges=[],
                    reason="nearby event detected; measured traffic has not crossed the intervention threshold",
                    confidence=result.confidence,
                    model_name="neural_hub",
                    score=result.score,
                    metrics=self._decision_metrics(observation),
                )
            return AgentDecision(
                agent_id=f"intersection_{self.junction_id}",
                time=observation["time"],
                action=result.action,
                avoid_edges=avoid_edges if result.action == "reroute_guidance" else [],
                reason=result.reason,
                confidence=result.confidence,
                model_name="neural_hub",
                score=result.score,
                metrics=self._decision_metrics(observation),
            )

        if nearby_events:
            return AgentDecision(
                agent_id=f"intersection_{self.junction_id}",
                time=observation["time"],
                action="event_monitoring",
                avoid_edges=[],
                reason="nearby event detected; measured traffic has not crossed the intervention threshold",
                confidence=1.0,
                model_name="event_aware_rule",
                score=observation["congestion_index"],
                metrics=self._decision_metrics(observation),
            )

        if observation["queue_length"] > self.queue_threshold or observation["mean_speed"] < self.speed_threshold:
            return AgentDecision(
                agent_id=f"intersection_{self.junction_id}",
                time=observation["time"],
                action="reroute_guidance",
                avoid_edges=observation["congested_edges"] or observation["incoming_edges"][:1],
                reason="queue length exceeds threshold or speed below threshold",
                confidence=1.0,
                model_name="rule_fallback",
                score=1.0,
                metrics=self._decision_metrics(observation),
            )
        return AgentDecision(
            agent_id=f"intersection_{self.junction_id}",
            time=observation["time"],
            action="no_action",
            avoid_edges=[],
            reason="traffic is normal",
            confidence=1.0,
            model_name="rule_fallback",
            score=0.0,
            metrics=self._decision_metrics(observation),
        )

    def log_decision(self, observation: dict, decision: AgentDecision, index: int | None = None) -> None:
        avoid = ",".join(decision.avoid_edges[:3]) if decision.avoid_edges else "-"
        prefix = f"agent=#{index:02d}" if index is not None else "agent=?"
        metrics = decision.metrics
        nearby_events = observation.get("active_events", [])
        event_text = ",".join(
            str(event.get("name") or event.get("event_type") or event.get("type"))
            for event in nearby_events
        ) or "无"
        action_text = {
            "no_action": "保持当前控制",
            "event_monitoring": "加强监测，暂不干预",
            "reroute_guidance": "发布绕行引导",
        }.get(decision.action, decision.action)
        reason_text = {
            "event_monitoring": "检测到附近事件，但排队和速度尚未达到干预阈值",
            "reroute_guidance": (
                "实测排队或低速达到阈值，或处于防止控制振荡的最短保持期"
            ),
            "no_action": "交通状态处于正常范围",
        }.get(decision.action, decision.reason)
        print(
            f"[路口Agent决策] 时刻={sim_time_to_hhmm(decision.time)} {prefix} "
            f"路口={self.junction_id} 附近事件={event_text} "
            f"车辆={metrics.get('vehicle_count', 0)} 排队={metrics.get('queue_length', 0)} "
            f"平均速度={float(metrics.get('mean_speed', 0.0)):.1f}米/秒 "
            f"邻域拥堵={float(metrics.get('neighbor_congestion', 0.0)):.2f} "
            f"决策={action_text} 避让道路={avoid} "
            f"依据={reason_text} 模型={decision.model_name}",
            flush=True,
        )

    def _congestion_index(self, queue_length: int, mean_speed: float, waiting_time: float) -> float:
        queue_pressure = min(float(queue_length) / max(self.queue_threshold, 1), 3.0)
        speed_pressure = max(0.0, 1.0 - float(mean_speed) / max(self.speed_threshold * 2.0, 0.1))
        waiting_pressure = min(float(waiting_time) / 1000.0, 3.0)
        return queue_pressure * 0.5 + speed_pressure * 0.3 + waiting_pressure * 0.2

    def _decision_metrics(self, observation: dict) -> dict[str, float | int | str]:
        static = observation.get("static_metrics", {})
        return {
            "queue_length": int(observation.get("queue_length", 0)),
            "mean_speed": float(observation.get("mean_speed", 0.0)),
            "vehicle_count": int(observation.get("vehicle_count", 0)),
            "waiting_time": float(observation.get("waiting_time", 0.0)),
            "route_flow": int(static.get("route_flow", 0)),
            "lane_count": int(static.get("lane_count", 0)),
            "major_edges": int(static.get("major_edges", 0)),
            "neighbor_congestion": float(observation.get("neighbor_congestion", 0.0)),
        }
