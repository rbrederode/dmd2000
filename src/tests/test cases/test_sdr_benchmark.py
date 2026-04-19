import json
import math
import sys
import time
from pathlib import Path

import numpy as np


SRC_DIR = Path(__file__).resolve().parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from models.comms import CommunicationStatus
from sdr.sdr import SDR, DEFAULT_READ_SIZE
from util.xbase import XHardwareFailure

import logging

logger = logging.getLogger(__name__)


def _restore_cached_configuration(sdr: SDR):
    """Re-open the SDR if needed and restore cached operating parameters."""

    if sdr.rtlsdr is None:
        if not sdr.open():
            raise XHardwareFailure("SDR device could not be reopened for benchmarking.")

    if sdr.center_freq is not None:
        sdr.rtlsdr.center_freq = sdr.center_freq
    if sdr.sample_rate is not None:
        sdr.rtlsdr.sample_rate = sdr.sample_rate
    if sdr.bandwidth is not None:
        sdr.rtlsdr.bandwidth = sdr.bandwidth
    if sdr.gain is not None:
        sdr.rtlsdr.gain = sdr.gain
    if sdr.freq_correction is not None:
        sdr.rtlsdr.ppm = sdr.freq_correction

    sdr.connected = CommunicationStatus.ESTABLISHED


def _read_samples_blocking(sdr: SDR, num_samples: int, chunk_size: int = DEFAULT_READ_SIZE):
    """Read complex samples from the SDR in smaller chunks to compare with direct reads."""

    if sdr.rtlsdr is None:
        logger.warning("SDR device not connected.")
        raise XHardwareFailure("SDR device not connected.")

    if num_samples <= 0:
        return np.zeros(0, dtype=np.complex64)

    chunk_size = max(1, min(int(chunk_size), int(num_samples)))
    chunks = []
    remaining = int(num_samples)

    while remaining > 0:
        request_size = min(chunk_size, remaining)
        try:
            chunk = sdr.rtlsdr.read_samples(request_size)
        except Exception as err:
            sdr._handle_read_error(err, f"while reading {request_size} samples")

        chunk = np.asarray(chunk, dtype=np.complex64)
        if chunk.size == 0:
            raise XHardwareFailure("SDR returned zero samples during blocking read.")

        chunks.append(chunk)
        remaining -= chunk.size

    if len(chunks) == 1:
        return chunks[0]

    return np.concatenate(chunks)[:num_samples]


def _build_skipped_benchmark_result(method_name: str, samples_per_read: int, iterations: int,
    reason: str, chunk_size: int | None = None) -> dict:
    """Create a benchmark result entry for a method that could not be run."""

    result = {
        "method": method_name,
        "iterations": iterations,
        "samples_per_read": samples_per_read,
        "total_requested_samples": samples_per_read * iterations,
        "total_samples_read": 0,
        "elapsed_sec": 0.0,
        "avg_read_sec": 0.0,
        "effective_samples_per_sec": 0.0,
        "success": False,
        "error": reason,
    }
    if chunk_size is not None:
        result["chunk_size"] = chunk_size
    return result


def _benchmark_read_method(sdr: SDR, method_name: str, read_fn, samples_per_read: int, iterations: int) -> dict:
    """Benchmark one SDR read method over a fixed number of one-second reads."""

    result = {
        "method": method_name,
        "iterations": iterations,
        "samples_per_read": samples_per_read,
        "total_requested_samples": samples_per_read * iterations,
        "total_samples_read": 0,
        "elapsed_sec": 0.0,
        "avg_read_sec": 0.0,
        "effective_samples_per_sec": 0.0,
        "success": True,
        "error": None,
    }

    try:
        _restore_cached_configuration(sdr)
        warmup = np.asarray(read_fn(samples_per_read), dtype=np.complex64)
        logger.info(f"SDR benchmark warmup for {method_name}: {warmup.size} samples")

        started = time.perf_counter()
        total_samples = 0

        for _ in range(iterations):
            samples = np.asarray(read_fn(samples_per_read), dtype=np.complex64)
            total_samples += samples.size

        elapsed = time.perf_counter() - started

        result["total_samples_read"] = total_samples
        result["elapsed_sec"] = elapsed
        result["avg_read_sec"] = elapsed / iterations if iterations > 0 else 0.0
        result["effective_samples_per_sec"] = total_samples / elapsed if elapsed > 0 else 0.0

    except Exception as err:
        result["success"] = False
        result["error"] = str(err)
        if isinstance(err, XHardwareFailure):
            result["error"] = str(err)
        else:
            try:
                sdr._handle_read_error(err, f"during {method_name} benchmark")
            except XHardwareFailure as hw_err:
                result["error"] = str(hw_err)

    return result


