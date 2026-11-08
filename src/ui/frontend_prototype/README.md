# Local engineering console and OPT workspace

The existing frontend prototype now includes a working **Results → Observation
Processing** workspace backed by the Python OPT pipeline.

## Start

WEB now serves this console and OPT in the same application process, on separate
Waitress listeners. Install the project dependencies in your Python environment:

```bash
python -m pip install -r src/requirements.txt
```

Create your first administrator once. The command prompts for a password twice;
there is no default account or password:

```bash
PYTHONPATH=src python -m web.users create --username ray --role admin
```

With `WEBHOOK_SECRET` already exported, start WEB from the repository root:

```bash
PYTHONPATH=src python -m web.web_server --profile local --entity_id web001 \
  --http_port 8000 --ui_port 8765 --ui_data_root ~/samples
```

Open **http://127.0.0.1:8765/** and sign in. Stop any existing `ui.opt_service`
session first to release port 8765. There is no need to launch that service
separately. The API on port 8000 retains token/HMAC authentication; UI, login,
account-management and OPT routes exist only on port 8765. Keep public tunnels
pointed exclusively at port 8000. The UI binds to **127.0.0.1** even when the API's
`--http_host` is set to an external address. It also checks localhost Host headers
and rejects cross-origin writes.

Use repeatable `--ui_data_root PATH` options to permit additional observation
roots (default `~/samples`); symlink-resolved paths must stay inside those roots.
`--ui_cache_dir PATH` retains generated results at a known location, otherwise a
temporary session folder is used. Source recordings are not modified. The UI has
four Waitress threads by default (`--ui_threads`); the API has its own pool
(`--http_threads`). `--no_ui` starts WEB with only the API listener.

Stop WEB with Ctrl+C; both listeners and running OPT workers are stopped.
Restarting creates a new in-memory processing-job registry. Previously exported
files remain on disk, but historical job folders are not automatically reopened.

## Accounts and permissions

Flask-Security manages username/password login, Argon2 password hashes, CSRF
protection, password changes and signed cookie sessions. Sessions expire after
eight hours. Self-registration and email recovery are disabled; administrators
create accounts through **Access** and can change roles, reset passwords and
disable accounts. Every signed-in user can use **Change my password**.

| Role | Permissions |
| --- | --- |
| Viewer | Read configuration and telescope status, inspect observations and existing results |
| Operator | Viewer access plus configuration edits, profile selection, processing and cancellation |
| Admin | Operator access plus user management |

Accounts, roles and persistent signing keys live in
`~/.local/share/dmd2000/web/`, independently of configuration profiles. Back up
`users.sqlite`, `session.key` and `password.salt` together. Do not commit them.
`--ui_state_dir PATH` or `DMD_UI_STATE_DIR` selects another location; pass the same
path to the account CLI with `--state-dir PATH`. Changing profiles does not change
users or their sessions. Disabling an account or resetting its password revokes
its existing sessions; the last active administrator cannot be disabled/demoted.

The CLI remains available for recovery:

```bash
PYTHONPATH=src python -m web.users list
PYTHONPATH=src python -m web.users update --username ray --password
PYTHONPATH=src python -m web.users create --username observer --role viewer
```

Passwords are prompted for, never passed as command-line values. SQLite users
are shared across profiles and console sessions on this host, not between hosts
unless they deliberately use the same state directory. The local HTTP listener
uses HttpOnly, SameSite=Strict cookies; public HTTPS UI deployment is outside this
local-only configuration.

For development, the standalone authenticated launcher is still available:
`PYTHONPATH=src python -m ui.opt_service --data-root ~/samples`. It uses the same
account database and expects WEB at `http://127.0.0.1:8000` (`--web-url` overrides).
Do not run it on the combined UI's port at the same time.

## Review, process, explore

1. Choose an observation directory from the suggestions or enter a path.
   Observations load automatically when the directory changes; **Find observations**
   refreshes the list. The scrollable table shows ten rows at a time, with the
   observation name/ID, dish, completed/total scan counts, counts by completed
   scan type and completed integration time. Use the filter to search names,
   IDs, dishes or types using the filter beside **Find observations**. Select a
   row and click **Open observation** in that same control pane. The catalogue collapses after opening; expand its
   heading to choose another observation. OPT reads the usual `<id>-obs.json` and scan products;
   an Observation Velocity CSV alone is not a processing input.
