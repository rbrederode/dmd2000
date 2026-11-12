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
   refreshes the list. Results uses the expandable saved-observation layout formerly
   shown under Schedule: ID, title, dish, saved completion status, scheduled UTC
   start/end, observing duration, creation UTC and creator contact. Filter by name,
   ID, dish or scan type. Expand a row to load its saved scans, including type,
   status, digitiser, tuning, sample times, Signal preview and SPR download.
   Reviewing a row does not select it for processing. Select its radio button to
   prepare **Input scans**, then use **Process observation**. There is no separate
   Open observation button. The catalogue stays expanded for further review.
   OPT reads the usual `<id>-obs.json` and scan products; an Observation Velocity
   CSV alone is not a processing input. Metadata loads are cached per directory;
   failed reads offer Retry, and late responses from an old directory are ignored.
2. In **Input scans**, use the checkboxes to include/exclude physical scan
   iterations. Selecting a row inspects it without changing its checkbox.
   Search, type filtering, Include/Exclude all, Previous/Next and arrow keys
   help navigate the list. Metadata scans not marked COMPLETE start excluded
   and their checkboxes are disabled.
3. Choose the working **Profile** in the always-visible page header.
   This selects the processing pipeline steps; changing it requires a full
   reprocess rather than a cached plot update. Click **Process observation**. A background process runs the accepted batch
   without terminal prompts, partial aggregate displays or GUI popups. Progress
   and an expandable log remain available. **Cancel** terminates the worker.
4. On completion, **Aggregate scans** lists complete load/calibration and SKY results
   by target, frequency scan and digitiser. Click an aggregate for its full
   signal display. Incomplete aggregates are omitted with a warning.
5. Switch directly between **Signal**, **Frequency**, **Velocity**, **Total Power**,
   **L-V Diagram**, and **Details**. L–V shows observation-wide results. Total
   Power defaults to **All SKY aggregates**; uncheck its box to follow the selected
   aggregate, including an individual calibration aggregate if desired. Frequency shows only the frequency spectrum, with no duplicate Total
   Power panel. Its **All SKY aggregates** box defaults to checked; uncheck it
   to follow the selected SKY target/frequency aggregate on the left. Velocity
   defaults to the selected SKY aggregate and has its own independent **All SKY
   aggregates** box. Calibration rows have no Frequency or H I Velocity view.
   **Open image** opens the full-resolution PNG. Calibration rows have a signal
   view but no H I velocity spectrum.
6. Change baseline limits/type or contour levels/smoothing, then **Update plots**
   to re-render velocity/L–V views from cached native spectra, without loading
   and processing the recorded files again. The selected completed aggregate
   remains selected. Changing the input exclusions or processing profile requires
   **Reprocess observation**, which reruns the scientific pipeline.
7. **Export results** downloads a ZIP with completed plots, CSV products, native
   spectrum caches, the processing settings and a manifest.

Velocity plots also place the legend outside on the right. Large overlays use
multiple columns, and the PNG expands for the legend without shrinking the data
axes. This applies to initial processing and **Update plots**.

Total Power has its own **All SKY aggregates** checkbox, checked by default.
Uncheck it to view the aggregate selected in the left-hand list; changing rows
switches the image immediately without starting another processing job. Both SKY
and calibration aggregates have individual Total Power plots. The **Resolution**
selector offers **Per scan** (default) and **Per second**. Both use the same
pipeline-processed physical-scan readings: Per second plots every recorded
second, while Per scan averages those readings within each scan. It does not
expand scan averages into invented second-by-second samples.

The seconds axis uses elapsed observation time when every contributing scan has
a recording timestamp, including real gaps between recordings. Lines are not
joined across physical scan boundaries. If timestamps are missing, the axis is
explicitly labelled recorded time with gaps excluded. Per-scan indices start at
one for each target/frequency/digitiser group.

Full processing exports `aggregate-NNNN-timeline.png` and
`aggregate-NNNN-timeline-seconds.png` for each aggregate, with `timeline.png` and
`timeline-seconds.png` for all SKY aggregates. `total-power-seconds.csv` and
`total-power-scans.csv` export the matching readings and means, including scan
IDs, recording timestamps where available, digitiser and scan kind. The existing
`*-sky-apr.csv` remains the separate synthesized-spectrum power export.
Changing resolution or selection uses existing images immediately. **Update
plots** preserves both modes and their CSVs; interactive spectral baseline
adjustments do not change these pipeline total-power products. Older completed
runs need one **Reprocess observation** to generate per-second products.

