# Web API

The external web server (`web.web_server`, port 8000) validates the existing
`WEBHOOK_SECRET` token or HMAC signature. CMD webhooks now use the target
application's existing command TCP API through `util.cmd_app`, independently of
TM's embedded HTTP API. DIG, DSH, ODT and OET configuration webhooks use the
`tm_web` TCP API on TM's `web` interface. Observation listing reads local/shared storage directly in WEB without TM. Keep the tunnel
pointed at port 8000.

## Shared log and trace root

`LaunchConfig.json` has a top-level `log_dir` alongside `scan_store_dir`:

```json
{
  "_type": "LaunchConfigModel",
  "profile": "local",
  "log_dir": "logs",
  "scan_store_dir": "~/samples/local",
  "launches": []
}
```

The supplied profiles use `"logs"`, which resolves to the existing repository
`dmd2000/logs` directory. Relative paths resolve against the repository root,
independently of the terminal's working directory. Absolute paths, including
mounted/shared storage, and `~` paths are also supported. Omitted/null settings
retain the repository default.

Continue launching applications normally:

```bash
./launch --profile local web001
```

The launcher resolves the profile path and passes `DMD_LOG_DIR` into the new
terminal before Python imports initialize logging. Direct module launches also
read their profile at logging startup. `--log_dir PATH` overrides the process
`DMD_LOG_DIR` value, which overrides the profile setting. An individual launch
entry may set `env.DMD_LOG_DIR` to override its profile's root.

Application logs remain under `app/`, HTML traces under `trace/`, and telemetry
under its existing `availability/`, `alarm/`, `pointing/` and `weather/` folders.
Application traces use defined entity IDs, for example `trace/tm001.html` or
`trace/dig001.html`, falling back to `trace/<app>.html` without an entity ID. Existing
historical files are not moved into a newly configured location. Restart the
applications after changing their output root.

WEB includes the resolved `log_dir` in `GET /api/profile`, profile discovery and
profile-selection responses. Switching the UI's resource profile selects that
profile's log root for future file viewing; it does not redirect a running
application's logging. WEB's startup root override is retained on RESYNC until a
resource-profile switch clears it, as with its scan-store override. The authenticated private UI uses this setting for on-demand log/trace viewing.
The public API listener does not expose these file routes.

## Private console and shared accounts

The WEB process also serves the frontend and OPT workspace on a separate,
**loopback-only** listener at `http://127.0.0.1:8765`. The public API port (8000)
does not serve UI, login, user-management or OPT routes. Continue exposing only
port 8000 through the tunnel. There is no separate console process to start.

Create the first administrator interactively before signing in:

```bash
PYTHONPATH=src python -m web.users create --username ray --role admin
```

Flask-Security uses SQLite and Argon2 password hashes. Users are shared across all
configuration profiles. Its state defaults to `~/.local/share/dmd2000/web/`;
`--ui_state_dir` / `DMD_UI_STATE_DIR` overrides it. Use the same path with the
account CLI's `--state-dir`. There is no default password or self-registration.
Administrators manage accounts under **Access**; viewer/operator/admin roles
control reads, configuration/processing writes and account administration.
Browser API calls require an active session and CSRF tokens for mutations. WEB's
machine API continues to require its token/HMAC credentials independently of UI
cookies. Password reset and account disabling revoke existing sessions.

`--ui_port` defaults to 8765; `--ui_threads` defaults to 4. The UI always binds to
127.0.0.1. `--ui_data_root` is repeatable and limits OPT file access (default
`~/samples`); `--ui_cache_dir` sets the results directory. `--no_ui` runs only the
API listener. Both listeners, TCP connections and OPT workers share WEB's startup
and shutdown lifecycle. Stop any older `ui.opt_service` session before using the
combined listener on the same port.

See [console setup and account recovery](../src/ui/frontend_prototype/README.md)
for the full workflow.

## Standalone web application and configuration requests

`web.web_server` now runs a `WebServer(App)` with its own `WebServerModel`, event
processors, timer manager, command endpoint, tracing and managed Waitress HTTP
server. Install the new HTTP-server dependency in the runtime environment:

```bash
python -m pip install -r src/requirements.txt
```

Start the two applications, for example (with `WEBHOOK_SECRET` already exported
in the web-server environment):

```bash
# On the TM machine; set --web_host to the WEB machine's reachable address.
PYTHONPATH=src python -m tm.tm --profile alston --entity_id tm001 --web_host 127.0.0.1 --web_port 50004

# On the WEB machine; set --tm_host to WEB's local listening address for TM.
PYTHONPATH=src python -m web.web_server --profile alston --entity_id web001 --tm_host 127.0.0.1 --tm_port 50004 --http_port 8000 --http_threads 10
```