2. In **Input scans**, use the checkboxes to include/exclude physical scan
   iterations. Selecting a row inspects it without changing its checkbox.
   Search, type filtering, Include/Exclude all, Previous/Next and arrow keys
   help navigate the list. Metadata scans not marked COMPLETE start excluded
   and their checkboxes are disabled.
3. Choose the **Configuration profile** in **Analysis settings** at the bottom.
   This selects the processing pipeline steps; changing it requires a full
   reprocess rather than a cached plot update. Click **Process observation**. A background process runs the accepted batch
   without terminal prompts, partial aggregate displays or GUI popups. Progress
   and an expandable log remain available. **Cancel** terminates the worker.
4. On completion, **Aggregate scans** lists complete load/calibration and SKY results
   by target, frequency scan and digitiser. Click an aggregate for its full
   signal display. Incomplete aggregates are omitted with a warning.
5. Switch directly between **Signal**, **Velocity**, **Total Power**,
   **L-V Diagram**, **Aggregate**, and **Details**. Timeline, overview and L–V
   show the observation-wide results. Velocity defaults to the selected SKY
   aggregate; **All SKY aggregates** overlays the complete observation.
   **Open image** opens the full-resolution PNG. Calibration rows have a signal
   view but no H I velocity spectrum.
6. Change baseline limits/type or contour levels/smoothing, then **Update plots**
   to re-render velocity/L–V views from cached native spectra, without loading
   and processing the recorded files again. The selected completed aggregate
   remains selected. Changing the input exclusions or processing profile requires
   **Reprocess observation**, which reruns the scientific pipeline.
7. **Export results** downloads a ZIP with completed plots, CSV products, native
   spectrum caches, the processing settings and a manifest.

Plots remain visible while a new job runs. Changed settings/exclusions show an
out-of-date banner. A failed or cancelled job does not replace the last completed
results. The workspace remembers directory, profile, analysis settings and scan
exclusions in browser-local storage; a page reload does not currently reconnect
to previous running/completed jobs automatically.

Baseline subtraction and L–V colour/contour semantics are the same as documented
in [OPT usage](../../../docs/opt.md). Amplitudes are not normalized. Signal displays
show the completed processing-pipeline spectra; baseline controls affect the
velocity and L–V analysis views. Cached reanalysis does not mutate those signal
images or original calibrated powers.

## Implementation

- `ui.opt_service`: Flask static/API server and isolated subprocess job lifecycle.
- `ui.opt_worker`: noninteractive OPT replay, completed-aggregate rendering and exports.
- `ui.opt_reanalysis`: derived plots from cached native LSRK data.
- `frontend_prototype/opt.js`: workspace state, scan selection, polling and plot controls.

Each worker renders with Matplotlib Agg and stdin disconnected. Python processing
and figure state never run in a Flask request thread. Job results are published
only after successful completion. Only completed, manifest-listed artifacts can
be downloaded. Raw observation inputs and their metadata are not rewritten.

The API supports configuration discovery, observation listing/opening, starting
and cancelling processing jobs, polling progress, cached reanalysis and artifact
download. The browser exchanges metadata and PNGs with this local service;
recorded spectra remain on the workstation.


## Catalogue totals and connection status

The directory catalogue reads metadata only, without loading spectra. It lists
newest observations first. Completed counts and integration times use only
physical scans whose status is COMPLETE; integration time is the sum of their
recorded scan durations, including SKY and calibration types. Planned observation
totals and incomplete scans are not included. An unreadable metadata file appears
as an unavailable row and does not prevent other observations being listed.

The badge displays **Local OPT connected** while the backend responds. A
lightweight health check runs every five seconds (with a four-second timeout).
On a failed connection, it turns red and reads **OPT Disconnected**. It returns
to connected automatically when the service responds again. A temporary outage
pauses job-status polling without discarding the workspace; when the same service
returns, polling resumes. Restarting the backend still clears its in-memory job
registry, as noted above. The Results heading is **Observation Processing**;
left tabs are **Input scans** and **Aggregate scans**, and the right view tabs
are **Signal**, **Velocity**, **Total Power**, **L-V Diagram**, **Aggregate** and
**Details**.

## Config → Dishes

The Dishes tab now uses the standalone WEB application's profile and dish APIs.
Run the combined WEB application with the desired starting profile and sign in
at `http://127.0.0.1:8765/#admin`. Its server-side bridge supplies the API token;
the browser does not need `WEBHOOK_SECRET`.


Open **http://127.0.0.1:8765/#admin** and choose **Dishes**. The combined console
connects automatically to WEB's API listener. The existing OPT Results view uses
its own configured data roots.