def benchmark_read_overhead(sdr: SDR, sample_rate: float = 2.4e6, duration_secs: int = 60,
    chunk_size: int = DEFAULT_READ_SIZE) -> dict:
    """Compare direct SDR reads against chunked helper reads for the same 60 seconds of data."""

    samples_per_read = int(sample_rate)
    iterations = int(duration_secs)
    original_sample_rate = sdr.sample_rate

    if iterations <= 0:
        raise ValueError("duration_secs must be greater than zero")
    if samples_per_read <= 0:
        raise ValueError("sample_rate must be greater than zero")

    _restore_cached_configuration(sdr)
    sdr.rtlsdr.sample_rate = sample_rate
    sdr.sample_rate = int(math.ceil(sdr.rtlsdr.sample_rate))

    logger.info(
        f"SDR benchmark starting: {iterations} reads of {samples_per_read} samples "
        f"({duration_secs} seconds at {sample_rate/1e6:.2f} MHz)"
    )

    benchmark = {
        "sample_rate": sdr.sample_rate,
        "duration_secs": iterations,
        "samples_per_read": samples_per_read,
        "chunk_size": int(chunk_size),
        "restore_error": None,
        "direct_read_samples": _benchmark_read_method(
            sdr=sdr,
            method_name="direct_read_samples",
            read_fn=lambda n: sdr.rtlsdr.read_samples(n),
            samples_per_read=samples_per_read,
            iterations=iterations,
        ),
        "blocking_helper_read_samples": _benchmark_read_method(
            sdr=sdr,
            method_name="blocking_helper_read_samples",
            read_fn=lambda n: _read_samples_blocking(sdr, n, chunk_size=chunk_size),
            samples_per_read=samples_per_read,
            iterations=iterations,
        ),
    }

    direct_elapsed = benchmark["direct_read_samples"]["elapsed_sec"]
    helper_elapsed = benchmark["blocking_helper_read_samples"]["elapsed_sec"]
    if direct_elapsed > 0 and helper_elapsed > 0:
        benchmark["helper_minus_direct_sec"] = helper_elapsed - direct_elapsed
        benchmark["helper_over_direct_pct"] = ((helper_elapsed / direct_elapsed) - 1.0) * 100.0
    else:
        benchmark["helper_minus_direct_sec"] = None
        benchmark["helper_over_direct_pct"] = None

    if original_sample_rate is not None:
        try:
            _restore_cached_configuration(sdr)
            sdr.rtlsdr.sample_rate = original_sample_rate
            sdr.sample_rate = int(math.ceil(sdr.rtlsdr.sample_rate))
        except XHardwareFailure as err:
            benchmark["restore_error"] = str(err)

    benchmark["device_available_after_benchmark"] = (
        sdr.rtlsdr is not None and sdr.connected == CommunicationStatus.ESTABLISHED
    )

    return benchmark