TM initiates the TCP connection during initialization, after loading its
configuration, and owns retries after a connection failure. WEB listens for TM;
its HTTP service can start and remain available before TM is ready. Both apps
must be restarted when upgrading from the previous connection direction.

The examples use loopback for a same-machine test. The Alston launch profile
sets both sides to `192.168.2.1`, where WEB listens and TM connects. When the
apps run on different machines, TM's `--web_host` must point to WEB, and WEB's
`--tm_host` must name a local address on the WEB machine. `--profile_dir` (or
`WEB_PROFILE_DIR`) selects a shared/local profile directory explicitly; otherwise
the web application uses repository `src/config/<profile>`. Default and Alston
profiles now include `WebServerModel.json`. The model stores the app's identity,
command endpoint, request timeout and live TM connection state. Network listener
settings and the TM connection are configured at startup; restart to change them.
Web RESYNC reloads the profile's identity/request timeout and command-dispatch
configuration without replacing live sockets.

| Setting | Default |
| --- | --- |
| TM `--web_host`, `--web_port` (WEB destination) | `localhost`, `50004` |
| Web `--tm_host`, `--tm_port` (local listener for TM) | `127.0.0.1`, `50004` (`WEB_TM_HOST` / `WEB_TM_PORT` overrides) |
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

## Select a resource profile

WEB starts with the profile supplied by `--profile` (or `--profile_dir`). The
frontend can discover and switch the profile used for file-backed resources:

| Method | Route | Result |
| --- | --- | --- |
| GET | `/api/profiles` | Available profiles and the current selection |
| GET | `/api/profile` | Current profile and effective observation directory |
| PUT | `/api/profile` | Select a profile using `{"profile":"local"}` |

```bash
curl -H "X-Webhook-Token: $WEBHOOK_SECRET" http://localhost:8000/api/profiles
```

```bash
curl -X PUT -H "X-Webhook-Token: $WEBHOOK_SECRET" -H 'Content-Type: application/json' \
  -d '{"profile":"alston"}' http://localhost:8000/api/profile
```

Responses use `ResponseEvent`. The current selection is returned as
`payload.profile` and `payload.scan_store_dir`. The list also contains
`payload.count` and `payload.profiles`; each entry has `profile`, `selected`,
`scan_store_dir` and `available`. An invalid LaunchConfig is listed as unavailable
with a message, and cannot replace the current selection.

Discovery searches immediate configuration directories beside the launch profile;
with `--profile_dir`, it searches that directory's siblings. Hidden directories,
symlinks and directories without JSON configuration files are excluded. Selection
accepts a profile name, not an arbitrary filesystem path.

Switching atomically changes the directory used by dish, digitiser, weather-station and pipeline reads/edits and the
observation default from the new profile's top-level `scan_store_dir` in
`LaunchConfig.json`. It clears any startup `--scan_store_dir` override. The
per-request observation `directory` override continues to work. A profile with no
storage default can still be selected; observation requests without an explicit
directory then return `store_not_known`, rather than using the previous profile.
WEB RESYNC reloads the selected profile's storage default.

The selection is shared by all callers of this WEB instance and lasts until WEB
restarts; startup arguments select the profile again on restart. Requests already
in progress finish against the profile they captured. This selection controls
file-backed resources; it does not change WEB's process identity, command target
lookup, TCP connections, or the profiles of running TM, SDP and Dish Manager.
These calls use the existing WEB authentication and HTTP tracing.

## Profile dish configuration

Dish APIs read and update `DishList.json` in WEB's selected resource profile,
initially chosen by `--profile` (or `--profile_dir`) and changeable via `/api/profile`. They work while TM and Dish Manager are offline.
They use the same token/HMAC authentication and HTTP tracing as other WEB routes.

| Method | Route | Result |
| --- | --- | --- |
| GET | `/api/dishes` | List configured dishes |
| GET | `/api/dishes/<dsh_id>` | Read one configured dish |
| POST | `/api/dishes` | Create a dish; duplicate IDs return 409 |
| PATCH | `/api/dishes/<dsh_id>` | Update supplied fields; omitted fields are retained |
| DELETE | `/api/dishes/<dsh_id>` | Delete one stored dish definition |

List the active profile's dishes:

```bash
curl -H "X-Webhook-Token: $WEBHOOK_SECRET" http://localhost:8000/api/dishes
```

Create a fixed dish using the existing model defaults for omitted settings:

