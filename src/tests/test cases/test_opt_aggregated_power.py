import csv
from types import SimpleNamespace

import numpy as np

from obs.opt import save_aggregated_power_csv


def _scan(scan_id, tgt_idx, freq_scan, center_freq, cal, loaded_seconds):
    return SimpleNamespace(
        scan_model=SimpleNamespace(
            scan_id=scan_id,
            tgt_idx=tgt_idx,
            freq_scan=freq_scan,
            center_freq=center_freq,
        ),
        cal=np.asarray(cal, dtype=float),
        get_loaded_seconds=lambda: loaded_seconds,
    )


def test_save_aggregated_power_csv_exports_the_plotted_values(tmp_path):
    scans = [
        _scan("obs-2-0", 2, 0, 1421e6, [[10, 20], [30, 40]], 2),
        _scan("obs-0-0", 0, 0, 1420e6, [[1, 2], [3, 4], [100, 200]], 2),
    ]

    output_path = save_aggregated_power_csv("observation-id", scans, str(tmp_path))

    assert output_path == str(tmp_path / "observation-id-sky-apr.csv")
    with open(output_path, newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert rows == [
        {
            "scan_id": "obs-0-0",
            "tgt_idx": "0",
            "freq_scan": "0",
            "integrated_scan": "1",
            "aggregated_power": "3.0",
        },
        {
            "scan_id": "obs-0-0",
            "tgt_idx": "0",
            "freq_scan": "0",
            "integrated_scan": "2",
            "aggregated_power": "7.0",
        },
        {
            "scan_id": "obs-2-0",
            "tgt_idx": "2",
            "freq_scan": "0",
            "integrated_scan": "1",
            "aggregated_power": "30.0",
        },
        {
            "scan_id": "obs-2-0",
            "tgt_idx": "2",
            "freq_scan": "0",
            "integrated_scan": "2",
            "aggregated_power": "70.0",
        },
    ]
