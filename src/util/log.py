import os
import time
import logging
import shutil
from typing import Dict
from logging.handlers import TimedRotatingFileHandler

class MillisecondFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        t = time.strftime(datefmt, ct)
        s = "%s:%03d" % (t, record.msecs)
        return s

# repository-level logs directory (all logs stored relative to repo root)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
repo_logs_dir = os.path.join(project_root, "logs")
os.makedirs(repo_logs_dir, exist_ok=True)

# move existing src/logs/alarm and src/logs/availability into top-level logs if present (best-effort)
src_logs_dir = os.path.join(project_root, "src", "logs")
for sub in ("alarm", "availability"):
    old_dir = os.path.join(src_logs_dir, sub)
    new_dir = os.path.join(repo_logs_dir, sub)
    try:
        if os.path.isdir(old_dir):
            os.makedirs(new_dir, exist_ok=True)
            for fname in os.listdir(old_dir):
                srcf = os.path.join(old_dir, fname)
                destf = os.path.join(new_dir, fname)
                if os.path.exists(destf):
                    continue
                try:
                    shutil.move(srcf, destf)
                except Exception:
                    continue
            try:
                os.rmdir(old_dir)
            except Exception:
                pass
    except Exception:
        pass

# Configure default console logging first (ensures StreamHandler exists and root level is set)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

root = logging.getLogger()

# Each application runs in its own process, so a process-wide identity lets
# records from processor, timer, TCP and other worker threads share one log.
_current_app_name = None

def set_current_app(app_name: str | None) -> None:
    """Register the current app so logs can be routed to its dedicated logfile."""
    global _current_app_name
    _current_app_name = app_name

def get_current_app() -> str | None:
    """Get the name of the currently running app."""
    return _current_app_name


class AppRoutingHandler(logging.Handler):
    """Routes all log records to the currently running app's dedicated logfile.

    Uses process-wide app context so records from every application thread are
    written to the same file.
    """

    def __init__(self, app_logs_dir: str):
        super().__init__()
        self.app_logs_dir = app_logs_dir
        self._app_handlers: Dict[str, TimedRotatingFileHandler] = {}
        os.makedirs(app_logs_dir, exist_ok=True)

    def _handler_for_app(self, app_name: str) -> TimedRotatingFileHandler:
        """Get or create a handler for the given app."""
        if app_name in self._app_handlers:
            return self._app_handlers[app_name]

        filename = app_name.replace('.', '_') + '.log'
        fpath = os.path.join(self.app_logs_dir, filename)
        fh = TimedRotatingFileHandler(fpath, when="midnight", interval=1, backupCount=14, encoding="utf-8", utc=True)
        fh.setFormatter(MillisecondFormatter('%(asctime)s %(levelname)s [%(name)s]: %(message)s',
                                            datefmt='%Y-%m-%d %H:%M:%S'))
        fh.setLevel(logging.DEBUG)
        self._app_handlers[app_name] = fh
        return fh

    def emit(self, record: logging.LogRecord) -> None:
        if self._closed:
            return

        app_name = get_current_app()
        if not app_name:
            return  # No app context; skip routing
        try:
            handler = self._handler_for_app(app_name)
            handler.emit(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        """Close all per-app handlers and reject records during shutdown."""
        self.acquire()
        try:
            for handler in self._app_handlers.values():
                handler.close()
            self._app_handlers.clear()
            super().close()
        finally:
            self.release()

# add app routing handler to root logger to capture all logs from running app + its dependencies
app_logs_dir = os.path.join(repo_logs_dir, "app")
if not any(isinstance(h, AppRoutingHandler) for h in root.handlers):
    root.addHandler(AppRoutingHandler(app_logs_dir))
