import os
import time
import logging
import shutil
import threading
from typing import Dict
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

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

# main rotating log file stored in repo logs directory
log_file = os.path.join(repo_logs_dir, "dmd2000.log")
modules_logs_dir = os.path.join(repo_logs_dir, "modules")
os.makedirs(modules_logs_dir, exist_ok=True)

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

# write to file with rotation
file_handler = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)  # or INFO
file_handler.setFormatter(MillisecondFormatter(
    '%(asctime)s %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))

# avoid adding the same handler twice (use handler class+filename check)
root = logging.getLogger()
already = any(isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == file_handler.baseFilename for h in root.handlers)
if not already:
    root.addHandler(file_handler)


class PerModuleTimedRotatingHandler(logging.Handler):
    """A dispatcher handler that writes records to per-module TimedRotatingFileHandlers.

    Each logger name gets its own file named by replacing '.' with '_' in the logger name.
    Files rotate daily at midnight and are kept for 14 days by default.
    """

    def __init__(self, modules_dir: str, when: str = "midnight", backupCount: int = 14):
        super().__init__()
        self.modules_dir = modules_dir
        self.when = when
        self.backupCount = backupCount
        self._handlers: Dict[str, TimedRotatingFileHandler] = {}

    def _handler_for(self, logger_name: str) -> TimedRotatingFileHandler:
        key = logger_name.replace('.', '_')
        if key in self._handlers:
            return self._handlers[key]

        fname = os.path.join(self.modules_dir, f"{key}.log")
        h = TimedRotatingFileHandler(fname, when=self.when, interval=1, backupCount=self.backupCount, encoding="utf-8", utc=True)
        h.setFormatter(MillisecondFormatter('%(asctime)s %(levelname)s [%(name)s]: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
        h.setLevel(logging.DEBUG)
        self._handlers[key] = h
        return h

    def emit(self, record: logging.LogRecord) -> None:
        try:
            handler = self._handler_for(record.name)
            handler.emit(record)
        except Exception:
            self.handleError(record)


# Thread-local context to track which app is currently running
_app_context = threading.local()

def set_current_app(app_name: str) -> None:
    """Register the current app so logs can be routed to its dedicated logfile."""
    _app_context.app_name = app_name

def get_current_app() -> str:
    """Get the name of the currently running app."""
    return getattr(_app_context, 'app_name', None)


class AppRoutingHandler(logging.Handler):
    """Routes all log records to the currently running app's dedicated logfile.

    Uses thread-local app context to determine which app logfile to write to.
    """

    # Map: app_name -> TimedRotatingFileHandler
    _app_handlers: Dict[str, TimedRotatingFileHandler] = {}

    def __init__(self, app_logs_dir: str):
        super().__init__()
        self.app_logs_dir = app_logs_dir
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
        app_name = get_current_app()
        if not app_name:
            return  # No app context; skip routing
        try:
            handler = self._handler_for_app(app_name)
            handler.emit(record)
        except Exception:
            self.handleError(record)

# add per-module timed rotating handler to root logger if not present
if not any(isinstance(h, PerModuleTimedRotatingHandler) for h in root.handlers):
    root.addHandler(PerModuleTimedRotatingHandler(modules_logs_dir))

# add app routing handler to root logger to capture all logs from running app + its dependencies
app_logs_dir = os.path.join(repo_logs_dir, "app")
if not any(isinstance(h, AppRoutingHandler) for h in root.handlers):
    root.addHandler(AppRoutingHandler(app_logs_dir))