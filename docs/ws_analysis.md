# Weather log analysis

From the repository root, run:

```bash
python src/ws/analysis.py logs/weather --threshold 15 --output weather_daily.csv
```

The tool uses only the Python standard library. It reads current logs, dated
plain logs and daily ZIP archives directly, without extracting them. Supply
one or more directories or file paths; directories are scanned without recursion.
You can also run it as `PYTHONPATH=src python -m ws.analysis ...`.

To restrict the CSV to a date range, pass `--start_date` and/or `--end_date`:

```bash
python src/ws/analysis.py logs/weather --threshold 15 \
  --start_date 2026-10-10 --end_date 2026-10-20 --output weather_daily.csv
```

Dates must use `YYYY-MM-DD`. Both boundaries include the entire specified UTC
day. Omitting `--start_date` includes the earliest available readings; omitting
`--end_date` includes readings through today's UTC date. Either argument can be
used alone. The aliases `--start-date` and `--end-date` are also accepted. A start
date after the effective end date is rejected. A range with no readings produces
a CSV containing only the column headers.

Filtering uses the date of each recorded timestamp. Input files are still read
and validated, including earlier readings that establish whether a gust was
already active at the start boundary. Such a gust remains attributed to its
original starting day, so it is not counted again inside the selected range.

Input defaults to UTF-8. For older single-byte logs, use `--encoding latin-1`
(or the known source encoding, such as `cp1252`). This applies to both plain
logs and ZIP contents; CSV output remains UTF-8. For example, from `src`:

```bash
python ws/analysis.py ../logs/weather --threshold 5 --encoding latin-1 --output daily_weather.csv
```

Decoding errors report the file, ZIP member where applicable, and line number.
The tool does not discard or replace undecodable bytes. Choosing an encoding
does not change which log row formats the parser supports.

Each CSV row represents one station on one selected UTC day, sorted by date then station.
Rerunning replaces the CSV, so changing `--threshold` rebuilds the results from
the original data. A parse or archive error leaves an existing CSV intact and
reports the source of the failure. Readings are sorted by recorded UTC time,
including logs with backward clock jumps; detected jumps produce warnings.
Exact duplicate timestamp/station/wind records from overlapping inputs count
once. Sorting uses a temporary SQLite database on disk to keep memory bounded,
and opens one input stream at a time. Temporary disk space must accommodate
the imported readings and sorting; the database is removed when processing ends.

Directory scans ignore hidden files, including macOS `._` metadata sidecars.
ZIP readers also ignore hidden members and `__MACOSX` metadata. Leading NUL
padding found in a log is removed with a source/line warning before parsing the
remaining reading. Padding does not count as a sample and cannot restore lost
data. Other malformed readings still fail analysis rather than being skipped.
If you accept omitting malformed readings, add `--skip-invalid-lines`. Each
skipped row is reported with its source and line number; it does not contribute
to sample counts or statistics, and it leaves gust state unchanged. Decoding
errors still stop processing. For example, from `src`:

```bash
python ws/analysis.py ../logs/weather --threshold 5 --skip-invalid-lines --output daily_weather.csv
```

The columns are:

| Column | Meaning |
| --- | --- |
| `date` | UTC date, `YYYY-MM-DD` |
| `weather_station_id` | Station identifier, e.g. `ws001` |
| `sample_count` | All recorded polls, including polls with missing wind |
| `min_wind_m_s` | Minimum valid wind speed |
| `max_wind_m_s` | Maximum valid wind speed |
| `avg_wind_m_s` | Arithmetic mean of valid wind readings |
| `time_of_max_wind_utc` | UTC time of the earliest maximum, `HH:MM:SS.sss` |
| `readings_above_threshold` | Count of valid readings strictly above the threshold |
| `threshold_m_s` | Threshold supplied on the command line |
| `gust_count` | Number of gust starts on this day |
| `first_reading_datetime_utc` | First poll, ISO datetime with `+00:00` |
| `last_reading_datetime_utc` | Last poll, ISO datetime with `+00:00` |

A gust begins when wind first exceeds the threshold and ends when it drops
below it. A reading equal to the threshold leaves the current state unchanged.
Missing readings and gaps also leave the state unchanged: they do not establish
that the wind dropped below the threshold. A gust spanning midnight is counted
on its starting day. An above-threshold first reading counts as one gust because
earlier conditions are unknown. Gust state is tracked separately for each station.

At exactly one poll per second, a complete UTC day has 86,400 recorded samples.
The actual total depends on poll interval, scheduling and downtime. Missing wind
values (`None`) do not contribute to wind statistics; an entirely missing day
has blank min/max/average/time-of-maximum fields. A day with no recorded polls
has no row. Timestamps are the logger's recording times. Analysing the active log
includes its currently available readings, so today's row is usually partial.
Older rows containing an additional sensor observation timestamp are supported;
that extra timestamp is ignored in favour of the logger's UTC recording time.

This tool runs on demand; it does not change recording or log rotation.
