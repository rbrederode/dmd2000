# Web API

The external web server (`web.web_server`, port 8000) validates the existing
`WEBHOOK_SECRET` token or HMAC signature. CMD webhooks now use the target
application's existing command TCP API through `util.cmd_app`, independently of
TM's embedded HTTP API. DIG, DSH, ODT and OET configuration webhooks use the
`tm_web` TCP API on TM's `web` interface. Observation listing still uses TM's
local HTTP API (`tm.web_api`, port 5001) during this migration. Keep the tunnel
pointed at port 8000.

## Standalone web application and configuration requests

`web.web_server` now runs a `WebServer(App)` with its own `WebServerModel`, event
processors, timer manager, command endpoint, tracing and managed Waitress HTTP
server. Install the new HTTP-server dependency in the runtime environment:

```bash
python -m pip install waitress
```

Start the two applications, for example (with `WEBHOOK_SECRET` already exported
in the web-server environment):

```bash
# On the TM machine; use its reachable interface address when web runs elsewhere.
PYTHONPATH=src python -m tm.tm --profile alston --entity_id tm001 --web_host 127.0.0.1 --web_port 50004

# On the web-server machine; set --tm_host to the TM machine's reachable address.
PYTHONPATH=src python -m web.web_server --profile alston --entity_id web001 --tm_host 127.0.0.1 --tm_port 50004 --http_port 8000 --http_threads 10
```

The examples use loopback for a same-machine test. The Alston launch profile
sets both sides to TM's existing `192.168.2.1` address. `--profile_dir` (or
`WEB_PROFILE_DIR`) selects a shared/local profile directory explicitly; otherwise
the web application uses repository `src/config/<profile>`. Default and Alston
profiles now include `WebServerModel.json`. The model stores the app's identity,
command endpoint, request timeout and live TM connection state. Network listener
settings and the TM connection are configured at startup; restart to change them.
Web RESYNC reloads the profile's identity/request timeout and command-dispatch
configuration without replacing live sockets.

| Setting | Default |
| --- | --- |
| TM `--web_host`, `--web_port` | `127.0.0.1`, `50004` |
| Web `--tm_host`, `--tm_port` | `127.0.0.1`, `50004` (`WEB_TM_HOST` / `WEB_TM_PORT` overrides) |
| Web `--http_host`, `--http_port`, `--http_threads` | `127.0.0.1`, `8000`, `10` |
| Web command endpoint | `127.0.0.1:60010`, overridable with `--cmd_host` / `--cmd_port` |
| Configuration response timeout | Profile `app.msg_timeout_ms` (default 10000); `--request_timeout` / `WEB_REQUEST_TIMEOUT` overrides in seconds |

The web app supports TRACE, DEBUG and RESYNC on its own command port. Its HTTP
service and direct command path remain available when TM is disconnected; its
app health is degraded until TM connects. `/health` remains a liveness check.
Like WS and SDP, the web application sends an `adv / set / status` message
immediately after connecting and on each normal application status update
(every 30 seconds). The value is its current `WebServerModel`, including app
health, command endpoint, arguments and processor state. TM stores the snapshot
in `telmodel.web_svr` and updates its timestamp. Status advice has no request
timer, correlation ID or acknowledgement and is sent only while connected.
One web application process owns the TM TCP connection and request timers; use
HTTP worker threads rather than sharing that connection across forked workers.

### Configuration request flow

Existing Google Sheets webhook payloads are supported unchanged. The four event
names `alston-rt.ui.dig`, `.dsh`, `.odt` and `.oet` become a `set` request to TM.
For example:

```bash
curl --include \
  -H "X-Webhook-Token: $WEBHOOK_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"event":"alston-rt.ui.oet","message":{"_type":"ObservationReset","obs_id":"obs001"}}' \
  http://127.0.0.1:8000/webhook
```

The TCP header uses `from: "web"`, `to: "tm"`, API version `1.0`, and
`echo_data.request_id` for correlation. The `api_call` contains:

```json
{
  "msg_type": "req",
  "action_code": "set",
  "property": "config",
  "value": {
    "category": "OET",
    "config": {"_type": "ObservationReset", "obs_id": "obs001"},
    "old_config": null
  }
}
```

`tm_web` validates the message. GET is reserved for future read operations and
currently returns an explicit error response. CMD configuration requests are
excluded; commands continue to use `cmd_app` directly.

