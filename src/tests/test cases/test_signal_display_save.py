from types import SimpleNamespace

import matplotlib
import numpy as np

matplotlib.use("Agg")

from matplotlib import pyplot as plt
from sdp.signal_display import SignalDisplay
from models.scan import ScanType
from sdp.channel_mask import ChannelFlag, empty_channel_flags


class FakeFigure:
    def __init__(self):
        self.saved_paths = []
        self.canvas = SimpleNamespace(draw=lambda: None)

    def savefig(self, path):
        self.saved_paths.append(path)


class FakeLine:
    def __init__(self):
        self.x = None
        self.y = None
        self.visible = None
        self.label = None

    def set_data(self, x, y):
        self.x = np.asarray(x)
        self.y = np.asarray(y)

    def set_visible(self, visible):
        self.visible = visible

    def set_ydata(self, y):
        self.y = np.asarray(y)

    def set_label(self, label):
        self.label = label


class FakeAxis:
    def get_legend(self):
        return None

    def legend(self, **_kwargs):
        pass

    def relim(self, **_kwargs):
        pass

    def autoscale_view(self, **_kwargs):
        pass


def make_scan(scan_id="obs-0-0-1"):
    scan_model = SimpleNamespace(
        scan_id=scan_id,
        read_start=None,
        dig_id="dig001",
        gain=10.0,
        duration=60,
        sample_rate=2.4e6,
        center_freq=1420.4e6,
        spectral_resolution=1024,
        scan_type=ScanType.SKY,
    )
    return SimpleNamespace(
        scan_model=scan_model,
        get_loaded_seconds=lambda: 1,
    )


def test_save_scan_figure_only_saves_each_scan_once(tmp_path):
    display = SignalDisplay(dig_id="dig001")
    display.fig = FakeFigure()
    display.scan = make_scan()
    display.sec = 1

    assert display.save_scan_figure(str(tmp_path)) is True
    assert display.save_scan_figure(str(tmp_path)) is False
    assert len(display.fig.saved_paths) == 1


def test_save_scan_figure_allows_next_scan_to_save(tmp_path):
    display = SignalDisplay(dig_id="dig001")
    display.fig = FakeFigure()
    display.scan = make_scan(scan_id="obs-0-0-1")
    display.sec = 1

    assert display.save_scan_figure(str(tmp_path)) is True

    display.scan = make_scan(scan_id="obs-0-0-2")
    display.sec = 1
    assert display.save_scan_figure(str(tmp_path)) is True
    assert len(display.fig.saved_paths) == 2


def test_total_power_axis_excludes_flagged_channels():
    display = SignalDisplay(dig_id="dig001")
    display.total_power_line = FakeLine()
    display.mean_tpwr_line = FakeLine()
    display.sig[4] = FakeAxis()
    cal_flags = empty_channel_flags((2, 3))
    cal_flags[:, 1] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    display.scan = SimpleNamespace(
        scan_model=SimpleNamespace(duration=2),
        cal=np.array([[1.0, 100.0, 3.0], [4.0, 200.0, 6.0]]),
        cal_flags=cal_flags,
    )

    display._update_total_power_axis(l_sec=2)

    np.testing.assert_array_equal(display.total_power_line.y, [4.0, 10.0])
    np.testing.assert_array_equal(display.mean_tpwr_line.y, [7.0, 7.0])
    assert display.mean_tpwr_line.visible is True


