"""Summarise weather logs and ZIP archives by station and UTC day.

Run from the repository root::

    python src/ws/analysis.py logs/weather --threshold 15 --output weather_daily.csv

Inputs can be directories or individual files. Each log must be in timestamp
order, as produced by the weather logger; files are merged chronologically.
Sample counts include polls with missing wind values. Wind statistics exclude
missing values, and remain blank when there are no valid wind readings.

A gust starts strictly above the threshold and ends strictly below it. Equality
and missing readings leave its state unchanged. A gust crossing midnight is
counted only on its starting day; gaps do not establish that a gust ended.
The first available above-threshold reading counts as a gust start.
"""

import argparse
import csv
import heapq
import math
import re
import sys
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date, datetime, timezone
from io import TextIOWrapper
from pathlib import Path
from typing import Iterable, Iterator, TextIO


COLUMNS = (
    "date", "weather_station_id", "sample_count", "min_wind_m_s",
    "max_wind_m_s", "avg_wind_m_s", "time_of_max_wind_utc",
    "readings_above_threshold", "threshold_m_s", "gust_count",
    "first_reading_datetime_utc", "last_reading_datetime_utc",
)
LOG_NAME = re.compile(r".+\.log(?:\.\d{4}-\d{2}-\d{2}(?:\.zip)?)?$")


@dataclass(frozen=True)
class Reading:
    timestamp: datetime
    station_id: str
    wind: float | None


def parse_readings(stream: TextIO, source: str) -> Iterator[Reading]:
    """Parse current logs and older rows with a trailing observation timestamp."""
    previous = None
    for line_number, line in enumerate(stream, 1):
        if not line.strip():
            continue
        try:
            fields = [field.strip() for field in line.split("|")]
            if len(fields) not in (8, 9) or not fields[1]:
                raise ValueError("expected timestamp, station, health and five measurements")
            timestamp = datetime.strptime(fields[0], "%Y-%m-%d %H:%M:%S,%f UTC").replace(
                tzinfo=timezone.utc
            )
            wind = None if fields[3] == "None" else float(fields[3])
            if wind is not None and (not math.isfinite(wind) or wind < 0):
                raise ValueError("wind must be finite and non-negative, or None")
            if previous is not None and timestamp < previous:
                raise ValueError("log timestamps are not in chronological order")
        except ValueError as exc:
            raise ValueError(f"{source}:{line_number}: {exc}") from exc
        previous = timestamp
        yield Reading(timestamp, fields[1], wind)


def discover_logs(inputs: Iterable[Path]) -> list[Path]:
    """Find weather logs; resolve repeated input paths once."""
    paths = set()
    for supplied in inputs:
        supplied = supplied.expanduser()
        if supplied.is_dir():
            paths.update(path.resolve() for path in supplied.iterdir()
                         if path.is_file() and LOG_NAME.fullmatch(path.name))
        elif supplied.is_file():
            paths.add(supplied.resolve())
        else:
            raise ValueError(f"input does not exist: {supplied}")
    if not paths:
        raise ValueError("no weather logs found")
    return sorted(paths)


def read_logs(paths: Iterable[Path]) -> Iterator[Reading]:
    """Stream and merge plain logs and ZIP members without extracting files."""
    with ExitStack() as stack:
        streams = []
        for path in paths:
            if path.suffix == ".zip":
                archive = stack.enter_context(zipfile.ZipFile(path))
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    stream = stack.enter_context(TextIOWrapper(archive.open(member), encoding="utf-8"))
                    streams.append(parse_readings(stream, f"{path}!{member.filename}"))
            else:
                stream = stack.enter_context(path.open(encoding="utf-8"))
                streams.append(parse_readings(stream, str(path)))
        # Identical records from overlapping inputs are included only once.
        seen_at_timestamp = set()
        previous_timestamp = None
        for reading in heapq.merge(*streams, key=lambda item: item.timestamp):
            if reading.timestamp != previous_timestamp:
                seen_at_timestamp.clear()
                previous_timestamp = reading.timestamp
            if reading not in seen_at_timestamp:
                seen_at_timestamp.add(reading)
                yield reading


