import csv
from datetime import datetime, timedelta
import logging
import os
import re
from typing import List, Tuple

Event = Tuple[datetime, str]  # (timestamp, state)

def _day_iter(start: datetime, end: datetime):
    """Generate days between start and end datetimes."""
    current = start.date()
    end_date = end.date()
    while current <= end_date:
        yield current
        current += timedelta(days=1)

def _collect_log_files(log_dir: str, app_name: str, start: datetime, end: datetime) -> List[str]:
    """ Collect log files for the given app within the date range. 
    Args:
        log_dir (str): Directory where log files are stored.
        app_name (str): Name of the application.
        start (datetime): Start datetime for log collection.
        end (datetime): End datetime for log collection.
    Returns:
        List[str]: List of log file paths.
    """
    base_log = os.path.join(log_dir, f"{app_name}.log")
    files = []
    seen = set()

    def add_if_exists(path: str):
        if os.path.exists(path) and path not in seen:
            files.append(path)
            seen.add(path)

    # Daily rotated logs used by the current availability logger.
    for day in _day_iter(start, end):
        add_if_exists(f"{base_log}.{day.strftime('%Y-%m-%d')}")

    add_if_exists(base_log)

    return files

def _parse_logs(log_files: List[str], app_name: str, heartbeat_timeout_sec: int, end_period: datetime) -> List[Event]:

    """ Parse log files to extract state transitions and heartbeat timeouts.
    Args:
        log_files (List[str]): List of log file paths.
        app_name (str): Name of the application.
        heartbeat_timeout_sec (int): Heartbeat timeout in seconds.
    Returns:
        List[Event]: List of events (timestamp, state).
    """
    state_pattern = re.compile(rf"App {re.escape(app_name)} health state transition .* -> (\w+)")
    heartbeat_pattern = re.compile(r"Heartbeat(?:\s+state=(\w+))?")

    transitions = []
    heartbeats = []

    end_tz = end_period.tzinfo

    for logfile in log_files:
        with open(logfile, "r", encoding="utf-8") as f:
            for line in f:
                ts_str = line.split("|")[0].strip()
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f")
                except ValueError:
                    logging.warning(f"Bad timestamp in {logfile}: {line.strip()}")
                    continue

                # Normalize timezone to match end_period to avoid naive/aware mixing
                if end_tz is not None and ts.tzinfo is None:
                    ts = ts.replace(tzinfo=end_tz)
                elif end_tz is None and ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)

                if m := state_pattern.search(line):
                    transitions.append((ts, m.group(1)))
                elif m := heartbeat_pattern.search(line):
                    heartbeats.append(ts)
                    heartbeat_state = m.group(1)
                    if heartbeat_state is not None:
                        transitions.append((ts, heartbeat_state))

    transitions.sort()
    heartbeats.sort()

    events: List[Event] = []

    # State transitions
    for ts, state in transitions:
        events.append((ts, state))

    # Heartbeat timeouts → FAILED
    for i in range(1, len(heartbeats)):
        gap = (heartbeats[i] - heartbeats[i - 1]).total_seconds()
        if gap > heartbeat_timeout_sec:
            timeout = heartbeats[i - 1] + timedelta(seconds=heartbeat_timeout_sec)
            events.append((timeout, "FAILED"))

    # Final heartbeat timeout relative to end_period
    if heartbeats:
        last_hb = heartbeats[-1]
        gap = (end_period - last_hb).total_seconds()
        if gap > heartbeat_timeout_sec:
            timeout = last_hb + timedelta(seconds=heartbeat_timeout_sec)
            events.append((timeout, "FAILED"))

    return sorted(events, key=lambda e: e[0])

def _build_state_intervals(events: List[Event], start: datetime, end: datetime) -> List[Tuple[str, float]]:
    """
    Returns list of (state, duration_seconds), covering full [start, end] period
    """
    intervals = []
    current_state = "UNKNOWN"
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

    # Make sure we capture the final interval to end
    if current_time < end:
        intervals.append((current_state, (end - current_time).total_seconds()))

    return intervals

def get_app_availability(log_dir: str, app_name: str, start_period: datetime, end_period: datetime, state_weights: dict = None, heartbeat_timeout_sec: int = 60) -> float:
    """ Calculate the availability percentage of an application over a specified period.
    Args:
        log_dir (str): Directory where log files are stored.
        app_name (str): Name of the application.
        start_period (datetime): Start datetime for availability calculation.
        end_period (datetime): End datetime for availability calculation.
        state_weights (dict, optional): Weights for each state. Defaults to None.
        heartbeat_timeout_sec (int, optional): Heartbeat timeout in seconds. Defaults to 60.
    Returns:
        float: Availability percentage (0.0 to 100.0).
    """
    
    if state_weights is None:
        state_weights = {
            "OK": 1.0,
            "DEGRADED": 0.5,
            "FAILED": 0.0,
            "UNKNOWN": 0.0
        }

    logs = _collect_log_files(log_dir, app_name, start_period, end_period)
    events = _parse_logs(logs, app_name, heartbeat_timeout_sec, end_period)
    intervals = _build_state_intervals(events, start_period, end_period)

    weighted_up = sum(
        duration * state_weights.get(state, 0.0)
        for state, duration in intervals
    )

    total = (end_period - start_period).total_seconds()
    return (weighted_up / total) * 100 if total > 0 else 0.0

