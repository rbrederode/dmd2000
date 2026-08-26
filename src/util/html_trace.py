"""Thread-safe, expandable tracing for application and protocol activity."""

from __future__ import annotations

import gzip
import html
import logging
import os
import re
import shutil
import socket
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ipc.message import Message


logger = logging.getLogger(__name__)

_DOCUMENT_HEADER = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: light dark; --rx:#1677c8; --tx:#16854d; --muted:#68707a; }}
body {{ font: 14px/1.45 system-ui, sans-serif; margin: 0; padding: 1rem 1.25rem 3rem; }}
header {{ position: sticky; top: 0; z-index: 2; padding: .7rem 0; background: Canvas; border-bottom: 1px solid #9996; }}
h1 {{ display: inline; margin: 0 1rem 0 0; font-size: 1.2rem; }}
button {{ margin-right: .4rem; }}
.session, .marker {{ margin: .8rem 0; padding: .65rem .8rem; border-left: .35rem solid var(--muted); background: #8881; }}
.session dl {{ display: grid; grid-template-columns: max-content 1fr; gap: .15rem .8rem; margin: .5rem 0 0; }}
.session dt {{ color: var(--muted); }} .session dd {{ margin: 0; }}
.marker.start {{ border-color: var(--tx); }} .marker.stop {{ border-color: #b66a00; }}
details.message {{ margin: .25rem 0; border: 1px solid #8885; border-radius: .3rem; }}
details.message > summary {{ cursor: pointer; padding: .45rem .65rem; font-family: ui-monospace, monospace; }}
details.rx > summary {{ border-left: .35rem solid var(--rx); }}
details.tx > summary {{ border-left: .35rem solid var(--tx); }}
details.exception > summary {{ border-left: .35rem solid #c62828; }}
details.error-message > summary {{ border-left: .35rem solid #c62828; }}
.connection {{ margin: .25rem 0; padding: .45rem .65rem; border: 1px solid #8885; border-radius: .3rem; font-family: ui-monospace, monospace; }}
.connection.connected {{ border-left: .35rem solid var(--tx); }}
.connection.disconnected {{ border-left: .35rem solid #b66a00; }}
.direction {{ display: inline-block; min-width: 2.2rem; font-weight: 700; }}
.kind {{ font-weight: 700; }} .meta {{ color: var(--muted); }}
pre {{ overflow: auto; margin: 0; padding: .8rem; border-top: 1px solid #8885; background: #8881; font: 12px/1.35 ui-monospace, monospace; }}
</style>
<script>
function setAll(open) {{ document.querySelectorAll('details.message').forEach(x => x.open = open); }}
</script>
</head>
<body>
<header><h1>{heading}</h1><button onclick="setAll(true)">Expand all</button><button onclick="setAll(false)">Collapse all</button></header>
"""

_ROTATED_TRACE_PATTERN = re.compile(
    r"^.+-\d{4}-\d{2}-\d{2}(?:-\d+)?\.html$"
)


class HTMLTrace:
    """Writes compact message flow entries with expandable wire-level details."""

    def __init__(
        self,
        output_dir: Path,
        app_name: str,
        app_version: str,
        server_name: Optional[str] = None,
        max_bytes: int = 25 * 1024 * 1024,
        backup_count: int = 14,
        manage_trace_archives: bool = False,
        archive_scan_interval: float = 60 * 60,
    ):
        self.output_dir = Path(output_dir).expanduser()
        self.app_name = app_name
        self.app_version = app_version
        self.server_name = server_name or socket.gethostname()
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._safe_app_name = re.sub(r"[^A-Za-z0-9_.-]", "_", app_name)
        self.path = self.output_dir / f"{self._safe_app_name}.html"
        self._lock = threading.RLock()
        self._stream = None
        self._started = False
        self._file_date = None
        self._message_count = 0
        self._archive_scan_interval = archive_scan_interval
        self._archive_manager_stop = threading.Event()
        self._archive_manager_thread = None

        if manage_trace_archives:
            self._start_archive_manager()

    @property
    def is_started(self) -> bool:
        with self._lock:
            return self._started

    def start(self, reason: str = "Tracing enabled") -> None:
        """Start tracing and append a visible session header and start marker."""

        with self._lock:
            if self._started:
                return

            timestamp = datetime.now(timezone.utc)
            self._open(timestamp)
            self._started = True
            # Append compatibility styling on every session so active trace
            # files created by an older version also gain newer entry styles.
            self._write(
                '<style>details.error-message > summary {'
                'border-left:.35rem solid #c62828 !important;}'
                '.connection {'
                'margin:.25rem 0;padding:.45rem .65rem;border:1px solid #8885;'
                'border-radius:.3rem;font-family:ui-monospace,monospace;}'
                '.connection.connected {border-left:.35rem solid var(--tx);}'
                '.connection.disconnected {border-left:.35rem solid #b66a00;}'
                '</style>\n'
            )
            self._write_session(timestamp, reason)
            self._write_marker("start", "Trace started", reason, timestamp)

    def stop(self, reason: str = "Tracing disabled") -> None:
        """Write a stop marker and close the active trace stream."""

        with self._lock:
            if not self._started:
                return

            self._write_marker("stop", "Trace stopped", reason, datetime.now(timezone.utc))
            self._started = False
            self._close()

    def stop_archive_manager(self) -> None:
        """Signal the optional archive manager to stop without waiting for it."""

        self._archive_manager_stop.set()

    def log_message(
        self,
        direction: str,
        msg: Message,
        interface: str,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Append a collapsed message-flow summary and expandable message detail."""

        with self._lock:
            if not self._started:
                return

            timestamp = self._as_utc(timestamp)
            self._rotate_if_needed(timestamp)

            direction = direction.upper()
            direction_class = "rx" if direction == "RX" else "tx"
            movement = "Received from" if direction == "RX" else "Sent to"
            api_call = self._get_api_call(msg)
            if api_call:
                msg_type = str(api_call.get("msg_type", "unknown")).upper()
                action_code = str(api_call.get("action_code", "unknown")).upper()
            else:
                msg_type, action_code = self._get_non_api_message_kind(msg, direction)

            descriptors = []
            for key in ("property", "method", "status"):
                value = api_call.get(key)
                if value is not None:
                    descriptors.append(f"{key}={value}")

            entity = self._call_getter(msg, "get_entity")
            if entity is not None:
                descriptors.append(f"entity={entity}")

            msg_length = getattr(msg, "msg_length", 0)
            if msg_length:
                descriptors.append(f"bytes={msg_length}")

            metadata = " · ".join(descriptors)
            metadata_html = f' <span class="meta">— {html.escape(metadata)}</span>' if metadata else ""
            summary = (
                f'<time>{html.escape(self._format_timestamp(timestamp))}</time> '
                f'<span class="direction">{direction}</span> '
                f'<span class="kind">[{html.escape(msg_type)} {html.escape(action_code)}]</span> '
                f'{movement} {html.escape(str(interface))}{metadata_html}'
            )
            detail = html.escape(str(msg))

            self._write(
                f'<details class="message {direction_class}"><summary>{summary}</summary>'
                f'<pre>{detail}</pre></details>\n',
                allow_rollover=True,
            )
            self._message_count += 1

    def log_connection(
        self,
        connected: bool,
        local_sap,
        remote_addr,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Append a connection or disconnection event to the trace."""

        with self._lock:
            if not self._started:
                return

            timestamp = self._as_utc(timestamp)
            self._rotate_if_needed(timestamp)

            local_name = getattr(local_sap, "description", None) or str(local_sap)
            if connected:
                css_class = "connected"
                abbreviation = "CX"
                summary = f"Connected service access point {local_name} to {remote_addr}"
            else:
                css_class = "disconnected"
                abbreviation = "DCX"
                summary = f"Disconnected service access point {local_name} from {remote_addr}"

            self._write(
                f'<div class="connection {css_class}">'
                f'<time>{html.escape(self._format_timestamp(timestamp))}</time> '
                f'<span class="direction">{abbreviation}</span> '
                f'{html.escape(summary)}</div>\n',
                allow_rollover=True,
            )
            self._message_count += 1

    def log_exception(
        self,
        exception: BaseException,
        description: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Append an expandable exception entry containing its full traceback."""

        if not isinstance(exception, BaseException):
            raise TypeError("exception must be a BaseException instance")

        with self._lock:
            if not self._started:
                return

            timestamp = self._as_utc(timestamp)
            self._rotate_if_needed(timestamp)

            exception_type = type(exception).__name__
            exception_message = str(exception)
            summary_description = description or exception_message or "No exception message"
            formatted_traceback = "".join(
                traceback.format_exception(
                    type(exception),
                    exception,
                    exception.__traceback__,
                )
            )

            summary = (
                f'<time>{html.escape(self._format_timestamp(timestamp))}</time> '
                f'<span class="direction">ERR</span> '
                f'<span class="kind">[EXCEPTION {html.escape(exception_type)}]</span> '
                f'{html.escape(summary_description)}'
            )
            if description and exception_message:
                summary += f' <span class="meta">— {html.escape(exception_message)}</span>'

            self._write(
                f'<details class="message exception"><summary>{summary}</summary>'
                f'<pre>{html.escape(formatted_traceback)}</pre></details>\n',
                allow_rollover=True,
            )
            self._message_count += 1

    def log_last_error_msg(
        self,
        error_message: str,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Append an expandable application last-error message entry."""

        with self._lock:
            if not self._started:
                return

            timestamp = self._as_utc(timestamp)
            self._rotate_if_needed(timestamp)

            full_message = str(error_message)
            single_line_message = " ".join(full_message.split())
            preview = single_line_message[:75]
            if len(single_line_message) > 75:
                preview += "..."
            summary = (
                f'<time>{html.escape(self._format_timestamp(timestamp))}</time> '
                f'<span class="direction">ERR</span> '
                f'{html.escape(preview)}'
            )

            self._write(
                f'<details class="message error-message"><summary style="border-left:.35rem solid #c62828">{summary}</summary>'
                f'<pre>{html.escape(full_message)}</pre></details>\n',
                allow_rollover=True,
            )
            self._message_count += 1

    def _open(self, timestamp: datetime) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._rotate_existing_file_if_old(timestamp)
        is_new = not self.path.exists() or self.path.stat().st_size == 0
        self._stream = self.path.open("a", encoding="utf-8")
        self._file_date = timestamp.date()
        self._message_count = 0 if is_new else 1

        if is_new:
            title = html.escape(f"{self.app_name} trace")
            self._stream.write(_DOCUMENT_HEADER.format(title=title, heading=title))
            self._stream.flush()

    def _close(self) -> None:
        if self._stream is not None:
            self._stream.flush()
            self._stream.close()
            self._stream = None

    def _rotate_if_needed(self, timestamp: datetime) -> None:
        # Delayed messages from an earlier date remain in the current file;
        # only move the active trace forward to a newer UTC day.
        if self._file_date is None or timestamp.date() <= self._file_date:
            return

        self._rollover(timestamp, "Trace continued after daily rollover")

    def _rotate_existing_file_if_old(self, timestamp: datetime) -> None:
        if not self.path.exists():
            return
        modified_date = datetime.fromtimestamp(self.path.stat().st_mtime, timezone.utc).date()
        if modified_date != timestamp.date():
            self._rotate_active_file(modified_date)

    def _rotate_active_file(self, file_date) -> None:
        if not self.path.exists() or file_date is None:
            return

        # The active file remains open-ended so tracing can append after a
        # toggle or restart. Archived files are finalized as complete HTML.
        with self.path.open("a", encoding="utf-8") as active_file:
            active_file.write("</body>\n</html>\n")

        rotated = self.output_dir / f"{self._safe_app_name}-{file_date.isoformat()}.html"
        suffix = 1
        while self._archive_path_in_use(rotated):
            rotated = self.output_dir / f"{self._safe_app_name}-{file_date.isoformat()}-{suffix}.html"
            suffix += 1
        self.path.replace(rotated)
        self._remove_expired_archives()

    def _rollover(self, timestamp: datetime, reason: str) -> None:
        was_started = self._started
        self._close()
        self._started = False
        self._rotate_active_file(self._file_date)
        self._open(timestamp)
        self._started = was_started
        if was_started:
            self._write_session(timestamp, reason)

    def _remove_expired_archives(self) -> None:
        if self.backup_count < 0:
            return

        archives = []
        for archive in [
            *self.output_dir.glob(f"{self._safe_app_name}-*.html"),
            *self.output_dir.glob(f"{self._safe_app_name}-*.html.gz"),
        ]:
            try:
                archives.append((archive.stat().st_mtime, archive))
            except FileNotFoundError:
                # TM may have claimed the archive for compression.
                continue

        archives.sort(key=lambda item: item[0], reverse=True)
        for _, archive in archives[self.backup_count:]:
            archive.unlink(missing_ok=True)

    @staticmethod
    def _archive_path_in_use(archive: Path) -> bool:
        """Return whether an archive name or one of its managed forms exists."""

        compressed = archive.with_suffix(".html.gz")
        claimed = Path(f"{archive}.compressing")
        return archive.exists() or compressed.exists() or claimed.exists()

    def _start_archive_manager(self) -> None:
        if self._archive_scan_interval <= 0:
            raise ValueError("archive_scan_interval must be greater than zero")

        self._archive_manager_thread = threading.Thread(
            target=self._run_archive_manager,
            name=f"{self._safe_app_name}-TraceArchiveManager",
            daemon=True,
        )
        self._archive_manager_thread.start()

    def _run_archive_manager(self) -> None:
        """Compress existing archives immediately and then scan periodically."""

        while not self._archive_manager_stop.is_set():
            try:
                self._compress_rotated_traces()
            except Exception:
                logger.exception(
                    "Trace archive manager failed while scanning %s",
                    self.output_dir,
                )
            if self._archive_manager_stop.wait(self._archive_scan_interval):
                return

    def _compress_rotated_traces(self) -> None:
        """Compress every completed trace archive in the shared trace directory."""

        if not self.output_dir.exists():
            return

        self._recover_interrupted_compressions()
        archives = sorted(
            path
            for path in self.output_dir.iterdir()
            if path.is_file() and _ROTATED_TRACE_PATTERN.fullmatch(path.name)
        )
        for archive in archives:
            try:
                self._compress_archive(archive)
            except FileNotFoundError:
                # A trace owner may apply retention while the directory is scanned.
                continue
            except Exception:
                logger.exception("Failed to compress trace archive %s", archive)

    def _recover_interrupted_compressions(self) -> None:
        for temporary in self.output_dir.glob("*.html.gz.tmp"):
            temporary.unlink(missing_ok=True)

        for claimed in self.output_dir.glob("*.html.compressing"):
            archive = Path(str(claimed).removesuffix(".compressing"))
            if not _ROTATED_TRACE_PATTERN.fullmatch(archive.name):
                continue
            if not archive.exists():
                try:
                    claimed.replace(archive)
                except FileNotFoundError:
                    pass

    def _compress_archive(self, archive: Path) -> None:
        """Safely gzip one archive, retaining the source if compression fails."""

        claimed = Path(f"{archive}.compressing")
        archive.replace(claimed)
        compressed = self._next_compressed_path(archive)
        temporary = Path(f"{compressed}.tmp")
        source_mtime = claimed.stat().st_mtime

        try:
            with temporary.open("xb") as raw_output:
                with gzip.GzipFile(
                    filename=archive.name,
                    mode="wb",
                    compresslevel=1,
                    fileobj=raw_output,
                    mtime=source_mtime,
                ) as gzip_output:
                    with claimed.open("rb") as source:
                        shutil.copyfileobj(source, gzip_output, length=1024 * 1024)

            temporary.replace(compressed)
            os.utime(compressed, (source_mtime, source_mtime))
            claimed.unlink()
            logger.info("Compressed trace archive %s to %s", archive, compressed)
        except Exception:
            temporary.unlink(missing_ok=True)
            if claimed.exists() and not archive.exists():
                claimed.replace(archive)
            raise

    @staticmethod
    def _next_compressed_path(archive: Path) -> Path:
        compressed = archive.with_suffix(".html.gz")
        suffix = 1
        while compressed.exists() or Path(f"{compressed}.tmp").exists():
            compressed = archive.with_name(f"{archive.stem}-{suffix}.html.gz")
            suffix += 1
        return compressed

    def _write_session(self, timestamp: datetime, reason: str) -> None:
        self._write(
            '<section class="session"><strong>DMD2000 trace session</strong><dl>'
            f'<dt>Server</dt><dd>{html.escape(self.server_name)}</dd>'
            f'<dt>Application</dt><dd>{html.escape(self.app_name)}</dd>'
            f'<dt>Application version</dt><dd>{html.escape(self.app_version)}</dd>'
            f'<dt>Started</dt><dd>{html.escape(self._format_timestamp(timestamp))}</dd>'
            f'<dt>Reason</dt><dd>{html.escape(reason)}</dd>'
            '</dl></section>\n'
        )

    def _write_marker(self, css_class: str, label: str, reason: str, timestamp: datetime) -> None:
        self._write(
            f'<div class="marker {css_class}"><time>{html.escape(self._format_timestamp(timestamp))}</time> '
            f'<strong>{html.escape(label)}</strong> — {html.escape(reason)}</div>\n'
        )

    def _write(self, content: str, allow_rollover: bool = False) -> None:
        if self._stream is None:
            raise RuntimeError("Trace is not open")

        content_size = len(content.encode("utf-8"))
        if (
            allow_rollover
            and self._message_count > 0
            and self.max_bytes > 0
            and self.path.exists()
            and self.path.stat().st_size > 0
            and self.path.stat().st_size + content_size > self.max_bytes
        ):
            self._rollover(
                datetime.now(timezone.utc),
                "Trace continued after size rollover",
            )

        self._stream.write(content)
        self._stream.flush()

    @staticmethod
    def _as_utc(timestamp: Optional[datetime]) -> datetime:
        if timestamp is None:
            return datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)

    @staticmethod
    def _format_timestamp(timestamp: datetime) -> str:
        return timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"

    @staticmethod
    def _get_api_call(msg: Message) -> dict:
        api_call = HTMLTrace._call_getter(msg, "get_api_call")
        return api_call if isinstance(api_call, dict) else {}

    @staticmethod
    def _get_non_api_message_kind(msg: Message, direction: str) -> tuple[str, str]:
        """Build a useful summary kind for protocol messages such as MD01Msg."""
        class_name = type(msg).__name__
        msg_type = re.sub(r"(?:Message|Msg)$", "", class_name).upper() or "MESSAGE"

        command = HTMLTrace._call_getter(msg, "get_cmd")
        action_code = str(command).strip() if command is not None else ""
        if action_code.lower().startswith("cmd:"):
            action_code = action_code[4:].strip()
        if not action_code:
            action_code = "RESPONSE" if direction == "RX" else "MESSAGE"

        return msg_type, action_code.upper()

    @staticmethod
    def _call_getter(obj, method_name):
        method = getattr(obj, method_name, None)
        return method() if callable(method) else None