The web app checks TM connectivity both before accepting the submission and
before sending it. It starts one uniquely named `Action.Timer` alongside the
message action; the timer holds the originating HTTP completion context. There
is no separate pending-request map. A matching response resolves that context
and cancels the timer. Expiry reports a timeout, disconnect fails in-flight
requests, and late replies are ignored. Reconnecting never replays an update.
An HTTP watchdog also bounds waits if local processor scheduling stalls.

TM's `process_web_msg` queues a `ConfigEvent` with `rsp_future`. The existing
processor barrier serializes configuration handling with other events. The
future resolves after the handler runs and its returned actions are scheduled;
validation failures, logged configuration errors, exceptions and unscheduled
actions produce error results. No TM worker waits on the future. Its completion
queues a response on the original TCP connection.

A successful result looks like:

```json
{
  "status": "success",
  "message": "OET configuration processed; actions scheduled",
  "payload": {"category": "OET", "phase": "actions_scheduled"},
  "request_id": "..."
}
```

This acknowledges TM's handling and action scheduling, not downstream hardware
completion. Errors retain `status: "error"`, a message and a code such as
`not_connected`, `connection_lost`, `configuration_failed` or `timeout`.
Configuration outcomes use HTTP 200 so gateways preserve the JSON body; malformed
HTTP payloads and authentication failures retain HTTP error statuses. A timeout
or disconnect after sending has an unknown outcome: TM may still process an
already queued update. Callers must not automatically retry it.

## Submit observation files through REST

`obs.inject` now uses the authenticated web application instead of TM's embedded
HTTP endpoint. From `src`, with `WEBHOOK_SECRET` set to the same token as the web
application:

```bash
python -m obs.inject --file config/local/obs_rotation.json
```

The default URL is `http://127.0.0.1:8000/webhook`. To use the public endpoint:

```bash
python -m obs.inject --file config/local/obs_rotation.json --url https://webhook.dmd2000.org/webhook
```

An explicit `--token` overrides `WEBHOOK_SECRET`. The utility reads and normalizes
an `ObsModel`, `ObsList` or raw observation list locally, expands the existing UTC
`{{NOW}}` / `{{NOW+<n>m}}` tokens, then posts an ODT webhook. The web application
forwards it over `tm_web` TCP and returns TM's configuration-processing result.
The client timeout defaults to 20 seconds; use `--timeout` to allow more time if
the server's configured request timeout is increased.

Success prints the observation IDs and TM's response message. Application
errors (including HTTP 200 with `status: "error"`), authentication failures and
transport failures produce a nonzero exit status. Timeouts report an unknown
outcome and are not automatically retried. Success means actions were scheduled,
not that an observation has executed. Existing ODT semantics apply: the submitted
list is authoritative for EMPTY observations; omitted EMPTY observations can be
removed from TM's list.

## Embedded TM HTTP API during migration

TM's Waitress worker count defaults to 4. Set a positive integer on TM's command
line to change it, for example:

```bash
PYTHONPATH=src python -m tm.tm --profile alston --entity_id tm001 --web_api_threads 10
```

This controls HTTP request workers independently of `--num_processors`, which
controls application event processors. The setting applies to Waitress; the
Flask development-server fallback does not use it. Restart TM to apply changes.

TM initializes `WebAPIHandler` with its event queue and a direct reference to its
live `telmodel`. API handlers read current submodels on each request, including
SDP replacements received through status updates. Model changes are submitted as
events through the queue. The shared reference does not provide an atomic
snapshot across multiple model fields; endpoints needing that consistency must
coordinate their reads with TM's updates.

## Command responses

CMD webhooks wait for the target application's command response. The external
server dispatches them through `web.cmd_handler.CommandHandler` and
`util.cmd_app`, using the existing command API and framed TCP transport:

```text
HTTP caller -> web_server -> cmd_handler -> cmd_app -> target command port
HTTP caller <- ResponseEvent JSON <- target command response
```

TM is required only when TM itself is the command target. For example, an SDP
RESYNC works with TM and its embedded HTTP API stopped:

```bash
curl --fail-with-body --max-time 25 \
  -H "X-Webhook-Token: $WEBHOOK_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"event":"alston-rt.ui.cmd","message":{"app":"SDP","cmd":"Resync"}}' \
  https://webhook.dmd2000.org/webhook
```

