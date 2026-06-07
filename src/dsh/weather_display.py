from __future__ import annotations

from typing import Optional

import datetime
import logging
import os
from pathlib import Path
import warnings

_mpl_cache = Path(os.environ.get("MPLCONFIGDIR", "/tmp/dmd2000-matplotlib"))
_mpl_cache.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_mpl_cache))

import matplotlib as mpl
import numpy as np
from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message="Unable to import Axes3D.*",
        category=UserWarning,
    )
    import matplotlib.pyplot as plt

from models.ws import WeatherStationList
from util.matplotlib_window import get_figure_visibility

mpl.rcParams["figure.raise_window"] = False

logger = logging.getLogger(__name__)

FIG_SIZE = (14, 7)


class WeatherDisplay:
    """Live display for a single weather station using the rolling WeatherStationList samples."""

    ATTR_ROWS = [
        ("Timeout", "Age"),
        ("Wind Avg", "Wind Avg"),
        ("Wind Gust", "Wind Gust"),
        ("Gust Count", "Gust Count"),
        ("Precipitation", "Precipitation"),
        ("Last Alarm", "Alarm"),
        ("Last Update", "Samples"),
    ]

    SUMMARY_ITEMS = [
        ("Last Mth Count", "Alarm Count"),
        ("Last Mth Active", "Activated"),
        ("Last Mth Clear", "Deactivated"),
        ("Mean Recovery", "MTTR"),
    ]

    RECT_LEFT_X, RECT_RIGHT_X = 2.5, 7.5
    RECT_W, RECT_H = 2.5, 0.35
    LABEL_GAP = 0.1
    GROUP_LEFT_X = 0.25
    GROUP_RIGHT_X = 10.36

    def __init__(self, weather_store: WeatherStationList, ws_id: str):
        self.weather_store = weather_store
        self.ws = weather_store.get_station(ws_id=ws_id)

        if self.ws is None:
            raise ValueError(f"WeatherDisplay: No weather station found with id {ws_id}")

        self.is_active = True

        self.fig = None
        self.attr_ax = None
        self.wind_ax = None
        self.precip_ax = None
        self.attr_rects = {}
        self.attr_texts = {}
        self.wind_line = None
        self.precip_line = None
        self.wind_avg_line = None
        self.wind_gust_line = None
        self.precip_thresh_line = None

        self.gs = GridSpec(1, 2, width_ratios=[0.36, 0.64], left=0.07, right=0.93, top=0.88, bottom=0.12, wspace=0.18)
        self._create_figure()

    def _create_figure(self):
        self.fig = plt.figure(num=f"Weather Station {self.ws.ws_id}", figsize=FIG_SIZE)
        self.attr_ax = self.fig.add_subplot(self.gs[0])
        plot_gs = GridSpecFromSubplotSpec(2, 1, subplot_spec=self.gs[1], hspace=0.50)
        self.wind_ax = self.fig.add_subplot(plot_gs[0])
        self.precip_ax = self.fig.add_subplot(plot_gs[1], sharex=self.wind_ax)


        self.fig.suptitle(f"Station Id: {self.ws.ws_id}, Lat: {self.ws.latitude:.2f}°, Lon: {self.ws.longitude:.2f}°", fontsize=12, y=0.96)
        self._init_attribute_axes()
        self._init_plot_axes()

        # Show the GUI window before visibility checks can suppress first refreshes.
        try:
            self.fig.show()
            self.fig.canvas.flush_events()
        except Exception as exc:
            logger.debug(f"Weather display for {self.ws.ws_id} could not show figure window: {exc}")

    def _init_attribute_axes(self):
        ax = self.attr_ax
        ax.set_title("Weather Attributes")
        ax.set_xlim(0, 10.45)
        ax.set_ylim(0, 8)
        ax.axis("off")

        ax.text(self.RECT_LEFT_X + self.RECT_W / 2, 7.45, "Thresholds", ha="center", va="center", fontsize=10, fontweight="bold")
        ax.text(self.RECT_RIGHT_X + self.RECT_W / 2, 7.45, "Actuals", ha="center", va="center", fontsize=10, fontweight="bold")

        y_positions = np.linspace(6.85, 2.05, len(self.ATTR_ROWS))

        group_rect = plt.Rectangle(
            (self.GROUP_LEFT_X, y_positions[4] - 0.38),
            self.GROUP_RIGHT_X - self.GROUP_LEFT_X,
            (y_positions[0] + 0.38) - (y_positions[4] - 0.38),
            fill=False,
            edgecolor="tab:gray",
            linewidth=1.0,
        )
        ax.add_patch(group_rect)

        self.attr_rects = {}
        self.attr_texts = {}

        for y, (left_label, right_label) in zip(y_positions, self.ATTR_ROWS):
            if left_label:
                ax.text(self.RECT_LEFT_X - self.LABEL_GAP, y, left_label, ha="right", va="center", fontsize=8)
                left_rect = plt.Rectangle(
                    (self.RECT_LEFT_X, y - self.RECT_H / 2),
                    self.RECT_W,
                    self.RECT_H,
                    color="tab:gray",
                    alpha=0.5,
                )
                ax.add_patch(left_rect)
                self.attr_rects[(left_label, "threshold")] = left_rect
                self.attr_texts[(left_label, "threshold")] = ax.text(
                    self.RECT_LEFT_X + self.RECT_W / 2, y, "", ha="center", va="center", fontsize=8
                )

            if right_label:
                ax.text(self.RECT_RIGHT_X - self.LABEL_GAP, y, right_label, ha="right", va="center", fontsize=8)
                right_rect = plt.Rectangle(
                    (self.RECT_RIGHT_X, y - self.RECT_H / 2),
                    self.RECT_W,
                    self.RECT_H,
                    color="tab:gray",
                    alpha=0.5,
                )
                ax.add_patch(right_rect)
                self.attr_rects[(right_label, "value")] = right_rect
                self.attr_texts[(right_label, "value")] = ax.text(
                    self.RECT_RIGHT_X + self.RECT_W / 2, y, "", ha="center", va="center", fontsize=8
                )

        summary_positions = [
            (0, 1.15),
            (1, 1.15),
            (1, 0.45),
            (0, 0.45),
        ]
        summary_rect_x = [self.RECT_LEFT_X, self.RECT_RIGHT_X]

        for (label, value_key), (col_idx, y) in zip(self.SUMMARY_ITEMS, summary_positions):
            rect_x = summary_rect_x[col_idx]
            ax.text(rect_x - self.LABEL_GAP, y, label, ha="right", va="center", fontsize=8)
            rect = plt.Rectangle(
                (rect_x, y - self.RECT_H / 2),
                self.RECT_W,
                self.RECT_H,
                color="tab:gray",
                alpha=0.5,
            )
            ax.add_patch(rect)
            self.attr_rects[(value_key, "summary")] = rect
            self.attr_texts[(value_key, "summary")] = ax.text(
                rect_x + self.RECT_W / 2, y, "", ha="center", va="center", fontsize=8
            )

        ax.text(
            self.RECT_LEFT_X,
            -0.04,
            "Last mth format: DD:HH:MM:SS",
            ha="left",
            va="bottom",
            fontsize=8,
            color="tab:gray",
        )
        ax.text(
            self.RECT_LEFT_X,
            -0.24,
            "Last mth data updated hourly",
            ha="left",
            va="bottom",
            fontsize=8,
            color="tab:gray",
        )

    def _init_plot_axes(self):
        self.wind_ax.set_title("Wind Speed")
        self.wind_ax.set_xlabel("Seconds")
        self.wind_ax.set_ylabel("Wind Speed [m/s]")
        self.wind_ax.grid(True)
        self.wind_ax.tick_params(axis="x", labelbottom=True)

        self.precip_ax.set_title("Precipitation")
        self.precip_ax.set_xlabel("Seconds")
        self.precip_ax.set_ylabel("Precipitation [mm]")
        self.precip_ax.grid(True)

        self.wind_line, = self.wind_ax.plot([], [], color="tab:blue", linewidth=2, label="Wind Speed")
        self.wind_avg_line = self.wind_ax.axhline(
            y=self.weather_store.threshold_wind_avg,
            color="tab:orange",
            linestyle="dashed",
            linewidth=1.5,
            label="Avg Threshold",
        )
        self.wind_gust_line = self.wind_ax.axhline(
            y=self.weather_store.threshold_wind_gust,
            color="tab:red",
            linestyle="dashed",
            linewidth=1.5,
            label="Gust Threshold",
        )

        self.precip_line, = self.precip_ax.plot([], [], color="tab:purple", linewidth=2, label="Precipitation")
        self.precip_thresh_line = self.precip_ax.axhline(
            y=self.weather_store.threshold_precipitation,
            color="tab:red",
            linestyle="dashed",
            linewidth=1.5,
            label="Precip Threshold",
        )

    def get_is_active(self) -> bool:
        return self.is_active

    def set_is_active(self, active: bool):
        self.is_active = active

    def _close_figure(self):
        if self.fig is not None:
            try:
                plt.close(num=f"Weather {self.ws.ws_id}")
            except Exception as exc:
                logger.debug(f"Weather display for {self.ws.ws_id} failed to close figure: {exc}")

    def is_visible_figure(self) -> Optional[bool]:
        return get_figure_visibility(self.fig)

    def display(self):
        try:
            if not self.is_active:
                self._close_figure()
                return

            if self.fig is None:
                return

            is_visible_fig = self.is_visible_figure()
            if is_visible_fig is False:
                return

            self._update_attributes()
            self._update_plot()

            if getattr(self.fig, "canvas", None) is None:
                logger.debug(f"Weather display for {self.ws.ws_id} has no canvas to draw")
                return

            self.fig.canvas.draw_idle()
            self.fig.canvas.flush_events()
            plt.pause(0.001)
        except Exception as exc:
            logger.exception(f"Weather display for {self.ws.ws_id} failed to refresh: {exc}")
            self.is_active = False
            self._close_figure()

    def _set_field(self, label: str, kind: str, text: str, alarm: bool = False, color: str = None):
        if (label, kind) not in self.attr_texts or (label, kind) not in self.attr_rects:
            return
        self.attr_texts[(label, kind)].set_text(text)
        if kind == "threshold":
            self.attr_rects[(label, kind)].set_color("tab:gray")
        elif kind == "summary":
            self.attr_rects[(label, kind)].set_color(color if color is not None else "tab:gray")
        else:
            self.attr_rects[(label, kind)].set_color(color if color is not None else ("tab:red" if alarm else "tab:green"))

    @staticmethod
    def _format_duration(minutes: float) -> str:
        total_seconds = max(0, int(round((minutes or 0.0) * 60.0)))
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        mins, secs = divmod(remainder, 60)
        return f"{days:02d}:{hours:02d}:{mins:02d}:{secs:02d}"

    def _update_attributes(self):
        metrics = self.weather_store.get_alarm_metrics(ws_id=self.ws.ws_id)

        latest_sample = metrics["latest_sample"]
        latest_age_sec = metrics["latest_age_sec"]
        latest_update = latest_sample.obs_time.strftime("%H:%M:%S") if latest_sample is not None else "—"
        last_alarm = self.weather_store.trigger_dt.strftime("%H:%M:%S") if self.weather_store.trigger_dt is not None else "—"

        self._set_field("Timeout", "threshold", f"{self.weather_store.threshold_timeout}s", metrics["timeout_triggered"])
        self._set_field("Age", "value", f"{latest_age_sec:.1f}s" if latest_age_sec is not None else "No Data", metrics["timeout_triggered"])

        self._set_field("Wind Avg", "threshold", f"{self.weather_store.threshold_wind_avg:.1f}m/s", metrics["wind_avg_triggered"])
        self._set_field("Wind Avg", "value", f"{metrics['avg_wind']:.1f}m/s", metrics["wind_avg_triggered"])

        self._set_field("Wind Gust", "threshold", f"{self.weather_store.threshold_wind_gust:.1f}m/s", metrics["gust_triggered"])
        self._set_field("Wind Gust", "value", f"{metrics['max_wind']:.1f}m/s", metrics["gust_triggered"])

        self._set_field("Gust Count", "threshold", f"{self.weather_store.threshold_wind_count:d}", metrics["gust_count_triggered"])
        gust_count_color = "orange" if 0 < metrics["gust_count"] <= self.weather_store.threshold_wind_count else None
        self._set_field("Gust Count", "value", f"{metrics['gust_count']:d}", metrics["gust_count_triggered"], color=gust_count_color)

        self._set_field("Precipitation", "threshold", f"{self.weather_store.threshold_precipitation:.1f}mm", metrics["precipitation_triggered"])
        self._set_field("Precipitation", "value", f"{metrics['avg_precipitation']:.1f}mm", metrics["precipitation_triggered"])

        self._set_field("Last Alarm", "threshold", last_alarm + " UTC" if last_alarm != "—" else "—", False)
        self._set_field("Alarm", "value", "TRIGGERED" if metrics["alarm_triggered"] else "OK", metrics["alarm_triggered"])

        self._set_field("Last Update", "threshold", latest_update + " UTC", False)
        self._set_field("Samples", "value", f"{metrics['sample_count']:d}", False)

        self._set_field("Alarm Count", "summary", f"{self.weather_store.last_mth_alarm_count:d}", False)
        self._set_field("Activated", "summary", self._format_duration(self.weather_store.last_mth_alarm_activated), False, color="tab:red")
        self._set_field("Deactivated", "summary", self._format_duration(self.weather_store.last_mth_alarm_deactivated), False, color="tab:green")
        self._set_field("MTTR", "summary", self._format_duration(self.weather_store.last_mth_alarm_mttr), False)

    def _update_plot(self):
        samples = self.weather_store.get_station_weather(
            ws_id=self.ws.ws_id,
            window_sec=self.weather_store.retention_period,
        )
        if not samples:
            self.wind_line.set_data([], [])
            self.precip_line.set_data([], [])
            self.wind_ax.relim()
            self.wind_ax.autoscale_view()
            self.precip_ax.relim()
            self.precip_ax.autoscale_view()
            return

        t0 = samples[0].obs_time
        times = np.array([(sample.obs_time - t0).total_seconds() for sample in samples], dtype=float)
        wind = np.array([np.nan if sample.wind_speed is None else sample.wind_speed for sample in samples], dtype=float)
        precip = np.array([np.nan if sample.precipitation is None else sample.precipitation for sample in samples], dtype=float)

        self.wind_line.set_data(times, wind)
        self.precip_line.set_data(times, precip)
        self.wind_line.set_label(f"Wind Speed {wind[~np.isnan(wind)][-1]:.2f} m/s" if np.any(~np.isnan(wind)) else "Wind Speed")
        self.precip_line.set_label(f"Precipitation {precip[~np.isnan(precip)][-1]:.2f} mm" if np.any(~np.isnan(precip)) else "Precipitation")
        self.wind_avg_line.set_ydata([self.weather_store.threshold_wind_avg, self.weather_store.threshold_wind_avg])
        self.wind_avg_line.set_label(f"Avg Threshold {self.weather_store.threshold_wind_avg:.2f} m/s")
        self.wind_gust_line.set_ydata([self.weather_store.threshold_wind_gust, self.weather_store.threshold_wind_gust])
        self.wind_gust_line.set_label(f"Gust Threshold {self.weather_store.threshold_wind_gust:.2f} m/s")
        self.precip_thresh_line.set_ydata([self.weather_store.threshold_precipitation, self.weather_store.threshold_precipitation])
        self.precip_thresh_line.set_label(f"Precip Threshold {self.weather_store.threshold_precipitation:.2f} mm")

        self.wind_ax.set_xlim(0.0, max(self.weather_store.retention_period + 1.0, times[-1]))

        self.wind_ax.relim()
        self.wind_ax.autoscale_view(scalex=False, scaley=True)
        self.precip_ax.relim()
        self.precip_ax.autoscale_view(scalex=False, scaley=True)

        wind_legend = self.wind_ax.get_legend()
        if wind_legend is not None:
            wind_legend.remove()
        self.wind_ax.legend(loc="upper left", fontsize=8)

        precip_legend = self.precip_ax.get_legend()
        if precip_legend is not None:
            precip_legend.remove()
        self.precip_ax.legend(loc="upper left", fontsize=8)
