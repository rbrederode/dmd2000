"""File-backed UI driver for the local dmd2000 web prototype.

The existing UI integration is push based: Telescope Manager periodically calls
``publish(model_dict)`` with serialised model snapshots. This driver keeps that
shape, but writes a compact browser-facing status document that the static
frontend can poll as JSON.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ui.drivers.driver import UIDriver

logger = logging.getLogger(__name__)

DEFAULT_STATUS_PATH = "src/ui/frontend_prototype/data/status.json"


class WebUIDriver(UIDriver):
    """Publish telescope model snapshots for the local web UI."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.status_path = self._resolve_path(
            self.config.get("status_path")
            or os.getenv("DMD2000_WEB_UI_STATUS_PATH", DEFAULT_STATUS_PATH)
        )
        raw_path = self.config.get("raw_path") or os.getenv(
            "DMD2000_WEB_UI_RAW_PATH", ""
        )
        self.raw_path = self._resolve_path(raw_path) if raw_path else None
        self.models: dict[str, dict[str, Any]] = {}
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        if self.raw_path:
            self.raw_path.parent.mkdir(parents=True, exist_ok=True)

    def publish(self, model_dict: dict) -> None:
        """Record a model snapshot and refresh the frontend status payload."""
        model_type = model_dict.get("_type", "")
        if not model_type:
            logger.warning("Web UI driver received a model without _type")
            return

        self.models[model_type] = model_dict
        status = self.build_status()
        self._write_json_atomic(self.status_path, status)
        if self.raw_path:
            self._write_json_atomic(self.raw_path, self.models)

    def build_status(self) -> dict[str, Any]:
        """Return the compact Status-tab payload consumed by app.js."""
        tm = self.models.get("TelescopeManagerModel", {})
        dm = self.models.get("DishManagerModel", {})
        dig = self.models.get("DigitiserList", {})
        sdp = self.models.get("ScienceDataProcessorModel", {})
        oda = self.models.get("ODAModel", {})
        ws = self.models.get("WeatherStationModel", {}) or self.models.get(
            "WeatherStationList", {}
        )

        dish = self._first(self._get(dm, "dish_store.dish_list", [])) or {}
        digitiser = self._first(self._get(dig, "dig_list", [])) or {}
        observation = self._current_observation(oda, digitiser)
        weather_summary = (
            self._first(self._get(ws, "weather_summaries", []))
            or self._first(
                self._get(dm, "weather_store.weather_summaries", [])
            )
            or {}
        )

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            "summary": {
                "tm": self._tm_summary(tm),
                "dish": self._dish_summary(dish),
                "digitiser": self._digitiser_summary(digitiser),
                "weather": self._weather_summary(weather_summary, dish),
            },
            "subsystems": [
                self._subsystem("TM", self._get(tm, "id") or "TM", tm),
                self._subsystem("DM", self._get(dm, "id") or "DM", dm),
                self._subsystem(
                    "DIG",
                    self._get(digitiser, "dig_id") or "DIG",
                    digitiser,
                ),
                self._subsystem(
                    "SDP", self._get(sdp, "sdp_id") or "SDP", sdp
                ),
                self._subsystem(
                    "WS",
                    self._get(weather_summary, "ws_id")
                    or self._get(ws, "ws_id")
                    or "WS",
                    ws,
                ),
            ],
            "observation": self._observation_summary(
                observation, dish, digitiser, sdp
            ),
        }

    def _tm_summary(self, tm: dict[str, Any]) -> dict[str, str]:
        allocations = self._get(tm, "allocations.alloc_list", []) or []
        active = [
            a for a in allocations if self._display(a.get("state")) == "ACTIVE"
        ]
        queue_size = self._get(tm, "app.queue_size", 0)
        state = self._health_label(self._get(tm, "app.health"))
        return {
            "state": state,
            "detail": f"Queue {queue_size}, allocations {len(active)}",
        }

    def _dish_summary(self, dish: dict[str, Any]) -> dict[str, str]:
        state = (
            self._display(dish.get("pointing_state"))
            or self._display(dish.get("mode"))
            or "UNKNOWN"
        )
        altaz = dish.get("pointing_altaz") or dish.get("desired_altaz") or {}
        alt = self._num(self._get(altaz, "alt"))
        az = self._num(self._get(altaz, "az"))
        if alt is not None and az is not None:
            detail = f"Alt {alt:.1f} deg, Az {az:.1f} deg"
        else:
            detail = (
                f"{dish.get('dsh_id', 'dish')} "
                f"{self._display(dish.get('mode')) or 'mode unknown'}"
            )
        return {"state": state, "detail": detail}

    def _digitiser_summary(self, dig: dict[str, Any]) -> dict[str, str]:
        scanning = dig.get("scanning")
        state = "SCANNING" if scanning else "IDLE"
        freq = self._num(dig.get("center_freq"))
        channels = dig.get("channels") or 0
        if freq:
            detail = f"{freq / 1e9:.4f} GHz, {channels} channels"
        else:
            detail = f"{dig.get('dig_id', 'digitiser')} ready"
        return {"state": state, "detail": detail}

    def _weather_summary(
        self, weather: dict[str, Any], dish: dict[str, Any]
    ) -> dict[str, str]:
        alarm = bool(dish.get("weather_alarm"))
        state = "ALARM" if alarm else "SAFE"
        avg = self._num(weather.get("wind_avg"))
        gust = self._num(weather.get("wind_max"))
        if avg is not None or gust is not None:
            detail = f"Wind avg {avg or 0:.1f} m/s, max {gust or 0:.1f} m/s"
        else:
            detail = "No recent weather summary"
        return {"state": state, "detail": detail}

    def _subsystem(
        self, label: str, name: str, model: dict[str, Any]
    ) -> dict[str, str]:
        app = model.get("app", {}) if isinstance(model, dict) else {}
        health = self._health_label(app.get("health") or model.get("health"))
        return {
            "name": str(name or label),
            "health": health,
            "health_class": self._chip_class(health),
            "state": self._state_for_model(model),
            "last_update": self._time_only(
                model.get("last_update") or app.get("last_update")
            ),
            "endpoint": self._endpoint(app),
        }

    def _observation_summary(
        self,
        obs: dict[str, Any],
        dish: dict[str, Any],
        dig: dict[str, Any],
        sdp: dict[str, Any],
    ) -> dict[str, str]:
        target = self._first(obs.get("targets", [])) or {}
        target_id = target.get("id") or target.get("target_id") or "No active target"
        coord = target.get("sky_coord", {}) or {}
        ra = self._num(coord.get("ra"))
        dec = self._num(coord.get("dec"))
        target_text = target_id
        if ra is not None and dec is not None:
            target_text = f"{target_id}, RA {ra:.4f} deg, Dec {dec:.4f} deg"

        target_cfg = self._first(obs.get("target_configs", [])) or {}
        feed = self._display(
            target_cfg.get("feed")
            or target_cfg.get("feed_type")
            or dish.get("feed_type")
        )
        pointing = self._display(target.get("pointing"))
        mode = ", ".join([part for part in (pointing, feed) if part])
        outputs = len(obs.get("spr_scans", []) or [])
        processing = len(sdp.get("processing_scans", []) or [])
        return {
            "id": obs.get("obs_id")
            or self._get(dig, "scanning.obs_id")
            or "No active observation",
            "state": self._display(obs.get("obs_state"))
            or ("Running" if dig.get("scanning") else "Idle"),
            "target": target_text,
            "mode": mode or "No active mode",
            "output": f"{outputs} summed-power files, {processing} processing scans",
        }

    def _current_observation(
        self, oda: dict[str, Any], dig: dict[str, Any]
    ) -> dict[str, Any]:
        obs_list = self._get(oda, "obs_store.obs_list", []) or []
        scanning_obs = self._get(dig, "scanning.obs_id")
        if scanning_obs:
            for obs in obs_list:
                if obs.get("obs_id") == scanning_obs:
                    return obs
        for obs in obs_list:
            if self._display(obs.get("obs_state")) in {
                "SCANNING",
                "READY",
                "CONFIGURING",
                "IDLE",
            }:
                return obs
        return self._first(obs_list) or {}

    @staticmethod
    def _resolve_path(value: str) -> Path:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        repo_root = Path(__file__).resolve().parents[4]
        return repo_root / path

    @staticmethod
    def _get(data: Any, path: str, default: Any = None) -> Any:
        cur = data
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    @staticmethod
    def _first(value: Any) -> Any:
        return value[0] if isinstance(value, list) and value else None

    @classmethod
    def _display(cls, value: Any) -> str:
        if isinstance(value, dict):
            if value.get("_type") == "enum.IntEnum":
                return str(value.get("value") or "")
            if "value" in value and len(value) <= 3:
                return str(value.get("value") or "")
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if value is None:
            return ""
        return str(value)

    @classmethod
    def _health_label(cls, value: Any) -> str:
        return cls._display(value) or "UNKNOWN"

    @classmethod
    def _state_for_model(cls, model: dict[str, Any]) -> str:
        for key in ("mode", "pointing_state", "obs_state", "scanning"):
            if key in model:
                value = model[key]
                if key == "scanning":
                    return "SCANNING" if value else "IDLE"
                return cls._display(value)
        app_running = cls._get(model, "app.app_running")
        if app_running is not None:
            return "RUNNING" if app_running else "STOPPED"
        return "UNKNOWN"

    @classmethod
    def _chip_class(cls, health: str) -> str:
        health = (health or "").upper()
        if health == "OK":
            return "is-live"
        if health == "DEGRADED":
            return "is-warn"
        if health == "FAILED":
            return "is-bad"
        return "is-empty"

    @classmethod
    def _time_only(cls, value: Any) -> str:
        raw = cls._display(value)
        if not raw:
            return "-"
        try:
            return (
                datetime.fromisoformat(raw.replace("Z", "+00:00"))
                .time()
                .isoformat(timespec="seconds")
                + "Z"
            )
        except ValueError:
            return raw

    @staticmethod
    def _endpoint(app: dict[str, Any]) -> str:
        args = app.get("arguments") or {}
        host = (
            args.get("local_host")
            or args.get("tm_host")
            or args.get("dig_host")
            or args.get("host")
            or ""
        )
        port = (
            args.get("local_port")
            or args.get("tm_port")
            or args.get("dig_port")
            or args.get("port")
            or ""
        )
        if host and port:
            return f"{host}:{port}"
        return "-"

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _write_json_atomic(path: Path, payload: Any) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        tmp_path.replace(path)