Use `"cmd":"Trace","value":"On"` for TRACE or
`"cmd":"Debug","value":"Off"` for DEBUG. Omit `value` for Trace/Debug to read
the current setting. The webhook's `message` can be a JSON object or a string
containing that object, preserving the Google Sheets calling format. The target
must be running, reachable, and support the command.

### Server-side command configuration

Configure the external web server using environment variables:

| Variable | Purpose | Default |
| --- | --- | --- |
| `WEB_PROFILE_DIR` | Local or shared profile directory used to resolve command endpoints | Repository `src/config/default` |
| `WEB_COMMAND_ENDPOINTS` | JSON endpoint overrides keyed by application name or digitiser ID | `{}` |
| `WEB_CMD_CONFIG` | Command registry file used by `cmd_app` | Repository `src/config/CmdRegistry.json` |
| `WEB_COMMAND_TIMEOUT` | Seconds allowed for connection and, separately, response | `5` |

For a web server on another machine, for example:

```bash
export WEB_PROFILE_DIR=/mnt/art/config/alston
export WEB_COMMAND_ENDPOINTS='{"sdp":{"host":"sdp-host","port":60004},"dm":{"host":"dm-host","port":60003},"dig003":{"host":"digitiser-host","port":60006}}'
python -m web.web_server
```

Set `WEBHOOK_SECRET` as usual before starting the server. Each endpoint override
requires both `host` and `port` and takes precedence over the profile. Overrides
can be used without access to the profile directory. Hosts must be reachable
from the web-server machine and match the targets' command listener settings.
In particular, an application's loopback or wildcard bind address may need a
reachable-host override; runtime `--cmd_host`/`--cmd_port` overrides must also be
reflected here.

Without an override, fixed applications use their profile model's
`app.app_cmd_host` and `app.app_cmd_port`, with existing model defaults for absent
fields/files: TM 60002, DM 60003, SDP 60004, WS 60005, on localhost. Digitiser IDs
are looked up in the profile's `DigitiserList.json`, using the digitiser's app
endpoint (default port 60006). Profile files are read per command, so file changes
apply to subsequent calls. The profile directory must be accessible; invalid or
unavailable configuration returns an error. Explicit endpoint overrides and
other environment changes take effect after restarting the web server. Endpoint
addresses cannot be supplied by the HTTP caller.

### Result and waiting behavior

The response remains a transport-independent `ResponseEvent` JSON envelope:
`status` (`success` or `error`), `message`, optional machine-readable `code`, and
optional `payload`. `payload.app` identifies the target and `payload.response`
contains its protocol reply. The HTTP layer adds a diagnostic `request_id`,
which does not provide polling or deduplication.

HTTP 200 means the API returned a command outcome, including a target error or
timeout. Check `status` for success/error and `code` for reasons such as
`timeout` or `command_failed`; HTTP 200 alone does not mean the command succeeded.
Invalid commands return 400; server configuration/internal errors return 500.
Each call owns its own command client and
response queue. The HTTP worker waits inside `cmd_app`; no TM worker or Future
is needed on this direct path. With the default timeout, connection and response
waits allow five seconds each, plus client cleanup and scheduling. A timeout
after sending means the outcome is unknown: the target may still complete the
command. Do not automatically retry commands with side effects.

Direct dispatch behaves like running `cmd_app` yourself. Registered commands
are validated using `WEB_CMD_CONFIG` and run at the target application's command
endpoint. Commands do not perform TM's observation bookkeeping; for example,
a direct DM STOP does not request an immediate observation abort in TM.

### Existing embedded TM API

`tm.web_api` remains available for observation listing and configuration
webhooks (DIG, DSH, ODT and OET). CMD webhooks sent to the embedded API return
HTTP 400 with a `ResponseEvent` directing callers to `web_server` or `cmd_app`;
they are not queued. TM no longer handles CMD ConfigEvents or forwards commands
to other applications. Its own command TCP endpoint still accepts commands
addressed directly to TM through the shared application command API.

Direct legacy callers can still submit configuration webhooks to the embedded
TM HTTP endpoint. The external web application uses the new TCP interface and
waits for TM's configuration-processing response instead of this legacy HTTP
acknowledgement.

Restart TM and the external web server to enable the WEB TCP configuration
interface. No Google Sheets webhook payload change is needed.

### Diagnosing non-JSON gateway errors