Spectrum and Total Power legend entries consistently use **Tgt x, Freq y**,
matching the Velocity legends.

The Total Power plot places its legend outside the data axes on the right.
Legends use up to 16 entries per column (two columns for 31 scans), and the
exported PNG extends to include the legend without compressing the data area.
Repeated colours use different line styles to distinguish larger scan sets.
Regenerate this cached product with **Reprocess observation** after an update;
**Update plots** retains the existing Total Power image.

The Frequency spectrum places its scan and diagnostic legend outside
the plot on the right, using multiple columns for larger scan sets. It uses
absolute MHz tick labels across the full
sampled frequency span, rather than centre-frequency offsets or a crop to the
requested bandwidth. Flagged samples remain masked and bandpass exclusions are
shaded. Frequency channel centres use the same FFT grid as the velocity view.
The worker exports `frequency.png` for the all-SKY view and
`aggregate-NNNN-frequency.png` for each SKY aggregate. Switching the checkbox or
selected row uses existing images without a processing job. **Update plots**
regenerates both individual images and the all-SKY overlay from the same corrected
spectra when baseline or MHz limits change. The overlay omits SNR/noise panels
and median-baseline lines. **Reprocess observation** is required to generate them
for a run created before this feature was added.

Individual Frequency spectra are generated directly from native channel
frequencies and the processed aggregate powers. They do not require pointing
metadata or a successful Velocity plot. Frequency-only observations cache their
powers, frequencies and flags for baseline/range updates. LSRK coordinates and
resolved Galactic direction are cached only when available. Velocity, L–V and
LAB comparison require the corresponding observing metadata; unavailable views
show the recorded reason rather than suggesting that no aggregate was selected.
Runs created before this separation need one reprocessing run to generate the
missing individual Frequency images and native cache.

Plots remain visible while a new job runs. Changed settings/exclusions show an
out-of-date banner. A failed or cancelled job does not replace the last completed
results. The workspace remembers directory, profile, analysis settings and scan
exclusions in browser-local storage; a page reload does not currently reconnect
to previous running/completed jobs automatically.

### LAB survey comparison

After processing, select one completed **SKY** aggregate and open **Velocity**.
Leave **All SKY aggregates** unchecked, then enable **LAB survey**. The view
compares the selected spectrum with the LAB survey at its cached, resolved
Galactic coordinates. **Beam FWHM [°]** defaults to 5 degrees and can be changed
(up to 60 degrees). It uses the LAB profiler's Gaussian extraction, paired survey
channels and normalized shape scoring. The nominal comparison uses zero coordinate offset; optional sweeping is
described below. This view does not estimate terminal velocity.

To search for a closer spectral match, use **Sweep LAB coordinates** (enabled by default) and set
minimum, maximum and step for both **Δl** and **Δb** in degrees. These are offsets
from the aggregate's resolved Galactic direction. Defaults are -10 to +10 degrees
in 1-degree steps for each axis (441 candidates). Select **Find best fit** to
start the background search; the existing progress and Cancel controls apply.
The plot updates to the candidate with the lowest normalized shape RMS within
the selected velocity limits, showing its Galactic coordinates, offsets, RMS
and correlation. Baseline correction and beam width remain fixed across the
search. Longitude wraps through 0°; candidates outside valid latitude coverage
are skipped. The complete candidate table is exported as `*-lab-sweep.csv`.

Ranges of -10 to +10 at 0.5-degree steps are supported (1681 candidates); the
maximum is 2500 candidates per request. A winning point on a sampled range edge
is marked in the plot and warnings so the range can be widened. This is a grid
minimum, not a calibrated pointing solution or confidence interval. Uncheck
**Sweep LAB coordinates** to regenerate the nominal-coordinate comparison.

The observation uses the current analysis baseline and is peak-scaled to LAB
for this comparison only, with that scaling stated in the legend. The ordinary
velocity view and exported calibrated powers retain their original amplitudes.
Uncheck **LAB survey** to return to that view. Selecting another aggregate clears
the checkbox; enable it again for the newly selected SKY aggregate. Calibration
and **All SKY aggregates** views do not offer LAB comparison. Changing analysis
settings and selecting **Update plots** regenerates an enabled comparison.

LAB extraction runs in the existing background worker using cached spectra,
without replaying observation recordings. The resulting comparison PNG, LAB CSV
and JSON metadata are included in **Export results**. **Open image** opens the
comparison at full resolution. Missing survey data or unusable spectra produce
a warning while retaining the ordinary analysis products.

