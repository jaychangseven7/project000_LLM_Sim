from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from src.events.event_spec import normalize_event_specs, specs_to_yaml_data
from src.utils.config_loader import ensure_parent, resolve_path


DEFAULT_SCENARIO_PATH = "data/scenarios/generated_events.yaml"

KW_ROAD_CLOSURE = ("\u5c01\u8def", "\u5173\u95ed", "\u7ba1\u5236", "closure", "closed")
KW_LARGE_EVENT = ("\u6d3b\u52a8", "\u6563\u573a", "\u4f53\u80b2", "\u6f14\u5531", "concert", "event")
KW_SEVERE = ("\u4e25\u91cd", "\u91cd\u5927", "severe", "high")
KW_LOW = ("\u8f7b\u5fae", "low", "minor")
KW_MAIN = ("\u4e3b\u5e72", "main")
KW_BUSINESS = ("CBD", "\u5546\u52a1")
KW_SCHOOL = ("\u5b66\u6821",)
KW_SHOPPING = ("\u5546\u573a",)
KW_EVENING_PEAK = ("\u665a\u9ad8\u5cf0",)
KW_MORNING_PEAK = ("\u65e9\u9ad8\u5cf0",)


class ScenarioGenerationError(RuntimeError):
    pass


def generate_event_scenario(
    prompt: str | None = None,
    input_file: str | Path | None = None,
    output_file: str | Path = DEFAULT_SCENARIO_PATH,
    edge_sampler=None,
    use_llm: bool = True,
) -> Path:
    raw_events = _load_structured_events(input_file) if input_file else None
    source = f"file={input_file}" if input_file else f"prompt={prompt}"
    if raw_events is None:
        prompt = prompt or "18:00 main road accident lasting 45 minutes"
        raw_events = _events_from_llm(prompt) if use_llm and os.getenv("OPENAI_API_KEY") else _events_from_rules(prompt)

    specs = normalize_event_specs(raw_events, edge_sampler=edge_sampler)
    if not specs:
        raise ScenarioGenerationError("No valid events generated from scenario input.")

    path = _write_scenario_yaml(output_file, specs_to_yaml_data(specs))

    print(f"[ScenarioGenerator] source={source}", flush=True)
    print(f"[ScenarioGenerator] generated_events={len(specs)} output={path}", flush=True)
    for spec in specs:
        print(
            f"[ScenarioGenerator] event={spec.event_id} type={spec.type} "
            f"time={_hhmm(spec.start_time)}-{_hhmm(spec.end_time)} "
            f"edges={len(spec.affected_edges)} severity={spec.severity} desc={spec.description}",
            flush=True,
        )
    return path


def _write_scenario_yaml(output_file: str | Path, data: dict[str, Any]) -> Path:
    path = ensure_parent(output_file)
    try:
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
        return path
    except PermissionError:
        fallback = ensure_parent("config/generated_events.yaml")
        with fallback.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
        print(f"[ScenarioGenerator] output denied, fallback={fallback}", flush=True)
        return fallback


def _load_structured_events(input_file: str | Path | None) -> list[dict[str, Any]]:
    if input_file is None:
        return []
    path = resolve_path(input_file)
    with path.open("r", encoding="utf-8") as fh:
        if path.suffix.lower() == ".json":
            data = json.load(fh)
        else:
            data = yaml.safe_load(fh) or {}
    if isinstance(data, list):
        return data
    return list(data.get("events", []))


def _events_from_rules(prompt: str) -> list[dict[str, Any]]:
    text = prompt.strip()
    start = _extract_start_time(text)
    duration = _extract_duration(text)
    event_type = _infer_event_type(text)
    severity = _infer_severity(text)
    event: dict[str, Any] = {
        "event_id": f"{event_type}_{start}",
        "type": event_type,
        "start_time": start,
        "end_time": start + duration,
        "severity": severity,
        "location": _infer_location(text),
        "description": text,
        "parameters": {},
    }
    if event_type == "large_event":
        event["zone"] = "event_zone"
        event["parameters"] = {
            "vehicle_count": _extract_vehicle_count(text),
            "vehicle_type": "event_vehicle",
        }
    elif event_type == "accident":
        event["parameters"] = {"speed_limit": {"low": 6.0, "medium": 4.0, "high": 2.0, "severe": 1.5}[severity]}
    return [event]


def _events_from_llm(prompt: str) -> list[dict[str, Any]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _events_from_rules(prompt)
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Convert a SUMO traffic scenario request into JSON only. "
                    "Return {\"events\":[...]} with event types limited to accident, road_closure, large_event. "
                    "Times may be HH:MM strings or seconds. Use parameters for speed_limit or vehicle_count."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return list(parsed.get("events", []))
    except Exception as exc:
        print(f"[ScenarioGenerator] LLM generation failed, fallback to rules: {exc}", flush=True)
        return _events_from_rules(prompt)


def _infer_event_type(text: str) -> str:
    if any(keyword in text for keyword in KW_ROAD_CLOSURE):
        return "road_closure"
    if any(keyword in text for keyword in KW_LARGE_EVENT):
        return "large_event"
    return "accident"


def _infer_severity(text: str) -> str:
    if any(keyword in text for keyword in KW_SEVERE):
        return "high"
    if any(keyword in text for keyword in KW_LOW):
        return "low"
    return "medium"


def _infer_location(text: str) -> str:
    if any(keyword in text for keyword in KW_MAIN):
        return "main_edges"
    if any(keyword in text for keyword in KW_BUSINESS):
        return "business_zone"
    if any(keyword in text for keyword in KW_SCHOOL):
        return "school_zone"
    if any(keyword in text for keyword in KW_SHOPPING):
        return "shopping_zone"
    return "auto"


def _extract_start_time(text: str) -> int:
    match = re.search(r"(\d{1,2})[:\uff1a](\d{1,2})", text)
    if match:
        return int(match.group(1)) * 3600 + int(match.group(2)) * 60
    match = re.search(r"(\d{1,2})\s*(?:\u70b9|\u65f6|h)", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1)) * 3600
    if any(keyword in text for keyword in KW_EVENING_PEAK):
        return 18 * 3600
    if any(keyword in text for keyword in KW_MORNING_PEAK):
        return 8 * 3600
    return 18 * 3600


def _extract_duration(text: str) -> int:
    match = re.search(r"(\d+)\s*(?:\u5206\u949f|min|minutes)", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1)) * 60
    match = re.search(r"(\d+)\s*(?:\u5c0f\u65f6|hour|hours)", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1)) * 3600
    return 45 * 60


def _extract_vehicle_count(text: str) -> int:
    match = re.search(r"(\d+)\s*(?:\u8f86|\u53f0|vehicles|cars)", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 250


def _hhmm(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}"
