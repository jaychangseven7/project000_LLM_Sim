from __future__ import annotations

import csv
import random
from pathlib import Path

from src.utils.config_loader import ensure_parent


PROFILE_WEIGHTS = [
    ("commuter", 0.52),
    ("student", 0.12),
    ("taxi", 0.12),
    ("freelancer", 0.14),
    ("event_participant", 0.10),
]


class ProfileGenerator:
    def __init__(self, seed: int = 42) -> None:
        self.rng = random.Random(seed)

    def generate(self, num_drivers: int, output_file: str | Path) -> list[dict]:
        profiles = []
        labels, weights = zip(*PROFILE_WEIGHTS)
        for i in range(num_drivers):
            profile_type = self.rng.choices(labels, weights=weights, k=1)[0]
            profiles.append({"driver_id": f"driver_{i + 1:04d}", "profile_type": profile_type})

        path = ensure_parent(output_file)
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["driver_id", "profile_type"])
            writer.writeheader()
            writer.writerows(profiles)
        return profiles

