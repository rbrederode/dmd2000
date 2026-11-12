from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
from matplotlib.collections import QuadMesh
import numpy as np
from astropy.coordinates import SkyCoord
from astropy import units as u

from obs.obs_display import ObsDisplay
from obs.opt import display_lv_after_enter


def prepared_spectrum(target, longitude, frequency=0, amplitude=1., velocity=None):
    velocity = np.array([-10., 0., 10., 20.]) if velocity is None else np.asarray(velocity)
    power = np.ma.array(np.arange(len(velocity), dtype=float) * amplitude, mask=False)
    power.mask[1] = True
    return {"target_id": target, "freq_scan": frequency, "center_freq": 1420e6,
            "velocity": velocity, "power": power,
            "direction": SkyCoord(l=longitude*u.deg, b=0*u.deg, frame="galactic")}


def observation(longitudes):
    targets = {target: SimpleNamespace(sky_coord=SkyCoord(l=lon*u.deg, b=0*u.deg, frame="galactic"))
               for target, lon in longitudes.items()}
    return SimpleNamespace(get_target_by_index=lambda target: targets.get(target))


def test_lv_columns_coordinates_masks_and_unscaled_amplitudes(tmp_path):
    display = ObsDisplay("lv")
    display.lsr_baseline_method = "linear"
    first = prepared_spectrum(1, 200, amplitude=1)
    second = prepared_spectrum(2, 210, amplitude=4, velocity=[-15., -5., 5., 15., 25.])
    # Explicit target coordinates take precedence over cached fallback directions.
    display.lsr_spectra = [first, second]
    originals = [item["power"].copy() for item in display.lsr_spectra]
    path = tmp_path / "lv.png"
    fig = display.display_lv(observation({1: 30, 2: 50}), str(path))
    try:
        axis = fig.axes[0]
        meshes = [item for item in axis.collections if isinstance(item, QuadMesh)]
        assert len(meshes) == 2
        for mesh, source, xedges in zip(meshes, [first, second], [[20, 40], [40, 60]]):
            assert mesh.get_cmap().name == "lab_white_blue"
            np.testing.assert_array_equal(mesh.get_coordinates()[0, :, 0], xedges)
            np.testing.assert_array_equal(mesh.get_array().reshape(-1).compressed(), source["power"].compressed())
            np.testing.assert_array_equal(np.ma.getmaskarray(mesh.get_array()).reshape(-1), source["power"].mask)
        assert meshes[0].norm is meshes[1].norm
        assert meshes[0].norm.vmax == 16
        assert "longitude" in axis.get_xlabel()
        assert "Galactic longitude 30.000° to 50.000°; latitude +0.000° to +0.000°" in axis.get_title()
        assert "LSRK velocity" in axis.get_ylabel()
        np.testing.assert_allclose(axis.get_ylim(), [-15, 25])
        # Crop the view while leaving each column's native velocity bins intact.
        np.testing.assert_array_equal(meshes[0].get_coordinates()[:, 0, 1], [-15, -5, 5, 15, 25])
        np.testing.assert_array_equal(meshes[1].get_coordinates()[:, 0, 1], [-20, -10, 0, 10, 20, 30])
        assert "Baseline-subtracted" in fig.axes[-1].get_ylabel()
        assert path.read_bytes().startswith(b"\x89PNG")
        for source, original in zip(display.lsr_spectra, originals):
            np.testing.assert_array_equal(source["power"].data, original.data)
            np.testing.assert_array_equal(source["power"].mask, original.mask)
    finally:
        plt.close(fig)


def test_lv_frequency_panels_and_longitude_wrap(tmp_path):
    display = ObsDisplay("wrap")
    display.lsr_baseline_method = "none"
    display.lsr_spectra = [prepared_spectrum(1, 359), prepared_spectrum(2, 1),
                           prepared_spectrum(1, 359, frequency=1, amplitude=2)]
    fig = display.display_lv(observation({1: 359, 2: 1}), str(tmp_path / "wrap.png"))
    try:
        assert len(fig.axes) == 3  # Two frequency panels plus shared colour bar.
        for axis in fig.axes[:2]:
            np.testing.assert_allclose(axis.get_xlim(), [362, 358])
            formatter = axis.xaxis.get_major_formatter()
            assert formatter(361, 0) == "1°"
            assert formatter(359, 0) == "-1°"
            assert formatter(360, 0) == "0°"
            assert axis.xaxis_inverted()
        norms = [mesh.norm for axis in fig.axes[:2] for mesh in axis.collections if isinstance(mesh, QuadMesh)]
        assert all(norm is norms[0] for norm in norms)
        assert "Frequency scan 1" in fig.axes[1].get_title()
        assert "-1.000° to 1.000° (across 0°)" in fig.axes[0].get_title()
    finally:
        plt.close(fig)