def benchmark_chunk_sizes(sdr: SDR, sample_rate: float = 2.4e6, duration_secs: int = 60,
    chunk_sizes: list[int] | None = None) -> dict:
    """Benchmark multiple helper chunk sizes against one direct-read baseline."""

    if chunk_sizes is None:
        chunk_sizes = [64 * 1024, 128 * 1024, 256 * 1024, 512 * 1024, 1024 * 1024]

    cleaned_chunk_sizes = []
    for chunk_size in chunk_sizes:
        chunk_size = int(chunk_size)
        if chunk_size <= 0:
            raise ValueError("chunk sizes must be greater than zero")
        if chunk_size not in cleaned_chunk_sizes:
            cleaned_chunk_sizes.append(chunk_size)

    samples_per_read = int(sample_rate)
    iterations = int(duration_secs)
    original_sample_rate = sdr.sample_rate

    if iterations <= 0:
        raise ValueError("duration_secs must be greater than zero")
    if samples_per_read <= 0:
        raise ValueError("sample_rate must be greater than zero")

    _restore_cached_configuration(sdr)
    sdr.rtlsdr.sample_rate = sample_rate
    sdr.sample_rate = int(math.ceil(sdr.rtlsdr.sample_rate))

    logger.info(
        f"SDR chunk-size benchmark starting: {iterations} reads of {samples_per_read} samples "
        f"({duration_secs} seconds at {sample_rate/1e6:.2f} MHz) across {len(cleaned_chunk_sizes)} chunk sizes"
    )

    results = {
        "sample_rate": sdr.sample_rate,
        "duration_secs": iterations,
        "samples_per_read": samples_per_read,
        "chunk_sizes": cleaned_chunk_sizes,
        "restore_error": None,
        "direct_read_samples": _benchmark_read_method(
            sdr=sdr,
            method_name="direct_read_samples",
            read_fn=lambda n: sdr.rtlsdr.read_samples(n),
            samples_per_read=samples_per_read,
            iterations=iterations,
        ),
        "helper_results": [],
    }

    direct_elapsed = results["direct_read_samples"]["elapsed_sec"]

    for chunk_size in cleaned_chunk_sizes:
        if sdr.rtlsdr is None or sdr.connected != CommunicationStatus.ESTABLISHED:
            helper_result = _build_skipped_benchmark_result(
                method_name=f"blocking_helper_read_samples_{chunk_size}",
                samples_per_read=samples_per_read,
                iterations=iterations,
                reason="Skipped because SDR device was unavailable after an earlier benchmark failure.",
                chunk_size=chunk_size,
            )
            helper_result["helper_minus_direct_sec"] = None
            helper_result["helper_over_direct_pct"] = None
            results["helper_results"].append(helper_result)
            continue

        helper_result = _benchmark_read_method(
            sdr=sdr,
            method_name=f"blocking_helper_read_samples_{chunk_size}",
            read_fn=lambda n, chunk_size=chunk_size: _read_samples_blocking(sdr, n, chunk_size=chunk_size),
            samples_per_read=samples_per_read,
            iterations=iterations,
        )
        helper_result["chunk_size"] = chunk_size

        helper_elapsed = helper_result["elapsed_sec"]
        if direct_elapsed > 0 and helper_elapsed > 0:
            helper_result["helper_minus_direct_sec"] = helper_elapsed - direct_elapsed
            helper_result["helper_over_direct_pct"] = ((helper_elapsed / direct_elapsed) - 1.0) * 100.0
        else:
            helper_result["helper_minus_direct_sec"] = None
            helper_result["helper_over_direct_pct"] = None

        results["helper_results"].append(helper_result)

    results["helper_results"].sort(
        key=lambda item: (
            not item["success"],
            item["elapsed_sec"] if item["elapsed_sec"] > 0 else float("inf"),
        )
    )

    if original_sample_rate is not None:
        try:
            _restore_cached_configuration(sdr)
            sdr.rtlsdr.sample_rate = original_sample_rate
            sdr.sample_rate = int(math.ceil(sdr.rtlsdr.sample_rate))
        except XHardwareFailure as err:
            results["restore_error"] = str(err)

    results["device_available_after_run"] = (
        sdr.rtlsdr is not None and sdr.connected == CommunicationStatus.ESTABLISHED
    )

    return results


def _summarise_repeated_benchmark(repeated_results: dict) -> dict:
    """Build compact averages for repeated chunk-size benchmark runs."""

    runs = repeated_results.get("runs", [])
    if not runs:
        return {}

    direct_runs = [run["direct_read_samples"] for run in runs if run["direct_read_samples"]["success"]]
    summary = {
        "direct_read_samples": {
            "num_successful_runs": len(direct_runs),
            "avg_elapsed_sec": float(np.mean([run["elapsed_sec"] for run in direct_runs])) if direct_runs else None,
            "min_elapsed_sec": float(np.min([run["elapsed_sec"] for run in direct_runs])) if direct_runs else None,
            "max_elapsed_sec": float(np.max([run["elapsed_sec"] for run in direct_runs])) if direct_runs else None,
            "avg_effective_samples_per_sec": float(np.mean([run["effective_samples_per_sec"] for run in direct_runs])) if direct_runs else None,
        },
        "helper_results": [],
    }

    chunk_sizes = repeated_results.get("chunk_sizes", [])
    for chunk_size in chunk_sizes:
        chunk_runs = []
        for run in runs:
            for helper_result in run["helper_results"]:
                if helper_result.get("chunk_size") == chunk_size and helper_result.get("success"):
                    chunk_runs.append(helper_result)

        helper_summary = {
            "chunk_size": chunk_size,
            "num_successful_runs": len(chunk_runs),
            "avg_elapsed_sec": float(np.mean([run["elapsed_sec"] for run in chunk_runs])) if chunk_runs else None,
            "min_elapsed_sec": float(np.min([run["elapsed_sec"] for run in chunk_runs])) if chunk_runs else None,
            "max_elapsed_sec": float(np.max([run["elapsed_sec"] for run in chunk_runs])) if chunk_runs else None,
            "avg_effective_samples_per_sec": float(np.mean([run["effective_samples_per_sec"] for run in chunk_runs])) if chunk_runs else None,
            "avg_helper_minus_direct_sec": float(np.mean([run["helper_minus_direct_sec"] for run in chunk_runs])) if chunk_runs else None,
            "avg_helper_over_direct_pct": float(np.mean([run["helper_over_direct_pct"] for run in chunk_runs])) if chunk_runs else None,
        }
        summary["helper_results"].append(helper_summary)

    summary["helper_results"].sort(
        key=lambda item: (
            item["avg_elapsed_sec"] is None,
            item["avg_elapsed_sec"] if item["avg_elapsed_sec"] is not None else float("inf"),
        )
    )

    return summary


