"""Schedule GQRX IQ recordings through the remote-control TCP interface."""

from __future__ import annotations

import argparse
import logging
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_GQRX_HOST = "127.0.0.1"
DEFAULT_GQRX_PORT = 7356
DEFAULT_TEST_DURATION_SECS = 5
GAIN_NAMES = ("LNA", "MIX", "IF")


class GQRXRemoteError(RuntimeError):
    """Raised when the GQRX remote interface rejects or cannot service a command."""

@dataclass
class GQRXStatus:
    version: str
    center_freq: int
    dsp_enabled: bool
    iq_recording: bool
    gains: dict[str, float]


class GQRXRemoteClient:
    """Small request/response client for GQRX remote-control commands."""

    def __init__(self, host: str = DEFAULT_GQRX_HOST, port: int = DEFAULT_GQRX_PORT, timeout: float = 5.0):
        """Initialize a GQRXRemoteClient instance.
            host: The GQRX remote-control host (default: 127.0.0.1)
            port: The GQRX remote-control port (default: 7356)
            timeout: The timeout for network operations (default: 5.0 seconds)
        """
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)

    def command(self, command: str) -> str:
        """Send a command to GQRX and return the response.
            command: The command string to send (without trailing newline).
            Returns the response string (without trailing newline).
            Raises GQRXRemoteError if the command fails or GQRX returns an error.
        """
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall((command + "\n").encode("ascii"))
                chunks: list[bytes] = []
                while True:
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if b"\n" in chunk:
                        break
        except OSError as exc:
            raise GQRXRemoteError(f"Could not connect to GQRX at {self.host}:{self.port}: {exc}") from exc

        response = b"".join(chunks).decode("utf-8", errors="replace").strip()
        if not response:
            raise GQRXRemoteError(f"GQRX returned no response for command: {command}")
        return response

    def query(self, command: str) -> str:
        """Send a query command to GQRX and return the response.
            command: The query command string to send (without trailing newline).
            Returns the response string (without trailing newline).
            Raises GQRXRemoteError if the command fails or GQRX returns an error.
        """
        response = self.command(command)
        if response == "RPRT 1":
            raise GQRXRemoteError(f"GQRX rejected query: {command}")
        return response

    def set_value(self, command: str) -> None:
        """Send a command to GQRX and verify the response.
            command: The command string to send (without trailing newline).
            Raises GQRXRemoteError if the command fails or GQRX returns an error.
        """
        response = self.command(command)
        if response != "RPRT 0":
            raise GQRXRemoteError(f"GQRX rejected command {command!r}: {response}")

    def get_frequency(self) -> int:
        return int(float(self.query("f")))

    def set_frequency(self, frequency_hz: float) -> None:
        self.set_value(f"F {int(float(frequency_hz))}")

    def get_version(self) -> str:
        return self.query("_")

    def get_dsp_enabled(self) -> bool:
        return _parse_bool_response(self.query("u DSP"), "DSP")

    def set_dsp_enabled(self, enabled: bool) -> None:
        self.set_value(f"U DSP {int(enabled)}")

    def get_iq_recording(self) -> bool:
        return _parse_bool_response(self.query("u IQRECORD"), "IQRECORD")

    def set_iq_recording(self, enabled: bool) -> None:
        self.set_value(f"U IQRECORD {int(enabled)}")

    def get_gain(self, name: str) -> float:
        return float(self.query(f"l {name}_GAIN"))

    def set_gain(self, name: str, value: float) -> None:
        self.set_value(f"L {name}_GAIN {float(value):g}")

    def get_status(self) -> GQRXStatus:
        """Get the current GQRX status as a GQRXStatus dataclass."""
        gains = {name: self.get_gain(name) for name in GAIN_NAMES}
        return GQRXStatus(
            version=self.get_version(),
            center_freq=self.get_frequency(),
            dsp_enabled=self.get_dsp_enabled(),
            iq_recording=self.get_iq_recording(),
            gains=gains,
        )


def _parse_bool_response(response: str, field_name: str) -> bool:
    """Parse a GQRX response that should be a boolean (0 or 1).
        response: The response string from GQRX.
        field_name: The name of the field being parsed (for error messages).
        Returns True if the response is "1", False if "0".
        Raises GQRXRemoteError if the response is not "0" or "1".
    """
    try:
        value = int(response)
    except ValueError as exc:
        raise GQRXRemoteError(f"Unexpected {field_name} response from GQRX: {response!r}") from exc
    if value not in (0, 1):
        raise GQRXRemoteError(f"Unexpected {field_name} state from GQRX: {response!r}")
    return bool(value)

