from __future__ import annotations

from dataclasses import dataclass

from src.events.event_model import TrafficEvent


@dataclass(frozen=True)
class TimeRule:
    earliest_start: int
    latest_start: int
    default_start: int
    label: str


# Guangzhou weekday peak periods follow DB4401/T 57. Concert traffic is
# represented by an arrival window before a typical 19:30 performance.
TIME_RULES = {
    "morning_peak": TimeRule(7 * 3600, 8 * 3600, 7 * 3600, "工作日早高峰"),
    "evening_peak": TimeRule(17 * 3600, 18 * 3600, 17 * 3600, "工作日晚高峰"),
    "concert": TimeRule(17 * 3600, 20 * 3600, 18 * 3600, "晚间演出进场"),
    "construction": TimeRule(9 * 3600, 15 * 3600, 9 * 3600, "日间计划施工"),
}


def validate_event_time(
    event: TrafficEvent,
    simulation_begin: float,
    time_mode: str,
    auto_correct: bool = True,
) -> list[str]:
    rule = _rule_for(event)
    if rule is None:
        return []

    absolute_start = (
        simulation_begin + event.start_time
        if time_mode == "relative"
        else event.start_time
    )
    second_of_day = int(absolute_start) % (24 * 3600)
    if rule.earliest_start <= second_of_day <= rule.latest_start:
        return []

    original_clock = _clock(second_of_day)
    message = (
        f"不合理时间：{rule.label}不应从{original_clock}开始；"
        f"允许开始窗口为{_clock(rule.earliest_start)}–{_clock(rule.latest_start)}"
    )
    if not auto_correct:
        return [message]

    duration = event.end_time - event.start_time
    corrected_absolute = (
        int(absolute_start // (24 * 3600)) * 24 * 3600 + rule.default_start
    )
    corrected_start = (
        corrected_absolute - simulation_begin
        if time_mode == "relative"
        else corrected_absolute
    )
    event.start_time = max(0.0, float(corrected_start))
    event.end_time = event.start_time + duration
    return [f"{message}；已自动调整到{_clock(rule.default_start)}"]


def _rule_for(event: TrafficEvent) -> TimeRule | None:
    if event.event_type == "rush_hour":
        if event.effects.get("peak_type") == "evening":
            return TIME_RULES["evening_peak"]
        return TIME_RULES["morning_peak"]
    if event.event_type in {"concert", "large_event"}:
        return TIME_RULES["concert"]
    if event.event_type in {"construction", "road_closure"}:
        return TIME_RULES["construction"]
    return None


def _clock(seconds: int) -> str:
    seconds %= 24 * 3600
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}"
