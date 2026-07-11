from __future__ import annotations

import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from src.utils.config_loader import ensure_parent, resolve_path


OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]


class MapBuildError(RuntimeError):
    pass


def _find_binary(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise MapBuildError(f"SUMO tool not found: {name}. Please check SUMO installation and PATH/SUMO_HOME.")
    return binary


def ensure_demo_map(config: dict, download_map: bool = False) -> Path:
    map_cfg = config["map"]
    net_file = resolve_path(map_cfg["net_file"])
    osm_file = resolve_path(map_cfg["osm_file"])
    visual_layers = bool(map_cfg.get("visual_layers", True)) and bool(map_cfg.get("poly_file"))
    poly_file = resolve_path(map_cfg["poly_file"]) if visual_layers else None

    if net_file.exists() and (not visual_layers or poly_file.exists()) and not download_map:
        return net_file

    if download_map or not osm_file.exists():
        if not (download_map or map_cfg.get("download_if_missing", False)):
            raise MapBuildError(
                f"Map file not found: {net_file}\n"
                f"Provide a SUMO net file, provide an OSM file at {osm_file}, or use --download-map."
            )
        download_osm(map_cfg, osm_file)

    if download_map or not net_file.exists():
        convert_osm_to_net(osm_file, net_file)

    if visual_layers and poly_file is not None and (download_map or not poly_file.exists()):
        convert_osm_to_poly(osm_file, net_file, poly_file)

    return net_file


def download_osm(map_cfg: dict, osm_file: Path) -> None:
    bbox = map_cfg["bbox"]
    query = f"""
    [out:xml][timeout:120][bbox:{bbox["south"]},{bbox["west"]},{bbox["north"]},{bbox["east"]}];
    (
      way["highway"];
      way["building"];
      relation["building"];
      way["landuse"];
      relation["landuse"];
      way["natural"];
      relation["natural"];
      way["waterway"];
      way["leisure"];
      relation["leisure"];
      way["amenity"];
      node["amenity"];
      way["tourism"];
      node["tourism"];
      relation["tourism"];
      way["shop"];
      node["shop"];
    );
    (._;>;);
    out body;
    """
    ensure_parent(osm_file)
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    print(f"[Map] Downloading OSM map: {map_cfg.get('city_label', map_cfg.get('name', 'demo_city'))}")
    errors: list[str] = []
    content = b""
    for endpoint in OVERPASS_URLS:
        try:
            req = urllib.request.Request(
                endpoint,
                data=data,
                headers={"User-Agent": "SUMO-GUI-Guangzhou-Demo/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                content = resp.read()
            break
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    else:
        raise MapBuildError(
            "OSM map download failed. Try again later or place a small .osm.xml map manually at:"
            f"\n{osm_file}\nOriginal errors:\n" + "\n".join(errors)
        )

    if b"<osm" not in content[:500]:
        raise MapBuildError(f"Overpass response does not look like OSM XML. Target path: {osm_file}")

    osm_file.write_bytes(content)
    print(f"[Map] OSM saved: {osm_file}")


def convert_osm_to_net(osm_file: Path, net_file: Path) -> None:
    netconvert = _find_binary("netconvert")
    ensure_parent(net_file)
    cmd = [
        netconvert,
        "--osm-files",
        str(osm_file),
        "--output-file",
        str(net_file),
        "--geometry.remove",
        "--roundabouts.guess",
        "--ramps.guess",
        "--junctions.join",
        "--tls.guess-signals",
        "--tls.discard-simple",
        "--remove-edges.isolated",
        "--no-turnarounds",
    ]
    print("[Map] Converting OSM to SUMO network...")
    result = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise MapBuildError(f"netconvert failed:\n{result.stderr or result.stdout}")
    print(f"[Map] SUMO network generated: {net_file}")


def convert_osm_to_poly(osm_file: Path, net_file: Path, poly_file: Path) -> None:
    polyconvert = _find_binary("polyconvert")
    typemap_file = _find_typemap_file()
    ensure_parent(poly_file)
    cmd = [
        polyconvert,
        "--net-file",
        str(net_file),
        "--osm-files",
        str(osm_file),
        "--type-file",
        str(typemap_file),
        "--output-file",
        str(poly_file),
        "--ignore-errors",
    ]
    print("[Map] Generating SUMO visual layers from OSM...")
    result = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise MapBuildError(f"polyconvert failed:\n{result.stderr or result.stdout}")
    print(f"[Map] SUMO visual layer file generated: {poly_file}")


def _find_typemap_file() -> Path:
    candidates: list[Path] = []
    sumo_gui = shutil.which("sumo-gui")
    if sumo_gui:
        candidates.append(Path(sumo_gui).resolve().parent.parent / "data" / "typemap" / "osmPolyconvert.typ.xml")
    candidates.append(Path("D:/Sumo/data/typemap/osmPolyconvert.typ.xml"))

    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise MapBuildError("Cannot find SUMO osmPolyconvert.typ.xml typemap file.")