def _format_status(status: GQRXStatus) -> str:
    """Format a GQRXStatus dataclass into a human-readable string."""
    gains = ", ".join(f"{name}={value:g} dB" for name, value in status.gains.items())
    return (
        f"GQRX version={status.version}, center_freq={status.center_freq} Hz, "
        f"DSP={int(status.dsp_enabled)}, IQRECORD={int(status.iq_recording)}, gains=({gains})"
    )

def _parse_start_time(value: str) -> datetime | None:
    """Parse a start time string.

    Accepts "now" or a time in hh:mm:ss format. "now" is resolved later,
    after GQRX configuration has been applied.
    """
    if value.strip().lower() == "now":
        return None

    try:
        parsed_time = datetime.strptime(value, "%H:%M:%S").time()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("start time must be 'now' or in hh:mm:ss format") from exc

    now = datetime.now()
    start = datetime.combine(now.date(), parsed_time)
    if start <= now:
        start += timedelta(days=1)
    return start

def _raw_files(directory: Path) -> set[Path]:
    """Return a set of Path objects for all .raw files in the specified directory."""
    return {path.resolve() for path in directory.glob("*.raw") if path.is_file()}

def _wait_for_new_raw_files(directory: Path, before: set[Path], timeout_secs: float = 10.0) -> list[Path]:
    """Wait for new .raw files to appear in the specified directory.
        directory: The directory to monitor for new .raw files.
        before: A set of Path objects representing the .raw files that existed before the wait.
        timeout_secs: The maximum time to wait for new files (default: 10 seconds).
        Returns a list of new Path objects for .raw files that appeared after the wait, sorted by modification time.
        If no new files appear within the timeout, returns an empty list. """
    deadline = time.monotonic() + timeout_secs
    while time.monotonic() <= deadline:
        new_files = sorted(_raw_files(directory) - before, key=lambda path: path.stat().st_mtime)
        if new_files:
            return new_files
        time.sleep(0.25)
    return []

def _delete_files(paths: list[Path]) -> None:
    """Delete the specified files, logging any errors.
        paths: A list of Path objects representing the files to delete.
    """
    for path in paths:
        try:
            path.unlink()
            logger.info("Deleted test IQ recording: %s", path)
        except OSError as exc:
            logger.warning("Could not delete test IQ recording %s: %s", path, exc)

def test_iq_recording(client: GQRXRemoteClient, recording_dir: Path, duration_secs: int = DEFAULT_TEST_DURATION_SECS) -> bool:
    """Test that GQRX can successfully start and stop an IQ recording.
        client: A GQRXRemoteClient instance.
        recording_dir: The directory where GQRX saves .raw files.
        duration_secs: The duration of the test recording in seconds (default: 5).
        Returns True if the test succeeded, False otherwise.
    """
    if client.get_iq_recording():
        logger.warning("GQRX is already IQ recording; refusing to run the startup test.")
        return False

    before = _raw_files(recording_dir)
    logger.info("Testing IQ recording for %s seconds...", duration_secs)
    try:
        client.set_iq_recording(True)
        time.sleep(duration_secs)
    finally:
        try:
            client.set_iq_recording(False)
        except GQRXRemoteError as exc:
            logger.warning("Failed to stop IQ recording after startup test: %s", exc)
            return False

    new_files = _wait_for_new_raw_files(recording_dir, before)
    if not new_files:
        logger.warning("Startup IQ recording test failed: no new .raw file appeared in %s", recording_dir)
        return False

    logger.info("Startup IQ recording test succeeded: %s", ", ".join(str(path) for path in new_files))
    _delete_files(new_files)
    return True

def wait_until(start_time: datetime) -> None:
    """Wait until the specified start time.
        start_time: A datetime object representing the local time to wait until.
    """
    while True:
        remaining = (start_time - datetime.now()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 1.0))

