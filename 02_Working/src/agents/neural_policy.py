from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn


@dataclass
class NeuralPolicyResult:
    action: str
    confidence: float
    score: float
    reason: str


class NeuralHubPolicy(nn.Module):
    """Lightweight HHAN/GAT-style inference policy for demo-time hub agents.

    This is an untrained, deterministic policy network. It keeps the paper-style
    shape of local edge attention plus hub-level features, while preserving
    interpretable congestion thresholds for a live SUMO demo.
    """

    def __init__(self, queue_threshold: int, speed_threshold: float, action_threshold: float = 0.55) -> None:
        super().__init__()
        torch.manual_seed(42)
        self.queue_threshold = max(float(queue_threshold), 1.0)
        self.speed_threshold = max(float(speed_threshold), 0.1)
        self.action_threshold = action_threshold
        self.edge_encoder = nn.Linear(4, 8)
        self.edge_attention = nn.Linear(8, 1)
        self.hub_head = nn.Linear(17, 1)
        self._init_deterministic_weights()
        self.eval()

    @torch.no_grad()
    def infer(self, observation: dict) -> NeuralPolicyResult:
        edge_features = observation.get("edge_features") or [[0.0, 0.0, 1.0, 0.0]]
        edge_tensor = torch.tensor(edge_features, dtype=torch.float32)
        edge_hidden = torch.tanh(self.edge_encoder(edge_tensor))
        attention = torch.softmax(self.edge_attention(edge_hidden).squeeze(-1), dim=0)
        edge_context = torch.sum(attention.unsqueeze(-1) * edge_hidden, dim=0)
        hub_tensor = torch.tensor(self._hub_features(observation), dtype=torch.float32)
        features = torch.cat([edge_context, hub_tensor], dim=0)
        logit = self.hub_head(features).squeeze()
        probability = torch.sigmoid(logit).item()
        action = "reroute_guidance" if probability >= self.action_threshold else "no_action"
        confidence = probability if action == "reroute_guidance" else 1.0 - probability
        reason = (
            "neural hub policy detected network-constrained congestion pressure"
            if action == "reroute_guidance"
            else "neural hub policy predicts manageable local traffic"
        )
        return NeuralPolicyResult(action=action, confidence=confidence, score=probability, reason=reason)

    def _hub_features(self, observation: dict) -> list[float]:
        static = observation.get("static_metrics", {})
        return [
            min(float(observation.get("vehicle_count", 0)) / 100.0, 3.0),
            min(float(observation.get("queue_length", 0)) / self.queue_threshold, 3.0),
            max(0.0, min(float(observation.get("mean_speed", 0.0)) / 15.0, 2.0)),
            min(float(observation.get("waiting_time", 0.0)) / 1000.0, 3.0),
            min(float(len(observation.get("incoming_edges", []))) / 8.0, 2.0),
            min(float(static.get("lane_count", 0)) / 20.0, 2.0),
            min(float(static.get("route_flow", 0)) / 1200.0, 3.0),
            min(float(len(observation.get("active_events", []))) / 3.0, 2.0),
            min(float(observation.get("neighbor_congestion", 0.0)), 3.0),
        ]

    def _init_deterministic_weights(self) -> None:
        with torch.no_grad():
            self.edge_encoder.weight.zero_()
            self.edge_encoder.bias.zero_()
            self.edge_encoder.weight[0, 0] = 0.7   # edge vehicle count
            self.edge_encoder.weight[1, 1] = 1.0   # halting vehicles
            self.edge_encoder.weight[2, 2] = -0.8  # speed, lower is worse
            self.edge_encoder.weight[3, 3] = 0.7   # waiting time
            self.edge_encoder.weight[4, 0] = 0.3
            self.edge_encoder.weight[4, 1] = 0.6
            self.edge_encoder.weight[5, 1] = 0.5
            self.edge_encoder.weight[5, 2] = -0.5
            self.edge_encoder.weight[6, 3] = 0.5
            self.edge_encoder.weight[7, 0] = 0.2
            self.edge_encoder.weight[7, 3] = 0.3

            self.edge_attention.weight.zero_()
            self.edge_attention.bias.zero_()
            self.edge_attention.weight[0, 0] = 0.5
            self.edge_attention.weight[0, 1] = 1.0
            self.edge_attention.weight[0, 3] = 0.7

            self.hub_head.weight.zero_()
            self.hub_head.bias.fill_(-2.2)
            # Edge context.
            self.hub_head.weight[0, 0] = 0.6
            self.hub_head.weight[0, 1] = 1.2
            self.hub_head.weight[0, 2] = -0.6
            self.hub_head.weight[0, 3] = 0.9
            self.hub_head.weight[0, 5] = 0.5
            # Hub-level features.
            self.hub_head.weight[0, 8] = 0.5
            self.hub_head.weight[0, 9] = 1.2
            self.hub_head.weight[0, 10] = -0.7
            self.hub_head.weight[0, 11] = 0.7
            self.hub_head.weight[0, 13] = 0.35
            self.hub_head.weight[0, 14] = 0.45
            self.hub_head.weight[0, 15] = 0.8
            self.hub_head.weight[0, 16] = 0.7
