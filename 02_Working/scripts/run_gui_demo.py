from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generate_demo_routes import generate
from src.events.scenario_generator import DEFAULT_SCENARIO_PATH, generate_event_scenario
from src.map.edge_sampler import EdgeSampler
from src.map.map_builder import ensure_demo_map
from src.simulation.sumo_gui_runner import SumoGuiRunner
from src.utils.config_loader import load_yaml, resolve_path


def main() -> None:
    parser = argparse.ArgumentParser(description="一键运行 SUMO GUI 广州城区交通仿真 Demo")
    parser.add_argument("--config", default="config/demo_gui.yaml")
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--delay", type=int, default=None)
    parser.add_argument("--scenario", default=None)
    parser.add_argument("--num-drivers", type=int, default=None)
    parser.add_argument("--auto-generate", action="store_true")
    parser.add_argument("--download-map", action="store_true")
    parser.add_argument("--event-prompt", default=None)
    parser.add_argument("--event-scenario", default=None)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()

    config = load_yaml(args.config)
    if args.num_drivers is not None:
        config["demo"]["num_drivers"] = args.num_drivers
    if args.scenario is not None:
        config["demo"]["scenario"] = args.scenario
    if args.delay is not None:
        config["simulation"]["gui_delay"] = args.delay

    net_file = ensure_demo_map(config, download_map=args.download_map)
    if args.event_prompt or args.event_scenario:
        if args.event_scenario:
            config["events"]["event_file"] = args.event_scenario
            print(f"[ScenarioGenerator] using scenario file={args.event_scenario}", flush=True)
        else:
            edge_sampler = EdgeSampler(net_file, seed=int(config["demo"].get("random_seed", 42)))
            generated_path = generate_event_scenario(
                prompt=args.event_prompt,
                output_file=DEFAULT_SCENARIO_PATH,
                edge_sampler=edge_sampler,
                use_llm=not args.no_llm,
            )
            config["events"]["event_file"] = str(generated_path)

    route_file = resolve_path(config["map"]["route_file"])
    should_generate = (
        args.auto_generate
        or args.download_map
        or args.num_drivers is not None
        or (config["activity"].get("generate_if_missing", True) and not route_file.exists())
    )
    if should_generate:
        generate(config, num_drivers=config["demo"]["num_drivers"], download_map=args.download_map)

    use_gui = args.gui or bool(config["simulation"].get("gui", True))
    SumoGuiRunner(config, gui=use_gui, delay=args.delay).run()


if __name__ == "__main__":
    main()