The shared profile selector above the Config tabs lists WEB's available configuration profiles. Changing it
updates WEB's shared resource profile, including its default observation search
directory; it does not switch the profiles of running telescope applications.
The dish table shows actual configured dishes, supports filtering, and opens the
first dish automatically. Click a dish row to edit it, or focus the row and press Enter or Space. The form exposes dish
geometry and location, feed, linked digitiser/weather station IDs, driver type,
polling period and typed driver settings for MD01, DRIFT and MOTION.

Use **New dish** to enter a unique ID and configuration, then **Create dish**.
Existing dish IDs cannot be renamed. **Save dish** validates and persists edits;
**Cancel edits** resets the form. **Delete dish** removes the selected stored
definition after confirming its ID and profile; any unsaved edits are discarded
only after that confirmation. The button is hidden while creating a new dish. Unsaved edits are marked and require a discard
confirmation before switching profiles, choosing a different dish, refreshing or
starting a new dish. Leaving the page also warns about unsaved changes. Navigating
to another console tab keeps the form in memory.

Saves use the file's ETag and an `X-Config-Profile` precondition. If another editor
changes the file, or another session changes WEB's selected profile, the save is
rejected and the draft is retained for review. **Refresh** loads the current
profile and disk version after a discard confirmation. A lost connection during a
save leaves its outcome uncertain: refresh to check the stored record before
trying again; the console does not automatically retry saves or deletions.

Profile edits are file-backed and do not need TM or Dish Manager running. They
take effect in Dish Manager on startup or a successful RESYNC under its existing
safe-state rules. Creating a dish definition does not start hardware or issue a
live pointing command. Weather/defaults/limits frontend tabs remain indicative
prototype views; their editors have not yet been connected.

The browser calls the console's same-origin `/api/web/` bridge. That bridge only
forwards the profile, dish and digitiser operations used by these editors, supplies WEB's token
on the server, preserves ETags and errors, and rejects cross-origin writes. The
WEB address is configured at service startup; browser requests cannot choose an
arbitrary upstream URL. Credentials are not embedded in HTML/JavaScript, sent to
the browser or saved in browser storage. A static-file server alone cannot supply
these authenticated API operations.


## Config → Digitisers

The Digitisers tab uses the same WEB connection and shared configuration profile
as Dishes. Select **Digitisers** to load actual definitions from the active
profile's `DigitiserList.json`. Click a row, or focus it and press Enter/Space, to
view and edit it. The list filters by ID, host or SDR type. **New digitiser** opens
a blank definition with model defaults; **Create digitiser**, **Save digitiser**,
**Cancel edits** and confirmed **Delete digitiser** follow the dish editor's
workflow. Existing IDs cannot be renamed.

The form covers local/command hosts and command port, SDR type, gain, sample rate,
bandwidth, centre frequency, frequency correction, channel count, bandpass filter,
GPIO power/load pins, temperature sensor and maximum temperature. Blank optional
hosts, command port, temperature limit or GPIO pins map to null. Supported
SDR/filter/sensor names follow the existing digitiser model. Runtime scan state,
connection status and EEPROM readings are not editing controls.

SDR and sensor options use JSON-object editors because their available keys depend
on the selected driver. Values merge with existing stored options; switching SDR
or sensor type clears the previous type's options. Selecting a different filter
clears its GPIO configuration. SAWbird H1 Barebones GPIO controls have an explicit
enable checkbox; blank pins disable the corresponding control. Invalid JSON,
arrays and non-finite numbers are rejected before saving, while unfinished input
still counts as an unsaved draft.

Switching between Dishes and Digitisers keeps each draft in memory. Changing the
shared profile or using **Refresh** checks unfinished edits in both editors before
discarding them. After selection, only the active resource list is loaded; other
editors load their new profile when opened. Version and profile conflicts keep the
draft and do not automatically retry the operation. Credentials remain in the
local UI service; its restricted bridge now permits digitiser CRUD operations too.

Saving creates/updates profile configuration rather than opening an SDR or starting
a digitiser application. Existing digitiser hardware and sensor settings load on
a successful idle RESYNC; some identity/network and process settings require a
restart. Creating a definition does not add a launcher entry or change a running
application automatically. Restart WEB after upgrading its console
bridge, then refresh the browser. WEB's existing digitiser APIs are unchanged.


## Status → Application details

The Status view reads `GET /api/telescope/model` from WEB through the local
console's authenticated `/api/web/telescope/model` bridge. WEB requests the full
serialized model from TM immediately on connection and every 30 seconds using
its event/timer framework. Browser sessions share that cache; selecting an app or
refreshing the frontend does not send another query to TM. The browser checks
WEB's cache every five seconds. The old generated `data/status.json` is no longer
the Status view's data source.

