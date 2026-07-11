from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scripts.generate_demo_routes import generate
from src.map.map_builder import ensure_demo_map
from src.simulation.sumo_gui_runner import SumoGuiRunner
from src.utils.config_loader import load_yaml, resolve_path


def main() -> None:
    parser = argparse.ArgumentParser(description="SUMO GUI 实时事件注入演示")
    parser.add_argument("--config", default="config/demo_gui.yaml")
    parser.add_argument("--event-config", default="config/events/demo_gui_events.yaml")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=57600,
        help="仿真步数；默认覆盖 06:30–22:30 的完整城市日周期",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=None,
        help="每个仿真步的 GUI 延迟毫秒数；GUI 模式默认 20，数值越大越慢",
    )
    parser.add_argument("--gui", action="store_true", help="使用 sumo-gui；不指定时使用无界面 sumo")
    parser.add_argument("--auto-generate", action="store_true")
    parser.add_argument("--download-map", action="store_true")
    parser.add_argument(
        "--event-seed",
        type=int,
        default=None,
        help="事件选址随机种子；不指定时每次运行随机选择，指定后可复现",
    )
    args = parser.parse_args()

    config = load_yaml(args.config)
    config.setdefault("events", {})
    config["events"].update(
        {
            "use_events": True,
            "event_file": args.event_config,
            "time_mode": "relative",
            "enable_gui_visualization": args.gui,
        }
    )
    if args.event_seed is not None:
        config["events"]["location_seed"] = args.event_seed
    config.setdefault("simulation", {})["gui_settings_file"] = "config/event_demo_gui.view.xml"
    config.setdefault("demo", {})["pause_on_event"] = False
    # Keep generic demo chatter quiet; event-aware IntersectionAgent decisions
    # are emitted separately when an event enters an agent's neighborhood.
    config["demo"]["show_console_logs"] = False
    agent_cfg = config.setdefault("agents", {})
    agent_cfg["use_intersection_agents"] = True
    agent_cfg["show_gui_markers"] = True
    agent_cfg["log_all_decisions"] = False
    agent_cfg["event_decision_log_interval"] = 900
    agent_cfg["intervention_hold_seconds"] = 600
    routing_cfg = config.setdefault("routing", {})
    routing_cfg["use_rerouting"] = True
    routing_cfg["max_rerouted_vehicles_per_interval"] = 20
    routing_cfg["rerouting_interval"] = 600
    ensure_demo_map(config, download_map=args.download_map)

    route_file = resolve_path(config["map"]["route_file"])
    if args.auto_generate or args.download_map or not route_file.exists():
        generate(
            config,
            num_drivers=int(config["demo"].get("num_drivers", 1000)),
            download_map=args.download_map,
        )

    effective_delay = args.delay if args.delay is not None else (20 if args.gui else 0)
    print(
        f"[GUIEventDemo] mode={'sumo-gui' if args.gui else 'sumo'} "
        f"event_config={resolve_path(args.event_config)} max_steps={args.max_steps} "
        f"delay={effective_delay}ms",
        flush=True,
    )
    SumoGuiRunner(
        config,
        gui=args.gui,
        delay=effective_delay,
        max_steps=max(1, args.max_steps),
    ).run()
    output_dir = resolve_path("../03_Outputs/events").resolve()
    print(f"[GUIEventDemo] event_log={output_dir / 'event_log.csv'}", flush=True)
    print(f"[GUIEventDemo] traffic_metrics={output_dir / 'traffic_metrics.csv'}", flush=True)
    print(f"[GUIEventDemo] summary={output_dir / 'gui_demo_summary.json'}", flush=True)


if __name__ == "__main__":
    main()