The server reads `~/github/lab-fit/lab.fit` by default, matching the profiler.
Set the server environment variable `DMD_LAB_FITS` to use another local cube.
The cube must be the Galactic CAR/LSR FITS format supported by the profiler;
no network download is performed.

Analysis controls apply consistently to initial processing and **Update plots**:

Individual-aggregate Frequency and Velocity plots show peak SNR (ratio and dB),
Robust Noise (1.4826 × MAD), median baseline and the noise windows/sample count
alongside the legend. The median baseline is also drawn as a labelled dashed line.
These share pipeline QA's smoothed-peak location, peak-exclusion noise windows
and median/MAD estimator. The pipeline's window fraction and smoothing settings
are retained with cached spectra for consistent interactive updates. LAB
comparisons show the same diagnostics in scaled plot
units and export them as `observation_noise` in their JSON. Unavailable estimates
are labelled explicitly. Changed baseline or range settings recalculate the
metrics during **Update plots**. All SKY aggregates views omit the diagnostic
annotations, including when only one SKY aggregate exists, and retain their
curve legends, bandpass exclusions and RFI markers.

| Control | Effect |
|---|---|
| Configuration profile | Selects pipeline configuration; requires **Reprocess observation** |
| Baseline | Linear or constant subtraction, flatten retaining the lowest fitted level, or no subtraction; fits automatic outer windows for single-SKY Frequency, Velocity and L–V |
| Velocity minimum / maximum | Velocity line-plot limits, L–V vertical limits and automatic baseline selection bounds; blank defaults to available recorded channels |
| Frequency minimum / maximum [MHz] | Single-SKY Frequency display and baseline bounds; blank uses the available frequency span |
| Contour smoothing | Velocity Gaussian FWHM for L–V contours only; 0 disables smoothing |
| Contour levels | Increasing positive calibrated-power levels for L–V contours; blank selects automatic levels |

Settings that do not apply to the active tab are disabled and greyed out.
Velocity enables the baseline and velocity limits. L–V enables the baseline, velocity limits and
contour controls. Its velocity limits remain available with **No subtraction**
because they set the vertical display range. Signal,
Total Power and Details disable these analysis controls and
**Update plots**. The configuration profile remains available for reprocessing.
Disabled controls retain their values when switching tabs.
Velocity initially shows the entire recorded spectrum, including grey bandpass
exclusions and red RFI markers at their transformed channel coordinates. Blank
limits are resolved per aggregate for baseline fitting and across all displayed
aggregates for an overlay. Enter limits to narrow the view and fitting interval;
clear them to return to the full range. Obsolete saved -100/+100 defaults are
reset on the first load of this version; subsequent explicit bounds are remembered.
The cache now retains channel flags. Previously processed jobs can still be
updated, but need one reprocessing run to display these annotations.
Frequency enables Baseline and its MHz minimum/maximum only when **All SKY
aggregates** is unchecked and a SKY aggregate is selected. The MHz fields sit
below the velocity-limit fields. Velocity limits remain disabled on Frequency;
contour controls remain disabled there as well. The all-SKY Frequency overlay
shows the same corrected powers and MHz limits as the individual plots from the
last completed update. Switch to an individual SKY view to edit these settings.

For single-aggregate Frequency, blank MHz limits use the full available channel
span. Baseline fitting uses the outermost 20% of valid channels in each half of
that frequency interval (split at its midpoint), with the same symmetric-clipped
linear/constant/flatten method as Velocity. No subtraction retains the processed aggregate powers.
The fit is evaluated across the native spectrum and the display is cropped to
the requested MHz bounds. Frequency and Velocity use independent fitting ranges;
their corrected powers can therefore differ. Neither correction mutates the
original cached data. `velocity.csv` continues to use the Velocity baseline.
Previously completed jobs need one **Reprocess observation** to cache observed
frequency channels; subsequent Frequency adjustments use **Update plots**.


Automatic baseline windows use the outer 20% of valid channels on each side of
zero within the selected limits, with symmetric outlier clipping. Linear and
constant modes require limits spanning zero. No subtraction bypasses baseline
selection. Bounds crop the L–V vertical display to the selected interval within
the common valid data coverage. The shared colour scale and automatic contour
levels use visible channel cells, excluding offscreen extremes. Bounds do not
truncate exported CSV data or change the native spectra. Baseline controls affect derived velocity/L–V plots and `velocity.csv`;
the saved Signal display is unchanged. Updated exports contain the new settings
and CSV values. Saved browser preferences preserve previously selected limits.
**Update plots** becomes available after processing and changing an analysis
setting; a profile or scan-selection change requires full reprocessing instead.

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
are **Signal**, **Frequency**, **Velocity**, **Total Power**, **L-V Diagram** and
**Details**.