The application table lists TM, DM, SDP, WS, WEB and every digitiser, with health,
state, last report time, availability, reliability, tracing, debug and command controls.
Click a row, or focus it and press Enter/Space, to open its detail pane. The pane
appears at full width below the application table on all screen sizes.
It retains its selected app through refreshes and can be closed with its X button.

Digitiser details show a desired/actual tuning table on the left and compact
driver/status information on the right. Set one field or use Apply settings for
load, gain, centre frequency, sample rate, bandwidth, frequency correction,
channels and scan duration. Scanning has its own ON/OFF control. Scientific
notation is accepted for numeric inputs. Desired values and dated responses
stay with each digitiser across refreshes and app switches; Use actual values
resets the draft to the current reports. Channels and scan duration use SDP's
reported digitiser configuration when available.

Live edits use `POST /api/digitiser-control/<dig_id>`, forwarding the normal
`alston-rt.ui.dig` webhook with `dig_id` and the requested fields. WEB's existing
configuration route puts a DIG ConfigEvent on TM's queue. A successful response
acknowledges scheduled actions; Actual changes only when reports arrive. This
does not edit saved profiles. Operator/admin access and CSRF are required;
stale or disconnected TM snapshots disable writes, pending requests prevent
duplicate sends, and transport failures have an unknown outcome without an
automatic retry. Restart WEB and refresh the browser to load these controls.

Beside Command endpoint, choose a command and press Send to target this app
(including its specific digitiser ID). Choices include RESYNC, TRACE/DEBUG
?/ON/OFF and commands from WEB's installed command registry, such as DM STOP
with a dish ID. Commands with values show a value field. Dated responses remain
with the app through polling and app selection changes. Sending requires operator
or admin access; pending sends prevent duplicates and failures are not retried.
The private UI's `GET /api/commands?app=<type>` supplies the command choices;
`POST /api/commands` validates the choice and uses the existing WEB command path.

Details include processor count, queue size, interfaces, connections and
endpoints, processor names/current events/processing times, app-specific fields such as dish states, SDR settings, scan counters, weather
driver state or WEB worker settings. Missing values appear as unavailable;
zero-valued percentages/counters and false tracing/debug values remain visible.
Times are labelled UTC. The displayed live profile is TM's running profile, not
the independent resource profile selected under Config.

For DM, Entity Connections lists MD01 controllers and network IMU endpoints with
configured host/port, DM's Client role, and last successful communication in UTC.
These drivers open connections per exchange. `DishModel.last_comms_success_dt`
records a decoded controller response and remains unchanged on later failures;
it is separate from the dish's general `last_update`. Older snapshots or dishes
without a recorded successful exchange show a dash. Every dish is listed; DRIFT
dishes show Not Applicable because they have no network controller.
Restart DM and TM after this model update, then
refresh the browser; normal status advice carries the new field through TM.

For WS, compact connection and driver diagnostics sit above a wind dashboard,
with Weather Attributes on the left and a larger Wind Speed chart on the right.
There is no Entity Connections pane, generic latest-reading section or raw
weather table. The chart uses DM's `WeatherStationList.weather_history`, with
average/gust threshold lines and UTC timestamps. Rolling summaries show the
actual wind sample count, average, maximum and RMS. Summaries are time-windowed
(`summary_window`, default 60 seconds); they do not use a fixed sample count.
DM retains instantaneous readings for `retention_period` (default 300 seconds)
and forwards at most 300 compact readings per station, evenly sampled when
needed. Alarm calculations and summaries continue to use the full-resolution
local data. `weather_metrics` supplies per-station actuals calculated by DM over
the alarm window (`threshold_timeout`, normally 30 seconds), including age,
wind average, gust maximum/count, sample count and alarm flags. These actuals
are separate from the normally 60-second rolling summary and never calculated
from downsampled graph points. The displayed age is age at DM's reported update;
timeout warnings use DM's timeout flag, avoiding false alarms from cache latency.
Shared DM alarm history is available under a collapsed trailing-month section.
Missing readings are labelled. Restart DM and TM after the model update and
refresh the browser to load the weather chart. History comes through the normal
TM snapshot cache and does not add a separate polling loop.

