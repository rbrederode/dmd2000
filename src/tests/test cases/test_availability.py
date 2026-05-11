# tests/test_availability.py

import os
import tempfile
from datetime import datetime, timedelta

from util.availability import (
    get_app_availability,
    get_app_reliability
)

def write_log(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")

def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]

def test_basic_availability_ok_then_failed():
    with tempfile.TemporaryDirectory() as tmp:
        app = "dm"
        log = os.path.join(tmp, f"{app}.log")

        start = datetime(2026, 2, 2, 18, 0, 0)
        end   = datetime(2026, 2, 2, 18, 10, 0)

        lines = [
            f"{ts(start)} | INFO | App dm health state transition UNKNOWN -> OK",
            f"{ts(start + timedelta(minutes=5))} | INFO | App dm health state transition OK -> FAILED",
        ]

        write_log(log, lines)

        availability = get_app_availability(tmp, app, start, end)

        # 5 min OK, 5 min FAILED → 50%
        assert abs(availability - 50.0) < 0.01

def test_heartbeat_timeout_marks_failed():
    with tempfile.TemporaryDirectory() as tmp:
        app = "dm"
        log = os.path.join(tmp, f"{app}.log")

        start = datetime(2026, 2, 2, 18, 0, 0)
        end   = datetime(2026, 2, 2, 18, 5, 0)

        lines = [
            f"{ts(start)} | INFO | App dm health state transition UNKNOWN -> OK",
            f"{ts(start + timedelta(seconds=10))} | INFO | Heartbeat received",
            # heartbeat gap > 60s → FAILED at +70s
        ]

        write_log(log, lines)

        availability = get_app_availability(
            tmp, app, start, end, heartbeat_timeout_sec=60
        )

        # ~70s OK, rest FAILED
        ok_seconds = 70
        total_seconds = (end - start).total_seconds()
        expected = (ok_seconds / total_seconds) * 100

        assert abs(availability - expected) < 1.0

def test_reliability_simple_cycles():
    with tempfile.TemporaryDirectory() as tmp:
        app = "dm"
        log = os.path.join(tmp, f"{app}.log")

        start = datetime(2026, 2, 2, 18, 0, 0)

        lines = [
            f"{ts(start)} | INFO | App dm health state transition UNKNOWN -> OK",
            f"{ts(start + timedelta(minutes=2))} | INFO | App dm health state transition OK -> FAILED",
            f"{ts(start + timedelta(minutes=4))} | INFO | App dm health state transition FAILED -> OK",
            f"{ts(start + timedelta(minutes=6))} | INFO | App dm health state transition OK -> FAILED",
        ]

        write_log(log, lines)

        end = start + timedelta(minutes=8)

        metrics = get_app_reliability(tmp, app, start, end)

        # OK intervals: 2 min, 2 min → MTBF = 120s
        # FAILED intervals: 2 min, 2 min → MTTR = 120s
        assert abs(metrics["mtbf_sec"] - 120) < 1
        assert abs(metrics["mttr_sec"] - 120) < 1
        assert abs(metrics["reliability_availability"] - 50.0) < 0.5

def test_daily_rotated_logs():
    with tempfile.TemporaryDirectory() as tmp:
        app = "dm"

        jan_log = os.path.join(tmp, f"{app}.log.2026-01-31")
        feb_log = os.path.join(tmp, f"{app}.log")

        jan_start = datetime(2026, 1, 31, 23, 50, 0)
        feb_start = datetime(2026, 2, 1, 0, 0, 0)

        write_log(jan_log, [
            f"{ts(jan_start)} | INFO | App dm health state transition UNKNOWN -> OK",
        ])

        write_log(feb_log, [
            f"{ts(feb_start)} | INFO | App dm health state transition OK -> FAILED",
        ])

        start = jan_start
        end = feb_start + timedelta(minutes=10)

        availability = get_app_availability(tmp, app, start, end)

        # 10 min OK, 10 min FAILED → ~50%
        assert abs(availability - 50.0) < 5.0
