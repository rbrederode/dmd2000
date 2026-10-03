from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
import queue
import threading
from typing import Any, Callable

import logging

logger = logging.getLogger(__name__)


@dataclass
class DeviceCommand:
    name: str
    args: tuple
    kwargs: dict
    future: Future


class DeviceWorker:
    """Own a device instance and execute every command on one dedicated thread."""

    def __init__(self, device_factory: Callable[[], Any], thread_name: str = "device-thread"):
        self._device_factory = device_factory
        self._commands: queue.Queue[DeviceCommand | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name=thread_name, daemon=True)
        self._started = threading.Event()
        self._startup_error: Exception | None = None
        self._terminal_error: Exception | None = None
        self._state_lock = threading.Lock()

    @property
    def startup_error(self) -> Exception | None:
        return self._startup_error

    def is_running(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        if self._thread.is_alive():
            return

        self._thread.start()
        self._started.wait()

    def stop(self) -> None:
        if not self._thread.is_alive():
            return

        self._commands.put(None)
        self._thread.join()

    def call(self, name: str, *args, **kwargs) -> Future:
        future = Future()

        # Keep command admission atomic with a fatal-device failure. Otherwise
        # another caller can queue a hardware operation after the worker has
        # decided to stop, leaving its Future unresolved.
        with self._state_lock:
            if self._terminal_error is not None:
                future.set_exception(self._terminal_error)
                return future
            if not self._thread.is_alive():
                error = self._startup_error or RuntimeError("Device worker is not running.")
                future.set_exception(error)
                return future

            self._commands.put(DeviceCommand(name=name, args=args, kwargs=kwargs, future=future))
        return future

    def _run(self) -> None:
        device = None
        try:
            try:
                device = self._device_factory()
            except Exception as exc:
                self._startup_error = exc
                return
            finally:
                self._started.set()

            while True:
                cmd = self._commands.get()
                if cmd is None:
                    break

                if cmd.future.cancelled():
                    continue

                try:
                    method = getattr(device, cmd.name)
                    result = method(*cmd.args, **cmd.kwargs)
                except Exception as exc:
                    cmd.future.set_exception(exc)
                    if self._is_fatal_device_error(exc):
                        # A failed USB operation can close or invalidate the
                        # native handle. Do not execute commands already queued
                        # by other application processors against that handle.
                        with self._state_lock:
                            self._terminal_error = exc
                            self._fail_pending_commands(exc)
                        break
                else:
                    cmd.future.set_result(result)
        finally:
            if device is not None:
                close = getattr(device, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception as exc:
                        logger.warning(f"Device worker close failed: {exc}")

    @staticmethod
    def _is_fatal_device_error(exc: Exception) -> bool:
        """Return whether an exception means the native device is unusable."""

        current = exc
        while current is not None:
            if current.__class__.__name__ in {"LibUSBError", "XHardwareFailure"}:
                return True
            current = current.__cause__ or current.__context__
        return False

    def _fail_pending_commands(self, exc: Exception) -> None:
        """Fail queued commands without executing them on an invalid handle."""

        while True:
            try:
                pending = self._commands.get_nowait()
            except queue.Empty:
                return

            if pending is not None and not pending.future.done():
                pending.future.set_exception(exc)
