from __future__ import annotations

import csv
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from src.agents.intersection_selector import select_intersections
from src.utils.config_loader import ensure_parent, resolve_path


VTYPES = {
    "private_car": {"vClass": "passenger", "guiShape": "passenger", "color": "0,0,255", "accel": "2.6", "decel": "4.5", "sigma": "0.5", "length": "5"},
    "commuter": {"vClass": "passenger", "guiShape": "passenger", "color": "0,180,0", "accel": "2.6", "decel": "4.5", "sigma": "0.5", "length": "5"},
    "taxi": {"vClass": "passenger", "guiShape": "taxi", "color": "255,255,0", "accel": "2.6", "decel": "4.5", "sigma": "0.5", "length": "5"},
    "event_vehicle": {"vClass": "passenger", "guiShape": "passenger", "color": "255,128,0", "accel": "2.3", "decel": "4.2", "sigma": "0.6", "length": "5"},
    "rerouted_vehicle": {"vClass": "passenger", "guiShape": "passenger", "color": "160,32,240", "accel": "2.6", "decel": "4.5", "sigma": "0.5", "length": "5"},
}


class RouteWriter:
    def __init__(self, config: dict) -> None:
        self.config = config

    def write_all(self, trips_csv: str | Path) -> None:
        map_cfg = self.config["map"]
        trip_xml = resolve_path(map_cfg["trip_file"])
        route_file = resolve_path(map_cfg["route_file"])
        add_file = resolve_path(map_cfg["additional_file"])
        sumocfg_file = resolve_path(map_cfg["sumocfg_file"])
        self.write_trip_xml(trips_csv, trip_xml)
        self.run_duarouter(trip_xml, route_file)
        self.write_additional(add_file)
        self.write_sumocfg(sumocfg_file, route_file, add_file)

    def write_additional(self, add_file: str | Path) -> None:
        root = ET.Element("additional")
        if self.config.get("agents", {}).get("show_gui_markers", True):
            self._add_intersection_agent_markers(root)
        self._write_xml(root, add_file)

    def _add_intersection_agent_markers(self, root: ET.Element) -> None:
        marker_color = self.config.get("agents", {}).get("marker_color", "255,0,255")
        marker_size = float(self.config.get("agents", {}).get("marker_size", 45))
        marker_line_width = str(self.config.get("agents", {}).get("marker_line_width", 2))
        marker_poi_size = str(self.config.get("agents", {}).get("marker_poi_size", 4))
        candidates = select_intersections(resolve_path(self.config["map"]["net_file"]), self.config)
        for idx, candidate in enumerate(candidates, start=1):
            x, y = candidate.x, candidate.y
            junction_id = candidate.junction_id
            ET.SubElement(
                root,
                "poi",
                {
                    "id": f"agent_marker_{idx:02d}_{junction_id}",
                    "type": "IntersectionAgent",
                    "x": f"{x:.2f}",
                    "y": f"{y:.2f}",
                    "color": marker_color,
                    "layer": "250",
                    "width": marker_poi_size,
                    "height": marker_poi_size,
                },
            )
            half = marker_size / 2
            shape = [
                (x, y - half),
                (x + half, y),
                (x, y + half),
                (x - half, y),
                (x, y - half),
            ]
            ET.SubElement(
                root,
                "poly",
                {
                    "id": f"agent_box_{idx:02d}_{junction_id}",
                    "type": "IntersectionAgent",
                    "color": marker_color,
                    "fill": "false",
                    "layer": "249",
                    "lineWidth": marker_line_width,
                    "shape": " ".join(f"{px:.2f},{py:.2f}" for px, py in shape),
                },
            )

    def write_trip_xml(self, trips_csv: str | Path, trip_xml: str | Path) -> None:
        root = ET.Element("routes")
        for vtype_id, attrs in VTYPES.items():
            ET.SubElement(root, "vType", {"id": vtype_id, **attrs})
        with resolve_path(trips_csv).open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                ET.SubElement(
                    root,
                    "trip",
                    {
                        "id": row["trip_id"],
                        "type": row["vehicle_type"],
                        "depart": str(int(float(row["depart"]))),
                        "from": row["from_edge"],
                        "to": row["to_edge"],
                        "departLane": "best",
                        "departSpeed": "max",
                    },
                )
        self._write_xml(root, trip_xml)

    def write_route_xml_direct(self, trips_csv: str | Path, route_file: str | Path) -> None:
        net = sumolib.net.readNet(str(resolve_path(self.config["map"]["net_file"])), withInternal=False)
        edge_by_id = {edge.getID(): edge for edge in net.getEdges() if not edge.getID().startswith(":")}
        root = ET.Element("routes")
        for vtype_id, attrs in VTYPES.items():
            ET.SubElement(root, "vType", {"id": vtype_id, **attrs})

        skipped = 0
        written = 0
        with resolve_path(trips_csv).open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    path, _ = net.getOptimalPath(edge_by_id[row["from_edge"]], edge_by_id[row["to_edge"]])
                except Exception:
                    path = None
                if not path:
                    skipped += 1
                    continue
                vehicle = ET.SubElement(
                    root,
                    "vehicle",
                    {
                        "id": row["trip_id"],
                        "type": row["vehicle_type"],
                        "depart": str(int(float(row["depart"]))),
                        "departLane": "best",
                        "departSpeed": "max",
                    },
                )
                ET.SubElement(vehicle, "route", {"edges": " ".join(edge.getID() for edge in path)})
                written += 1
        self._write_xml(root, route_file)
        if skipped:
            print(f"[Route] 已跳过 {skipped} 条不可达出行，保留 {written} 条可运行车辆路线。")

    def run_duarouter(self, trip_xml: Path, route_file: Path) -> None:
        duarouter = shutil.which("duarouter")
        if not duarouter:
            raise RuntimeError("未找到 duarouter，请确认 SUMO 已安装并配置 PATH。")
        ensure_parent(route_file)
        cmd = [
            duarouter,
            "-n",
            str(resolve_path(self.config["map"]["net_file"])),
            "--route-files",
            str(trip_xml),
            "-o",
            str(route_file),
            "--ignore-errors",
            "--repair",
            "--remove-loops",
        ]
        print("[Route] 正在调用 duarouter 生成可运行 route...")
        result = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise RuntimeError(f"duarouter 失败：\n{result.stderr or result.stdout}")
        if result.stderr.strip():
            print(result.stderr.strip())
        self._ensure_route_has_vtypes(route_file)

    def write_sumocfg(self, sumocfg_file: str | Path, route_file: str | Path, add_file: str | Path) -> None:
        sim = self.config["simulation"]
        root = ET.Element("configuration")
        input_node = ET.SubElement(root, "input")
        ET.SubElement(input_node, "net-file", {"value": str(resolve_path(self.config["map"]["net_file"]))})
        ET.SubElement(input_node, "route-files", {"value": str(route_file)})
        additional_files = [str(add_file)]
        map_cfg = self.config.get("map", {})
        if map_cfg.get("visual_layers", True) and map_cfg.get("poly_file"):
            poly_file = resolve_path(map_cfg["poly_file"])
            if poly_file.exists():
                additional_files.append(str(poly_file))
        ET.SubElement(input_node, "additional-files", {"value": ",".join(additional_files)})
        time_node = ET.SubElement(root, "time")
        ET.SubElement(time_node, "begin", {"value": str(sim["begin_time"])})
        ET.SubElement(time_node, "end", {"value": str(sim["end_time"])})
        ET.SubElement(time_node, "step-length", {"value": str(sim["step_length"])})
        gui_node = ET.SubElement(root, "gui_only")
        ET.SubElement(gui_node, "start", {"value": str(bool(sim.get("auto_start", True))).lower()})
        ET.SubElement(gui_node, "delay", {"value": str(sim.get("gui_delay", 20))})
        self._write_xml(root, sumocfg_file)

    def _ensure_route_has_vtypes(self, route_file: Path) -> None:
        tree = ET.parse(route_file)
        root = tree.getroot()
        existing = {node.get("id"): node for node in root.findall("vType")}
        for vtype_id, attrs in VTYPES.items():
            if vtype_id in existing:
                existing[vtype_id].attrib.update(attrs)
        for vtype_id, attrs in reversed(list(VTYPES.items())):
            if vtype_id not in existing:
                root.insert(0, ET.Element("vType", {"id": vtype_id, **attrs}))
        tree.write(route_file, encoding="utf-8", xml_declaration=True)

    def _write_xml(self, root: ET.Element, path: str | Path) -> None:
        full_path = ensure_parent(path)
        ET.indent(root)
        ET.ElementTree(root).write(full_path, encoding="utf-8", xml_declaration=True)