def test_signal_display_masks_spectra_marks_rfi_and_shades_bandpass():
    display = SignalDisplay(dig_id="dig001")
    scan_model = SimpleNamespace(
        scan_id="obs-0-0-1",
        dig_id="dig001",
        duration=2,
        sample_rate=6.0,
        center_freq=1420.4e6,
        spectral_resolution=6,
        scan_type=ScanType.SKY,
        gain=10.0,
        synthesised=False,
    )
    spr_flags = empty_channel_flags((2, 6))
    cal_flags = empty_channel_flags((2, 6))
    mpr_flags = empty_channel_flags(6)
    cal_flags[0, [0, 1, 5]] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    cal_flags[0, 3] |= int(ChannelFlag.RFI_DETECTED)
    mpr_flags[[0, 1, 5]] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    scan = SimpleNamespace(
        scan_model=scan_model,
        spr=np.array([[1.0, 2.0, 3.0, 40.0, 5.0, 6.0], np.zeros(6)]),
        cal=np.array([[1.0, 2.0, 3.0, 40.0, 5.0, 6.0], np.zeros(6)]),
        mpr=np.array([1.0, 2.0, 3.0, 40.0, 5.0, 6.0]),
        spr_flags=spr_flags,
        cal_flags=cal_flags,
        mpr_flags=mpr_flags,
        scan_qa=None,
        mean_real=0.0,
        mean_imag=0.0,
        get_loaded_seconds=lambda: 1,
    )

    display.set_scan(scan, None)
    display._update_spectrum_axes(1)
    display._update_waterfall()
    display._update_qa_overlay(1)

    np.testing.assert_array_equal(
        np.ma.getmaskarray(display.cal_line.get_ydata()),
        [False, False, False, True, False, False],
    )
    np.testing.assert_array_equal(display.cal_rfi_line.get_xdata(), display.freq_axis[[3]])
    np.testing.assert_array_equal(display.cal_rfi_line.get_ydata(), [4.0])
    assert display.cal_rfi_line.get_label() == "RFI (CAL)"
    assert len(display.mpr_rfi_line.get_xdata()) == 0
    assert display.mpr_rfi_line.get_label() == "_nolegend_"
    assert len(display.bandpass_spans) == 4  # Two excluded regions on each spectrum axis.
    assert "Usable: 3/6 channels" in display.qa_text.get_text()
    assert "RFI filled" not in display.qa_text.get_text()
    assert display.qa_text.get_fontsize() == 8
    assert all(text.get_fontsize() == 8 for text in display.sig[1].get_legend().get_texts())
    waterfall_mask = np.ma.getmaskarray(display.pwr_im.get_array())
    np.testing.assert_array_equal(waterfall_mask[0], [False, False, False, True, False, False])
    assert np.all(waterfall_mask[1])
    np.testing.assert_allclose(display.pwr_im.get_clim(), [3.02, 4.98])
    assert display.pwr_im.norm.clip is True
    assert display.pwr_im.get_cmap().get_bad()[-1] == 0.35
    bandpass_overlay = np.asarray(display.bandpass_im.get_array())
    np.testing.assert_allclose(bandpass_overlay[0, :, 3], [0.28, 0.28, 0.0, 0.0, 0.0, 0.28])
    assert not np.any(bandpass_overlay[1, :, 3])

    empty_mpr_qa = SimpleNamespace(
        baseline=None,
        noise_db=None,
        signal_pwr_db=None,
        signal_db=None,
        snr_db=None,
        signal_start=None,
        signal_end=None,
        fwhm=None,
        dynamic_range_db=None,
    )
    cal_qa = SimpleNamespace(rfi_fraction=0.0068)
    scan.scan_qa = SimpleNamespace(
        getQA=lambda pipeline, _idx: cal_qa if pipeline == "cal" else empty_mpr_qa,
    )
    display._update_qa_overlay(1)
    assert "RFI Frac: 1 channels, 0.68%" in display.qa_text.get_text()

    plt.close(display.fig)


def test_waterfall_percentile_limits_fall_back_for_constant_data():
    limits = SignalDisplay._percentile_limits(np.array([5.0, 5.0, np.nan]))

    assert limits is not None
    assert limits[0] < 5.0 < limits[1]


def test_total_power_axis_reconstructs_rfi_without_changing_cal_data():
    display = SignalDisplay(dig_id="dig001")
    display.total_power_line = FakeLine()
    display.mean_tpwr_line = FakeLine()
    display.sig[4] = FakeAxis()
    cal = np.array([[1.0, 100.0, 3.0], [4.0, 200.0, 6.0]])
    original = cal.copy()
    cal_flags = empty_channel_flags(cal.shape)
    cal_flags[:, 1] |= int(ChannelFlag.RFI_DETECTED)
    display.scan = SimpleNamespace(
        scan_model=SimpleNamespace(duration=2),
        cal=cal,
        cal_flags=cal_flags,
    )

    display._update_total_power_axis(l_sec=2)

    np.testing.assert_allclose(display.total_power_line.y, [6.0, 15.0])
    np.testing.assert_array_equal(cal, original)
