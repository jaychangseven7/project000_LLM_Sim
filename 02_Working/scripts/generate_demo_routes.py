from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.activity.activity_chain_generator import ActivityChainGenerator
from src.activity.profile_generator import ProfileGenerator
from src.activity.route_writer import RouteWriter
from src.activity.trip_converter import TripConverter
from src.map.edge_sampler import EdgeSampler
from src.map.map_builder import ensure_demo_map
from src.utils.config_loader import load_yaml, resolve_path


def generate(config: dict, num_drivers: int | None = None, download_map: bool = False) -> None:
    if num_drivers is not None:
        config["demo"]["num_drivers"] = num_drivers
    net_file = ensure_demo_map(config, download_map=download_map)
    seed = int(config["demo"].get("random_seed", 42))
    profiles = ProfileGenerator(seed).generate(config["demo"]["num_drivers"], config["activity"]["profile_file"])
    chains = ActivityChainGenerator(seed).generate(profiles, config["activity"]["activity_chain_file"])
    sampler = EdgeSampler(net_file, seed=seed)
    trips = TripConverter(sampler).convert(chains, config["activity"]["trips_file"])
    RouteWriter(config).write_all(config["activity"]["trips_file"])
    print(f"[Generate] 已生成 {len(profiles)} 个司机、{len(trips)} 条出行记录。")
    print(f"[Generate] route 文件：{resolve_path(config['map']['route_file'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 SUMO GUI Demo 的活动链和 route 文件")
    parser.add_argument("--config", default="config/demo_gui.yaml")
    parser.add_argument("--num-drivers", type=int, default=None)
    parser.add_argument("--download-map", action="store_true")
    args = parser.parse_args()
    config = load_yaml(args.config)
    generate(config, num_drivers=args.num_drivers, download_map=args.download_map)


if __name__ == "__main__":
    main()

