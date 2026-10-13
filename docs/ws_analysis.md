# Weather log analysis

From the repository root, run:

```bash
python src/ws/analysis.py logs/weather --threshold 15 --output weather_daily.csv
```

The tool uses only the Python standard library. It reads current logs, dated
plain logs and daily ZIP archives directly, without extracting them. Supply
one or more directories or file paths; directories are scanned without recursion.
You can also run it as `PYTHONPATH=src python -m ws.analysis ...`.

Each CSV row represents one station on one UTC day, sorted by date then station.
Rerunning replaces the CSV, so changing `--threshold` rebuilds the results from
the original data. A parse or archive error leaves an existing CSV intact and
reports the source of the failure. Input files must each be chronological.
Exact duplicate timestamp/station/wind records from overlapping inputs count
once. Memory use is bounded by one day's station summaries and merge buffers;
input streams remain open during the merge.

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
