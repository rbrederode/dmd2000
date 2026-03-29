from __future__ import annotations

from typing import TYPE_CHECKING

import datetime
import logging
from pathlib import Path

import matplotlib as mpl
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.gridspec import GridSpec

from models.dsh import Capability, DishMode, PointingState

if TYPE_CHECKING:
    from dsh.drivers.driver import DishDriver
    from models.dsh import DishModel

# Disable automatic window raising (backend-specific)
mpl.rcParams["figure.raise_window"] = False

try:
    from AppKit import NSApplication

    HAS_APPKIT = True
except ImportError:
    HAS_APPKIT = False
    print("AppKit not available. Install pyobjc: pip install pyobjc")

logger = logging.getLogger(__name__)

FIG_SIZE = (14, 7)  # Default figure size for plots


class DishDisplay:
    """Performance-oriented dish display with the same plots and public interface as dish_display.py."""

    ATTR_LABELS = [                     # Attribute layout: (x, y, label) — x determines left (0) vs right (5) column
        (0, 0.5, "Target"),
        (0, 1.5, "Weather"),
        (5, 1.5, "Health"),
        (0, 2.5, "Lat/Long"),
        (5, 2.5, "Failures"),
        (0, 3.5, "Feed"),
        (5, 3.5, "Digitiser"),
        (0, 4.5, "Pointing State"),
        (5, 4.5, "Driver Type"),
        (0, 5.5, "Mode"),
        (5, 5.5, "Capability"),
        (0, 6.5, "Velocity Az"),
        (5, 6.5, "Velocity Alt"),
        (0, 7.5, "Deviation Az"),
        (5, 7.5, "Deviation Alt"),
        (0, 8.5, "Pointing Az"),
        (5, 8.5, "Pointing Alt"),
        (0, 9.5, "Desired Az"),
        (5, 9.5, "Desired Alt"),
    ]

    RECT_LEFT_X, RECT_RIGHT_X = 2.5, 7.5
    RECT_W, RECT_H = 2.5, 0.5
    LABEL_GAP = 0.1  # gap between label text and rectangle edge

    # Predefine colors
    MODE_COLOURS = {
        DishMode.STARTUP: "tab:olive",
        DishMode.SHUTDOWN: "dimgray",
        DishMode.STANDBY_LP: "tab:blue",
        DishMode.STANDBY_FP: "tab:blue",
        DishMode.MAINTENANCE: "tab:red",
        DishMode.STOW: "tab:orange",
        DishMode.CONFIG: "yellow",
        DishMode.OPERATE: "tab:green",
        DishMode.UNKNOWN: "tab:purple",
    }

    def __init__(self, driver: DishDriver):
        """Initialise the dish display for a driver.

        Parameters:
            driver: Dish driver whose model and history arrays will be displayed.
        """
        self.is_active = True  # Is this dish display instance active
        self.driver = driver  # Current dish driver being displayed

        logger.info(
            f"Dish display initialized for dish {self.driver.dsh_model.dsh_id} "
            f"with feed {self.driver.dsh_model.feed.value} and driver {self.driver.dsh_model.driver_type.name}"
        )

        self.fig = None         # Figure for the dish display
        self.axes = [None] * 5  # Axes for the dish subplots
        self.attr_rects = {}    # Attribute rectangles keyed by label name
        self.attr_texts = {}    # Attribute value texts keyed by label name

        self.gs0 = GridSpec(1, 3, width_ratios=[1, 1, 1], left=0.07, right=0.93, top=0.90, bottom=0.3, wspace=0.2)  # dish displays
        self.gs1 = GridSpec(1, 2, width_ratios=[0.3, 0.70], height_ratios=[1], left=0.07, right=0.93, top=0.20, bottom=0.08, wspace=0.2)  # pec plot

        self._reset_artist_refs()
        self._create_figure()

    def __str__(self):
        """Return a short string representation of the dish display."""
        return f"DishDisplay(dsh_id={self.driver.dsh_model.dsh_id}, is_active={self.is_active})"

    def _reset_artist_refs(self):
        """Reset dynamic matplotlib artist handles used by the update methods."""
        self.pointing_alt_line = None
        self.pointing_az_line = None
        self.pointing_min_alt_line = None
        self.pointing_max_alt_line = None
        self.desired_alt_line = None
        self.desired_az_line = None
        self.pec_alt_fill = None
        self.pec_az_fill = None

    def _create_figure(self):
        """Create the figure, axes, static formatting, and persistent attribute artists."""
        self.fig = plt.figure(num=f"Dish {self.driver.dsh_model.dsh_id}", figsize=FIG_SIZE)

        self.axes = [None] * 5  # Initialize axes for the subplots
        self.axes[0] = self.fig.add_subplot(self.gs0[0])  # Attributes such as pointing state and dish mode
        self.axes[1] = self.fig.add_subplot(self.gs0[1])  # Pointing altitude and azimuth timeline
        self.axes[2] = self.fig.add_subplot(self.gs0[2])  # Desired altitude and azimuth timeline
        self.axes[3] = self.fig.add_subplot(self.gs1[0])  # Dish mode timeline
        self.axes[4] = self.fig.add_subplot(self.gs1[1])  # PEC plot

        self.fig.subplots_adjust(top=0.78)
        self.fig.suptitle(
            f"Dish Id: {self.driver.dsh_model.dsh_id} Diameter: {self.driver.dsh_model.diameter}m FD Ratio: {self.driver.dsh_model.fd_ratio:.2f}",
            fontsize=12,
            y=0.96,
        )

        self.init_attribute_axes(self.axes[0])  # Initialise axes for attributes such as pointing state and dish mode
        self.init_pointing_axes(self.axes[1], "Pointing Altitude and Azimuth", "Pointing AltAz [Deg]")
        self.init_desired_axes(self.axes[2], "Desired Altitude and Azimuth", "Desired AltAz [Deg]")
        self.init_mode_axis(self.axes[3])
        self.init_pec_axes(self.axes[4])        # Initialise the PEC axes

        self._create_timeline_artists()

    def _create_timeline_artists(self):
        """Create persistent artists for the dynamic history plots."""
        self.pointing_alt_line, = self.axes[1].plot([], [], color="tab:blue", label="Pointing Alt")
        self.pointing_az_line, = self.axes[1].plot([], [], color="tab:red", label="Pointing Az")
        self.pointing_min_alt_line = self.axes[1].axhline(y=0, color="tab:purple", linestyle="dashed", linewidth=1.5, label="Min Alt")
        self.pointing_max_alt_line = self.axes[1].axhline(y=0, color="tab:purple", linestyle="dashed", linewidth=1.5, label="Max Alt")
        self.axes[1].legend(loc="upper left")

        self.desired_alt_line, = self.axes[2].plot([], [], color="tab:green", label="Desired Alt")
        self.desired_az_line, = self.axes[2].plot([], [], color="tab:orange", label="Desired Az")
        self.axes[2].legend(loc="upper left")

    def _close_figure(self):
        """Close the dish figure window if it exists.

        Returns:
            None.
        """
        if self.fig is not None:
            plt.close(num=f"Dish {self.driver.dsh_model.dsh_id}")

    def is_visible_figure(self) -> bool:
        """Return whether this display figure is the active visible window, or None if unknown.

        Returns:
            True if visible, False if not visible, or None when visibility cannot be determined.
        """
        if not HAS_APPKIT or self.fig is None:
            logger.warning(
                f"Dish display checking whether figure for {self.driver.dsh_model.dsh_id} is visible but "
                + ("AppKit not available" if not HAS_APPKIT else "figure is None")
            )
            return None

        key_window = NSApplication.sharedApplication().keyWindow()
        if key_window is None:
            return None

        key_window_title = key_window.title()
        fig_title = self.fig.canvas.manager.get_window_title()
        return fig_title == key_window_title

    def get_is_active(self) -> bool:
        """Return whether this dish display is active.

        Returns:
            True if updates are enabled, otherwise False.
        """
        return self.is_active

    def set_is_active(self, active: bool):
        """Set whether this dish display should update.

        Parameters:
            active: True to enable updates, False to disable them.

        Returns:
            None.
        """
        self.is_active = active

    def display(self):
        """Refresh all dynamic parts of the dish display for the current driver state.

        Returns:
            None.
        """
        # If the dish display is not active
        if not self.is_active:
            self._close_figure()  # Close the figure if it exists
            return

        # If no figure, then log warning and return
        if self.fig is None:
            logger.warning(f"Dish display for {self.driver.dsh_model.dsh_id} cannot display when figure is None")
            return

        # Check if the dish display figure is still visible
        is_visible_fig = self.is_visible_figure()
        if is_visible_fig is False:
            return

        logger.debug(f"Dish display updating for dish {self.driver.dsh_model.dsh_id}")

        if self.driver.dsh_model is not None:
            self._update_attribute_axis(self.driver.dsh_model)
            self._update_mode_timeline(self.driver.dsh_model)

        self._update_pointing_axis()
        self._update_desired_axis()
        self._update_pec_axis()
        self._draw(is_visible_fig)

    def _update_attribute_axis(self, model: DishModel):
        """Update the static attribute panel with the latest dish-model values.

        Parameters:
            model: Latest dish model to render in the attribute panel.

        Returns:
            None.
        """
        self.attr_texts["Failures"].set_text(f"{model.driver_failures}")
        self.attr_rects["Failures"].set_color(
            {"OK": "tab:green", "DEGRADED": "gold", "FAILED": "tab:red", "UNKNOWN": "tab:gray"}.get(model.health.name, "tab:gray")
        )
        self.attr_texts["Lat/Long"].set_text(f"{model.latitude:.1f}°,{model.longitude:.1f}°")
        self.attr_texts["Driver Type"].set_text(model.driver_type.name)
        self.attr_texts["Feed"].set_text(model.feed.name)
        self.attr_texts["Digitiser"].set_text(model.dig_id or "—")

        self.attr_texts["Health"].set_text(model.health.name)
        self.attr_rects["Health"].set_color(
            {"OK": "tab:green", "DEGRADED": "gold", "FAILED": "tab:red", "UNKNOWN": "tab:gray"}.get(model.health.name, "tab:gray")
        )

        self.attr_texts["Weather"].set_text("ALARM" if model.weather_alarm else "OK")
        self.attr_rects["Weather"].set_color("tab:red" if model.weather_alarm else "tab:green")
            
        self.attr_texts["Mode"].set_text(model.mode.name)
        self.attr_rects["Mode"].set_color(self.MODE_COLOURS.get(model.mode, "tab:gray"))

        capability_label = "DEGRADED" if model.capability.name == "OPERATE_DEGRADED" else model.capability.name
        self.attr_texts["Capability"].set_text(capability_label)
        self.attr_rects["Capability"].set_color(
            {
                "UNAVAILABLE": "tab:gray",
                "STANDBY": "tab:blue",
                "CONFIGURING": "tab:olive",
                "OPERATE_FULL": "tab:green",
                "OPERATE_DEGRADED": "gold",
            }.get(model.capability.name, "tab:gray")
        )

        self.attr_texts["Pointing State"].set_text(model.pointing_state.name)
        self.attr_rects["Pointing State"].set_color(
            {"SLEW": "gold", "READY": "tab:blue", "UNKNOWN": "tab:red", "TRACK": "tab:green", "SCAN": "tab:olive"}.get(
                model.pointing_state.name, "tab:gray"
            )
        )

        if model.target is not None:
            target_desc = f"{model.target.id or '—'} {model.target.pointing.name if model.target.pointing is not None else '—'}"
            self.attr_texts["Target"].set_text(target_desc)
            self.attr_rects["Target"].set_color("tab:blue" if model.target.id is not None else "tab:gray")
        else:
            self.attr_texts["Target"].set_text("—")
            self.attr_rects["Target"].set_color("tab:gray")

        if model.pointing_altaz is not None and isinstance(model.pointing_altaz, dict):
            self.attr_texts["Pointing Az"].set_text(f"{model.pointing_altaz.get('az', 0):.4f}°")
            self.attr_texts["Pointing Alt"].set_text(f"{model.pointing_altaz.get('alt', 0):.4f}°")

        if model.desired_altaz is not None and isinstance(model.desired_altaz, dict):
            self.attr_texts["Desired Az"].set_text(f"{model.desired_altaz.get('az', 0):.4f}°")
            self.attr_texts["Desired Alt"].set_text(f"{model.desired_altaz.get('alt', 0):.4f}°")

        if model.velocity_altaz is not None and isinstance(model.velocity_altaz, dict):
            self.attr_texts["Velocity Az"].set_text(f"{model.velocity_altaz.get('az', 0):.4f}°/s")
            self.attr_texts["Velocity Alt"].set_text(f"{model.velocity_altaz.get('alt', 0):.4f}°/s")

        pec_alt, pec_az = self.driver.get_current_pec()
        if pec_alt is not None and pec_az is not None:
            self.attr_texts["Deviation Az"].set_text(f"{pec_az:.4f}°")
            self.attr_texts["Deviation Alt"].set_text(f"{pec_alt:.4f}°")
            self.attr_rects["Deviation Az"].set_color("tab:red" if abs(pec_az) > 10 else "gold" if abs(pec_az) > 1 else "tab:green")
            self.attr_rects["Deviation Alt"].set_color("tab:red" if abs(pec_alt) > 10 else "gold" if abs(pec_alt) > 1 else "tab:green")
        else:
            self.attr_texts["Deviation Az"].set_text("—")
            self.attr_texts["Deviation Alt"].set_text("—")
            self.attr_rects["Deviation Az"].set_color("tab:gray")
            self.attr_rects["Deviation Alt"].set_color("tab:gray")

    def _update_pointing_axis(self):
        """Update the pointing-altitude and pointing-azimuth timeline artists.

        Returns:
            None.
        """
        if self.driver.pointing_altaz_hist is None:
            return

        pointing_hist_copy = self.driver.pointing_altaz_hist.copy()
        mask = pointing_hist_copy[:, 0] > 0
        if not np.any(mask):
            return

        dates = [datetime.datetime.fromtimestamp(ts) for ts in pointing_hist_copy[mask, 0]]
        alt_pointing = pointing_hist_copy[mask, 1]
        az_pointing = pointing_hist_copy[mask, 2]

        self.pointing_alt_line.set_data(dates, alt_pointing)
        self.pointing_az_line.set_data(dates, az_pointing)
        self.pointing_alt_line.set_label(f"Pointing Alt {alt_pointing[-1]:.2f}°")
        self.pointing_az_line.set_label(f"Pointing Az {az_pointing[-1]:.2f}°")

        # Draw min/max altitude limit lines
        min_alt, max_alt = self.driver.get_min_max_alt()
        self.pointing_min_alt_line.set_ydata([min_alt, min_alt])
        self.pointing_max_alt_line.set_ydata([max_alt, max_alt])
        self.pointing_min_alt_line.set_label(f"Min Alt {min_alt:.1f}°")
        self.pointing_max_alt_line.set_label(f"Max Alt {max_alt:.1f}°")

        self.axes[1].relim()
        self.axes[1].autoscale_view(scalex=True, scaley=True)
        legend = self.axes[1].get_legend()
        if legend is not None:
            legend.remove()
        self.axes[1].legend(loc="upper left")

    def _update_desired_axis(self):
        """Update the desired-altitude and desired-azimuth timeline artists.

        Returns:
            None.
        """
        if self.driver.desired_altaz_hist is None:
            return

        desired_hist_copy = self.driver.desired_altaz_hist.copy()
        mask = desired_hist_copy[:, 0] > 0
        if not np.any(mask):
            return

        dates = [datetime.datetime.fromtimestamp(ts) for ts in desired_hist_copy[mask, 0]]
        alt_desired = desired_hist_copy[mask, 1]
        az_desired = desired_hist_copy[mask, 2]

        self.desired_alt_line.set_data(dates, alt_desired)
        self.desired_az_line.set_data(dates, az_desired)
        self.desired_alt_line.set_label(f"Desired Alt {alt_desired[-1]:.2f}°")
        self.desired_az_line.set_label(f"Desired Az {az_desired[-1]:.2f}°")

        self.axes[2].relim()
        self.axes[2].autoscale_view(scalex=True, scaley=True)
        legend = self.axes[2].get_legend()
        if legend is not None:
            legend.remove()
        self.axes[2].legend(loc="upper left")

    def _update_pec_axis(self):
        """Update the periodic-error plot from the latest PEC history.

        Returns:
            None.
        """
        if self.driver.pec_hist is None:
            return

        pec_hist_copy = self.driver.pec_hist.copy()
        mask = pec_hist_copy[:, 0] > 0
        if not np.any(mask):
            return

        dates = [datetime.datetime.fromtimestamp(ts) for ts in pec_hist_copy[mask, 0]]
        alt_pec = pec_hist_copy[mask, 1]
        az_pec = pec_hist_copy[mask, 2]
        alt_pec_rms, az_pec_rms = self.driver.get_rms_pec()

        self.axes[4].cla()
        self.axes[4].fill_between(dates, alt_pec, alpha=0.2, color="tab:blue", label=f"Alt PEC {alt_pec[-1]:.3f}° (RMS: {alt_pec_rms:.3f}°)")
        self.axes[4].fill_between(dates, az_pec, alpha=0.2, color="tab:red", label=f"Az PEC {az_pec[-1]:.3f}° (RMS: {az_pec_rms:.3f}°)")
        self.init_pec_axes(self.axes[4])

        handles, labels = self.axes[4].get_legend_handles_labels()
        if handles:
            self.axes[4].legend(loc="upper left", fontsize=7)

    def _update_mode_timeline(self, dish_model: DishModel):
        """Redraw the dish-mode timeline from the model's mode history.

        Parameters:
            dish_model: Dish model providing the mode history array.

        Returns:
            None.
        """
        hist = dish_model.get_mode_hist()
        if hist.shape[0] == 0:
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        now_unix = now.timestamp()

        # -----------------------------------------------------
        # Build segments
        # Each row: [timestamp, old_mode, new_mode]
        # -----------------------------------------------------
        segments = []
        for i in range(len(hist)):
            t_start = hist[i, 0]
            new_mode = DishMode(int(hist[i, 2]))
            t_end = hist[i + 1, 0] if i < len(hist) - 1 else now_unix
            segments.append((t_start, t_end - t_start, new_mode))

        # -----------------------------------------------------
        # Convert to matplotlib time
        # -----------------------------------------------------
        self.clear_axes_data(self.axes[3])

        for start_unix, duration_sec, mode in segments:
            start_dt = datetime.datetime.fromtimestamp(start_unix, tz=datetime.timezone.utc)
            start_mpl = mdates.date2num(start_dt)
            duration_days = duration_sec / 86400.0

            self.axes[3].broken_barh([(start_mpl, duration_days)], (0, 4), facecolors=self.MODE_COLOURS[mode])

        # Update x-limits to cover all data
        start_dt = datetime.datetime.fromtimestamp(hist[0, 0], tz=datetime.timezone.utc)
        self.axes[3].set_xlim(mdates.date2num(start_dt), mdates.date2num(now))

        # Re-apply mode axis formatting (cleared by clear_axes_data)
        self.init_mode_axis(self.axes[3])
        self.axes[3].figure.canvas.draw_idle()

    def _draw(self, is_visible_fig):
        """Render the figure using the appropriate draw path for current visibility.

        Parameters:
            is_visible_fig: True if this figure is the active window, False if not, or None if unknown.

        Returns:
            None.
        """
        if is_visible_fig is None:
            plt.draw()
            plt.pause(0.0001)
        elif is_visible_fig is True:
            self.fig.canvas.draw()
            self.fig.canvas.flush_events()
        else:
            self.fig.canvas.draw_idle()
            self.fig.canvas.flush_events()

    def save_dsh_figure(self, output_dir: str) -> bool:
        """Save the current dish figure to disk.

        Parameters:
            output_dir: Directory to save the PNG file into.

        Returns:
            True if the figure was saved, otherwise False.
        """
        if self.fig is None:
            return False

        if output_dir is None or output_dir == "":
            output_dir = "."

        filename = f"{output_dir}/{self.driver.dsh_model.dsh_id}.png"
        filepath = Path(filename).expanduser()
        filepath.parent.mkdir(parents=True, exist_ok=True)

        self.fig.savefig(str(filepath))
        logger.info(f"Dish display for dish {self.driver.dsh_model.dsh_id} figure saved to {filepath}")
        return True

    def init_pec_axes(self, axes):
        """Initialise the periodic-error correction subplot axis.

        Parameters:
            axes: Matplotlib axis to configure.

        Returns:
            None.
        """
        axes.set_title("Periodic Error Correction (PEC)")
        axes.set_ylabel("PEC [Deg]")
        axes.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        axes.set_xlabel("Time [HH:MM:SS]")
        axes.grid(True)

    def init_attribute_axes(self, axes):
        """Initialise the dish attribute panel and create persistent text/rectangle artists.

        Parameters:
            axes: Matplotlib axis to configure.

        Returns:
            None.
        """
        axes.set_title("Dish Attributes")
        axes.grid(True)
        axes.set_xlim(0, 10)
        axes.set_ylim(0, 10)
        axes.axis("off")

        self.attr_rects = {}
        self.attr_texts = {}

        for x, y, label in self.ATTR_LABELS:
            rect_x = self.RECT_LEFT_X if x < 5 else self.RECT_RIGHT_X
            rect_w = self.RECT_W * 3.1 if label in ["Target"] else self.RECT_W
            axes.text(rect_x - self.LABEL_GAP, y, label, ha="right", va="center", fontsize=9)
            rect = plt.Rectangle((rect_x, y - 0.2), rect_w, self.RECT_H, color="tab:gray", alpha=0.5)
            axes.add_patch(rect)
            self.attr_rects[label] = rect
            # Value text centered inside the rectangle
            txt = axes.text(rect_x + rect_w / 2, y, "", ha="center", va="center", fontsize=8)
            self.attr_texts[label] = txt

        # Target Type labels can be long (e.g. FIVE_POINT_SCAN) — use a smaller font
        self.attr_texts["Target"].set_fontsize(6)

    def init_mode_axis(self, axes):
        """Initialise the dish-mode timeline axis and legend.

        Parameters:
            axes: Matplotlib axis to configure.

        Returns:
            None.
        """
        axes.set_ylim(0, 4)
        axes.set_yticks([])
        axes.set_ylabel("Dish Mode")
        axes.xaxis.set_major_formatter(mdates.DateFormatter("%M:%S"))
        axes.xaxis.set_major_locator(mdates.AutoDateLocator())
        axes.tick_params(axis="x", labelsize=7)
        axes.set_title("Dish Mode Timeline")
        axes.grid(False)

        # Create a list of colored rectangles for the legend
        legend_handles = [mpatches.Patch(color=color, label=mode.name) for mode, color in self.MODE_COLOURS.items()]
        # Add the legend below the axis, left-justified, in two rows
        axes.legend(handles=legend_handles, bbox_to_anchor=(-0.3, -0.2), loc="upper left", fontsize=7, title_fontsize=7, ncol=5)

    def init_pointing_axes(self, axes, title: str, ylabel: str):
        """Initialise a timeline axis for pointing history.

        Parameters:
            axes: Matplotlib axis to configure.
            title: Axis title text.
            ylabel: Y-axis label text.

        Returns:
            None.
        """
        axes.set_title(title)
        axes.set_ylabel(ylabel)
        axes.xaxis.set_major_formatter(mdates.DateFormatter("%M:%S"))
        axes.tick_params(axis="x", labelsize=7)
        axes.set_xlabel("Time [MM:SS]")
        axes.grid(True)

    def init_desired_axes(self, axes, title: str, ylabel: str):
        """Initialise a timeline axis for desired pointing history.

        Parameters:
            axes: Matplotlib axis to configure.
            title: Axis title text.
            ylabel: Y-axis label text.

        Returns:
            None.
        """
        self.init_pointing_axes(axes, title, ylabel)

    def clear_axes_data(self, ax):
        """Remove dynamic artists from an axis without removing titles or labels.

        Parameters:
            ax: Matplotlib axis to clean.

        Returns:
            None.
        """
        for line in ax.get_lines():
            line.remove()

        for coll in ax.collections:
            coll.remove()

        for patch in list(ax.patches):
            patch.remove()

        for img in ax.images:
            img.remove()

        for txt in ax.texts:
            txt.remove()

        legend = ax.get_legend()
        if legend is not None:
            legend.remove()