## Config → Dishes

The Dishes tab now uses the standalone WEB application's profile and dish APIs.
Run the combined WEB application with the desired starting profile and sign in
at `http://127.0.0.1:8765/#admin`. Its server-side bridge supplies the API token;
the browser does not need `WEBHOOK_SECRET`.


Open **http://127.0.0.1:8765/#admin** and choose **Dishes**. The combined console
connects automatically to WEB's API listener. The existing OPT Results view uses
its own configured data roots.

The shared profile selector in the page header lists WEB's available configuration profiles and also selects the Results processing profile. Changing it
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


## Schedule → Queued and running observations

Schedule reads the same authenticated `/api/web/telescope/model` cache as Status.
It shows observations in EMPTY or IDLE that have not completed or passed their
scheduling window, and current CONFIGURING, READY and SCANNING executions.
Current executions appear first; each group is ordered by scheduled start UTC.
The table shows approximately ten observations before scrolling, with a sticky
header and horizontal scrolling for narrow screens.

Columns match the queue overview: ID, title, dish ID, reported status, scheduled
start/end UTC, estimated observing duration (HH:MM:SS), creation UTC and creator
contact. Click a row, or activate its ID button with the keyboard, to expand all
scans prepared by TM. The scrollable scan list includes target/frequency/iteration
indices, target name, type, reported status, digitiser, centre frequency, gain,
duration and first/last sample UTC. The current scan is highlighted without
replacing its reported status. Before TM prepares scans, an explicit message
appears instead of an invented scan plan.

The browser checks the shared cache every five seconds; WEB requests new TM
snapshots every thirty seconds. Refresh rereads that cache. Expanded observations,
keyboard focus and scroll positions survive updates. Stale/unavailable data are
labelled, and the last successful snapshot remains visible during outages.

Completed observations can remain IDLE in the model, so completed scan lists and
the execution target cursor are also checked before including an observation.
Saved observations are now browsed in **Results**, using the selected observation
directory, independently of WEB's active resource profile. Schedule contains
only the live queue and does not poll the disk catalogue. Results loads scan
metadata on expansion; selecting a radio button prepares processing without an
Open observation step. The completion presentation comes from saved metadata,
so an archived SCANNING state does not establish that a service is currently
running. Failed metadata reads offer Retry. Changing directories clears cached
scan details and prevents late responses from populating the new selection.

Each COMPLETE scan also has a **Signal** icon immediately before SPR. It opens
the saved `*-sigfig.png` final-sample signal display in a modal preview with a
**Download PNG** link. Close or Escape dismisses the preview. The image is
loaded on demand; polling does not reload an open preview. Missing images are
reported in the dialog without rerunning signal processing. The same archive,
profile and authenticated file-access rules as SPR downloads apply.

Each COMPLETE scan has a small download icon in the rightmost SPR column in
Schedule's live details and Results' saved details. Signal and SPR in Results
read the exact saved observation from the chosen processing directory through
`/api/opt/observations/<file-id>/scans/<target>/<frequency>/<iteration>/<product>`
with `directory=...`, where product is `signal` or `spr`. These read-only routes
apply the configured data-root checks, completion checks and archive path/symlink
restrictions; they do not depend on WEB's resource profile or a TM connection.
Schedule's live file actions continue through the authenticated WEB bridge.
Missing files produce errors without rerunning acquisition or processing.

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
does not edit saved profiles unless Save Pipeline Configuration is selected. This button saves the pipeline to the selected profile and updates Reset to Config Profile. Operator/admin access and CSRF are required;
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
individual app timestamps describe their last reported state. New app models
start with `last_update: null`, displayed as `—` until populated by an app
status update. An explicitly null app timestamp never falls back to a parent
model creation time. Explicit timestamps loaded from saved configuration are
preserved; these are historical values rather than proof of recent contact. The snapshot is
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


The L–V longitude axis increases towards the left, with longitudes above 180°
labelled as negative (359° appears as -1°). Nearby pointings across 0° remain
adjacent. Display coverage follows the observed longitudes; an observation that
only samples positive longitudes does not add an empty negative-longitude half.


