import os
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, List, Optional, Tuple

AlarmEvent = Tuple[datetime, bool]  # (timestamp, alarm_active)
logger = logging.getLogger(__name__)


def _parse_log_timestamp(ts_str: str, end_tz) -> datetime | None:
    """Parse a log timestamp, supporting explicit UTC and legacy local-time lines."""
    if ts_str.endswith(" UTC"):
        try:
            ts = datetime.strptime(ts_str.removesuffix(" UTC"), "%Y-%m-%d %H:%M:%S,%f")
            ts = ts.replace(tzinfo=timezone.utc)
            return ts.astimezone(end_tz) if end_tz is not None else ts.replace(tzinfo=None)
        except ValueError:
            return None

    try:
        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f")
    except ValueError:
        return None

    local_tz = datetime.now().astimezone().tzinfo
    ts = ts.replace(tzinfo=local_tz)
    return ts.astimezone(end_tz) if end_tz is not None else ts.replace(tzinfo=None)


def _day_iter(start: datetime, end: datetime):
    """Generate days between start and end datetimes."""
    current = start.date()
    end_date = end.date()
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def collect_log_files(log_dir: str, log_name: str, start: datetime, end: datetime) -> List[str]:
    """Collect base and rotated log files for a reporting period."""
    base_log = os.path.join(log_dir, f"{log_name}.log")
    files = []
    seen = set()

    def add_if_exists(path: str):
        if os.path.exists(path) and path not in seen:
            files.append(path)
            seen.add(path)

    for day in _day_iter(start, end):
        add_if_exists(f"{base_log}.{day.strftime('%Y-%m-%d')}")

    add_if_exists(base_log)
    return files


def _iter_sanitized_log_lines(logfile: str):
    """Yield decoded log lines while tolerating embedded NUL bytes."""
    data = open(logfile, "rb").read()
    nul_count = data.count(b"\x00")
    if nul_count:
        logger.warning(
            "Alarm log %s contains %d NUL byte(s). This often indicates external truncation or rotation while the app still had the file open.",
            logfile,
            nul_count,
        )
        data = data.replace(b"\x00", b"")

    for line in data.decode("utf-8", errors="replace").splitlines():
        if line:
            yield line


def parse_alarm_transition_logs(
    log_files: Iterable[str],
    transition_parser: Callable[[str], Optional[bool]],
    end_period: datetime,
) -> List[AlarmEvent]:
    """Parse transition logs into ordered alarm-active / alarm-clear events.

    Parameters:
        log_files: Iterable of log file paths to inspect.
        transition_parser: Callback returning ``True`` for alarm active,
            ``False`` for alarm clear, and ``None`` for non-transition lines.
        end_period: Datetime used to normalize naive timestamps.
    """
    events: List[AlarmEvent] = []
    end_tz = end_period.tzinfo

    for logfile in log_files:
        for line in _iter_sanitized_log_lines(logfile):
            ts_str = line.split("|")[0].strip()
            ts = _parse_log_timestamp(ts_str, end_tz)
            if ts is None:
                continue

            state = transition_parser(line)
            if state is not None:
                events.append((ts, state))

    return sorted(events, key=lambda event: event[0])


def weather_alarm_transition_parser(line: str) -> Optional[bool]:
    """Parse DM weather alarm transition lines.

    Expected forms:
        WeatherAlarm transition state=ACTIVE
        WeatherAlarm transition state=CLEAR
    """
    match = re.search(r"WeatherAlarm transition state=(ACTIVE|CLEAR)", line)
    if not match:
        return None
    return match.group(1) == "ACTIVE"


def build_alarm_intervals(events: List[AlarmEvent], start: datetime, end: datetime) -> List[Tuple[bool, float]]:
    """Return alarm-active intervals covering the full reporting period."""
    intervals: List[Tuple[bool, float]] = []
    current_state = False
    current_time = start

    for ts, new_state in events:
        if ts < start:
            current_state = new_state
            continue
        if ts > end:
            break

        duration = (ts - current_time).total_seconds()
        if duration > 0:
            intervals.append((current_state, duration))

        current_state = new_state
        current_time = ts

    if current_time < end:
        intervals.append((current_state, (end - current_time).total_seconds()))

    return intervals


def get_alarm_rsp_efficiency(
    log_dir: str,
    log_name: str,
    start_period: datetime,
    end_period: datetime,
    transition_parser: Callable[[str], Optional[bool]] = weather_alarm_transition_parser,
) -> dict:
    """Calculate alarm-response efficiency metrics for a reporting period.

    Returns:
        A dictionary containing:
        - ``mean_time_to_alarm_sec``: mean duration of clear periods
        - ``mean_time_to_recovery_sec``: mean duration of active alarm periods
        - ``downtime_sec``: total time alarm was active
        - ``uptime_sec``: total time alarm was not active
    """
    log_files = collect_log_files(log_dir, log_name, start_period, end_period)
    events = parse_alarm_transition_logs(log_files, transition_parser, end_period)
    intervals = build_alarm_intervals(events, start_period, end_period)
    events_in_period = [(ts, state) for ts, state in events if start_period <= ts <= end_period]

    clear_durations = [duration for active, duration in intervals if not active]
    active_durations = [duration for active, duration in intervals if active]

    uptime_sec = sum(clear_durations)
    downtime_sec = sum(active_durations)

    mean_time_to_alarm_sec = sum(clear_durations) / len(clear_durations) if clear_durations else 0.0
    mean_time_to_recovery_sec = sum(active_durations) / len(active_durations) if active_durations else 0.0

    return {
        "mean_time_to_alarm_sec": mean_time_to_alarm_sec,
        "mean_time_to_recovery_sec": mean_time_to_recovery_sec,
        "downtime_sec": downtime_sec,
        "uptime_sec": uptime_sec,
        "alarm_count": len([1 for _, state in events_in_period if state]),
        "recovery_count": len([1 for _, state in events_in_period if not state]),
    }