def test_lv_single_longitude_and_repeated_targets_are_not_overpainted(tmp_path):
    display = ObsDisplay("repeat")
    display.lsr_baseline_method = "linear"
    display.lsr_spectra = [prepared_spectrum(1, 30), prepared_spectrum(2, 30, amplitude=2)]
    fig = display.display_lv(observation({1: 30, 2: 30}), str(tmp_path / "repeat.png"))
    try:
        assert len(fig.axes) == 3
        assert "Galactic longitude 30.000° to 30.000°" in fig.axes[0].get_title()
        assert "targets" not in fig.axes[1].get_title()
        np.testing.assert_allclose(fig.axes[0].get_xlim(), [30.5, 29.5])
    finally:
        plt.close(fig)


def test_lv_missing_coordinates_and_missing_spectra_skip_safely(tmp_path):
    display = ObsDisplay("missing")
    path = tmp_path / "missing.png"
    assert display.display_lv(observation({}), str(path)) is None
    source = prepared_spectrum(1, 30)
    source["direction"] = None
    display.lsr_spectra = [source]
    assert display.display_lv(observation({}), str(path)) is None
    assert not path.exists()
    display.set_scan([])
    assert not display.lsr_spectra


def test_enter_precedes_new_lv_plot(monkeypatch, tmp_path):
    events = []
    figure = object()
    def draw(obs, path):
        events.append("draw")
        assert obs == "observation"
        return figure
    display = SimpleNamespace(lsr_spectra=[object()], display_lv=draw)
    monkeypatch.setattr("builtins.input", lambda prompt: events.append("enter"))
    assert display_lv_after_enter(display, "observation", str(tmp_path / "lv.png")) is figure
    assert events == ["enter", "draw"]
    display.lsr_spectra = []
    events.clear()
    assert display_lv_after_enter(display, "observation", str(tmp_path / "lv.png")) is None
    assert not events


def test_lv_reuses_prepared_baselines_without_recalculating(tmp_path, monkeypatch):
    from obs import lsr
    import obs.obs_display as module
    velocities = np.linspace(-100., 150., 251)
    power = 10 + .01 * velocities + 2 * np.exp(-((velocities - 10) / 10)**2)
    scan = SimpleNamespace(
        scan_model=SimpleNamespace(tgt_idx=1, freq_scan=0, center_freq=1420e6,
                                   sample_rate=1e6, spectral_resolution=len(velocities), scan_id="aggregate"),
        mpr=power.copy(), mpr_flags=np.zeros(len(velocities), dtype=np.uint32),
    )
    obs = observation({1: 30})
    monkeypatch.setattr(lsr, "scan_observing_context", lambda *a: (obs.get_target_by_index(1).sky_coord.icrs, None, None))
    monkeypatch.setattr(lsr, "lsr_velocity", lambda *a: velocities)
    display = ObsDisplay("reuse")
    display.scans = [scan]
    first = display.display_lsr(obs, str(tmp_path / "lsr.png"))
    assert len(display.lsr_spectra) == 1
    corrected = first.axes[0].lines[0].get_ydata().copy()
    def unexpected_fit(*args, **kwargs):
        raise AssertionError("L–V must not fit a second baseline")
    monkeypatch.setattr(module, "subtract_spectrum_baseline", unexpected_fit)
    second = display.display_lv(obs, str(tmp_path / "lv.png"))
    try:
        mesh = second.axes[0].collections[0]
        np.testing.assert_array_equal(mesh.get_array().reshape(-1), corrected)
        np.testing.assert_array_equal(scan.mpr, power)
    finally:
        plt.close(first)
        plt.close(second)


def test_lv_without_common_velocity_coverage_skips_plot(tmp_path, caplog):
    display = ObsDisplay("disjoint")
    display.lsr_baseline_method = "linear"
    display.lsr_spectra = [prepared_spectrum(1, 30, velocity=[0., 1., 2., 3.]),
                           prepared_spectrum(2, 40, velocity=[10., 11., 12., 13.])]
    path = tmp_path / "disjoint.png"
    assert display.display_lv(observation({1: 30, 2: 40}), str(path)) is None
    assert not path.exists()
    assert "no shared valid velocity coverage" in caplog.text


