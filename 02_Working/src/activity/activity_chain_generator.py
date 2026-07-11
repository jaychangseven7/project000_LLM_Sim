from __future__ import annotations

import json
import random
from pathlib import Path

from src.utils.config_loader import ensure_parent


def _hhmm(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:00"


class ActivityChainGenerator:
    def __init__(self, seed: int = 42) -> None:
        self.rng = random.Random(seed)

    def generate(self, profiles: list[dict], output_file: str | Path) -> list[dict]:
        chains = [self._chain_for(p["driver_id"], p["profile_type"]) for p in profiles]
        path = ensure_parent(output_file)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(chains, fh, ensure_ascii=False, indent=2)
        return chains

    def _chain_for(self, driver_id: str, profile_type: str) -> dict:
        if profile_type == "commuter":
            depart = self.rng.randint(7 * 3600 + 20 * 60, 8 * 3600 + 45 * 60)
            back = self.rng.randint(17 * 3600 + 25 * 60, 19 * 3600 + 15 * 60)
            return self._simple(driver_id, profile_type, depart, back, "business_zone")
        if profile_type == "student":
            depart = self.rng.randint(7 * 3600, 8 * 3600 + 20 * 60)
            back = self.rng.randint(16 * 3600 + 30 * 60, 18 * 3600 + 30 * 60)
            return self._simple(driver_id, profile_type, depart, back, "school_zone")
        if profile_type == "taxi":
            first = self.rng.randint(6 * 3600 + 30 * 60, 10 * 3600)
            second = self.rng.randint(12 * 3600, 16 * 3600)
            third = self.rng.randint(18 * 3600, 21 * 3600)
            return {
                "driver_id": driver_id,
                "profile_type": profile_type,
                "activities": [
                    {"type": "idle", "start_time": "06:00:00", "end_time": _hhmm(first), "zone": "business_zone"},
                    {"type": "pickup", "start_time": _hhmm(first + 900), "end_time": _hhmm(second), "zone": "shopping_zone"},
                    {"type": "pickup", "start_time": _hhmm(second + 900), "end_time": _hhmm(third), "zone": "residential_zone"},
                    {"type": "idle", "start_time": _hhmm(third + 1200), "end_time": "23:00:00", "zone": "business_zone"},
                ],
            }
        if profile_type == "event_participant":
            arrive = self.rng.randint(18 * 3600 + 30 * 60, 19 * 3600 + 45 * 60)
            leave = self.rng.randint(20 * 3600, 20 * 3600 + 45 * 60)
            return self._simple(driver_id, profile_type, arrive, leave, "event_zone")

        depart = self.rng.randint(9 * 3600, 11 * 3600)
        back = self.rng.randint(15 * 3600, 17 * 3600 + 30 * 60)
        return self._simple(driver_id, profile_type, depart, back, "shopping_zone")

    def _simple(self, driver_id: str, profile_type: str, depart: int, back: int, target_zone: str) -> dict:
        return {
            "driver_id": driver_id,
            "profile_type": profile_type,
            "activities": [
                {"type": "home", "start_time": "06:00:00", "end_time": _hhmm(depart), "zone": "residential_zone"},
                {"type": "main", "start_time": _hhmm(depart + 1800), "end_time": _hhmm(back), "zone": target_zone},
                {"type": "home", "start_time": _hhmm(back + 1800), "end_time": "23:00:00", "zone": "residential_zone"},
            ],
        }