@dataclass
class DailySummary:
    day: date
    station_id: str
    first: datetime
    last: datetime
    sample_count: int = 0
    valid_count: int = 0
    minimum: float | None = None
    maximum: float | None = None
    mean: float = 0.0
    maximum_time: datetime | None = None
    above_count: int = 0
    gust_count: int = 0

    def add(self, reading: Reading, threshold: float, gust_started: bool) -> None:
        self.sample_count += 1
        self.last = reading.timestamp
        if reading.wind is None:
            return
        wind = reading.wind
        self.valid_count += 1
        self.mean += (wind - self.mean) / self.valid_count
        self.minimum = wind if self.minimum is None else min(self.minimum, wind)
        if self.maximum is None or wind > self.maximum:
            self.maximum = wind
            self.maximum_time = reading.timestamp
        self.above_count += int(wind > threshold)
        self.gust_count += int(gust_started)

    def row(self, threshold: float) -> list:
        def number(value):
            return "" if value is None else f"{value:.6f}"

        return [
            self.day.isoformat(), self.station_id, self.sample_count,
            number(self.minimum), number(self.maximum),
            number(self.mean if self.valid_count else None),
            self.maximum_time.time().isoformat(timespec="milliseconds") if self.maximum_time else "",
            self.above_count, threshold, self.gust_count,
            self.first.isoformat(timespec="milliseconds"),
            self.last.isoformat(timespec="milliseconds"),
        ]


def daily_summaries(readings: Iterable[Reading], threshold: float) -> Iterator[DailySummary]:
    """Aggregate chronological readings, retaining only one day's summaries."""
    if not math.isfinite(threshold) or threshold < 0:
        raise ValueError("threshold must be finite and non-negative")
    summaries = {}
    gust_active = {}
    current_day = None
    for reading in readings:
        day = reading.timestamp.date()
        if current_day is not None and day != current_day:
            yield from (summaries[key] for key in sorted(summaries))
            summaries.clear()
        current_day = day
        active = gust_active.get(reading.station_id, False)
        started = reading.wind is not None and reading.wind > threshold and not active
        if started:
            gust_active[reading.station_id] = True
        elif reading.wind is not None and reading.wind < threshold:
            gust_active[reading.station_id] = False
        if reading.station_id not in summaries:
            summaries[reading.station_id] = DailySummary(
                day, reading.station_id, reading.timestamp, reading.timestamp
            )
        summaries[reading.station_id].add(reading, threshold, started)
    yield from (summaries[key] for key in sorted(summaries))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+", type=Path, help="weather log directories, plain logs or ZIP archives")
    parser.add_argument("--threshold", required=True, type=float, help="wind threshold in m/s (strictly exceeded)")
    parser.add_argument("--output", type=Path, default=Path("weather_daily.csv"), help="summary CSV (default: weather_daily.csv)")
    args = parser.parse_args(argv)
    # Finish writing a temporary file before replacing an existing summary.
    import os
    import tempfile

    temporary = None
    try:
        paths = discover_logs(args.inputs)
        output = args.output.expanduser().resolve()
        if output in paths:
            raise ValueError("output must not overwrite an input log")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=output.parent,
                                         prefix=".weather-summary-", suffix=".csv", delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.writer(stream)
            writer.writerow(COLUMNS)
            count = 0
            for summary in daily_summaries(read_logs(paths), args.threshold):
                writer.writerow(summary.row(args.threshold))
                count += 1
        os.replace(temporary, output)
        print(f"Wrote {count} daily summary rows to {output}")
        return 0
    except (OSError, ValueError, zipfile.BadZipFile, EOFError) as exc:
        print(f"Weather analysis failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
