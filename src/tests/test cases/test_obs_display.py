from types import SimpleNamespace

import matplotlib
import numpy as np

matplotlib.use("Agg")

from matplotlib import pyplot as plt
from matplotlib.colors import to_rgba
from matplotlib.ticker import AutoMinorLocator, MaxNLocator

from obs.obs_display import ObsDisplay, SCAN_COLOURS
from sdp.channel_mask import ChannelFlag, empty_channel_flags


def test_integrated_total_power_uses_readable_ticks_and_non_yellow_colours(tmp_path):
    cal_flags = empty_channel_flags((134, 4))
    cal_flags[:, 2:] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    scan = SimpleNamespace(
        scan_model=SimpleNamespace(scan_id="obs-2-0", center_freq=1.42e9, freq_scan=0),
        cal=np.ones((134, 4)),
        cal_flags=cal_flags,
        get_loaded_seconds=lambda: 134,
    )
    display = ObsDisplay("test-observation")
    display.scans = [scan]
    display._create_figure()

    display._plot_velocity_panel()
    display.fig.canvas.draw()

    assert isinstance(display.ax_vel.xaxis.get_major_locator(), MaxNLocator)
    assert isinstance(display.ax_vel.xaxis.get_minor_locator(), AutoMinorLocator)
    visible_ticks = [tick for tick in display.ax_vel.get_xticks() if 1 <= tick <= 134]
    visible_minor_ticks = [tick for tick in display.ax_vel.xaxis.get_minorticklocs() if 1 <= tick <= 134]
    assert len(visible_ticks) < 20
    assert len(visible_minor_ticks) > len(visible_ticks)
    assert all(float(tick).is_integer() for tick in visible_ticks)
    assert display.ax_vel.lines[0].get_color() == SCAN_COLOURS[0]
    np.testing.assert_array_equal(display.ax_vel.lines[0].get_ydata(), np.full(134, 2.0))
    assert all(to_rgba(colour)[:3] != to_rgba("yellow")[:3] for colour in SCAN_COLOURS)

    output_path = tmp_path / "observation-id-sky-apr.png"
    assert display.save_integrated_total_power(str(output_path)) == str(output_path)
    assert output_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    plt.close(display.fig)
