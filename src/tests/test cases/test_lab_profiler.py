from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tarfile

import matplotlib
import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy import units as u
from astropy.io import fits

matplotlib.use("Agg")
hp = pytest.importorskip("healpy")

from models.obs import ObsModel
from models.scan import ScanModel, ScanState, ScanType
from models.target import TargetModel, TargetScanSet
from util import lab_profiler as lab


def write_slice(path, vector=True, ordering="RING", weight=1):
    shape = (3, 4) if vector else (12,)
    table = fits.BinTableHDU.from_columns([
        fits.Column(name="TEMPERATURE", format="4E" if vector else "E", unit="K", array=np.full(shape, 25.0)),
        fits.Column(name="SIMULATION", format="4E" if vector else "E", array=np.full(shape, weight)),
    ])
    table.header["NSIDE"] = 1
    table.header["ORDERING"] = ordering
    table.writeto(path)


@pytest.mark.parametrize("vector,ordering", [(True, "RING"), (False, "NESTED")])
def test_extract_keeps_mean_kelvin_and_velocity_bin_centres(tmp_path, vector, ordering):
    path = tmp_path / "LAB_cut+0.fits"
    write_slice(path, vector, ordering)
    direction = SkyCoord(l=82*u.deg, b=-5*u.deg, frame="galactic")
    profile = lab.extract_profile([(0, path)], direction, datetime.now(timezone.utc))
    np.testing.assert_array_equal(profile.brightness_k, [25])
    np.testing.assert_array_equal(profile.velocity_lsr_kms, [5])
    assert lab.find_slices(tmp_path) == [(0, path)]


def test_zero_weight_masked_and_outputs_survive(tmp_path):
    path = tmp_path / "LAB_cut-10.fits"
    write_slice(path, weight=0)
    profile = lab.extract_profile([(-10, path)], SkyCoord(l=82*u.deg, b=-5*u.deg, frame="galactic"), datetime.now(timezone.utc))
    assert np.isnan(profile.brightness_k[0])
    csv, png = lab.save_profile(profile, tmp_path / "outputs" / "profile", show=False)
    assert csv.read_text().startswith("velocity_lsr_kms,mean_brightness_K,weight")
    assert png.read_bytes().startswith(b"\x89PNG")


def test_local_slices_do_not_download(tmp_path, monkeypatch):
    path = tmp_path / "LAB_cut+0.fits"
    write_slice(path)
    monkeypatch.setattr(lab, "urlopen", lambda *a, **k: pytest.fail("Unexpected download"))
    with lab.survey_slices(tmp_path) as slices:
        assert slices == [(0, path)]
    assert path.exists()


def archive_bytes():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:bz2") as archive:
        for name in ("nested/LAB_cut+0.fits", "../../LAB_cut-10.fits", "other.txt"):
            member = tarfile.TarInfo(name)
            member.size = 4
            archive.addfile(member, io.BytesIO(b"data"))
        link = tarfile.TarInfo("LAB_cut+10.fits")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        archive.addfile(link)
    return stream.getvalue()


@pytest.mark.parametrize("fail_inside", [False, True])
def test_download_extracts_flat_regular_slices_and_cleans_up(tmp_path, monkeypatch, fail_inside):
    monkeypatch.setattr(lab, "urlopen", lambda *a, **k: io.BytesIO(archive_bytes()))
    folder = None
    try:
        with lab.survey_slices(tmp_path / "missing") as slices:
            assert [v for v, p in slices] == [-10, 0]
            folder = slices[0][1].parent
            assert all(p.parent == folder and p.read_bytes() == b"data" for v, p in slices)
            assert not (folder / "LAB_cut+10.fits").exists()
            if fail_inside:
                raise RuntimeError("test downstream failure")
    except RuntimeError:
        assert fail_inside
    assert folder is not None and not folder.exists()


def test_pointing_from_metadata_uses_actual_altaz_and_midpoint(tmp_path):
    start = datetime(2026, 10, 24, 11, 25, tzinfo=timezone.utc)
    scan = ScanModel(obs_id="test", tgt_idx=0, freq_scan=0, scan_iter=0, dig_id="dig003",
                     scan_type=ScanType.SKY, status=ScanState.COMPLETE,
                     read_start=start, read_end=start + timedelta(seconds=20),
                     tgt_acq_altaz={"alt": 18., "az": 48.})
    obs = ObsModel(obs_id="test", latitude=53.234123, longitude=-2.305485, height=77.,
                   targets=[TargetModel(tgt_idx=0, altaz={"alt": 90., "az": 0.})],
                   target_scans=[TargetScanSet(tgt_idx=0, scans=[scan])])
    obs.save_to_disk(output_dir=str(tmp_path))
    galactic, midpoint = lab.get_obs_pointing(tmp_path, "test", scan.scan_id)
    assert midpoint == start + timedelta(seconds=10)
    assert 81 < galactic.l.deg < 84
    assert -7 < galactic.b.deg < -4
    with pytest.raises(ValueError, match="Available scans"):
        lab.get_obs_pointing(tmp_path, "test", "missing")

    # Sidereal/Galactic target coordinates take precedence over fixed Alt/Az.
    obs.targets[0].sky_coord = SkyCoord(l=100*u.deg, b=10*u.deg, frame="galactic").icrs
    obs.save_to_disk(output_dir=str(tmp_path))
    galactic, _ = lab.get_obs_pointing(tmp_path, "test", scan.scan_id)
    assert galactic.l.deg == pytest.approx(100)
    assert galactic.b.deg == pytest.approx(10)

    # The existing metadata CLI still selects the same scan and keeps its name.
    args = lab.build_parser().parse_args(["--obs", "test", "--scan", scan.scan_id, "--dir", str(tmp_path)])
    context = lab.resolve_profile_context(args)
    assert context.input_mode == "metadata"
    assert context.output_name == scan.scan_id
    assert context.observed_at == midpoint
    assert context.location == (53.234123, -2.305485, 77.)


