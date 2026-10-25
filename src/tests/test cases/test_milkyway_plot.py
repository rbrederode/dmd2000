from datetime import datetime, timezone
import json
from types import SimpleNamespace

import matplotlib
import numpy as np
import pytest
from astropy import units as u
from astropy.coordinates import SkyCoord

matplotlib.use("Agg")
from matplotlib import pyplot as plt
from matplotlib.text import Annotation

from util import milkyway_plot as plotter
from obs import obs_pointing


@pytest.mark.parametrize("longitude,signs", [(0, (0, 1)), (90, (-1, 0)), (180, (0, -1)), (270, (1, 0))])
def test_sightline_matches_illustration_longitude_grid(longitude, signs):
    endpoint = np.asarray(plotter.sightline_endpoint(longitude))
    for value, sign in zip(endpoint, signs):
        if sign:
            assert value * sign > 0
        else:
            assert value == pytest.approx(0, abs=1e-12)
    xmin, xmax, ymin, ymax = plotter.IMAGE_EXTENT
    assert xmin < endpoint[0] < xmax
    assert ymin < endpoint[1] < ymax


def test_plot_saves_arrow_from_sun_without_distorting_image(tmp_path, monkeypatch):
    monkeypatch.setattr(plt, "imread", lambda path: np.zeros((10, 10, 3)))
    path = tmp_path / "output" / "sightline.png"
    galactic = SkyCoord(l=82*u.deg, b=-5*u.deg, frame="galactic")
    figure = plotter.plot_sightline(galactic, datetime.now(timezone.utc), "obs", "obs-1-0-0", path, show=False)
    try:
        axes = figure.axes[0]
        arrows = [text for text in axes.texts if isinstance(text, Annotation)]
        assert len(arrows) == 1
        assert arrows[0].get_position() == (0, 0)
        np.testing.assert_allclose(arrows[0].xy, plotter.sightline_endpoint(82))
        assert axes.get_aspect() == 1
        assert any("b=-5.000" in text.get_text() for text in figure.texts)
        assert path.read_bytes().startswith(b"\x89PNG")
    finally:
        plt.close(figure)


def test_galactic_pole_has_no_misleading_plane_direction(tmp_path, monkeypatch):
    monkeypatch.setattr(plt, "imread", lambda path: np.zeros((10, 10, 3)))
    figure = plotter.plot_sightline(SkyCoord(l=0*u.deg, b=90*u.deg, frame="galactic"),
                                    datetime.now(timezone.utc), "obs", "scan", tmp_path / "pole.png", show=False)
    try:
        assert not any(isinstance(text, Annotation) for text in figure.axes[0].texts)
        assert "perpendicular" in figure.axes[0].texts[0].get_text()
    finally:
        plt.close(figure)


def test_direct_options_match_lab_profiler_defaults_and_utc_handling():
    from util import lab_profiler

    now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
    for options in (["--galactic", "442.2361", "-5.6143"],
                    ["--galactic", "82.2361", "-5.6143", "--location", "45", "-70", "100",
                     "--dt", "2026-10-24T12:25:50+01:00"]):
        args = plotter.build_parser().parse_args(options)
        context = plotter.resolve_pointing_context(args, now=now)
        reference = lab_profiler.resolve_profile_context(lab_profiler.build_parser().parse_args(options), now=now)
        assert context.metadata() == reference.metadata()
        assert context.output_name == reference.output_name
        assert context.observed_at.tzinfo is timezone.utc
    assert plotter.resolve_pointing_context(plotter.build_parser().parse_args(["--galactic", "0", "0"]), now=now).location == obs_pointing.JODRELL_BANK_LOCATION


@pytest.mark.parametrize("mode", ["galactic", "metadata"])
def test_cli_saves_png_and_context_for_both_modes(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(plt, "imread", lambda path: np.zeros((10, 10, 3)))
    dt = datetime(2026, 10, 24, 11, 25, 50, tzinfo=timezone.utc)
    if mode == "galactic":
        options = ["--galactic", "82.2361", "-5.6143", "--location", "45", "-70", "100", "--dt", dt.isoformat()]
    else:
        monkeypatch.setattr(obs_pointing, "get_obs_pointing", lambda *a: (SkyCoord(l=82.2361*u.deg, b=-5.6143*u.deg, frame="galactic"), dt))
        monkeypatch.setattr(obs_pointing.ObsModel, "load_from_disk", lambda **k: SimpleNamespace(latitude=45., longitude=-70., height=100.))
        options = ["--obs", "obs", "--scan", "obs-1-0-0"]
    assert plotter.main([*options, "--output-dir", str(tmp_path), "--no-show"]) == 0
    assert len(list(tmp_path.glob("*.png"))) == 1
    metadata = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert metadata["input_mode"] == mode
    assert metadata["observed_at_utc"] == dt.isoformat()
    assert metadata["location"] == {"latitude_deg": 45, "longitude_deg": -70, "height_m": 100}
    if mode == "metadata":
        assert (tmp_path / "obs-1-0-0-milkyway.png").exists()


@pytest.mark.parametrize("options", [
    [], ["--obs", "obs"], ["--galactic", "0", "91"],
    ["--galactic", "0", "0", "--obs", "obs", "--scan", "scan"],
    ["--obs", "obs", "--scan", "scan", "--dt", "2026-10-24T11:00:00Z"],
    ["--galactic", "0", "0", "--location", "0", "181", "0"],
    ["--galactic", "0", "0", "--dt", "bad"],
])
def test_invalid_options_do_not_start_plotting(options, monkeypatch):
    monkeypatch.setattr(plotter, "plot_sightline", lambda *a, **k: pytest.fail("Invalid options must not plot"))
    with pytest.raises(SystemExit) as exc:
        plotter.main(options)
    assert exc.value.code == 2


def test_direct_plot_title_omits_generated_name_and_shows_site(tmp_path, monkeypatch):
    monkeypatch.setattr(plt, "imread", lambda path: np.zeros((10, 10, 3)))
    context = plotter.resolve_pointing_context(plotter.build_parser().parse_args([
        "--galactic", "82", "-5", "--dt", "2026-10-24T11:00:00Z"]))
    figure = plotter.plot_sightline(context.galactic, context.observed_at, None, None,
                                    tmp_path / "direct.png", show=False, context=context)
    try:
        assert figure._suptitle.get_text() == "Milky Way — Target Line of Sight"
        assert any("Datetime UTC" in text.get_text() for text in figure.texts)
        assert any("Site: 53.234123" in text.get_text() for text in figure.texts)
    finally:
        plt.close(figure)