The snapshot timestamp describes when TM assembled its latest known model;
individual app timestamps describe their last reported state. The snapshot is
not a synchronized fresh poll of every application. On a timeout, disconnection
or old cache, the console retains the last model with a visible stale indicator.
Before the first successful snapshot it shows an unavailable/waiting state.
Credential-labelled fields are redacted before the model leaves TM. The pane is
read-only and does not send commands or change live application state.

Restart TM and WEB after installing these changes, then sign in at
**http://127.0.0.1:8765/#status**. The combined WEB process supplies the API token
to its local console bridge.

The top-level **Observation Executions** widget shows the number of observations
in progress and lists every active observation's ID, dish and execution state.
CONFIGURING, READY and SCANNING count as active; IDLE observations waiting for
resources, unresourced observations and aborted/faulted observations do not.
The widget uses the same cached TM snapshot as application status and supports
multiple simultaneous executions. Missing execution data is shown as unavailable.

The application detail pane omits row-level health, state, update time,
availability/reliability and tracing/debug. The complete last error appears first
in the detail pane, prefixed with its recorded UTC date/time.
Details concentrate on processors, queues, interfaces, connections and
application-specific diagnostics; the heading identifies the selected app.

The suite version appears beside **dmd2000** in the console header and mobile
drawer, rather than in each app detail pane. It comes from WEB’s installed
version/build via the authenticated session endpoint and remains available when
TM is offline.

## Application logs and HTML traces

In **Status → Applications**, click the value in **Debug** to open the current
application log, or **Tracing** to open its HTML trace. Both On and Off are links;
viewing a file never changes either setting. The application details close when
a file pane is opened. Log and Trace may both remain open, with Log above Trace.
Each pane identifies its application and has its own **X**, **Refresh** and
**Download** controls. Clicking another application row returns to its details
and closes the file panes.

File contents load on demand. The five-second status refresh does not reload
files, reset expanded trace messages or move the file viewer's scroll position.
Use the pane's Refresh control to read new content. HTML traces retain their
expand/collapse controls and run in an isolated iframe without access to the
console session. The HTML document is loaded using a temporary object URL for
Chrome/Safari compatibility; closing/reloading the pane releases that URL.

The selected WEB configuration profile supplies `log_dir`. The pane displays the
profile, relative filename and modification time in UTC. WEB must have filesystem
access to this directory, including shared storage for applications on other
machines. Only current `app/*.log` and `trace/*.html` files selected by application
are available; archives are not yet browsable. No arbitrary filesystem paths are
accepted. The private UI requires login (viewer access is sufficient); these
file routes are not available on the public API listener.

Large logs show the latest 512 KiB, with Download providing the complete file.
Trace previews support files up to 32 MiB; larger traces remain downloadable.
Application traces use `trace/<entity-id>.html` when the launch entity ID is
defined, otherwise `trace/<app>.html`. Digitiser application logs
currently share `app/dig.log`; this is labelled in the log pane. A missing trace
is reported clearly and may mean tracing has not produced a file, or an older
application process still needs restarting to adopt the entity-specific filename.

Restart WEB after installing the new file endpoints, then refresh the browser.

## Tracing and debugging controls

Each application's Tracing and Debug cells contain an On/Off file link and a
separate switch. The links open traces/logs; the switches change the setting.
Operators and administrators can toggle; viewers can read files but their
switches are disabled. The server also enforces permissions and CSRF protection.
There is no Command dropdown or RESYNC control in the Status table.

Each click sends one TRACE or DEBUG ON/OFF command through WEB's existing command
webhook/TCP framework. Controls are disabled while a command is pending. A
successful response updates the switch immediately and is reconciled only when
a non-stale TM snapshot includes an application report newer than the successful
command acknowledgement; failures retain its last confirmed state. Status
polling never resends commands. The response area below Applications shows the
app, UTC response time, status/error code and message. Missing responses report
an unknown outcome without retrying.

## Config → RESYNC

An **App** selector and **RESYNC** button sit beside the Dishes/Digitisers/Weather
subtabs. Choose TM, DM, SDP, WS, WEB or a digitiser from the selected profile, then
press RESYNC. The button turns amber while waiting, green after an application
success response, or red on failure. The adjacent message shows the application,
UTC response time, status/code and response text. Changing the app clears the
previous result. Network failures report an unknown outcome and are not retried.

Save configuration changes before RESYNC; unfinished edits disable the button.
Pending RESYNC also locks profile switches and editor operations until it
returns. Operators/admins can send it; viewers see disabled controls. RESYNC
reloads the target application's own launch profile and respects its existing
safe-state rules. Selecting a different resource profile does not switch a
running application's launch profile.
