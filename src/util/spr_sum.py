"""Aggregate channel power from a range of observation SPR CSV files.

Channel indices are zero-based and inclusive. Output rows are numbered from one
in increasing scan-ID order, preserving the row order within each scan file.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
from pathlib import Path
from typing import Iterable, Sequence


ScanId = tuple[int, int, int]
ScanFile = tuple[ScanId, Path]


def parse_scan_id(value: str) -> ScanId:
    """Parse ``tgt_id-freq_scan-scan_iter`` into a numeric scan ID."""

    match = re.fullmatch(r"(\d+)-(\d+)-(\d+)", value.strip())
    if match is None:
        raise argparse.ArgumentTypeError(
            f"invalid scan ID {value!r}; expected tgt_id-freq_scan-scan_iter"
        )
    return tuple(int(part) for part in match.groups())


def _normalise_observation_id(observation_id: str) -> str:
    """Return the observation-ID form used by generated scan filenames."""

    return re.sub(r'[:\\/*?"<>|]', "", observation_id).lower()


def discover_spr_files(
    directory: Path,
    observation_id: str,
    start_scan: ScanId,
    end_scan: ScanId,
) -> list[ScanFile]:
    """Find matching SPR files in the requested inclusive scan-ID range."""

    if not directory.is_dir():
        raise ValueError(f"sample directory does not exist or is not a directory: {directory}")
    if start_scan > end_scan:
        raise ValueError(
            f"start scan {format_scan_id(start_scan)} is after end scan "
            f"{format_scan_id(end_scan)}"
        )

    filename_prefix = re.escape(_normalise_observation_id(observation_id))
    pattern = re.compile(
        rf"^{filename_prefix}-(\d+)-(\d+)-(\d+)-.+-spr\.csv$",
        re.IGNORECASE,
    )
    matches: list[ScanFile] = []

    for path in directory.iterdir():
        if not path.is_file():
            continue
        match = pattern.fullmatch(path.name)
        if match is None:
            continue
        scan_id = tuple(int(part) for part in match.groups())
        if start_scan <= scan_id <= end_scan:
            matches.append((scan_id, path))

    matches.sort(key=lambda item: (item[0], item[1].name.lower()))
    if not matches:
        raise ValueError(
            f"no SPR files found for observation {observation_id!r} in {directory} "
            f"from {format_scan_id(start_scan)} through {format_scan_id(end_scan)}"
        )
    return matches


def aggregate_rows(
    scan_files: Iterable[ScanFile],
    channel_start: int,
    channel_end: int,
) -> list[float]:
    """Sum the requested inclusive channel range for every selected SPR row."""

    if channel_start < 0 or channel_end < 0:
        raise ValueError("channel indices must be non-negative")
    if channel_start > channel_end:
        raise ValueError("channel start must not be greater than channel end")

    sums: list[float] = []
    for _scan_id, path in scan_files:
        with path.open("r", newline="") as csv_file:
            reader = csv.reader(csv_file)
            for source_row, fields in enumerate(reader, start=1):
                if not fields or all(not field.strip() for field in fields):
                    continue
                if channel_end >= len(fields):
                    raise ValueError(
                        f"{path.name} row {source_row} has {len(fields)} channels; "
                        f"channel {channel_end} was requested"
                    )
                try:
                    selected = (
                        float(fields[channel])
                        for channel in range(channel_start, channel_end + 1)
                    )
                    sums.append(math.fsum(selected))
                except ValueError as exc:
                    raise ValueError(
                        f"{path.name} row {source_row} contains non-numeric channel data"
                    ) from exc
    return sums


def write_aggregate(output_path: Path, power_sums: Iterable[float]) -> int:
    """Write one-based aggregate row numbers and summed power values."""

    row_count = 0
    with output_path.open("w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(("row", "summed_power"))
        for row_count, power_sum in enumerate(power_sums, start=1):
            writer.writerow((row_count, format(power_sum, ".17g")))
    return row_count


def format_scan_id(scan_id: ScanId) -> str:
    """Format a numeric scan ID for messages."""

    return "-".join(str(part) for part in scan_id)


def aggregate_observation(
    observation_id: str,
    directory: Path,
    start_scan: ScanId,
    end_scan: ScanId,
    channel_start: int,
    channel_end: int,
) -> tuple[Path, int, int]:
    """Select, aggregate, and write an observation's requested SPR rows."""

    if not observation_id or Path(observation_id).name != observation_id or "\\" in observation_id:
        raise ValueError("observation ID must be a non-empty filename-safe identifier")

    scan_files = discover_spr_files(directory, observation_id, start_scan, end_scan)
    power_sums = aggregate_rows(scan_files, channel_start, channel_end)
    output_path = directory / f"{observation_id}-agg.csv"
    row_count = write_aggregate(output_path, power_sums)
    return output_path, len(scan_files), row_count


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""

    parser = argparse.ArgumentParser(
        description="Sum an inclusive channel range for rows in observation SPR files."
    )
    parser.add_argument("-o", "--observation-id", required=True, help="Observation ID")
    parser.add_argument(
        "-d",
        "--directory",
        required=True,
        type=Path,
        help="Directory containing SPR CSV files",
    )
    parser.add_argument(
        "-ss",
        "--start-scan",
        required=True,
        type=parse_scan_id,
        help="Inclusive first scan as tgt_id-freq_scan-scan_iter",
    )
    parser.add_argument(
        "-se",
        "--end-scan",
        required=True,
        type=parse_scan_id,
        help="Inclusive last scan as tgt_id-freq_scan-scan_iter",
    )
    parser.add_argument(
        "-cs",
        "--channel-start",
        required=True,
        type=int,
        help="Inclusive zero-based first channel",
    )
    parser.add_argument(
        "-ce",
        "--channel-end",
        required=True,
        type=int,
        help="Inclusive zero-based last channel",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the SPR aggregation command."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        output_path, file_count, row_count = aggregate_observation(
            observation_id=args.observation_id,
            directory=args.directory.expanduser(),
            start_scan=args.start_scan,
            end_scan=args.end_scan,
            channel_start=args.channel_start,
            channel_end=args.channel_end,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(f"Wrote {row_count} rows from {file_count} SPR files to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
