"""Standalone tests: PYTHONPATH=src python -m unittest discover -s 'src/tests/test cases' -p test_ws_analysis.py."""

import csv
from datetime import datetime, timezone
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from ws.analysis import daily_summaries, discover_logs, main, parse_readings, read_logs


def line(timestamp, wind, station="ws001"):
    return f"{timestamp} UTC | {station} | OK | {wind} | None | None | None | None\n"


class WeatherAnalysisTests(unittest.TestCase):
    def test_cli_date_filters_are_inclusive_with_defaults_and_gust_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with zipfile.ZipFile(root / "ws.log.2026-10-07.zip", "w") as archive:
                # Select by record date even when the archive spans UTC days.
                archive.writestr("ws.log.2026-10-07", (
                    line("2026-10-07 23:59:59,999", 12)
                    + line("2026-10-08 00:00:00,000", 13)
                    + line("2026-10-08 23:59:59,999", 5)
                ))
            (root / "ws.log").write_text(
                line("2026-10-09 00:00:00,000", 14)
                + line("2026-10-09 23:59:59,999", 15, "ws002")
                + line("2026-10-10 00:00:00,000", 20)
            )
            cases = [
                ([], [("2026-10-07", "ws001"), ("2026-10-08", "ws001"),
                      ("2026-10-09", "ws001"), ("2026-10-09", "ws002")]),
                (["--start_date", "2026-10-08"],
                 [("2026-10-08", "ws001"), ("2026-10-09", "ws001"), ("2026-10-09", "ws002")]),
                (["--end_date", "2026-10-08"], [("2026-10-07", "ws001"), ("2026-10-08", "ws001")]),
                (["--start_date", "2026-10-08", "--end_date", "2026-10-09"],
                 [("2026-10-08", "ws001"), ("2026-10-09", "ws001"), ("2026-10-09", "ws002")]),
                (["--start-date", "2026-10-08", "--end-date", "2026-10-08"], [("2026-10-08", "ws001")]),
                (["--start_date", "2026-10-10", "--end_date", "2026-10-10"], [("2026-10-10", "ws001")]),
                (["--start_date", "2026-10-01", "--end_date", "2026-10-02"], []),
            ]
            for options, expected in cases:
                with self.subTest(options=options), mock.patch("ws.analysis.datetime", wraps=datetime) as clock:
                    clock.now.return_value = datetime(2026, 10, 9, 23, 30, tzinfo=timezone.utc)
                    output = root / "daily.csv"
                    self.assertEqual(main([str(root), "--threshold", "10", "--output", str(output), *options]), 0)
                    clock.now.assert_called_once_with(timezone.utc)
                    with output.open(newline="") as stream:
                        reader = csv.DictReader(stream)
                        records = list(reader)
                        self.assertIn("gust_count", reader.fieldnames)
                    self.assertEqual([(row["date"], row["weather_station_id"]) for row in records], expected)
                    for row in records:
                        if row["date"] == "2026-10-08":
                            self.assertEqual(row["sample_count"], "2")
                            self.assertEqual(row["gust_count"], "0")  # Started before the selected range.
                            self.assertEqual(row["first_reading_datetime_utc"], "2026-10-08T00:00:00.000+00:00")
                            self.assertEqual(row["last_reading_datetime_utc"], "2026-10-08T23:59:59.999+00:00")

    def test_cli_rejects_invalid_dates_and_reversed_ranges_without_replacing_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "daily.csv"
            output.write_text("existing summary\n")
            cases = [
                ["--start_date", "2026-02-30"],
                ["--end_date", "not-a-date"],
                ["--start_date", "20261008"],
                ["--end_date", "2026-10-8"],
                ["--start_date", "2026-10-09", "--end_date", "2026-10-08"],
                ["--start_date", "2026-10-10"],  # After the default UTC end date.
            ]
            for options in cases:
                with self.subTest(options=options), mock.patch("ws.analysis.datetime", wraps=datetime) as clock:
                    clock.now.return_value = datetime(2026, 10, 9, tzinfo=timezone.utc)
                    with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as error:
                        main([str(root), "--threshold", "10", "--output", str(output), *options])
                    self.assertEqual(error.exception.code, 2)
                    self.assertEqual(output.read_text(), "existing summary\n")
                    self.assertEqual(list(root.glob(".weather-summary-*")), [])

    def test_macos_metadata_is_ignored_in_directories_and_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "._ws.log").write_bytes(b"\x00\x05\xb0metadata")
            (root / "._ws.log.2026-10-07.zip").write_bytes(b"not a zip")
            archive_path = root / "ws.log.2026-10-07.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("__MACOSX/._ws.log.2026-10-07", b"\xb0metadata")
                archive.writestr("._ws.log.2026-10-07", b"\xb0metadata")
                archive.writestr("ws.log.2026-10-07", line("2026-10-07 12:00:00,000", 7))
            self.assertEqual(discover_logs([root]), [archive_path.resolve()])
            reading, = list(read_logs(discover_logs([root])))
            self.assertEqual(reading.wind, 7)

    def test_backward_clock_jumps_and_nul_padding_are_handled(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "ws.log"
            log.write_bytes((
                line("2026-10-08 00:00:01,000", 8)
                + "\x00" * 1892 + line("2026-10-07 23:59:59,000", 7)
                + line("2026-10-08 00:00:02,000", 9)
            ).encode("utf-8"))
            with io.StringIO() as messages, mock.patch("sys.stderr", messages):
                readings = list(read_logs([log]))
                self.assertIn("removed 1892 leading NUL", messages.getvalue())
                self.assertIn("1 backward timestamp jump", messages.getvalue())
            self.assertEqual([r.wind for r in readings], [7, 8, 9])
            summaries = list(daily_summaries(readings, 5))
            self.assertEqual([s.sample_count for s in summaries], [1, 2])
            self.assertEqual([s.gust_count for s in summaries], [1, 0])
            self.assertEqual(summaries[1].first.second, 1)
            # NUL bytes embedded in a wind number must not silently become valid.
            log.write_bytes(line("2026-10-08 00:00:01,000", "1\x002").encode("utf-8"))
            with self.assertRaises(ValueError):
                list(read_logs([log]))

    def test_skipping_corrupt_readings_is_explicit_and_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "ws.log"
            log.write_bytes((line("2026-10-13 16:42:00,000", 7)
                             + "\x00" * 1969
                             + "42:00,311 UTC | ws002 | UNKNOWN | 0 | None | None | None | None\n"
                             + line("2026-10-13 16:42:01,000", 9)).encode("utf-8"))
            with io.StringIO() as messages, mock.patch("sys.stderr", messages):
                with self.assertRaises(ValueError):
                    list(read_logs([log]))
                output = root / "summary.csv"
                self.assertEqual(main([str(log), "--threshold", "5", "--skip-invalid-lines",
                                       "--output", str(output)]), 0)
                self.assertIn(f"{log.resolve()}:2: skipped invalid reading", messages.getvalue())
            with output.open(newline="") as stream:
                row, = list(csv.DictReader(stream))
            self.assertEqual(row["sample_count"], "2")
            self.assertEqual(row["avg_wind_m_s"], "8.000000")
            self.assertEqual(row["gust_count"], "1")

    def test_legacy_encoding_for_plain_and_zipped_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = line("2026-10-07 12:00:00,000", 7).replace(
                " | None | None | None | None", " | 180\N{DEGREE SIGN} | None | None | None"
            )
            raw = text.encode("latin-1")
            (root / "ws.log").write_bytes(raw)
            with zipfile.ZipFile(root / "ws.log.2026-10-07.zip", "w") as archive:
                archive.writestr("ws.log.2026-10-07", raw)
            for path in discover_logs([root]):
                with self.subTest(path=path):
                    with self.assertRaises(ValueError) as error:
                        list(read_logs([path]))
                    self.assertIn(str(path), str(error.exception))
                    self.assertIn(":1: cannot decode using utf-8", str(error.exception))
                    self.assertIn("--encoding", str(error.exception))
                    reading, = list(read_logs([path], "latin-1"))
                    self.assertEqual(reading.wind, 7)
            output = root / "daily.csv"
            self.assertEqual(main([str(root), "--threshold", "5", "--encoding", "latin-1",
                                   "--output", str(output)]), 0)
            with output.open(encoding="utf-8", newline="") as stream:
                row, = list(csv.DictReader(stream))
            self.assertEqual(row["sample_count"], "1")
            self.assertEqual(row["gust_count"], "1")
            saved = output.read_bytes()
            with io.StringIO() as messages, mock.patch("sys.stderr", messages):
                self.assertEqual(main([str(root), "--threshold", "5", "--output", str(output)]), 1)
                self.assertIn("cannot decode", messages.getvalue())
            self.assertEqual(output.read_bytes(), saved)

    def test_legacy_observation_timestamp_uses_recording_day(self):
        text = line("2026-10-07 23:59:59,000", 5).rstrip("\n") + " | 2026-10-08T00:00:00+00:00\n"
        summary, = daily_summaries(parse_readings(io.StringIO(text), "legacy"), 10)
        self.assertEqual(summary.day.isoformat(), "2026-10-07")
        self.assertEqual(summary.minimum, 5)

    def test_statistics_threshold_equality_missing_and_tied_maximum(self):
        winds = [None, 5, 11, 10, None, 12, 9, 12, 5]
        text = "".join(line(f"2026-10-07 12:00:{index:02d},000", wind)
                       for index, wind in enumerate(winds))
        summary, = daily_summaries(parse_readings(io.StringIO(text), "test"), 10)
        self.assertEqual(summary.sample_count, 9)
        self.assertEqual(summary.valid_count, 7)
        self.assertEqual(summary.minimum, 5)
        self.assertEqual(summary.maximum, 12)
        self.assertAlmostEqual(summary.mean, 64 / 7)
        self.assertEqual(summary.maximum_time.second, 5)  # Earliest tied maximum.
        self.assertEqual(summary.above_count, 3)
        self.assertEqual(summary.gust_count, 2)
        self.assertEqual(summary.first.second, 0)
        self.assertEqual(summary.last.second, 8)

    def test_midnight_and_station_gust_states(self):
        text = (
            line("2026-10-07 23:59:59,000", 12)
            + line("2026-10-08 00:00:00,000", 12)
            + line("2026-10-08 00:00:00,000", 12, "ws002")
            + line("2026-10-08 00:00:01,000", 8)
            + line("2026-10-08 00:00:02,000", 15)
        )
        summaries = list(daily_summaries(parse_readings(io.StringIO(text), "test"), 10))
        self.assertEqual([(s.day.isoformat(), s.station_id, s.sample_count, s.gust_count)
                          for s in summaries], [
            ("2026-10-07", "ws001", 1, 1),
            ("2026-10-08", "ws001", 3, 1),
            ("2026-10-08", "ws002", 1, 1),
        ])

    def test_missing_day_has_blank_wind_statistics(self):
        summary, = daily_summaries(parse_readings(io.StringIO(
            line("2026-10-07 12:00:00,000", None)), "test"), 10)
        self.assertEqual(summary.row(10)[3:7], ["", "", "", ""])
        self.assertEqual(summary.gust_count, 0)

    def test_cli_merges_archives_and_logs_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            early = line("2026-10-07 00:00:00,000", 5)
            later = line("2026-10-07 12:00:00,000", 15)
            (root / "ws.log").write_text(later)
            with zipfile.ZipFile(root / "ws.log.2026-10-07.zip", "w") as archive:
                archive.writestr("ws.log.2026-10-07", early + later)
            output = root / "daily.csv"
            self.assertEqual(main([str(root), "--threshold", "10", "--output", str(output)]), 0)
            with output.open(newline="") as stream:
                row, = list(csv.DictReader(stream))
            self.assertEqual(row["sample_count"], "2")
            self.assertEqual(row["avg_wind_m_s"], "10.000000")
            self.assertEqual(row["gust_count"], "1")
            self.assertEqual(row["first_reading_datetime_utc"], "2026-10-07T00:00:00.000+00:00")
            paths = discover_logs([root, root / "ws.log"])
            self.assertEqual(len(paths), 2)
            self.assertEqual(len(list(read_logs(paths))), 2)

    def test_bad_input_preserves_existing_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "ws.log"
            log.write_text("not a weather row\n")
            output = root / "daily.csv"
            output.write_text("existing summary\n")
            self.assertEqual(main([str(log), "--threshold", "10", "--output", str(output)]), 1)
            self.assertEqual(output.read_text(), "existing summary\n")
            self.assertEqual(list(root.glob(".weather-summary-*")), [])

    def test_invalid_winds_and_thresholds(self):
        for wind in ("nan", "inf", "-1"):
            with self.subTest(wind=wind), self.assertRaisesRegex(ValueError, "test:1"):
                list(parse_readings(io.StringIO(line("2026-10-07 12:00:00,000", wind)), "test"))
        for threshold in (float("nan"), float("inf"), -1):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                list(daily_summaries([], threshold))


if __name__ == "__main__":
    unittest.main()
