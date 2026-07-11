from __future__ import annotations

import csv
from pathlib import Path

from src.map.edge_sampler import EdgeSampler
from src.utils.config_loader import ensure_parent


def parse_time(value: str) -> int:
    h, m, s = [int(part) for part in value.split(":")]
    return h * 3600 + m * 60 + s


PROFILE_TO_VTYPE = {
    "commuter": "commuter",
    "student": "private_car",
    "taxi": "taxi",
    "freelancer": "private_car",
    "event_participant": "event_vehicle",
}


class TripConverter:
    def __init__(self, edge_sampler: EdgeSampler) -> None:
        self.edge_sampler = edge_sampler

    def convert(self, chains: list[dict], output_file: str | Path) -> list[dict]:
        rows: list[dict] = []
        for chain in chains:
            activities = chain["activities"]
            for idx in range(len(activities) - 1):
                origin = activities[idx]["zone"]
                target = activities[idx + 1]["zone"]
                depart = parse_time(activities[idx]["end_time"])
                from_edge, to_edge = self.edge_sampler.sample_reachable_pair(origin, target)
                rows.append(
                    {
                        "trip_id": f"{chain['driver_id']}_trip_{idx + 1:03d}",
                        "driver_id": chain["driver_id"],
                        "profile_type": chain["profile_type"],
                        "depart": depart,
                        "from_edge": from_edge,
                        "to_edge": to_edge,
                        "vehicle_type": PROFILE_TO_VTYPE.get(chain["profile_type"], "private_car"),
                    }
                )

        rows.sort(key=lambda r: (r["depart"], r["trip_id"]))
        path = ensure_parent(output_file)
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [])
            writer.writeheader()
            writer.writerows(rows)
        return rows