def run_scheduler(args: argparse.Namespace) -> int:
    """Run the GQRX IQ recording scheduler with the specified arguments.
        args: An argparse.Namespace object containing the parsed command-line arguments.
        Returns 0 on success, 1 on failure.
    """
    recording_dir = Path(args.directory).expanduser().resolve()
    if not recording_dir.exists() or not recording_dir.is_dir():
        logger.warning("Recording directory does not exist or is not a directory: %s", recording_dir)
        return 1

    client = GQRXRemoteClient(port=args.port)
    # Check that we can connect to GQRX and get its status before proceeding.
    try:
        status = client.get_status()
    except GQRXRemoteError as exc:
        logger.warning("Could not establish usable GQRX remote-control connection: %s", exc)
        return 1

    logger.info("Connected to GQRX at %s:%s", DEFAULT_GQRX_HOST, args.port)
    logger.info("%s", _format_status(status))

    # Apply optional configuration settings (center frequency and gain) if specified on the command line.
    try:
        if args.center_freq is not None:
            logger.info("Setting center frequency to %d Hz", int(args.center_freq))
            client.set_frequency(args.center_freq)

        if args.gain is not None:
            logger.info("Setting all GQRX gains to %g dB", args.gain)
            for name in GAIN_NAMES:
                client.set_gain(name, args.gain)
    except GQRXRemoteError as exc:
        logger.warning("Could not apply requested GQRX settings: %s", exc)
        return 1

    # Verify that DSP is enabled before starting the IQ recording tests.
    if not status.dsp_enabled:
        logger.info("DSP is stopped; starting DSP before recording tests.")
        try:
            client.set_dsp_enabled(True)
        except GQRXRemoteError as exc:
            logger.warning("Could not start GQRX DSP: %s", exc)
            return 1

    # Verify that the requested settings were applied successfully.
    try:
        status = client.get_status()
        logger.info("Post-configuration %s", _format_status(status))
    except GQRXRemoteError as exc:
        logger.warning("Could not verify GQRX settings after configuration: %s", exc)
        return 1

    if args.start_time is None:
        start_time = datetime.now()
        logger.info("Start time is 'now'; skipping startup IQ recording test.")
        logger.info("Starting as soon as configuration has been applied: %s", start_time.strftime("%Y-%m-%d %H:%M:%S"))
    else:
        start_time = args.start_time
        # Run a test IQ recording to ensure that GQRX is functioning correctly before scheduling the actual recording.
        logger.info("Running startup IQ recording test for %s seconds.", DEFAULT_TEST_DURATION_SECS)
        try:
            if not test_iq_recording(client, recording_dir):
                return 1
        except GQRXRemoteError as exc:
            logger.warning("Startup IQ recording test failed: %s", exc)
            return 1

        logger.info("Waiting until %s local time to start IQ recording.", start_time.strftime("%Y-%m-%d %H:%M:%S"))
        wait_until(start_time)

    logger.info("Starting scheduled IQ recording for %s seconds.", args.duration)
    try:
        client.set_iq_recording(True)
        time.sleep(args.duration)
    except GQRXRemoteError as exc:
        logger.warning("Scheduled IQ recording failed: %s", exc)
        return 1
    finally:
        logger.info("Stopping scheduled IQ recording.")
        try:
            client.set_iq_recording(False)
        except GQRXRemoteError as exc:
            logger.warning("Could not stop scheduled IQ recording cleanly: %s", exc)
            return 1

    logger.info("Scheduled IQ recording complete.")
    return 0

def build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line argument parser for the GQRX scheduler."""
    parser = argparse.ArgumentParser(description="Schedule a GQRX IQ recording through its remote-control interface.")
    parser.add_argument("-t", "--time", dest="start_time", type=_parse_start_time, required=True, help="Local start time in hh:mm:ss format, or 'now' to start immediately after configuration.")
    parser.add_argument("-d", "--duration", type=int, required=True, help="IQ recording duration in seconds.")
    parser.add_argument("-cf", "--center-frequency", dest="center_freq", type=float, default=None, help="Optional center frequency in Hz.")
    parser.add_argument("-g", "--gain", type=float, default=None, help="Optional gain in dB (1-15) to set for LNA, MIX, and IF.")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_GQRX_PORT, help=f"GQRX remote-control port. Default: {DEFAULT_GQRX_PORT}.")
    parser.add_argument("-dir", "--directory", required=True, help="Directory configured in GQRX where raw IQ recordings appear.")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"), help="Logging verbosity.")
    return parser

def main(argv: list[str] | None = None) -> int:
    """Main entry point for the GQRX scheduler script.
        argv: Optional list of command-line arguments (default: None, which uses sys.argv).
        Returns 0 on success, 1 on failure.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.duration <= 0:
        parser.error("-d/--duration must be greater than zero")
    if args.port <= 0 or args.port > 65535:
        parser.error("-p/--port must be between 1 and 65535")
    if args.gain is not None and (args.gain < 1 or args.gain > 15):
        parser.error("-g/--gain must be between 1 and 15 dB")

    return run_scheduler(args)

if __name__ == "__main__":
    raise SystemExit(main())
