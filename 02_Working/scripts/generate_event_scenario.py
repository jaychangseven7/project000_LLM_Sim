from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.events.scenario_generator import DEFAULT_SCENARIO_PATH, generate_event_scenario
from src.map.edge_sampler import EdgeSampler
from src.map.map_builder import ensure_demo_map
from src.utils.config_loader import load_yaml


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate AgentSUMO-style event scenario YAML.")
    parser.add_argument("--config", default="config/demo_gui.yaml")
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--input-file", default=None)
    parser.add_argument("--output", default=DEFAULT_SCENARIO_PATH)
    parser.add_argument("--download-map", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()

    config = load_yaml(args.config)
    net_file = ensure_demo_map(config, download_map=args.download_map)
    edge_sampler = EdgeSampler(net_file, seed=int(config["demo"].get("random_seed", 42)))
    generate_event_scenario(
        prompt=args.prompt,
        input_file=args.input_file,
        output_file=args.output,
        edge_sampler=edge_sampler,
        use_llm=not args.no_llm,
    )


if __name__ == "__main__":
    main()