def benchmark_chunk_sizes_repeated(sdr: SDR, repeats: int = 3, sample_rate: float = 2.4e6,
    duration_secs: int = 60, chunk_sizes: list[int] | None = None) -> dict:
    """Repeat the chunk-size benchmark several times, including the direct baseline each run."""

    repeats = int(repeats)
    if repeats <= 0:
        raise ValueError("repeats must be greater than zero")

    if chunk_sizes is None:
        chunk_sizes = [64 * 1024, 512 * 1024, 32 * 1024]

    repeated_results = {
        "repeats": repeats,
        "sample_rate": int(sample_rate),
        "duration_secs": int(duration_secs),
        "chunk_sizes": [int(chunk_size) for chunk_size in chunk_sizes],
        "runs": [],
    }

    logger.info(
        f"SDR repeated chunk-size benchmark starting: {repeats} runs, "
        f"{duration_secs} seconds each, sample rate {sample_rate/1e6:.2f} MHz"
    )

    for run_idx in range(1, repeats + 1):
        logger.info(f"SDR repeated chunk-size benchmark run {run_idx}/{repeats}")
        try:
            run_result = benchmark_chunk_sizes(
                sdr=sdr,
                sample_rate=sample_rate,
                duration_secs=duration_secs,
                chunk_sizes=chunk_sizes,
            )
        except XHardwareFailure as err:
            run_result = {
                "run_index": run_idx,
                "sample_rate": int(sample_rate),
                "duration_secs": int(duration_secs),
                "samples_per_read": int(sample_rate),
                "chunk_sizes": [int(chunk_size) for chunk_size in chunk_sizes],
                "direct_read_samples": _build_skipped_benchmark_result(
                    method_name="direct_read_samples",
                    samples_per_read=int(sample_rate),
                    iterations=int(duration_secs),
                    reason=str(err),
                ),
                "helper_results": [],
                "restore_error": str(err),
                "device_available_after_run": False,
            }
        run_result["run_index"] = run_idx
        repeated_results["runs"].append(run_result)

        if not run_result.get("device_available_after_run", True):
            logger.warning(
                f"SDR repeated chunk-size benchmark stopping after run {run_idx} because the SDR "
                "device is no longer available."
            )
            break

    repeated_results["summary"] = _summarise_repeated_benchmark(repeated_results)
    return repeated_results