The **Processing pipeline** panel below the observation summary loads the
selected profile's ordered steps. Enable/disable steps and choose a baseline
method directly; expand the JSON editor for parameters and ordering. Edits are
per-run and require **Reprocess observation**. **Reset to profile** discards
edits; no profile files are written. Results include `pipeline-config.json`.
A selected aggregate indicates when pipeline baseline subtraction was applied.
Interactive No subtraction restores the processed aggregate, including any
pipeline corrections. See [baseline configuration](../../../docs/observation_baseline.md#optional-processing-pipeline-baseline).


### Working profile and application launch profiles

The single **Profile** selector in the header controls WEB's file-backed
configuration/resources and the Results processing pipeline. Config and Results
have no separate selectors. It loads on any starting tab, checks unsaved Config
edits before switching, and is disabled during OPT processing. Results uses the
accepted WEB selection, not an independently remembered processing profile.
Existing plots and exports remain available after changing profile; reprocessing
is required before they represent the newly selected pipeline. Results initially uses the selected Config Profile’s `scan_store_dir` and
resets to that directory on each profile switch, refreshing the observation
list and clearing its previous input selection. Users may override the
directory afterward. A profile without `scan_store_dir` uses the first
permitted OPT data root. Existing directory-access restrictions still apply. Profiles
without a PipelineConfig.json remain usable for configuration, but processing
is disabled for them.

The Status applications table separately shows **Launch profile**, taken from
each application's reported `app.arguments.profile`, or — when unavailable.
No running profile is inferred from the header, TM profile or another app.
Stopped entries show the last reported launch profile when available.

Changing the header does not switch running applications. RESYNC reloads each
application's own launch configuration. Restart an application with a different
`--profile` to change its running profile. WEB's explicit `--profile_dir` override,
when used, still determines its launch configuration directory.


The pipeline's **Pipeline Steps** section provides parameter fields beside each
step. Context and supported methods/estimators use dropdowns; numeric settings
use number inputs. BANDPASS_FILTER and BASELINE expose minimum/maximum
percentage pairs with Add range and removal controls. RFI_FLAG exposes its
sigma threshold and odd channel-window size. QA exposes its exclusion-window
fraction, smoothing channels and noise estimator. Existing additional JSON
parameters receive controls based on their value type. Optional default values
are displayed without inserting them into the run configuration until edited.
Invalid numeric values/ranges block processing and show an error. The advanced
JSON editor remains available for adding, removing, or reordering steps and
for digitiser-specific configurations; those edits refresh the inline fields.


The pipeline list shows all implemented built-in steps, supplied by OPT's
`available_steps` catalog. Unconfigured rows are unchecked with disabled
parameter controls. Check a row to add it to that digitiser's run pipeline;
new stages are inserted before later stages, preserving existing step order.
Unchecking a configured row preserves its parameters with `enabled: false`.
Merely displaying an unconfigured row does not change the JSON or run settings.
The dedicated Add CAL baseline step button has been removed. GAIN_CAL and
TSYS_CAL currently contain placeholder implementations and are not offered
as working calibration steps; existing explicit profile entries are retained.


Pipeline controls use a table with **Enabled**, **Step**, **Context**, and
**Parameters** columns. Rows are grouped spr, cal, then mpr; configured step
order is retained within each context and missing steps follow configured
ones in that context. This display grouping does not reorder the configuration
JSON. Edits continue to target the original configured entry, including
repeated steps in different contexts. Step names use readable sentence case,
and all table text uses the shared section text size.


Each pipeline step has a question-mark button that displays the implementation's
description in a help panel to the right of the table. Help is available for
unconfigured/disabled steps and read-only viewers without changing any settings.
The selected question mark stays highlighted while parameters are edited.
On narrower screens the help panel sits below the table.


The pipeline help panel uses a wider right-hand column and includes an overview,
parameter-by-parameter explanations, and usage notes. Content comes from
`ui.pipeline_help` through `available_steps[].help` in the OPT configuration
response. Help is displayed as escaped text. The RFI threshold is explicitly
labelled as a MAD multiplier, matching its implementation.

The help panel scrolls only when its content exceeds the remaining height
alongside the steps table. Its height limit follows table resizing and
expansion; on narrow screens, stacked help keeps its natural height.


The help panel initially shows a general pipeline guide defining SPR (summed
power spectrum), CAL (calibrated spectrum), and MPR (mean power spectrum).
It explains stage invocation, passing values and flags between steps, averaging,
and when pipeline changes versus interactive plot adjustments apply. It also
clarifies that saved SPR input does not replay spr-only steps. The question
mark beside **Pipeline Steps** restores this overview after viewing an
individual step. The guide is supplied as `pipeline_overview` in OPT's config
response and does not modify processing settings.
