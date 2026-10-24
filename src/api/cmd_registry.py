"""Configuration-backed registry for application-specific commands."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Callable

from util.xbase import XAPIValidationFailed


DEFAULT_COMMAND_CONFIG = Path(__file__).resolve().parents[1] / "config" / "CmdRegistry.json"
VALUE_TYPES = {
    "str": str,
    "int": int,
    "float": (int, float),
    "bool": bool,
}


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    value_required: bool
    value_type: str
    value_pattern: str | None = None

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "CommandSpec":
        if not isinstance(data, dict):
            raise ValueError(f"Command '{name}' configuration must be an object")

        command_name = str(name).strip().lower()
        if not command_name or not re.fullmatch(r"[a-z][a-z0-9_-]*", command_name):
            raise ValueError(f"Invalid command name '{name}'")

        value_config = data.get("value", {})
        if not isinstance(value_config, dict):
            raise ValueError(f"Command '{command_name}' value configuration must be an object")

        value_type = value_config.get("type", "str")
        if value_type not in VALUE_TYPES:
            raise ValueError(
                f"Command '{command_name}' has unsupported value type '{value_type}'"
            )

        value_pattern = value_config.get("pattern")
        if value_pattern is not None:
            if value_type != "str" or not isinstance(value_pattern, str):
                raise ValueError(
                    f"Command '{command_name}' pattern requires a string value type"
                )
            re.compile(value_pattern)

        return cls(
            name=command_name,
            description=str(data.get("description") or command_name),
            value_required=bool(value_config.get("required", False)),
            value_type=value_type,
            value_pattern=value_pattern,
        )

    def coerce_value(self, value):
        """Convert CLI/UI text into the configured wire type."""
        if value is None:
            return None
        if self.value_type == "str":
            return str(value)
        if self.value_type == "bool":
            if isinstance(value, bool):
                return value
            normalised = str(value).strip().lower()
            if normalised in ("true", "on", "1"):
                return True
            if normalised in ("false", "off", "0"):
                return False
            raise XAPIValidationFailed(
                f"Registered command '{self.name}' requires value type bool"
            )
        try:
            return int(value) if self.value_type == "int" else float(value)
        except (TypeError, ValueError) as exc:
            raise XAPIValidationFailed(
                f"Registered command '{self.name}' requires value type {self.value_type}"
            ) from exc

    def validate(self, api_call: dict) -> None:
        if "property" in api_call:
            raise XAPIValidationFailed(
                f"Registered command '{self.name}' does not accept a property"
            )

        value = api_call.get("value")
        if self.value_required and value is None:
            raise XAPIValidationFailed(
                f"Registered command '{self.name}' requires a value"
            )
        if value is None:
            return

        expected_type = VALUE_TYPES[self.value_type]
        if not isinstance(value, expected_type) or (
            self.value_type in ("int", "float") and isinstance(value, bool)
        ):
            raise XAPIValidationFailed(
                f"Registered command '{self.name}' requires value type {self.value_type}"
            )
        if isinstance(value, str) and not value.strip():
            raise XAPIValidationFailed(
                f"Registered command '{self.name}' requires a non-empty value"
            )
        if self.value_pattern is not None and re.fullmatch(self.value_pattern, value) is None:
            raise XAPIValidationFailed(
                f"Registered command '{self.name}' value does not match its configured pattern"
            )


class CommandRegistry:
    """Configured command specifications and explicitly registered handlers."""

    def __init__(self, app_name: str, specs=None, source_path: Path | None = None):
        self.app_name = str(app_name or "").strip().lower()
        self.source_path = Path(source_path) if source_path is not None else None
        self._specs = {spec.name: spec for spec in (specs or [])}
        self._handlers: dict[str, Callable] = {}

    @classmethod
    def load(cls, app_name: str, path=None) -> "CommandRegistry":
        source_path = Path(path or DEFAULT_COMMAND_CONFIG).expanduser()
        with source_path.open("r", encoding="utf-8") as config_file:
            data = json.load(config_file)

        if not isinstance(data, dict) or data.get("_type") != "CmdRegistry":
            raise ValueError(f"Invalid command registry in {source_path}")
        applications = data.get("applications")
        if not isinstance(applications, dict):
            raise ValueError("Command registry requires an applications object")

        app_key = str(app_name or "").strip().lower()
        app_config = applications.get(app_key, {})
        if not isinstance(app_config, dict):
            raise ValueError(f"Command registry application '{app_key}' must be an object")
        command_configs = app_config.get("commands", {})
        if not isinstance(command_configs, dict):
            raise ValueError(
                f"Command registry application '{app_key}' requires a commands object"
            )

        specs = [
            CommandSpec.from_dict(name, command_config)
            for name, command_config in command_configs.items()
        ]
        return cls(app_name=app_key, specs=specs, source_path=source_path)

    @property
    def command_names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def has_command(self, name: str) -> bool:
        return str(name or "").strip().lower() in self._specs

    def get_spec(self, name: str) -> CommandSpec | None:
        return self._specs.get(str(name or "").strip().lower())

    def validate(self, api_call: dict) -> CommandSpec:
        command_name = str(api_call.get("command") or "").strip().lower()
        spec = self.get_spec(command_name)
        if spec is None:
            raise XAPIValidationFailed(
                f"Application '{self.app_name}' does not register command '{command_name}'"
            )
        spec.validate(api_call)
        return spec

    def coerce_value(self, command_name: str, value):
        spec = self.get_spec(command_name)
        if spec is None:
            raise XAPIValidationFailed(
                f"Application '{self.app_name}' does not register command '{command_name}'"
            )
        return spec.coerce_value(value)

    def register_handler(self, name: str, handler: Callable) -> None:
        command_name = str(name or "").strip().lower()
        if not self.has_command(command_name):
            raise ValueError(
                f"Cannot register handler for unconfigured command '{command_name}' "
                f"on application '{self.app_name}'"
            )
        if not callable(handler):
            raise TypeError(f"Command handler for '{command_name}' must be callable")
        self._handlers[command_name] = handler

    def get_handler(self, name: str):
        return self._handlers.get(str(name or "").strip().lower())

    def validate_handlers(self) -> None:
        missing = sorted(set(self._specs) - set(self._handlers))
        if missing:
            raise ValueError(
                f"Application '{self.app_name}' has no handlers for configured commands: "
                + ", ".join(missing)
            )
