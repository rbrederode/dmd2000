from types import SimpleNamespace

from sdp.signal_display import SignalDisplay
from models.scan import ScanType


class FakeFigure:
    def __init__(self):
        self.saved_paths = []

    def savefig(self, path):
        self.saved_paths.append(path)


def make_scan(scan_id="obs-0-0-1"):
    scan_model = SimpleNamespace(
        scan_id=scan_id,
        read_start=None,
        dig_id="dig001",
        gain=10.0,
        duration=60,
        sample_rate=2.4e6,
        center_freq=1420.4e6,
        channels=1024,
        scan_type=ScanType.SKY,
    )
    return SimpleNamespace(scan_model=scan_model)


def test_save_scan_figure_only_saves_each_scan_once(tmp_path):
    display = SignalDisplay(dig_id="dig001")
    display.fig = FakeFigure()
    display.scan = make_scan()

    assert display.save_scan_figure(str(tmp_path)) is True
    assert display.save_scan_figure(str(tmp_path)) is False
    assert len(display.fig.saved_paths) == 1


def test_save_scan_figure_allows_next_scan_to_save(tmp_path):
    display = SignalDisplay(dig_id="dig001")
    display.fig = FakeFigure()
    display.scan = make_scan(scan_id="obs-0-0-1")

    assert display.save_scan_figure(str(tmp_path)) is True

    display.scan = make_scan(scan_id="obs-0-0-2")
    assert display.save_scan_figure(str(tmp_path)) is True
    assert len(display.fig.saved_paths) == 2
