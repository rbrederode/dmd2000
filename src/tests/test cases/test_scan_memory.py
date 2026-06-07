from datetime import datetime, timedelta, timezone

import numpy as np

from models.fil import FilterBank
from models.scan import ScanDataSource, ScanModel, ScanState, ScanType
from obs.scan import Scan


def _build_scan_model(**overrides) -> ScanModel:
    params = dict(
        obs_id="obs001",
        tgt_idx=0,
        freq_scan=0,
        scan_type=ScanType.SKY,
        dig_id="dig001",
        created=datetime.now(timezone.utc),
        start_idx=0,
        duration=2,
        sample_rate=8,
        spectral_resolution=4,
        center_freq=1420.4e6,
        gain=10.0,
        load=False,
        status=ScanState.EMPTY,
        last_update=datetime.now(timezone.utc),
    )
    params.update(overrides)
    return ScanModel(
        **params
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
    assert scan.pwr.shape == (2, 4)
    assert scan.data_source == ScanDataSource.RAW


def test_filterbank_writes_unnormalised_float32_rows(tmp_path):
    scan_model = _build_scan_model(
        duration=1,
        sample_rate=1000,
        spectral_resolution=10,
        filter_bank=FilterBank(enabled=True, temporal_resolution=10.0, dtype="uint8"),
        files_directory=str(tmp_path),
        files_prefix="test-scan",
    )
    scan = Scan(scan_model=scan_model)
    rng = np.random.default_rng(42)
    iq = (rng.normal(size=1000) + 1j * rng.normal(size=1000)).astype(np.complex64)
    read_start = datetime.now(timezone.utc)
    read_end = read_start + timedelta(seconds=1)

    loaded = scan.load_samples(
        sec=1,
        iq=iq,
        read_start=read_start,
        read_end=read_end,
    )

    fb_path = tmp_path / "test-scan-fb.dat"
    data = np.fromfile(fb_path, dtype=np.float32)

    assert loaded is True
    assert scan.scan_model.status == ScanState.COMPLETE
    assert fb_path.exists()
    assert data.shape == (100 * 10,)
    assert scan._fb_data.shape == (100, 10)
    np.testing.assert_allclose(data.reshape(100, 10), scan._fb_data)


def test_filterbank_sub_bandwidth_selects_centered_subband():
    scan_model = _build_scan_model(
        duration=1,
        sample_rate=1000,
        spectral_resolution=2,
        filter_bank=FilterBank(enabled=True, temporal_resolution=10.0, sub_bandwidth=200.0, dtype="uint8"),
    )
    scan = Scan(scan_model=scan_model)
    t = np.arange(1000, dtype=np.float32)
    iq = np.exp(2j * np.pi * t / 10.0).astype(np.complex64)

    fb = scan._fb_rows_from_iq(iq)

    assert scan._fb_sub_bandwidth() == 200.0
    assert fb.shape == (100, 2)
