from __future__ import annotations

from math import hypot

from src.agents.intersection_agent import AgentDecision, IntersectionAgent
from src.agents.intersection_selector import IntersectionCandidate, log_selected_intersections, select_intersections
from src.agents.neural_policy import NeuralHubPolicy


class AgentManager:
    def __init__(self, net_file: str, config: dict) -> None:
        self.config = config
        self.enabled = config["agents"].get("use_intersection_agents", True)
        self.interval = int(config["agents"].get("decision_interval", 300))
        self.start_time = int(config["agents"].get("monitor_start_time", 28800))
        self.log_all_decisions = bool(config["agents"].get("log_all_decisions", False))
        self.neighbor_radius = float(config["agents"].get("neighbor_radius_meters", 900))
        self.last_decision_time = -10**9
        self.decision_log_interval = int(
            config["agents"].get("event_decision_log_interval", 900)
        )
        self._last_logged_time: dict[str, float] = {}
        self._last_logged_signature: dict[str, tuple] = {}
        self.intervention_hold = int(
            config["agents"].get("intervention_hold_seconds", 600)
        )
        self._intervention_until: dict[str, float] = {}
        self._held_avoid_edges: dict[str, list[str]] = {}
        self.agents = self._build_agents(net_file, config) if self.enabled else []
        if self.enabled:
            print(
                f"[Demo] Created {len(self.agents)} intersection Agents for local congestion monitoring.",
                flush=True,
            )

    def _build_agents(self, net_file: str, config: dict) -> list[IntersectionAgent]:
        candidates = select_intersections(net_file, config)
        log_selected_intersections(candidates)
        policy_model = config["agents"].get("policy_model", "rule_fallback")
        shared_policy = None
        if policy_model == "neural_hub":
            shared_policy = NeuralHubPolicy(
                queue_threshold=int(config["agents"].get("queue_threshold", 15)),
                speed_threshold=float(config["agents"].get("speed_threshold", 4.0)),
                action_threshold=float(config["agents"].get("neural_action_threshold", 0.55)),
            )
            print("[Demo] Intersection policy model: neural_hub attention inference.", flush=True)

        return [
            IntersectionAgent(
                junction_id=candidate.junction_id,
                incoming_edges=candidate.incoming_edges,
                queue_threshold=int(config["agents"].get("queue_threshold", 15)),
                speed_threshold=float(config["agents"].get("speed_threshold", 4.0)),
                static_metrics=self._static_metrics(candidate),
                policy_model=policy_model,
                policy=shared_policy,
            )
            for candidate in candidates
        ]

    def step(self, traci_conn, current_time: float, active_events: list[dict]) -> list[AgentDecision]:
        if not self.enabled or current_time < self.start_time or current_time - self.last_decision_time < self.interval:
            return []
        self.last_decision_time = current_time

        nearby_by_agent = [
            self._nearby_events(agent, active_events) for agent in self.agents
        ]
        observations = [
            agent.observe(traci_conn, current_time, nearby_events)
            for agent, nearby_events in zip(self.agents, nearby_by_agent)
        ]
        self._attach_neighbor_congestion(observations)

        decisions: list[AgentDecision] = []
        for idx, (agent, observation) in enumerate(zip(self.agents, observations), start=1):
            decision = agent.decide(observation)
            decision = self._stabilize_decision(
                agent,
                observation,
                decision,
                current_time,
            )
            self._update_gui_marker(traci_conn, idx, agent, decision)
            if self._should_log_decision(agent, observation, decision, current_time):
                agent.log_decision(observation, decision, index=idx)
            decisions.append(decision)
        return decisions

    def _stabilize_decision(
        self,
        agent: IntersectionAgent,
        observation: dict,
        decision: AgentDecision,
        current_time: float,
    ) -> AgentDecision:
        key = agent.junction_id
        if decision.action == "reroute_guidance":
            self._intervention_until[key] = current_time + self.intervention_hold
            self._held_avoid_edges[key] = list(decision.avoid_edges)
            return decision
        if (
            observation.get("active_events")
            and current_time < self._intervention_until.get(key, -1)
        ):
            return AgentDecision(
                agent_id=decision.agent_id,
                time=decision.time,
                action="reroute_guidance",
                avoid_edges=list(self._held_avoid_edges.get(key, [])),
                reason="minimum control hold is active to prevent oscillating guidance",
                confidence=decision.confidence,
                model_name="event_control_hysteresis",
                score=decision.score,
                metrics=decision.metrics,
            )
        return decision

    def _nearby_events(
        self,
        agent: IntersectionAgent,
        active_events: list[dict],
    ) -> list[dict]:
        x = float(agent.static_metrics.get("x", 0.0))
        y = float(agent.static_metrics.get("y", 0.0))
        incoming = set(agent.incoming_edges)
        nearby: list[dict] = []
        for event in active_events:
            affected = set(
                event.get("affected_edges", event.get("target_edges", []))
            )
            center = event.get("center")
            radius = float(event.get("radius", 120.0))
            direct = bool(incoming & affected)
            spatial = False
            if isinstance(center, (list, tuple)) and len(center) >= 2:
                spatial = (
                    hypot(x - float(center[0]), y - float(center[1]))
                    <= self.neighbor_radius + radius
                )
            if direct or spatial:
                nearby.append(event)
        return nearby

    def _should_log_decision(
        self,
        agent: IntersectionAgent,
        observation: dict,
        decision: AgentDecision,
        current_time: float,
    ) -> bool:
        nearby_ids = tuple(
            sorted(
                str(event.get("event_id", event.get("event_type", "")))
                for event in observation.get("active_events", [])
            )
        )
        if not nearby_ids and decision.action == "no_action":
            return bool(self.log_all_decisions)
        signature = (
            decision.action,
            nearby_ids,
            int(observation.get("queue_length", 0)) // 3,
        )
        key = agent.junction_id
        changed = signature != self._last_logged_signature.get(key)
        due = (
            current_time - self._last_logged_time.get(key, -10**9)
            >= self.decision_log_interval
            and int(observation.get("vehicle_count", 0)) > 0
        )
        if self.log_all_decisions or changed or due:
            self._last_logged_signature[key] = signature
            self._last_logged_time[key] = current_time
            return True
        return False

    @staticmethod
    def _update_gui_marker(
        traci_conn,
        index: int,
        agent: IntersectionAgent,
        decision: AgentDecision,
    ) -> None:
        color = {
            "event_monitoring": (255, 210, 0, 255),
            "reroute_guidance": (255, 60, 60, 255),
        }.get(decision.action, (0, 180, 255, 255))
        marker_id = f"agent_marker_{index:02d}_{agent.junction_id}"
        box_id = f"agent_box_{index:02d}_{agent.junction_id}"
        try:
            traci_conn.poi.setColor(marker_id, color)
        except Exception:
            pass
        try:
            traci_conn.polygon.setColor(box_id, color)
        except Exception:
            pass

    def _attach_neighbor_congestion(self, observations: list[dict]) -> None:
        for observation in observations:
            static = observation.get("static_metrics", {})
            x = float(static.get("x", 0.0))
            y = float(static.get("y", 0.0))
            neighbor_values = []
            for other in observations:
                if other is observation:
                    continue
                other_static = other.get("static_metrics", {})
                distance = hypot(x - float(other_static.get("x", 0.0)), y - float(other_static.get("y", 0.0)))
                if distance <= self.neighbor_radius:
                    neighbor_values.append(float(other.get("congestion_index", 0.0)))
            observation["neighbor_congestion"] = (
                sum(neighbor_values) / len(neighbor_values) if neighbor_values else 0.0
            )

    def _static_metrics(self, candidate: IntersectionCandidate) -> dict:
        return {
            "x": candidate.x,
            "y": candidate.y,
            "score": candidate.score,
            "route_flow": candidate.route_flow,
            "complexity": candidate.complexity,
            "degree": candidate.degree,
            "lane_count": candidate.lane_count,
            "major_edges": candidate.major_edges,
        }