A target connection timeout returns HTTP 200 with a JSON `ResponseEvent`
whose `status` is `error`, `code` is `timeout`, and message is `Timed out connecting
to target application`. This preserves the application result through gateways
that replace HTTP 502/504 response bodies. A proxy or tunnel failure can still
return an HTTP error with an HTML error page, plain text, or its own
JSON error format. That response does not establish whether the target command
ran. The Apps Script's `decodeWebhookResponse` helper preserves valid application
responses and describes non-application 502/503/504 responses as gateway errors
with an unknown command outcome.

To distinguish an application timeout from a tunnel error, compare the public
response with a request directly to the running web server, while TM is stopped:

```bash
curl --include --max-time 20 \
  -H "X-Webhook-Token: $WEBHOOK_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"event":"alston-rt.ui.cmd","message":{"app":"TM","cmd":"Resync"}}' \
  http://127.0.0.1:8000/webhook
```

The checked-in `src/web/config.yml` now targets port 8000, matching the external
web server. Check the configuration actually used by the running tunnel; a copy
elsewhere or a remotely managed route may differ. Updating this repository file
does not change an already running tunnel. If that file is active, restart the
tunnel after updating it. Copy the updated `decodeWebhookResponse` and
`sendWebhook` functions from `src/javascript/app_scripts.js` into Google Apps
Script to apply the improved gateway-error display.

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

The web application searches storage directly; TM and SDP do not need to be
running. The default comes from `config/<profile>/LaunchConfig.json`, using the
web application's `--profile` (or `--profile_dir`) setting:

```json
{
  "_type": "LaunchConfigModel",
  "profile": "local",
  "scan_store_dir": "~/samples/local",
  "launches": []
}
```

Both SDP and WEB use this top-level `scan_store_dir` by default. Each application
can override it with `--scan_store_dir`. For WEB, a request's `directory` parameter
takes precedence over both the command-line override and the profile value. WEB
reloads its profile default on RESYNC; restart SDP after changing its default.
The shared setting replaces `--scan_store_dir` in the profile's SDP launch entry.
Older profiles without the shared setting retain SDP's existing built-in default;
WEB returns `store_not_known` unless an explicit directory is provided.

An empty `directory` is rejected with 400; a missing or unreadable explicit
directory returns 503 without falling back to the default. Paths refer to the
filesystem accessible to WEB. Relative paths use WEB's working directory, and
`~` expands to WEB's user home directory. When WEB and SDP run on separate
machines, both need access to the configured storage path.

The response uses the same `ResponseEvent` JSON envelope as CMD calls. Callers
always read `status` (`success` or `error`) and `message`; an optional `code`
identifies an application error. The listing is in `payload.count` and
`payload.observations`, rather than at the top level:

```json
{
  "status": "success",
  "message": "Found 1 observations",
  "payload": {
    "count": 1,
    "observations": [
      {
        "id": "obs001",
        "filename": "obs001-obs.json",
        "folder": "2026/November",
        "title": "Example observation",
        "dish": "dish003",
        "state": "COMPLETE",
        "started_at": "2026-11-06T02:00:00+00:00"
      }
    ]
  }
}
```

Each observation has:

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

An empty store returns HTTP 200, `status: "success"`, and a payload containing
`count: 0` and `observations: []`. Errors have `status: "error"` and `message`,
with an optional `code` such as `invalid_request`, `store_not_known`, or
`directory_unavailable`. The web application returns the common response envelope
and also returns `status` and `message` for authentication errors. Clients should check the HTTP status
and application status before reading `payload`.

Other responses are:

| HTTP status | Meaning |
| --- | --- |
| 400 | An explicit search directory is empty |
| 401 | Missing or invalid authentication |
| 500 | Configuration could not be loaded or an unexpected listing error occurred |
| 503 | The search directory/subtree is unavailable, or no default directory is configured |

Restart the web application and SDP after installing these changes. This endpoint
provides discovery only; file downloads and remote OPT processing are not yet
connected to it.


## HTTP tracing

When WEB tracing is enabled, `logs/trace/web.html` includes expandable HTTP RX
request and TX response entries alongside the TCP messages. This also covers
locally handled routes such as `/api/observations` and `/health`, and error
responses. Matching request IDs link each pair; responses show the HTTP status
and elapsed processing time. Tracing does not change the HTTP response body.

Details include headers, query parameters and JSON bodies. Credential fields are
redacted; non-JSON, streamed and large bodies are omitted. The existing WEB TRACE
ON/OFF command controls these entries together with the TCP trace.