```bash
curl -X POST -H "X-Webhook-Token: $WEBHOOK_SECRET" -H 'Content-Type: application/json' \
  -d '{"dsh_id":"dish004","short_desc":"New fixed dish","diameter":1.2,"latitude":53.801,"longitude":-2.5933,"height":90,"driver_type":"DRIFT","driver_config":{"alt":45,"az":180},"feed_type":"H3T_1420"}' \
  http://localhost:8000/api/dishes
```

Edit its description and fixed altitude:

```bash
curl -X PATCH -H "X-Webhook-Token: $WEBHOOK_SECRET" -H 'Content-Type: application/json' \
  -d '{"short_desc":"Updated fixed dish","driver_config":{"alt":50}}' \
  http://localhost:8000/api/dishes/dish004
```

Responses use `ResponseEvent`. Lists have `payload.profile`, `payload.count` and
`payload.dishes`; individual reads and writes have `payload.profile` and
`payload.dish`. Successful creation returns HTTP 201 with a `Location` header;
reads and updates return 200. Writes include `payload.phase: "stored"`.

The editable fields are `dsh_id` (creation only), `short_desc`, `diameter`,
`fd_ratio`, `latitude`, `longitude`, `height`, `ws_id`, `feed_type`, `feed_config`,
`dig_id`, `driver_type`, `driver_config` and `driver_poll_period`. Enum values use
names such as `H3T_1420`, `MD01`, `DRIFT` or `MOTION`. A new dish needs a unique
`dsh_id` and supported `driver_type`. Driver configuration is validated using the
existing MD01, Drift or Motion model; its omitted fields use model defaults on
creation, and retain stored values on update. Changing driver type replaces the
previous driver's configuration. Unknown fields and invalid values return 400.
Runtime mode, pointing, health and observation state are excluded from this API.

GET and successful write responses include an `ETag` for the entire profile dish
file. A frontend editor should retain that value and send it in `If-Match` when
saving, including the enclosing quotes. If another editor changed the file, the
save returns HTTP 412 with code `version_conflict`; reload before saving again.
Without `If-Match`, a PATCH merges its fields into the latest stored definition.
File writes use a process/thread lock and atomic replacement, preserving other
dishes, unrelated metadata and existing legacy fields. Other tools editing this
file should use the same lock to coordinate simultaneous writes.

