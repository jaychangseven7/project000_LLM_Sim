from __future__ import annotations

import shutil

import traci

from src.map.edge_sampler import EdgeSampler
from src.simulation.demo_controller import DemoController
from src.utils.config_loader import resolve_path
from src.utils.logger import DemoLogger


class SumoGuiRunner:
    def __init__(
        self,
        config: dict,
        gui: bool = True,
        delay: int | None = None,
        max_steps: int | None = None,
    ) -> None:
        self.config = config
        self.gui = gui
        self.delay = delay if delay is not None else int(config["simulation"].get("gui_delay", 20))
        self.max_steps = max_steps
        self.logger = DemoLogger(config["demo"].get("show_console_logs", True))

    def run(self) -> None:
        binary_name = "sumo-gui" if self.gui else "sumo"
        binary = shutil.which(binary_name)
        if not binary:
            raise RuntimeError(f"未找到 {binary_name}，请确认 SUMO 已安装并配置 PATH。")
        sumocfg = resolve_path(self.config["map"]["sumocfg_file"])
        net_file = resolve_path(self.config["map"]["net_file"])
        edge_sampler = EdgeSampler(net_file, seed=int(self.config["demo"].get("random_seed", 42)))

        self.logger.banner(
            [
                "[Demo] SUMO GUI 城市交通仿真展示启动",
                f"[Demo] 地图：{self.config['map'].get('city_label', self.config['map']['name'])}",
                f"[Demo] 司机数量：{self.config['demo']['num_drivers']}",
                "[Demo] 仿真时间：06:00 - 23:00",
            ]
        )

        cmd = [
            binary,
            "-c",
            str(sumocfg),
            "--start",
            "--delay",
            str(self.delay),
            "--no-step-log",
            "true",
            "--duration-log.disable",
            "true",
            "--quit-on-end",
            str(bool(self.config["simulation"].get("quit_on_end", False))).lower(),
        ]
        gui_settings_file = self.config["simulation"].get("gui_settings_file")
        if self.gui and gui_settings_file:
            cmd.extend(["--gui-settings-file", str(resolve_path(gui_settings_file))])
        controller = None
        sumo_process = None
        traci.start(cmd)
        sumo_process = getattr(traci.getConnection(), "_process", None)
        try:
            controller = DemoController(self.config, str(net_file), edge_sampler, self.logger)
            end_time = float(self.config["simulation"]["end_time"])
            step_count = 0
            keep_alive_for_events = bool(
                self.config.get("events", {}).get("use_events", False)
            )
            while traci.simulation.getTime() < end_time and (
                keep_alive_for_events
                or traci.simulation.getMinExpectedNumber() > 0
            ):
                if self.max_steps is not None and step_count >= self.max_steps:
                    break
                traci.simulationStep()
                controller.step(traci, traci.simulation.getTime())
                step_count += 1
        finally:
            if controller is not None:
                controller.close(traci)
            traci.close(wait=not self.gui)
            if self.gui and sumo_process is not None and sumo_process.poll() is None:
                # SUMO GUI may keep the window open after TraCI disconnects when
                # quit_on_end is false. End only the process started by this runner.
                sumo_process.terminate()
                try:
                    sumo_process.wait(timeout=3)
                except Exception:
                    sumo_process.kill()
            self.logger.line("[Demo] SUMO GUI 城市交通仿真展示结束。")
