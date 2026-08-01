from types import SimpleNamespace

import numpy as np

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