These operations save profile configuration. They do not send live configuration
commands or automatically reload running applications. Dish Manager loads the
file on startup or a successful RESYNC, subject to its existing safe-state checks;
TM subsequently learns the updated dish model through Dish Manager status.
New dishes retain the model's default runtime state until the applications load
and configure them. The frontend prototype's Admin → Dishes editor uses these routes through its
local console bridge. It sends the expected profile in `X-Config-Profile`; a
profile change from another session returns HTTP 409 (`profile_changed`) before
the request accesses a different profile. This optional header also applies to
the digitiser, weather-station and pipeline resource APIs. See the
[frontend guide](../src/ui/frontend_prototype/README.md#admin--dishes).

## Profile digitiser and weather-station configuration

These APIs use WEB's selected resource profile, with the same authentication,
HTTP tracing, `ResponseEvent` envelopes, ETags and optional `If-Match` checks as
the dish APIs. They work independently of TM and write configuration files only.

| Method | Digitiser route | Weather-station route | Result |
| --- | --- | --- | --- |
| GET | `/api/digitisers` | `/api/weather-stations` | List definitions |
| GET | `/api/digitisers/<dig_id>` | `/api/weather-stations/<ws_id>` | Read one |
| POST | `/api/digitisers` | `/api/weather-stations` | Create one |
| PATCH | `/api/digitisers/<dig_id>` | `/api/weather-stations/<ws_id>` | Edit supplied fields |
| DELETE | `/api/digitisers/<dig_id>` | `/api/weather-stations/<ws_id>` | Delete one definition |

Lists return `payload.digitisers` or `payload.weather_stations`, together with
`profile` and `count`. Single-resource responses return `payload.digitiser` or
`payload.weather_station`. Creation returns 201 and a `Location` header; reads and
updates return 200. Writes include `payload.phase: "stored"`. Duplicate IDs return
409, missing definitions/files return 404, invalid edits return 400, and stale
`If-Match` versions return 412. Identifiers cannot be renamed through PATCH.

### Digitisers

Definitions are read from `DigitiserList.json`. Editable fields are `dig_id`
(creation only), `bpf_type`, `bpf_config`, `temp_type`, `temp_config`, `temp_max`,
`gain`, `sample_rate`, `bandwidth`, `center_freq`, `freq_correction`, `channels`,
`sdr_type`, `sdr_config`, `local_host`, `app_cmd_host` and `app_cmd_port`.
The last three map to the digitiser's stored application settings; runtime app
state, connection flags, scanning state and EEPROM readings are excluded.

SDR types follow `DigitiserModel`: `rtlsdr`, `soapy`, `airspy`, `gqrxraw`,
`airstream` and `rtlstream`. Temperature types are `none` and `bme280`.
`bpf_type` uses a `BandpassFilterType` name. SAWBIRD_H1_BAREBONES configurations
accept `gpio_pin_power` and `gpio_pin_load` (integer pins 0–27 or null); a null
`bpf_config` disables its configured GPIO controls. SDR and temperature options
are dictionaries validated to the extent provided by the existing model; saving
does not open or test hardware. Nested configuration edits merge supplied keys.
Changing SDR, temperature or filter type clears the previous type's settings.

```bash
curl -H "X-Webhook-Token: $WEBHOOK_SECRET" http://localhost:8000/api/digitisers
```

```bash
curl -X POST -H "X-Webhook-Token: $WEBHOOK_SECRET" -H 'Content-Type: application/json' \
  -d '{"dig_id":"dig004","local_host":"192.168.2.104","sdr_type":"rtlstream","sdr_config":{"ring_seconds":10,"read_timeout_sec":10}}' \
  http://localhost:8000/api/digitisers
```

```bash
curl -X PATCH -H "X-Webhook-Token: $WEBHOOK_SECRET" -H 'Content-Type: application/json' \
  -d '{"temp_max":45,"sdr_config":{"read_timeout_sec":15}}' \
  http://localhost:8000/api/digitisers/dig004
```

The frontend prototype's Admin → Digitisers view supports listing, editing,
creation and confirmed deletion through these endpoints, under the shared Admin
profile selector. See the [frontend guide](../src/ui/frontend_prototype/README.md#admin--digitisers).

Digitisers reload their existing hardware/sensor configuration on a successful
RESYNC when idle. Network identity and other process settings may require a
restart; adding a definition does not launch a digitiser process.

### Weather stations

Station definitions are read from `WeatherStationList.json`. Editable fields are
`ws_id` (creation only), `location`, `latitude`, `longitude` and `elevation`.
Creation requires a unique `ws_id` and non-empty `location`. Coordinates and
elevation may be null; otherwise they use the existing model's valid ranges.
Other stations, weather readings, summaries and list-level alarm thresholds are
preserved on disk.

```bash
curl -H "X-Webhook-Token: $WEBHOOK_SECRET" http://localhost:8000/api/weather-stations
```

```bash
curl -X POST -H "X-Webhook-Token: $WEBHOOK_SECRET" -H 'Content-Type: application/json' \
  -d '{"ws_id":"ws003","location":"New observatory","latitude":53.801,"longitude":-2.5933,"elevation":90}' \
  http://localhost:8000/api/weather-stations
```

```bash
curl -X PATCH -H "X-Webhook-Token: $WEBHOOK_SECRET" -H 'Content-Type: application/json' \
  -d '{"location":"Updated observatory","elevation":95}' \
  http://localhost:8000/api/weather-stations/ws003
```

The weather application driver settings remain in the separate
`WeatherStationModel.json`; these station collection endpoints do not edit that
file or its hardware driver. TM and Dish Manager load the station list during
startup or their normal RESYNC flow. New definitions do not launch weather
applications or alter running weather-monitoring configuration automatically.

## Profile pipeline steps

Pipeline operations read and edit the selected profile's `PipelineConfig.json`.
Its `steps_map` contains named, ordered lists, commonly `default` and digitiser
IDs such as `dig001`. These are stored configuration operations, independent of
TM and SDP, with the existing authentication, HTTP tracing, `ResponseEvent`
envelopes, atomic writes and optional `If-Match` version checks.

| Method | Route | Result |
| --- | --- | --- |
| GET | `/api/pipeline` | All maps and built-in step names |
| POST | `/api/pipeline/steps-maps` | Create a named map |
| GET | `/api/pipeline/steps-maps/<name>` | Read an explicit map |
| GET | `/api/pipeline/steps-maps/<name>/steps` | List its ordered steps |
| POST | `/api/pipeline/steps-maps/<name>/steps` | Append or insert a step |
| GET | `/api/pipeline/steps-maps/<name>/steps/<index>` | Read a step |
| PATCH | `/api/pipeline/steps-maps/<name>/steps/<index>` | Edit a step |
| DELETE | `/api/pipeline/steps-maps/<name>` | Delete a named map |
| DELETE | `/api/pipeline/steps-maps/<name>/steps/<index>` | Delete a step |

List the current maps or the default steps:

```bash
curl -H "X-Webhook-Token: $WEBHOOK_SECRET" http://localhost:8000/api/pipeline
curl -H "X-Webhook-Token: $WEBHOOK_SECRET" http://localhost:8000/api/pipeline/steps-maps/default/steps
```

Create a new digitiser-specific map with initial steps (`steps` may be empty or
omitted). If the profile has no pipeline file yet, this creates it:

```bash
curl -X POST -H "X-Webhook-Token: $WEBHOOK_SECRET" -H 'Content-Type: application/json' \
  -d '{"name":"dig004","steps":[{"step":"DC_SPIKE","params":{"context":"spr"}},{"step":"QA","params":{"context":"mpr","window_frac":0.2}}]}' \
  http://localhost:8000/api/pipeline/steps-maps
```

Insert a step at zero-based index 1. Omit `index` to append:

```bash
curl -X POST -H "X-Webhook-Token: $WEBHOOK_SECRET" -H 'Content-Type: application/json' \
  -d '{"index":1,"step":"RFI_FLAG","params":{"context":"cal","threshold":5,"window_size":21}}' \
  http://localhost:8000/api/pipeline/steps-maps/dig004/steps
```

Edit the inserted step's threshold, retaining its other parameters:

```bash
curl -X PATCH -H "X-Webhook-Token: $WEBHOOK_SECRET" -H 'Content-Type: application/json' \
  -d '{"params":{"threshold":6}}' \
  http://localhost:8000/api/pipeline/steps-maps/dig004/steps/1
```

GET `/api/pipeline` returns `payload.profile`, `payload.steps_map`, `payload.count`
(number of maps) and `payload.step_types`. A map response contains `name`, `steps`
and `count` (number of steps). Every step has a zero-based `index`, a `step` name
and `params`. Single-step reads and step writes additionally include
`payload.step`. Writes include `payload.phase: "stored"`. Creation returns 201
and a `Location` header; reads and edits return 200. Duplicate map names return
409; missing maps and step indices return 404; invalid input returns 400.

Step indices change after insertion. Frontend editors should send the returned
`ETag` in `If-Match` when editing or inserting; a stale version returns 412
without changing the file. Other maps, step metadata and unrelated file fields
are preserved. These explicit map endpoints return 404 for a missing digitiser
map; they do not silently edit `default` through SDP's runtime fallback rule.

A new step requires a non-empty `step` name. Built-in names are accepted without
regard to case and stored as StepType values; custom registry keys/class paths
remain supported as strings. WEB does not resolve or import those plugins.
`params` must be a JSON object; omitted params start empty. PATCH merges supplied
top-level parameter keys, preserving the others; nested values replace their
corresponding value. Changing step type clears the old parameters before applying
new ones. Step-specific parameter semantics and plugin availability remain the
responsibility of the processing pipeline; saving does not execute it.

Saved pipeline changes take effect when SDP loads the profile on startup or
RESYNC. The frontend editor is not wired to these APIs yet.

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
running. The default comes from the selected resource profile's
`LaunchConfig.json`, initially chosen by WEB's `--profile` (or `--profile_dir`):

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

Catalogue entries also include scheduled start/end, estimated observing duration,
total integration time, creation time, creator contact, end/last-update times,
scan counts, completion and an opaque file `revision`. Individual scan lists are
not transferred by the catalogue endpoint.

`GET /api/observations/<filename-id>?folder=<relative-folder>` retrieves the saved
`ObsModel` in `payload.observation`, including its target scan sets. `filename-id`
is `filename` without the `-obs.json` suffix, which may differ from the observation
ID stored inside the metadata. The storage root follows the same profile/default
and optional `directory` selection as the catalogue. Absolute/traversing folders
and symbolic links are rejected. Responses are non-cacheable and credential fields
are redacted. Invalid paths return 400, missing observations 404 and unavailable
or invalid metadata 503.

Catalogue and detail requests accept `X-Config-Profile` as a selection precondition
(409 if it changed). The private UI bridges these read-only routes using WEB's
selected storage root and relative `folder` values; it does not accept arbitrary
`directory` overrides. The Schedule history view excludes queued/running IDs,
collapses duplicate disk IDs to the newest modified copy and loads scans only on
expansion. Saved execution states do not establish whether recording completed;
the history view uses scan completion and saved target progress.

`GET /api/observations/<observation-id>/scans/<target>/<frequency>/<iteration>/spr`
downloads a completed scan's SPR CSV as `text/csv` with an attachment filename.
The same authentication and `X-Config-Profile` guard apply. With `folder`, the
observation identifier is the metadata filename without `-obs.json`; without
`folder`, WEB locates the newest saved copy matching the observation ID.
WEB checks the saved scan status and uses `files_prefix` to locate its spectrum,
preferring the metadata folder so relocated archives work. A recorded
`files_directory` is considered only within the selected archive. Arbitrary
filenames, outside paths and symbolic links cannot be downloaded. Incomplete
scans return 409; missing scans/files return 404. No processing is performed.

`GET /api/observations/<observation-id>/scans/<target>/<frequency>/<iteration>/signal`
returns the saved final-sample `files_prefix` + `-sigfig.png` as `image/png`,
with its original attachment filename. It applies the same completion, archive,
authentication and profile checks as SPR. Missing PNGs return 404. No image is
regenerated. The private `/api/web/…/signal` bridge streams the PNG for the
Schedule preview and download controls.

The private `/api/web/…/spr` bridge streams the CSV without exposing WEB's token.
For browser download links, its optional `profile` query parameter is forwarded
as `X-Config-Profile`; only relative `folder` and `profile` parameters are accepted.

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


## Delete stored definitions

DELETE calls remove a single dish, digitiser or weather-station definition, a
named pipeline map, or a step at its current zero-based index. They use WEB's
selected resource profile and the same authentication, `If-Match` and optional
`X-Config-Profile` checks as edits. No request body is required. For example:

```bash
curl -X DELETE -H "X-Webhook-Token: $WEBHOOK_SECRET" \
  -H 'X-Config-Profile: local' \
  http://localhost:8000/api/dishes/dish004
```

Frontend clients should include the latest quoted ETag in `If-Match`. Success
returns HTTP 200 with `ResponseEvent`, an updated ETag, `payload.profile` and
`payload.phase: "stored"`. Resource deletion responses have `payload.deleted_id`;
pipeline responses identify the map with `payload.name`, and step deletion also
returns `deleted_index` and the remaining, reindexed `steps`. Missing definitions
return 404, stale versions return 412, and an unexpected profile returns 409.
Deleting the final entry leaves an empty collection; the configuration file is
retained.

Deletion is atomic and removes only the selected definition. Other definitions,
references, pipeline maps, weather-list settings and observations are retained;
there is no cascade into other configuration files or recorded data. Running
applications load the changed configuration through their existing startup or
RESYNC behaviour. The frontend dish editor asks for confirmation before deletion.
The console bridge forwards dish DELETE calls with the same server-side token and
cross-origin protections as other editor writes. The frontend Digitisers editor also uses the restricted console bridge for
CRUD operations. Weather and pipeline frontend editors remain to be connected.


## Cached live telescope model

`GET /api/telescope/model` returns WEB's most recent telescope-model snapshot.
WEB polls TM immediately on a connection/reconnection and every 30 seconds using
`tm_web` GET requests with property `telescope_model`. Each query has a unique
correlation ID and timeout timer; only one is outstanding at a time. No WEB
worker waits for the response. HTTP reads share the cache and never trigger a
new TM query. Status advice and configuration requests retain their existing flow.

```bash
curl -H "X-Webhook-Token: $WEBHOOK_SECRET" http://localhost:8000/api/telescope/model
```

A successful `ResponseEvent` contains `payload.telescope_model`, `profile` (TM's
running profile), `snapshot_at`, `received_at`, `age_seconds`, `stale`,
`tm_connected`, `poll_interval_seconds` and `last_error`. The model uses the
existing `TelescopeModel.to_dict()` structure. Credential-labelled fields are
redacted at TM before transmission; application model timestamps are retained.
The snapshot is assembled from TM's latest known state, not an atomic fresh
measurement of every application.

Timeouts and disconnects retain the last successful snapshot and mark it stale.
A cache older than 35 seconds is also marked stale. A valid cached response,
including a stale snapshot, returns HTTP 200; before the first snapshot the
endpoint returns 503 with code `model_unavailable`. Late/duplicate responses
cannot replace a newer query result. Reconnection requests a fresh model
immediately. The endpoint is authenticated, traced and has a separate 120/minute
limit so routine frontend polling does not consume the configuration API's
100/hour limit. Responses are not browser-cacheable.

The frontend Status overview and selected application pane use this snapshot;
Config profile selection does not change the TM connection or live profile.
The full model includes observation/scan metadata and duplicated subsystem
information. Snapshot size and serialization cost may grow with observation
history; a narrower status representation can be added if measurements justify
it, while keeping the same polling/cache architecture.

## Private UI diagnostic file viewing

The local UI listener serves `GET /api/diagnostics/<application>/<kind>`, where
application is `tm`, `dm`, `sdp`, `ws`, `web` or `dig`, and kind is `log` or `trace`.
UI login is required; viewer, operator and admin roles can read files. These routes
are not registered on the public port-8000 API or its token bridge.

The current resource profile supplies the log root. `profile=<name>` is an
optional selection precondition (409 if it changed), and `entity=<entity-id>`
selects an application's instance trace. Application logs map to `app/<app>.log`;
traces map to `trace/<entity-id>.html`, falling back to `trace/<app>.html` when
the entity is omitted, empty or `<undefined>`.
Digitiser logs still share `app/dig.log`. No client-supplied path is accepted and
symlinked file/subdirectory access is refused.

A JSON ResponseEvent-style result contains profile, application/entity, relative
filename, UTC modification time, original size, truncation/shared-log flags,
format and content. Log previews tail 512 KiB; HTML previews are limited to 32 MiB
(413 otherwise). `download=1` returns the complete current file as an attachment.
All responses are non-cacheable. A file not yet generated returns 404.

In the Status application table, **Debug** links to the log and **Tracing** links
to the trace regardless of their current On/Off value. Opening either closes the
application detail pane. Independent panes appear below the table, Log first,
each with Refresh, Download and X. Selecting an application row returns to its
details. File panes do not follow the automatic model refresh; HTML is isolated
in a sandboxed frame with local expand/collapse behaviour.

## Private UI application commands

`POST /api/commands` on the private UI accepts `{ "app": "web", "cmd": "trace",
"value": "ON" }`, TRACE/DEBUG values ON or OFF, or `cmd: "resync"` without a value.
It requires an operator/admin session and CSRF token. The bridge sends a single
`alston-rt.ui.cmd` webhook to WEB using its server-side token, and returns the
application's ResponseEvent result. Browser requests cannot choose a host, port,
upstream URL or arbitrary command. Transport/invalid-response failures return
`outcome_unknown` without retrying. This route is not exposed by the public API.

The Status table places TRACE/DEBUG switches beside their View file links and removes the Command dropdown. The full last error
now appears at the top of application details with its recorded UTC timestamp.
A response area below the table shows application, response time, status/code
and message for the toggle command. Successful responses update the switch until
a non-stale snapshot contains an application report newer than the command
acknowledgement; failed commands retain the prior state.
RESYNC is not offered in the Status table.

The Config page provides a separate App selector and RESYNC button beside its
resource subtabs. It uses the same private command endpoint with `{ "app": "dm",
"cmd": "resync" }`. The selector includes fixed applications and configured
digitisers. The button/message indicate pending, application success or failure;
responses include UTC arrival time and error codes. Unsaved drafts and pending
configuration activity disable submission, and viewer permissions remain read-only.
RESYNC reloads the application's startup profile, independently of WEB's resource
profile selection, and commands are never automatically retried.


### Live dish engineering controls

Status application details show App Connections and Entity Connections side by
side (stacked on narrow screens). TM lists individual digitisers from its digitiser
store using their `tm_connected` states; SDP uses its own digitiser store and
`sdp_connected` states. Digitiser details put the SDR connection in the entity
table. Entity interfaces are excluded from App Connections; missing states are
shown as UNKNOWN. The Configured host column uses a digitiser’s
`app.arguments.local_host` or an SDR’s `sdr_config.host`, when present. It remains
visible when disconnected and is not a separately recorded TCP peer address.
Server port shows the digitiser’s reported `tm_port`/`sdp_port`, falling back to
the selected app’s `dig_port` listener configuration. For SDRs it uses
`sdr_config.port`. These are server listening ports; digitiser TCP clients use
dynamically assigned local source ports, which are not stored in the model. Dish Manager details then show an alphabetical engineering table of
its reported dishes. Queue size and processor count sit below Connections; either control expands
or collapses the processor table with current events and processing times. It
starts collapsed for each application and retains its open state through model
refreshes. The command endpoint is right-aligned above the Connections table. Select a dish to open its detail pane below the application
pane. The pane compares desired and actual altitude/azimuth in degrees, shows
Mode and Capability, target ID, target-matched PEC RMS in arcseconds, driver
failures and timestamped errors. Desired Mode/Capability selections survive
background model refreshes; Actual always reflects the cached dish report.
A compact polar plot uses north-up, clockwise azimuth and a linear altitude
scale from 0° at the horizon to 90° at the centre. Actual is a filled blue dot;
Desired is an outlined magenta diamond, so coincident positions remain visible.
Hover or focus a marker to read its coordinates. Missing/invalid positions are
omitted, below-horizon positions are labelled, and stale snapshots are marked.
The plot refreshes with the shared model cache and does not send pointing commands.

Private UI endpoints (operator/admin session and CSRF token required):

- `POST /api/dish-control/<dish_id>` with one property, for example
  `{"mode":"STOW"}` or `{"capability":"STANDBY"}`. Names are validated against
  the existing dish enums. The UI bridge sends a single `alston-rt.ui.dsh`
  webhook with `{"dsh_id":"dish002","mode":"STOW"}` to WEB, which uses the
  existing TM ConfigEvent processing. Success acknowledges actions scheduled,
  not completion at the dish. TM/DM retain transition validation. Controls are
  disabled when the TM snapshot is stale or TM is disconnected.
- `POST /api/commands` with `{"app":"dm","cmd":"stop","value":"dish002"}`
  sends the existing STOP command directly to Dish Manager through WEB's
  command handler. STOP remains available with a stale TM snapshot and while
  a Mode/Capability request is pending. It stops the selected dish's movement;
  it is a software command, not a hardware emergency-stop circuit.

Both paths show the returned application status/message with UTC response time.
Transport failures have an unknown outcome and are not automatically retried.
These controls do not edit `DishList.json` or change the selected file-backed
configuration profile; they target the running applications using WEB's launch
profile. Viewer accounts can inspect all reported details but cannot submit
controls. The public API listener does not serve these private UI endpoints.


### Results observation review and saved scan files

The Results frontend uses the expandable saved-observation presentation formerly
shown in Schedule. Expanding a row reads `/api/opt/open` for the selected directory
and filename ID, including redacted saved metadata for its scan table. Selecting
a radio button prepares that observation for processing; the UI has no separate
Open observation step. Schedule now displays only queued/running observations.

The local authenticated frontend service provides the read-only route
`GET /api/opt/observations/<file-id>/scans/<target>/<frequency>/<iteration>/<product>?directory=...`,
where product is `signal` (saved final-sample PNG) or `spr` (spectrum CSV).
It resolves against the chosen Results directory within configured data roots,
independently of WEB's active profile. It uses the same completion and safe file
lookup as WEB scan downloads: 409 for incomplete scans, 404 for missing/unsafe
files, and 400 for invalid product/directory input. No processing is launched.


OPT configuration responses include `pipelines`, a profile-to-digitiser-map of
editable `{step, params}` lists. `POST /api/opt/process` accepts an optional
`pipeline` digitiser map for that run. Overrides allow built-in step names only,
contexts spr/cal/mpr, and serializable parameters; runtime scan/queue references
are rejected. BASELINE requires cal context and linear/constant/none method.
The server snapshots the effective profile even without overrides. Profile
files are not written. Results export `pipeline-config.json`; aggregates include
`pipeline_baseline_applied`. Cached reanalysis retains the snapshot and applies
only interactive plot adjustments to processed spectra.


The console header provides one working-profile selector for both file-backed
WEB resources and OPT processing. Selection still uses `PUT /api/profile` (or
its `/api/web/profile` proxy), and OPT sends that accepted profile with the next
processing request. This selection does not change application launch arguments
or send RESYNC. Each Status application row shows its reported
`app.arguments.profile`; unavailable values are shown as unknown. RESYNC reloads
an application's launch profile; changing that profile requires restarting it.


The shared Config Profile selection forwards `scan_store_dir` from WEB's
profile response to Results. Initial loading and profile switches use this
directory and refresh observation discovery; the directory remains manually
editable afterward. Missing scan_store_dir falls back to OPT's first permitted
data root. This does not expand the server's allowed data roots.


`GET /api/opt/config` also returns `available_steps`, containing implemented
built-in step names, initial `params`, and descriptions. The pipeline editor
uses it to display unconfigured steps without modifying the profile or run.
Only checking a step adds an entry to the submitted pipeline.

### Total Power during OPT plot updates

OPT analysis settings accept `total_power_basis: "frequency" | "velocity"`
(default `frequency`). The frontend sends the active spectral view when Update
plots is clicked; L–V uses `velocity`. This selects which aggregate fit supplies
the correction for both Total Power resolutions, individual/all-SKY images and
their CSV exports. Frequency and Velocity retain their own fitting bounds.
Original pipeline totals and affine reconstruction responses are cached once;
updates do not replay scans or accumulate corrections. Legacy results without
this cache require reprocessing before corrected Total Power can be displayed.

`POST /api/opt/pipeline` accepts `{ "profile": "rotation", "pipeline": {"default": [...]}}`
to validate and atomically save the edited pipeline to that existing profile's
`PipelineConfig.json`. The response supplies `profile`, normalized editable
`pipeline`, and a success `message`. Numeric range pairs remain on one line.
This write uses the private UI's operator/admin and CSRF requirements.
