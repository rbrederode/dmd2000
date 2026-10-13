"""Summarise weather logs and ZIP archives by station and UTC day.

Run from the repository root::

    python src/ws/analysis.py logs/weather --threshold 15 --output weather_daily.csv

Inputs can be directories or individual files. Readings are sorted by timestamp
using a temporary disk database, including logs with backward clock jumps.
Sample counts include polls with missing wind values. Wind statistics exclude
missing values, and remain blank when there are no valid wind readings.

A gust starts strictly above the threshold and ends strictly below it. Equality
and missing readings leave its state unchanged. A gust crossing midnight is
counted only on its starting day; gaps do not establish that a gust ended.
The first available above-threshold reading counts as a gust start.
"""

import argparse
import codecs
import csv
import math
import re
import sqlite3
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Iterator


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


def parse_readings(stream: Iterable[str], source: str, skip_invalid_lines: bool = False) -> Iterator[Reading]:
    """Parse current logs and older rows with a trailing observation timestamp."""
    previous = None
    clock_jumps = 0
    for line_number, line in enumerate(stream, 1):
        unpadded = line.lstrip("\x00")
        if unpadded != line:
            print(f"Warning: {source}:{line_number}: removed {len(line) - len(unpadded)} "
                  "leading NUL padding bytes; no readings recovered from the padding.", file=sys.stderr)
            line = unpadded
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
                clock_jumps += 1
        except ValueError as exc:
            if skip_invalid_lines:
                print(f"Warning: {source}:{line_number}: skipped invalid reading: {exc}", file=sys.stderr)
                continue
            raise ValueError(f"{source}:{line_number}: {exc}") from exc
        previous = timestamp
        yield Reading(timestamp, fields[1], wind)
    if clock_jumps:
        print(f"Warning: {source}: {clock_jumps} backward timestamp jump(s); "
              "readings will be sorted by recorded UTC time.", file=sys.stderr)


def discover_logs(inputs: Iterable[Path]) -> list[Path]:
    """Find weather logs; resolve repeated input paths once."""
    paths = set()
    for supplied in inputs:
        supplied = supplied.expanduser()
        if supplied.is_dir():
            paths.update(path.resolve() for path in supplied.iterdir()
                         if path.is_file() and not path.name.startswith(".")
                         and LOG_NAME.fullmatch(path.name))
        elif supplied.is_file():
            paths.add(supplied.resolve())
        else:
            raise ValueError(f"input does not exist: {supplied}")
    if not paths:
        raise ValueError("no weather logs found")
    return sorted(paths)


def decode_lines(stream: BinaryIO, source: str, encoding: str) -> Iterator[str]:
    """Decode strictly, reporting the exact input and line on failure."""
    for line_number, raw in enumerate(stream, 1):
        try:
            yield raw.decode(encoding)
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"{source}:{line_number}: cannot decode using {encoding}: {exc}. "
                "Use --encoding with the log's encoding (e.g. latin-1 for older "
                "single-byte logs)."
            ) from exc


def read_logs(paths: Iterable[Path], encoding: str = "utf-8",
              skip_invalid_lines: bool = False) -> Iterator[Reading]:
    """Sort on disk, keeping RAM bounded and opening one input at a time."""
    with tempfile.TemporaryDirectory(prefix="weather-analysis-") as directory:
        connection = sqlite3.connect(str(Path(directory) / "readings.sqlite"))
        try:
            connection.execute("PRAGMA temp_store=FILE")
            connection.execute("CREATE TABLE readings (timestamp TEXT, station_id TEXT, wind REAL)")

            def load(stream: BinaryIO, source: str) -> None:
                connection.executemany(
                    "INSERT INTO readings VALUES (?, ?, ?)",
                    ((r.timestamp.isoformat(timespec="microseconds"), r.station_id, r.wind)
                     for r in parse_readings(decode_lines(stream, source, encoding), source, skip_invalid_lines)),
                )

            for path in paths:
                if path.suffix == ".zip":
                    try:
                        with zipfile.ZipFile(path) as archive:
                            for member in archive.infolist():
                                parts = PurePosixPath(member.filename).parts
                                if member.is_dir() or any(part.startswith(".") or part == "__MACOSX" for part in parts):
                                    continue
                                with archive.open(member) as stream:
                                    load(stream, f"{path}!{member.filename}")
                    except zipfile.BadZipFile as exc:
                        raise ValueError(f"{path}: {exc}") from exc
                else:
                    with path.open("rb") as stream:
                        load(stream, str(path))
            connection.commit()
            # Exact overlapping timestamp/station/wind records count once.
            for timestamp, station_id, wind in connection.execute(
                "SELECT DISTINCT timestamp, station_id, wind FROM readings "
                "ORDER BY timestamp, station_id, wind"
            ):
                yield Reading(datetime.fromisoformat(timestamp), station_id, wind)
        finally:
            connection.close()


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
    parser.add_argument("--encoding", default="utf-8", help="input log encoding, e.g. latin-1 (default: utf-8); CSV output is UTF-8")
    parser.add_argument("--skip-invalid-lines", action="store_true", help="warn and skip malformed readings; default is to stop (decoding errors still stop)")
    args = parser.parse_args(argv)
    # Finish writing a temporary file before replacing an existing summary.
    import os

    temporary = None
    try:
        try:
            codecs.lookup(args.encoding)
        except LookupError as exc:
            raise ValueError(f"unknown input encoding: {args.encoding}") from exc
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
            for summary in daily_summaries(read_logs(paths, args.encoding, args.skip_invalid_lines), args.threshold):
                writer.writerow(summary.row(args.threshold))
                count += 1
        os.replace(temporary, output)
        print(f"Wrote {count} daily summary rows to {output}")
        return 0
    except (OSError, ValueError, zipfile.BadZipFile, EOFError, sqlite3.Error) as exc:
        print(f"Weather analysis failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