def test_direct_defaults_use_jodrell_and_one_current_utc_time(monkeypatch):
    monkeypatch.setattr(lab, "get_obs_pointing", lambda *a: pytest.fail("Metadata must not be read"))
    args = lab.build_parser().parse_args(["--galactic", "442.2361", "-5.6143"])
    now = datetime(2026, 10, 25, 12, 30, tzinfo=timezone.utc)
    context = lab.resolve_profile_context(args, now=now)
    assert context.observed_at == now
    assert context.location == lab.JODRELL_BANK_LOCATION
    assert context.galactic.l.deg == pytest.approx(82.2361)
    assert context.galactic.b.deg == pytest.approx(-5.6143)
    assert "20261025T123000" in context.output_name
    assert context.obs_id is None and context.scan_id is None


@pytest.mark.parametrize("value", ["2026-10-24T11:25:50Z", "2026-10-24T11:25:50", "2026-10-24T12:25:50+01:00"])
def test_dt_is_normalised_to_utc(value):
    args = lab.build_parser().parse_args(["--galactic", "82", "-5", "--dt", value,
                                         "--location", "45", "-70", "100"])
    context = lab.resolve_profile_context(args)
    assert context.observed_at == datetime(2026, 10, 24, 11, 25, 50, tzinfo=timezone.utc)
    assert context.location == (45, -70, 100)


@pytest.mark.parametrize("options", [
    [], ["--obs", "test"], ["--scan", "scan"],
    ["--galactic", "82", "-5", "--obs", "test"],
    ["--obs", "test", "--scan", "scan", "--dt", "2026-10-24T11:00:00Z"],
    ["--obs", "test", "--scan", "scan", "--location", "45", "0", "77"],
    ["--galactic", "0", "91"], ["--galactic", "nan", "0"],
    ["--galactic", "0", "0", "--location", "91", "0", "0"],
    ["--galactic", "0", "0", "--location", "0", "181", "0"],
    ["--galactic", "0", "0", "--location", "0", "0", "nan"],
    ["--galactic", "0", "0", "--dt", "not-a-date"],
    ["--galactic", "0", "0", "--dt", "2026-10-24"],
])
def test_bad_inputs_fail_before_survey_access(options, monkeypatch):
    monkeypatch.setattr(lab, "survey_slices", lambda *a: pytest.fail("No survey access on invalid input"))
    monkeypatch.setattr(lab, "get_obs_pointing", lambda *a: pytest.fail("No metadata lookup on invalid input"))
    with pytest.raises(SystemExit) as exc:
        lab.main(options)
    assert exc.value.code == 2


def test_direct_cli_saves_context_and_same_spectrum_for_different_site_and_time(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, "get_obs_pointing", lambda *a: pytest.fail("Metadata must not be read"))
    slices = tmp_path / "slices"
    slices.mkdir()
    write_slice(slices / "LAB_cut+0.fits")
    outputs = []
    for index, options in enumerate([
        ["--dt", "2026-10-24T11:25:50Z"],
        ["--dt", "2026-10-25T11:25:50Z", "--location", "0", "0", "10"],
    ]):
        output = tmp_path / f"output{index}"
        assert lab.main(["--galactic", "82.2361", "-5.6143", "--lab-dir", str(slices),
                         "--output-dir", str(output), "--no-show", *options]) == 0
        files = list(output.iterdir())
        assert {path.suffix for path in files} == {".csv", ".png", ".json"}
        metadata = json.loads(next(output.glob("*.json")).read_text())
        assert metadata["input_mode"] == "galactic"
        assert metadata["galactic_longitude_deg"] == pytest.approx(82.2361)
        assert metadata["observed_at_utc"].endswith("+00:00")
        assert metadata["location"]["height_m"] == (77 if index == 0 else 10)
        outputs.append(np.loadtxt(next(output.glob("*.csv")), delimiter=",", skiprows=1))
    np.testing.assert_array_equal(outputs[0], outputs[1])