def test_auto_gain_loop(sdr: SDR, repeats: int = 10, sample_rate: float = 2.4e6,
    time_in_secs: float = 0.1, p_threshold: float = 0.05) -> dict:
    """Run auto-gain repeatedly and capture per-run timing and failures."""

    repeats = int(repeats)
    if repeats <= 0:
        raise ValueError("repeats must be greater than zero")
    if time_in_secs <= 0:
        raise ValueError("time_in_secs must be greater than zero")

    results = {
        "repeats": repeats,
        "sample_rate": float(sample_rate),
        "time_in_secs": float(time_in_secs),
        "p_threshold": float(p_threshold),
        "runs": [],
        "summary": {},
    }

    for run_idx in range(1, repeats + 1):
        started = time.perf_counter()
        run_result = {
            "run_index": run_idx,
            "success": True,
            "gain": None,
            "elapsed_sec": 0.0,
            "error": None,
        }

        try:
            gain = sdr.get_auto_gain(
                sample_rate=sample_rate,
                time_in_secs=time_in_secs,
                p_threshold=p_threshold,
            )
            run_result["gain"] = gain
            logger.info(
                f"SDR auto-gain loop test run {run_idx}/{repeats} completed with gain {gain} dB"
            )
        except Exception as err:
            run_result["success"] = False
            run_result["error"] = str(err)
            logger.error(
                f"SDR auto-gain loop test run {run_idx}/{repeats} failed: {err}"
            )

        run_result["elapsed_sec"] = time.perf_counter() - started
        results["runs"].append(run_result)

        if sdr.rtlsdr is None or sdr.connected != CommunicationStatus.ESTABLISHED:
            logger.warning(
                f"SDR auto-gain loop test stopping after run {run_idx} because the SDR device "
                "is no longer available."
            )
            break

    successful_runs = [run for run in results["runs"] if run["success"]]
    results["summary"] = {
        "completed_runs": len(results["runs"]),
        "successful_runs": len(successful_runs),
        "failed_runs": len(results["runs"]) - len(successful_runs),
        "avg_elapsed_sec": float(np.mean([run["elapsed_sec"] for run in successful_runs])) if successful_runs else None,
        "gains": [run["gain"] for run in successful_runs],
        "device_available_after_test": sdr.rtlsdr is not None and sdr.connected == CommunicationStatus.ESTABLISHED,
    }

    return results


def main():
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sdr = SDR()

    try:
        if len(sys.argv) <= 1:
            raise ValueError(
                "Specify one of: benchmark_read_overhead, benchmark_chunk_sizes, "
                "benchmark_chunk_sizes_repeated, test_auto_gain_loop"
            )

        command = sys.argv[1]

        if command == "benchmark_read_overhead":
            duration_secs = int(sys.argv[2]) if len(sys.argv) > 2 else 60
            sample_rate = float(sys.argv[3]) if len(sys.argv) > 3 else 2.4e6
            benchmark = benchmark_read_overhead(sdr=sdr, sample_rate=sample_rate, duration_secs=duration_secs)
            logger.info("SDR benchmark results:\n%s", json.dumps(benchmark, indent=2))
            return

        if command == "benchmark_chunk_sizes":
            duration_secs = int(sys.argv[2]) if len(sys.argv) > 2 else 60
            sample_rate = float(sys.argv[3]) if len(sys.argv) > 3 else 2.4e6
            chunk_sizes = [int(arg) for arg in sys.argv[4:]] if len(sys.argv) > 4 else None
            benchmark = benchmark_chunk_sizes(
                sdr=sdr,
                sample_rate=sample_rate,
                duration_secs=duration_secs,
                chunk_sizes=chunk_sizes,
            )
            logger.info("SDR chunk-size benchmark results:\n%s", json.dumps(benchmark, indent=2))
            return

        if command == "benchmark_chunk_sizes_repeated":
            repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 3
            duration_secs = int(sys.argv[3]) if len(sys.argv) > 3 else 60
            sample_rate = float(sys.argv[4]) if len(sys.argv) > 4 else 2.4e6
            chunk_sizes = [int(arg) for arg in sys.argv[5:]] if len(sys.argv) > 5 else [65536, 524288, 32768]
            benchmark = benchmark_chunk_sizes_repeated(
                sdr=sdr,
                repeats=repeats,
                sample_rate=sample_rate,
                duration_secs=duration_secs,
                chunk_sizes=chunk_sizes,
            )
            logger.info("SDR repeated chunk-size benchmark results:\n%s", json.dumps(benchmark, indent=2))
            return

        if command == "test_auto_gain_loop":
            repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 10
            sample_rate = float(sys.argv[3]) if len(sys.argv) > 3 else 2.4e6
            time_in_secs = float(sys.argv[4]) if len(sys.argv) > 4 else 0.1
            results = test_auto_gain_loop(
                sdr=sdr,
                repeats=repeats,
                sample_rate=sample_rate,
                time_in_secs=time_in_secs,
            )
            logger.info("SDR auto-gain loop test results:\n%s", json.dumps(results, indent=2))
            return

        raise ValueError(f"Unknown command: {command}")

    finally:
        sdr.close()


if __name__ == "__main__":
    main()