def test_lv_blue_asinh_and_calibrated_power_contours(tmp_path):
    from matplotlib.colors import AsinhNorm
    display = ObsDisplay("contours")
    display.lsr_baseline_method = "linear"
    velocity = np.linspace(-10, 10, 101)
    display.lsr_spectra = []
    for target, longitude, amplitude in [(1, 30, .1), (2, 40, .15), (3, 50, .2)]:
        source = prepared_spectrum(target, longitude, velocity=velocity)
        source["power"] = np.ma.array(amplitude * np.exp(-(velocity / 4)**2))
        display.lsr_spectra.append(source)
    fig = display.display_lv(observation({1: 30, 2: 40, 3: 50}), str(tmp_path / "contours.png"), contour_levels=[.02, .05, .1])
    try:
        axis = fig.axes[0]
        assert isinstance(axis.collections[0].norm, AsinhNorm)
        assert axis.collections[0].norm.vmin == 0
        assert axis.collections[0].norm.vmax == .2
        assert axis.collections[0].get_cmap()(0) == (1, 1, 1, 1)
        contour = axis.collections[-1]
        np.testing.assert_array_equal(contour.levels, [.02, .05, .1])
        assert any("a.u." in text.get_text() for text in axis.texts)
        assert "[a.u.]" in fig.axes[-1].get_ylabel()
    finally:
        plt.close(fig)
    disabled = display.display_lv(observation({1: 30, 2: 40, 3: 50}), str(tmp_path / "plain.png"), contour_levels=[])
    try:
        assert all(isinstance(item, QuadMesh) for item in disabled.axes[0].collections)
    finally:
        plt.close(disabled)


def test_lv_contour_resampling_preserves_masks_and_velocity_gaps():
    from util.lv_plot import interpolate_lv_contour_column
    velocity = np.array([0., 1., 2., 3., 4., 8., 9.])
    power = np.ma.array(velocity * 2, mask=[False, False, True, False, False, False, False])
    grid = np.arange(-1., 10., .5)
    actual = interpolate_lv_contour_column(velocity, power, grid)
    for low, high in [(0, 1), (3, 4), (8, 9)]:
        inside = (grid >= low) & (grid <= high)
        np.testing.assert_allclose(actual[inside], grid[inside] * 2)
    missing = (grid < 0) | ((grid > 1) & (grid < 3)) | ((grid > 4) & (grid < 8)) | (grid > 9)
    assert np.isnan(actual[missing]).all()


def test_lv_contours_do_not_span_a_missing_longitude_in_a_frequency_panel(tmp_path):
    display = ObsDisplay("missing-frequency")
    display.lsr_baseline_method = "linear"
    display.lsr_spectra = [prepared_spectrum(1, 30, frequency=0),
                           prepared_spectrum(2, 40, frequency=1),
                           prepared_spectrum(3, 50, frequency=0)]
    fig = display.display_lv(observation({1: 30, 2: 40, 3: 50}), str(tmp_path / "gap.png"))
    try:
        assert all(isinstance(item, QuadMesh) for item in fig.axes[0].collections)
    finally:
        plt.close(fig)


def test_lv_contour_smoothing_is_display_only_and_does_not_bridge_masks():
    from util.lv_plot import interpolate_lv_contour_column
    velocity = np.arange(100.)
    power = np.ma.array(np.tile([0., .01], 50), mask=False)
    power.mask[50] = True
    original = power.copy()
    result = interpolate_lv_contour_column(velocity, power, velocity, smoothing_fwhm=4.)
    assert np.isnan(result[50])
    assert np.std(result[10:40]) < np.std(power[10:40])
    np.testing.assert_array_equal(power.data, original.data)
    np.testing.assert_array_equal(power.mask, original.mask)


def test_lv_selected_velocity_limits_also_exclude_offscreen_colour_extremes(tmp_path):
    display = ObsDisplay("limits")
    display.lsr_baseline_method = "none"
    velocity = np.arange(-100., 101., 10.)
    spectrum = prepared_spectrum(1, 30, velocity=velocity)
    spectrum["power"] = np.ma.array(np.full(len(velocity), 4.), mask=False)
    spectrum["power"][-1] = 10000  # Outside the requested display range.
    display.lsr_spectra = [spectrum]
    original = spectrum["power"].copy()
    for low, high in [(-20, 30), (0, 30)]:
        fig = display.display_lv(observation({1: 30}), str(tmp_path / "limited.png"),
                                 vmin=low, vmax=high)
        try:
            np.testing.assert_allclose(fig.axes[0].get_ylim(), [low, high])
            mesh = next(item for item in fig.axes[0].collections if isinstance(item, QuadMesh))
            assert mesh.norm.vmax == 4
            np.testing.assert_array_equal(mesh.get_array().reshape(-1), original)
            np.testing.assert_array_equal(spectrum["power"], original)
        finally:
            plt.close(fig)


def test_lv_limits_are_clamped_to_coverage_and_empty_range_is_reported(tmp_path, caplog):
    display = ObsDisplay("coverage")
    display.lsr_baseline_method = "none"
    display.lsr_spectra = [prepared_spectrum(1, 30)]
    fig = display.display_lv(observation({1: 30}), str(tmp_path / "limited.png"), vmin=-200, vmax=300)
    try:
        np.testing.assert_allclose(fig.axes[0].get_ylim(), [-15, 25])
    finally:
        plt.close(fig)
    path = tmp_path / "empty.png"
    assert display.display_lv(observation({1: 30}), str(path), vmin=100, vmax=200) is None
    assert not path.exists()
    assert "selected limits" in caplog.text
