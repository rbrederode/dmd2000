# Web API

The external web server (`web.web_server`, port 8000) validates the existing
`WEBHOOK_SECRET` token or HMAC signature before forwarding requests to TM's
local API (`tm.web_api`, port 5001). Keep the tunnel pointed at port 8000.

TM's Waitress worker count defaults to 4. Set a positive integer on TM's command
line to change it, for example:

```bash
PYTHONPATH=src python -m tm.tm --profile alston --entity_id tm001 --web_api_threads 10
```

This controls HTTP request workers independently of `--num_processors`, which
controls application event processors. The setting applies to Waitress; the
Flask development-server fallback does not use it. Restart TM to apply changes.

## Command responses

CMD webhooks wait for the target application's command response. For example:

```bash
curl --fail-with-body --max-time 25 \
  -H "X-Webhook-Token: $WEBHOOK_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"event":"alston-rt.ui.cmd","message":{"app":"TM","cmd":"Trace","value":"On"}}' \
  https://webhook.dmd2000.org/webhook
```

Use `"cmd":"Debug","value":"On"` for DEBUG, or `"cmd":"Resync"` for RESYNC.
Omit `value` for Trace/Debug to read the current setting. The target application
must be running with a configured command endpoint and support the command.

The response includes `status`, `message`, a diagnostic `request_id`, an optional
machine-readable error `code`, and an optional JSON `payload`. For target replies,
`payload.app` identifies the target and `payload.response` contains its protocol
reply (including its status, message and value when supplied). The request ID
does not provide polling or deduplication.
HTTP 200 means the target reported success; 400 means TM rejected the command;
502 means a target error or forwarding failure; 504 means a timeout.

TM validates the CMD event and starts its existing daemon command thread without
waiting on a TM event worker. That thread runs `cmd_app` and resolves the event's
in-process future when it finishes. Only the HTTP worker waits on the future.
Each request has its own future. Non-CMD webhooks retain their immediate
acknowledgement behavior.

Futures carry a transport-independent `ResponseEvent` from `env.events`, with
`status` (`success` or `error`), `message`, optional `payload`, and optional `code`.
TM supplies application error codes such as `invalid_request`, `command_failed`,
`dispatch_failed`, or `timeout`. Only `web_api` translates these to HTTP statuses
and serializes the response; another in-process caller can consume the same
result directly without knowing about HTTP.

The API waits at most 15 seconds, including time queued in TM. `cmd_app` allows
5 seconds to connect and another 5 seconds for a reply. The external web server
allows 20 seconds for a CMD request to TM; clients should allow more than that.
These are processing waits, not a guarantee of total latency under HTTP worker
saturation. If the API times out before TM starts dispatch, it cancels the queued
command and reports `payload.cancelled_before_dispatch: true`. After dispatch begins,
a timeout reports an unknown outcome and the command may still complete; do not
automatically retry a command with side effects. A target response timeout has
the same uncertainty. Restart both TM and the external web server to use this
response path.

## List recorded observations

```bash
curl --fail-with-body \
  -H "X-Webhook-Token: $WEBHOOK_SECRET" \
  https://webhook.dmd2000.org/api/observations
```

For a local request, use `http://127.0.0.1:8000/api/observations` instead.
The environment variable must be set in the terminal issuing the request.

To select a search root explicitly, supply the optional `directory` query
parameter. Use URL encoding for paths containing spaces or special characters:

```bash
curl --fail-with-body --get \
  -H "X-Webhook-Token: $WEBHOOK_SECRET" \
  --data-urlencode 'directory=/data/observations' \
  https://webhook.dmd2000.org/api/observations
```

An explicit directory takes precedence and works even when SDP has not reported
a `scan_store_dir`. If `directory` is omitted, the endpoint reads that setting
from the latest SDP status model held by TM. Subsequent requests use updated SDP
settings automatically. An empty `directory` is rejected with 400; a missing or
unreadable explicit directory returns 503 without falling back to SDP.

Paths refer to the filesystem accessible to TM. Absolute paths are recommended;
relative paths are resolved from TM's working directory, and `~` expands to TM's
user home directory. TM must have filesystem access to the chosen root and its
subdirectories. Without an explicit directory, requests return 503 until SDP
reports its store location.

The response contains `count` and an `observations` array. Each observation has:

- `id`: the recorded `obs_id` (or filename stem if metadata is unavailable).
- `filename`: the observation metadata filename.
- `folder`: the containing folder relative to the search root, using `/`
  separators; `.` means the search root itself (for example, `2026/November`).
- `title`, `dish`, `state`, `started_at`: summary fields from readable metadata.
- `error`: present when metadata could not be read or parsed.

The root and all subdirectories are searched for `*-obs.json` files. Symbolic
links to files or directories are skipped. Spectra are not loaded or transferred,
and no files are changed. An unreadable subtree returns 503 rather than silently
omitting observations from the result.
The list includes observations in any recorded state; presence does not imply
that recording has completed. Ordering is by relative metadata path. Entries
with the same filename or observation ID in different folders are retained;
use `folder` and `filename` together to identify the metadata file.

An empty store returns HTTP 200 with an empty array. Other responses are:

| HTTP status | Meaning |
| --- | --- |
| 400 | An explicit search directory is empty |
| 401 | Missing or invalid authentication |
| 502 | TM cannot be reached or returned an unexpected response |
| 503 | The search directory/subtree is unavailable, or SDP's store is not yet known when no directory was supplied |
| 504 | The request to TM timed out |

Restart both TM and the web server after installing API changes. This endpoint
provides discovery only; file downloads and remote OPT processing are not yet
connected to it.
