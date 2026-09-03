import csv
from types import SimpleNamespace

import numpy as np

from obs.opt import _fmt_mpr_total_power, _mpr_total_power, save_aggregated_power_csv
from sdp.channel_mask import ChannelFlag, empty_channel_flags


def _scan(scan_id, tgt_idx, freq_scan, center_freq, cal, loaded_seconds, cal_flags=None):
    cal = np.asarray(cal, dtype=float)
    return SimpleNamespace(
        scan_model=SimpleNamespace(
            scan_id=scan_id,
            tgt_idx=tgt_idx,
            freq_scan=freq_scan,
            center_freq=center_freq,
        ),
        cal=cal,
        cal_flags=empty_channel_flags(cal.shape) if cal_flags is None else cal_flags,
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


def test_mpr_total_power_sums_the_duration_averaged_spectrum():
    scan = SimpleNamespace(mpr=np.array([1.25, 2.5, 3.75]))

    assert _mpr_total_power(scan) == 7.5
    assert _fmt_mpr_total_power(scan) == "7.5000e+00"


def test_mpr_total_power_is_blank_when_mpr_is_unavailable_or_invalid():
    assert _mpr_total_power(SimpleNamespace(mpr=None)) is None
    assert _fmt_mpr_total_power(SimpleNamespace(mpr=np.array([]))) == ""
    assert _fmt_mpr_total_power(SimpleNamespace(mpr=np.array([1.0, np.nan]))) == "1.0000e+00"
    assert _fmt_mpr_total_power(SimpleNamespace(mpr=np.array([np.nan]))) == ""


def test_mpr_total_power_excludes_flagged_channels():
    flags = empty_channel_flags(3)
    flags[1] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    scan = SimpleNamespace(mpr=np.array([1.0, 100.0, 3.0]), mpr_flags=flags)

    assert _mpr_total_power(scan) == 4.0


def test_mpr_total_power_reconstructs_rfi_flagged_channels():
    flags = empty_channel_flags(3)
    flags[1] |= int(ChannelFlag.RFI_DETECTED)
    scan = SimpleNamespace(mpr=np.array([1.0, 100.0, 3.0]), mpr_flags=flags)

    assert _mpr_total_power(scan) == 6.0


def test_save_aggregated_power_csv_excludes_flagged_channels(tmp_path):
    flags = empty_channel_flags((2, 3))
    flags[:, 1] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    scan = _scan("obs-0-0", 0, 0, 1420e6, [[1, 100, 3], [4, 200, 6]], 2, flags)

    output_path = save_aggregated_power_csv("observation-id", [scan], str(tmp_path))

    with open(output_path, newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert [row["aggregated_power"] for row in rows] == ["4.0", "10.0"]


def test_save_aggregated_power_csv_reconstructs_rfi_flagged_channels(tmp_path):
    flags = empty_channel_flags((2, 3))
    flags[:, 1] |= int(ChannelFlag.RFI_DETECTED)
    scan = _scan("obs-0-0", 0, 0, 1420e6, [[1, 100, 3], [4, 200, 6]], 2, flags)

    output_path = save_aggregated_power_csv("observation-id", [scan], str(tmp_path))

    with open(output_path, newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert [row["aggregated_power"] for row in rows] == ["6.0", "15.0"]