def get_app_reliability(log_dir: str, app_name: str, start_period: datetime, end_period: datetime, heartbeat_timeout_sec: int = 60) -> dict:
    """ Calculate the reliability metrics (MTBF, MTTR, Availability) of an application over a specified period.
    Args:
        log_dir (str): Directory where log files are stored.
        app_name (str): Name of the application.
        start_period (datetime): Start datetime for reliability calculation.
        end_period (datetime): End datetime for reliability calculation.
        heartbeat_timeout_sec (int, optional): Heartbeat timeout in seconds. Defaults to 60.
    Returns:
        dict: Dictionary with MTBF, MTTR, and Availability percentage.
    """

    logs = _collect_log_files(log_dir, app_name, start_period, end_period)
    events = _parse_logs(logs, app_name, heartbeat_timeout_sec, end_period)
    intervals = _build_state_intervals(events, start_period, end_period)

   # Only "OK" and "DEGRADED" are UP, everything else is DOWN
    up_durations = [duration for state, duration in intervals if state in ["OK", "DEGRADED"]]
    down_durations = [duration for state, duration in intervals if state not in ["OK", "DEGRADED"]]

    mtbf = sum(up_durations) / len(up_durations) if up_durations else 0.0
    mttr = sum(down_durations) / len(down_durations) if down_durations else 0.0

    reliability = (mtbf / (mtbf + mttr) * 100) if (mtbf + mttr) > 0 else 100.0

    return {
        "mtbf_sec": mtbf,
        "mttr_sec": mttr,
        "reliability": reliability,
        "reliability_availability": reliability,
    }

def generate_report(log_dir: str, app_name: str, start_period: datetime, end_period: datetime, bucket_minutes: int = 60, heartbeat_timeout_sec: int = 60,
                            state_weights: dict = None,  output_csv: str = None) -> None:
    """
    Compute rolling availability and reliability for an app, split into time buckets.

    Args:
        log_dir (str): Directory with log files.
        app_name (str): Application name.
        start_period (datetime): Start of analysis period.
        end_period (datetime): End of analysis period.
        bucket_minutes (int, optional): Duration of each bucket in minutes. Defaults to 60.
        heartbeat_timeout_sec (int, optional): Heartbeat timeout in seconds. Defaults to 60.
        state_weights (dict, optional): Weights for states. Defaults to OK=1.0, DEGRADED=0.5, FAILED=0.0.
        output_csv (str, optional): CSV file path to write results. Defaults to None.
    """
    if state_weights is None:
        state_weights = {"OK": 1.0, "DEGRADED": 0.5, "FAILED": 0.0, "UNKNOWN": 0.0}

    # Collect logs and parse events
    logs = _collect_log_files(log_dir, app_name, start_period, end_period)
    events = _parse_logs(logs, app_name, heartbeat_timeout_sec, end_period)

    # Generate buckets
    bucket_delta = timedelta(minutes=bucket_minutes)
    bucket_start = start_period
    results: List[Dict] = []

    while bucket_start < end_period:
        bucket_end = min(bucket_start + bucket_delta, end_period)

        # Filter events for this bucket
        bucket_intervals = _build_state_intervals(events, bucket_start, bucket_end)

        # Weighted availability
        weighted_up = sum(duration * state_weights.get(state, 0.0) for state, duration in bucket_intervals)
        total_duration = sum(duration for _, duration in bucket_intervals)
        availability = (weighted_up / total_duration * 100.0) if total_duration > 0 else 0.0

        # MTBF / MTTR
        up_durations = [duration for state, duration in bucket_intervals if state == "OK"]
        down_durations = [duration for state, duration in bucket_intervals if state != "OK"]

        mtbf = sum(up_durations) / len(up_durations) if up_durations else 0.0
        mttr = sum(down_durations) / len(down_durations) if down_durations else 0.0
        reliability_availability = (mtbf / (mtbf + mttr) * 100.0) if (mtbf + mttr) > 0 else 100.0

        results.append({
            "bucket_start": bucket_start,
            "bucket_end": bucket_end,
            "availability_pct": round(availability, 2),
            "mtbf_sec": round(mtbf, 2),
            "mttr_sec": round(mttr, 2),
            "reliability_availability_pct": round(reliability_availability, 2)
        })

        bucket_start = bucket_end

    if output_csv is None:
        output_csv = f"{app_name}_{start_period.strftime('%Y%m%d%H%M')}_{end_period.strftime('%Y%m%d%H%M')}.csv"

    # Write CSV
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        for row in results:
            writer.writerow(row)

    print(f"Rolling metrics written to {output_csv}")

if __name__ == "__main__":
    # Example usage
    log_directory = os.path.expanduser("./logs/availability")
    app = "dm"
    start = datetime(2026, 2, 2, 18, 48, 0)
    end = datetime(2026, 2, 3, 21, 40, 0)

    availability_percentage = get_app_availability(log_directory, app, start, end)
    reliability_metrics = get_app_reliability(log_directory, app, start, end)
    generate_report(log_directory, app, start, end)

    print(f"Reliability metrics for {app} from {start} to {end}:")
    print(reliability_metrics)
    print(f"Availability for {app} from {start} to {end}: {availability_percentage:.2f}%")
