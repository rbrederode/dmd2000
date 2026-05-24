import logging
from typing import Iterable

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.gridspec import GridSpec
from matplotlib.ticker import MultipleLocator

logger = logging.getLogger(__name__)

mpl.rcParams["figure.raise_window"] = False

FIG_SIZE = (14, 8)


class ObsDisplay:
    """Display aggregated observation scans across the full frequency span."""

    def __init__(self, obs_id: str):
        """Initialise the observation display.

        Parameters:
            obs_id: Observation identifier used in the figure title.
        """
        self.obs_id = obs_id
        self.scans = []
        self.fig = None
        self.ax_freq = None
        self.ax_vel = None
        self.freq_min_mhz = None
        self.freq_max_mhz = None

    def __str__(self):
        return f"ObsDisplay(obs_id={self.obs_id})"

    def _create_figure(self):
        """Create the matplotlib figure and axes."""
        self.fig = plt.figure(num=f"Observation {self.obs_id}", figsize=FIG_SIZE)
        gs = GridSpec(2, 1, height_ratios=[1, 1], hspace=0.28, top=0.92, bottom=0.10, left=0.08, right=0.95)
        self.ax_freq = self.fig.add_subplot(gs[0])
        self.ax_vel = self.fig.add_subplot(gs[1])

    def _prepare_figure(self):
        """Create or clear the figure prior to plotting."""
        if self.fig is None:
            self._create_figure()
        else:
            self.ax_freq.cla()
            self.ax_vel.cla()

    def set_scan(self, scans: Iterable, obs=None):
        """Set the aggregated scans to display.

        Parameters:
            scans: Iterable of aggregated SKY Scan instances to plot.
            obs: Optional Observation model used to derive the full frequency span.
        """
        if scans is None:
            self.scans = []
            return

        self.scans = sorted(
            [scan for scan in scans if getattr(scan, "scan_model", None) is not None],
            key=lambda scan: (scan.scan_model.center_freq, scan.scan_model.freq_scan),
        )

        self.freq_min_mhz = None
        self.freq_max_mhz = None
        if obs is not None and getattr(obs, "target_scans", None) is not None:
            freq_min_vals = [ts.freq_min / 1e6 for ts in obs.target_scans if getattr(ts, "freq_min", None) is not None]
            freq_max_vals = [ts.freq_max / 1e6 for ts in obs.target_scans if getattr(ts, "freq_max", None) is not None]
            if freq_min_vals:
                self.freq_min_mhz = min(freq_min_vals)
            if freq_max_vals:
                self.freq_max_mhz = max(freq_max_vals)

    def _plot_frequency_panel(self):
        """Plot all aggregated scans across the full frequency range."""
        self.ax_freq.set_title("Aggregated Sky Scans")
        self.ax_freq.set_xlabel("Frequency (MHz)")
        self.ax_freq.set_ylabel("Power Spectrum [a.u.]")
        self.ax_freq.grid(True, alpha=0.25)

        if not self.scans:
            self.ax_freq.text(0.5, 0.5, "No aggregated scans available", ha="center", va="center", transform=self.ax_freq.transAxes)
            return

        freq_spans = []
        cmap = mpl.colormaps["plasma"]
        colours = cmap(np.linspace(0, 1, len(self.scans)))

        for idx, scan in enumerate(self.scans):
            start_mhz = (scan.scan_model.center_freq - scan.scan_model.sample_rate / 2.0) / 1e6
            end_mhz = (scan.scan_model.center_freq + scan.scan_model.sample_rate / 2.0) / 1e6
            freq_axis = np.linspace(start_mhz, end_mhz, scan.scan_model.spectral_resolution)
            freq_spans.extend([start_mhz, end_mhz])

            y = scan.mpr if scan.mpr is not None else np.zeros(scan.scan_model.spectral_resolution)
            scan_id_parts = str(scan.scan_model.scan_id).split("-")
            label = f"Scan {'-'.join(scan_id_parts[-2:])}" if len(scan_id_parts) >= 2 else f"Scan {scan.scan_model.scan_id}"
            self.ax_freq.plot(freq_axis, y, color=colours[idx], linewidth=1.5, label=label)

        if self.freq_min_mhz is not None and self.freq_max_mhz is not None:
            self.ax_freq.set_xlim(self.freq_min_mhz, self.freq_max_mhz)
        elif freq_spans:
            self.ax_freq.set_xlim(min(freq_spans), max(freq_spans))

        x_min, x_max = self.ax_freq.get_xlim()
        if x_min <= 1420.4 <= x_max:
            self.ax_freq.axvline(x=1420.4, color="blue", linestyle="--", label="HI Line")

        self.ax_freq.legend(loc="upper right")

    def _plot_velocity_panel(self):
        """Use the lower half of the display for integrated total power per synthesized scan row."""
        self.ax_vel.set_title("Integrated Total Power")
        self.ax_vel.set_xlabel("Integrated Scans")
        self.ax_vel.set_ylabel("Power [a.u.]")
        self.ax_vel.grid(True, alpha=0.25)
        self.ax_vel.xaxis.set_major_locator(MultipleLocator(1))

        if not self.scans:
            self.ax_vel.text(
                0.5,
                0.5,
                "No aggregated scans available",
                ha="center",
                va="center",
                transform=self.ax_vel.transAxes,
            )
            return

        cmap = mpl.colormaps["plasma"]
        colours = cmap(np.linspace(0, 1, len(self.scans)))
        max_secs = 0

        for idx, scan in enumerate(self.scans):
            if scan.cal is None or scan.get_loaded_seconds() <= 0:
                continue

            tpw_sum = np.sum(scan.cal[:scan.get_loaded_seconds(), :], axis=1)
            if tpw_sum.size == 0:
                continue

            time_axis = np.arange(tpw_sum.size) + 1
            scan_id_parts = str(scan.scan_model.scan_id).split("-")
            label = f"Scan {'-'.join(scan_id_parts[-2:])}" if len(scan_id_parts) >= 2 else f"Scan {scan.scan_model.scan_id}"
            self.ax_vel.plot(time_axis, tpw_sum, color=colours[idx], linewidth=1.5, label=label)
            mean_tpw = float(np.mean(tpw_sum))
            self.ax_vel.axhline(mean_tpw, color=colours[idx], linestyle="--", linewidth=1.0, alpha=0.9)
            max_secs = max(max_secs, time_axis[-1])

        if max_secs > 0:
            self.ax_vel.set_xlim(1, max_secs)

        self.ax_vel.legend(loc="upper right")

    def display(self):
        """Render the observation display."""
        self._prepare_figure()
        self.fig.suptitle(f"Observation {self.obs_id}", fontsize=12)
        self._plot_frequency_panel()
        self._plot_velocity_panel()
        self.fig.canvas.draw_idle()
        plt.show(block=False)
