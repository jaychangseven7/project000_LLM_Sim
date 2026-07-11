from __future__ import annotations


class VehicleAgentInterface:
    def observe(self, vehicle_id: str, traci_conn):
        raise NotImplementedError

    def decide(self, observation):
        raise NotImplementedError

    def act(self, vehicle_id: str, action, traci_conn):
        raise NotImplementedError

