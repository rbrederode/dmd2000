from datetime import datetime, timedelta, timezone

import numpy as np

from models.scan import ScanDataSource, ScanModel, ScanState, ScanType
from obs.scan import Scan


def _build_scan_model() -> ScanModel:
    return ScanModel(
        obs_id="obs001",
        tgt_idx=0,
        freq_scan=0,
        scan_type=ScanType.SKY,
        dig_id="dig001",
        created=datetime.now(timezone.utc),
        start_idx=0,
        duration=2,
        sample_rate=8,
        channels=4,
        center_freq=1420.4e6,
        gain=10.0,
        load=False,
        status=ScanState.EMPTY,
        last_update=datetime.now(timezone.utc),
    )


def test_scan_does_not_retain_iq_by_default():
    scan = Scan(scan_model=_build_scan_model())
    iq = np.array([1 + 1j, 2 + 0j, 0 + 3j, 1 - 1j, 2 + 2j, 3 + 0j, 0 + 1j, 1 + 0j], dtype=np.complex64)
    read_start = datetime.now(timezone.utc)
    read_end = read_start + timedelta(microseconds=1)

    loaded = scan.load_samples(
        sec=1,
        iq=iq,
        read_start=read_start,
        read_end=read_end,
    )

    assert loaded is True
    assert scan.raw is None
    assert scan.pwr is None
    assert scan.data_source == ScanDataSource.SPR
    assert scan.loaded_secs == [True, False]
    assert scan.spr[0, :].shape == (4,)
    assert scan.cal[0, :].shape == (4,)
    assert scan.mpr.shape == (4,)
    assert scan.mean_real > 0.0
    assert scan.mean_imag > 0.0


def test_scan_can_optionally_retain_iq():
    scan = Scan(scan_model=_build_scan_model(), retain_iq=True)
    iq = np.array([1 + 1j, 2 + 0j, 0 + 3j, 1 - 1j, 2 + 2j, 3 + 0j, 0 + 1j, 1 + 0j], dtype=np.complex64)
    read_start = datetime.now(timezone.utc)
    read_end = read_start + timedelta(microseconds=1)

    loaded = scan.load_samples(
        sec=1,
        iq=iq,
        read_start=read_start,
        read_end=read_end,
    )

    assert loaded is True
    assert scan.raw is not None
    assert scan.pwr is not None
    assert scan.raw.shape == (4, 4)
    assert scan.pwr.shape == (4, 4)
    assert scan.data_source == ScanDataSource.RAW
