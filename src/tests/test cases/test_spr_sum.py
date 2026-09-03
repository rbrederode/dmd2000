import csv
import subprocess
import sys
from pathlib import Path

import pytest

from util.spr_sum import aggregate_observation, discover_spr_files, parse_scan_id


def _write_spr(path: Path, rows: list[list[float]]) -> None:
    with path.open("w", newline="") as csv_file:
        csv.writer(csv_file).writerows(rows)


def test_aggregate_observation_orders_scans_and_uses_inclusive_channels(tmp_path):
    observation_id = "ODT-2026-08-22T120000Z-dish002"
    file_prefix = observation_id.lower()
    _write_spr(
        tmp_path / f"{file_prefix}-1-0-2-dig002-g40-du60-sky-spr.csv",
        [[20, 21, 22, 23], [30, 31, 32, 33]],
    )
    _write_spr(
        tmp_path / f"{file_prefix}-1-0-1-dig002-g40-du60-sky-spr.csv",
        [[0, 1, 2, 3], [10, 11, 12, 13]],
    )
    _write_spr(
        tmp_path / f"{file_prefix}-1-0-3-dig002-g40-du60-sky-spr.csv",
        [[100, 100, 100, 100]],
    )
    _write_spr(
        tmp_path / "another-observation-1-0-1-dig002-sky-spr.csv",
        [[999, 999, 999, 999]],
    )

    output_path, file_count, row_count = aggregate_observation(
        observation_id=observation_id,
        directory=tmp_path,
        start_scan=(1, 0, 1),
        end_scan=(1, 0, 2),
        channel_start=1,
        channel_end=2,
    )

    assert file_count == 2
    assert row_count == 4
    assert output_path == tmp_path / f"{observation_id}-agg.csv"
    with output_path.open(newline="") as csv_file:
        assert list(csv.reader(csv_file)) == [
            ["row", "summed_power"],
            ["1", "3"],
            ["2", "23"],
            ["3", "43"],
            ["4", "63"],
        ]


def test_discover_spr_files_sorts_scan_id_numerically(tmp_path):
    for scan_iter in (10, 2):
        _write_spr(
            tmp_path / f"obs001-0-0-{scan_iter}-dig001-sky-spr.csv",
            [[scan_iter]],
        )

    matches = discover_spr_files(tmp_path, "obs001", (0, 0, 0), (0, 0, 10))

    assert [scan_id for scan_id, _path in matches] == [(0, 0, 2), (0, 0, 10)]


def test_aggregate_rejects_channel_outside_input_row(tmp_path):
    _write_spr(tmp_path / "obs001-0-0-0-dig001-sky-spr.csv", [[1, 2]])

    with pytest.raises(ValueError, match="has 2 channels; channel 2 was requested"):
        aggregate_observation("obs001", tmp_path, (0, 0, 0), (0, 0, 0), 0, 2)


def test_parse_scan_id_rejects_wrong_shape():
    with pytest.raises(Exception, match="expected tgt_id-freq_scan-scan_iter"):
        parse_scan_id("1-2")


def test_command_line_interface(tmp_path):
    _write_spr(tmp_path / "obs001-0-0-0-dig001-sky-spr.csv", [[1, 2, 3]])
    script = Path(__file__).parents[2] / "util" / "spr_sum.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "-o",
            "obs001",
            "-d",
            str(tmp_path),
            "-ss",
            "0-0-0",
            "-se",
            "0-0-0",
            "-cs",
            "0",
            "-ce",
            "2",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Wrote 1 rows from 1 SPR files" in result.stdout
    assert (tmp_path / "obs001-agg.csv").exists()
