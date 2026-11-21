import importlib
import logging
from collections import defaultdict
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

WEATHER_STATION_DRIVER_NAMESPACE = "weather_station_driver"
DISH_DRIVER_NAMESPACE = "dish_driver"
IMU_DRIVER_NAMESPACE = "imu_driver"
PIPELINE_STEP_NAMESPACE = "pipeline_step"

_registries: Dict[str, Dict[str, Callable[..., Any]]] = defaultdict(dict)


def _normalize(name: str) -> str:
    if not name or not isinstance(name, str):
        raise ValueError("registry name must be a non-empty string")
    return name.strip().lower()


def register(namespace: str, name: str, ctor: Callable[..., Any]) -> None:
    """Register a constructor in a named registry namespace."""
    namespace_key = _normalize(namespace)
    name_key = _normalize(name)
    _registries[namespace_key][name_key] = ctor
    logger.info("Registered %s '%s' -> %s", namespace_key, name_key, ctor)


def get(namespace: str, name: str) -> Optional[Callable[..., Any]]:
    """Return a registered constructor for the given namespace/name pair."""
    namespace_key = _normalize(namespace)
    name_key = _normalize(name)
    return _registries.get(namespace_key, {}).get(name_key)


def list_registered(namespace: str) -> Dict[str, Callable[..., Any]]:
    """Return a copy of the registry mapping for a namespace."""
    namespace_key = _normalize(namespace)
    return dict(_registries.get(namespace_key, {}))


def load_class_from_path(path: str) -> Any:
    """Load a class from a module path.

    Supported formats: 'package.module:ClassName' or 'package.module.ClassName'.
    """
    if not path or not isinstance(path, str):
        raise ValueError("path must be a string")

    if ":" in path:
        module_name, attr_name = path.split(":", 1)
    else:
        parts = path.rsplit(".", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid class path '{path}'")
        module_name, attr_name = parts

    module = importlib.import_module(module_name)
    try:
        return getattr(module, attr_name)
    except AttributeError as exc:
        raise ImportError(f"module '{module_name}' has no attribute '{attr_name}'") from exc


def resolve(namespace: str, ref: str) -> Optional[Callable[..., Any]]:
    """Resolve a registry name or a module path into a constructor."""
    if not ref:
        return None

    ctor = get(namespace, ref)
    if ctor is not None:
        return ctor

    try:
        loaded = load_class_from_path(ref)
    except Exception:
        return None

    return loaded
