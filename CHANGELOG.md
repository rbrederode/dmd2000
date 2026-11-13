# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- SDP now matches pending scans by observation, target and frequency-scan identity as well as radio settings and read-counter range. Target/configuration changes retire unfinished scans from the previous identity without completion advice; late ODT packets cannot modify the current target. Configuration and sample handling share the per-digitiser lock, while completed scans, LOAD calibration data and other digitisers are preserved. Pending-scan counts account for retired scans across digitisers, and manual scans retain their existing metadata compatibility. This fixes cross-target sample assignment; overload handling and retry limits are unchanged.

- Downgraded rtlstream/airstream sample-age threshold exceedances from hardware errors to warnings. Reads continue with their original timestamps, sample age and dropped-sample metadata, avoiding observation aborts caused solely by buffered age. Actual read, producer and reset failures remain errors.

- Column headings remain visible while scrolling Engineering Status, dish inspection, Configuration, Schedule and Results catalogue tables, including the corner heading for tables with a frozen first column.

- Results connection messages now say OPT connected and refer to the OPT service without the ambiguous Local prefix, distinguishing service availability from the separately selected Config Profile.

- Stale snapshot and refresh-failure messages use the amber warning badge styling consistently in Engineering Status, Schedule and dish inspection, including when TM remains connected without providing updates. Healthy snapshot messages return to their normal muted appearance.

- Engineering Status and Schedule hide the TM snapshot badge during normal operation; it remains visible for stale snapshots, disconnections and refresh failures.

- Schedule and Engineering Status now use the same snapshot information message, including received age in seconds, polling interval and stale/error details.

- Fixed Engineering Status Refresh to request and await a fresh TM snapshot, matching Schedule Refresh. The snapshot timestamp and received age now reflect the new response; the button shows a waiting state and failed requests mark retained data stale.

- Moved Schedule observation controls to the right of Created by Contact and styled them with the same magenta primary-button colour as New observation.

- Schedule Refresh now requests and waits for a fresh TM snapshot rather than only rereading WEB’s cache. It updates the shared cache and displays a waiting state; failed refreshes retain the previous data as stale and report the error. Background polling continues to read the shared cache.

- Added Schedule controls to delete unstarted observations, abort executing observations and restart aborted observations from the first scan before their scheduling block ends. Aborted observations remain in the live schedule until that deadline. Controls validate the live TM state, retain saved files on deletion and request an immediate shared TM snapshot to confirm changes.

- Documented the DMD2000 release procedure, including changelog promotion, work-branch merges, validation on main and the final release commit, VERSION updates, retained per-version branches, annotated tags, GitHub publication and maintenance fixes merged back without downgrading main's release metadata.

- Added a scan-completion progress bar beside the expanded Schedule observation's `Scans — x of y complete` heading. It counts completed physical scans and refreshes with the shared TM snapshot, including zero-progress and completed states.

- Dish inspection now formats PEC altitude/azimuth RMS and altitude/azimuth velocities to three decimal places. Site latitude and longitude retain their existing precision.

- LAB survey and terminal-velocity option changes now update only the selected aggregate when shared analysis settings are unchanged. Other aggregate and observation-wide plots are preserved; progress identifies the selected work instead of cycling through every spectrum. Results are cached per aggregate and settings, choices are restored when revisiting an aggregate, and cached variants switch without another server job. Shared baseline/range/contour updates retain the observation-wide path and invalidate the option caches. Both paths share the same baseline correction; navigation and overlay toggles do not apply pending shared analysis changes.

- Added an opt-in Terminal velocity selector to individual SKY Velocity plots, hidden when All SKY aggregates is selected and initially set to No method. Threshold edge, Connected edge and Erfc wing fit reuse the LAB profiler's existing positive-LSRK-edge estimators, selected velocity limits, channel flags and pipeline QA noise policy. Individual plots show measurements or detection-limit/failure statuses, with JSON exports; LAB overlays reuse the observation measurement. No method restores the unannotated plot without another calculation.

- Added a Pointing tab immediately before Details in the processed-observation workspace. The static sky chart appears on the left with configured target, target acquisition, scan-start and scan-end details on the right. Acquisition uses the first physical scan; boundaries use the first scan's first pointing reference and the last scan's last reference. Sidereal targets show both RA/Dec and Galactic coordinates; configured Alt/Az and offset/five-point parameters appear only when relevant. Missing recorded values remain unavailable rather than being presented as measured coordinates.

- Pointing sky views and downloadable sky/MPR GIF animations use cached physical-scan metadata and pipeline-processed spectra without replaying scan processing. Markers prefer each scan's first recorded pointing and timestamp; labelled target-at-midpoint estimates remain available for older supported targets, while offset/five-point scans require recorded references. Static charts read metadata only. Animation generation prepares and caches all aggregate sky charts, uses one frame per included physical SKY scan, and reports frame progress beside its controls. Sky/animation views share a fitted, scrollable display; the Sky view button appears only once an animation exists.

- Added an editable animation frame interval, with a 100 ms fallback and a suggested interval targeting a ten-second loop within 100–500 ms per frame. Existing GIF timing changes preserve frame pixels, palettes and compressed image data without rendering the sky or spectra again. The UI reports saved timing and detects ignored interval requests; workers reject missing timing from older web-server handlers with a restart message.

- All SKY aggregates now defaults to enabled on Frequency, Velocity and Total Power; Total Power defaults to Per second. A Show mean checkbox between Resolution and All SKY aggregates controls the mean reference lines for both resolutions and individual/all-SKY views. Prepared image variants switch immediately, retain the same power readings and follow interactive plot updates.

- Total Power plot updates now follow the active Frequency or Velocity baseline correction and selected range, refreshing both resolutions and their CSV exports. Cached reconstruction responses apply each aggregate's correction consistently to its physical-scan readings, preserving time variation without replaying scans, accumulating corrections or fitting a different baseline to every second.

- OPT scan lists and plot headers now show the pointing mode and configured target coordinates or body ID. RA/Dec targets include Galactic coordinates; fixed Alt/Az targets show those coordinates instead, and offset/five-point targets identify the displayed position as the target centre.

- Dish pointing offset and position angle accept unavailable values and are updated from measured Alt/Az on the regular current-pointing poll, using the shared target-centre calculation. The pointing log appends both values, and TM's first/last scan pointing references retain `pointing_offset` and `pointing_position_angle` alongside datetime and Alt/Az. Legacy records without the additional fields remain readable.

- RTL and Airspy streaming drivers now timestamp sample blocks from measured producer read-start/end wall-clock intervals, preserving timing through ring-buffer splits, merges, drops and resets. Freshness measures how long buffered reads have waited since completion, so normal read overhead does not accumulate into false sample age. Exceeding the age threshold now logs a warning and continues delivery; actual read, producer and reset failures remain errors. Targeted DEBUG diagnostics report read duration, delivery rate, nominal timeline lag and ring occupancy; these host-read timestamps do not claim hardware acquisition timing.

- Fixed repeated aborts after a configuration failure races with dish target acquisition. Cleanup always sends standby and target-clear commands despite stale TM state; standby READY polls cannot restart scanning/tracking, DM forwards the actual acquisition failure message, and inactive observations do not receive repeated abort transitions. Repeated OET abort events leave cleanup and the release timer unchanged.

- Added Save Pipeline Configuration to persist OPT's edited pipeline atomically to the selected Config Profile. Expanded pipeline help explains SPR per-channel/per-second storage, CAL corrections, MPR reduction, step ordering and reprocessing. Pipeline/observation pane ordering, spacing and parameter-field alignment were improved. LAB sweep defaults are ±10 degrees in longitude and latitude with one-degree steps; RESYNC help now explicitly describes reloading the app's Config Profile from disk.

- Added local and rotation solar-offset observation examples, including five- and ten-minute outbound/return passes, and refreshed commissioning load/rotation settings and local digitiser backend selection.

- Updated rotation commissioning observations to five-minute integrations at 35 dB gain, with a three-hour scheduling block, and added rotation-continuation and Sadr tracking examples. The five-minute solar drive example includes a short AUTO1 gain acquisition, a LOAD measurement and outbound/return SKY passes. The rotation profile's flatten baseline step is currently configured as disabled.

- Fixed All SKY Frequency overlays retaining the earlier pipeline-only image after interactive baseline updates. Initial rendering and cached updates now build the overlay from the exact corrected individual spectra, preserving matching powers/ranges and channel annotations without adding SNR panels or median-baseline lines.

- Fixed missing individual Frequency plots when an observation has no pointing metadata for LSRK conversion. Frequency rendering/caching and interactive updates now work independently of Velocity; unavailable Velocity/L–V views show the reason and older missing Frequency results request reprocessing.

- Added Per second / Per scan resolution to frontend Total Power, for all SKY or selected aggregates. Both modes use processed physical-scan total-power readings; scan points are their per-scan means. Seconds preserve recording timestamps/gaps where available and explicitly use recorded time otherwise. Both plot modes and matching CSV exports survive cached spectral updates.

- Interactive SNR and robust-noise diagnostics now derive smoothing and peak-exclusion widths from the min/max-selected channel range, as well as excluding out-of-range powers. Restricted Frequency/Velocity estimates match calculating on the selected spectrum alone.

- Unified default noise/SNR diagnostics around pipeline QA's median/MAD and smoothed-peak exclusion policy. The selector is shared by QA, the profiler and interactive plots; plot annotations now say Robust Noise and Median baseline. Individual Frequency/Velocity plots also draw the median baseline, and cached updates retain the pipeline QA settings. Mean/RMS and legacy positive-edge noise selection remain explicit profiler options.

- Restricted frontend SNR/noise annotations to individual aggregate Frequency, Velocity and LAB comparison plots. All SKY aggregates views omit these panels, including single-SKY observations; normal legends and channel flags remain visible.

- Added peak SNR, RMS noise, mean floor and noise-window/sample-count annotations to frontend Frequency, Velocity and LAB comparison plots. All use the existing shared estimators and profiler diagnostic, now housed in `sdp.spectral_metrics` with profiler-compatible imports. Initial/cached updates use the same diagnostic; LAB JSON exports include the noise metrics separately from shape RMS.

- Velocity analysis now defaults to the full recorded channel range instead of ±100 km/s. Blank bounds select automatic limits; explicit limits also bound baseline fitting. Initial and cached Velocity plots show grey bandpass exclusions and red RFI markers, with channel flags retained in native caches. Older jobs require reprocessing to recover these annotations. Single-aggregate Frequency updates also retain the flag annotations.

- Removed the horizontal zero-power reference from initial and updated Velocity plots so it no longer expands the power scale. The vertical zero-LSRK-velocity marker remains.

- Removed the zero reference line from single-aggregate Frequency plots. With no interactive subtraction, a single SKY aggregate now uses the same data-driven vertical scale as the All SKY aggregates view.

- Interactive baseline settings now start at No subtraction on each page load, ignoring remembered baseline methods; omitted API baseline settings also default to none. This display setting does not change the pipeline baseline configuration in the selected profile.

- Added `Flatten (retain fitted level)` to interactive Analysis settings, supporting both initial processing and cached Frequency/Velocity plot updates on top of the configured pipeline output.

- Enabled BASELINE with method `flatten` in every configuration profile, after calibration and flagging and before QA, using the outer 0–10% and 90–100% usable-channel windows. New pipeline BASELINE steps also default to `flatten`; explicit `linear` subtraction and interactive plot defaults retain their behaviour.

- Added pipeline baseline method `flatten`: removes the fitted linear slope while retaining the lowest fitted background level within the usable passband. Flagged channels do not set that level; existing `linear` subtraction and defaults are unchanged.

- Removed the redundant noise-RMS-to-signal-peak dB line from LAB plot annotations; peak SNR, mean noise floor and noise RMS remain visible. Existing JSON fields are retained.

- LAB profile plot headers include the observation ID from metadata, or from the standard `<observation_id>_vlsr.csv` filename for direct-coordinate comparisons.

- LAB comparison plots and JSON now include scaled-observation peak SNR, mean noise floor, noise RMS and RMS-noise level in dB relative to the signal peak. The documented power-like 10-log convention uses the configured noise windows independently of terminal method; unavailable or zero-noise cases do not emit infinite dB values.

- LAB threshold plots show the mean noise floor as a dashed horizontal line alongside the dotted noise-floor-plus-N-sigma threshold, with separate numeric legend entries. Connected comparisons draw the shared observation-derived floor and threshold once.

- LAB linear/constant observation baselines now automatically select the outermost 20% of valid channels on each side of zero within the displayed range, then use a clipped line-free fit. Explicit baseline windows override the default; `--no-baseline-auto` restores lower-envelope fitting. Actual windows are printed and recorded in JSON; insufficient two-sided coverage is rejected. Profile plots show a baseline/noise-window text box at the upper left and the legend at the upper right, identifying shared noise ranges and when a noise threshold is unused.

- Added LAB `--terminal-method connected` to follow the dominant emission wing to a sustained below-threshold return, ignoring later detached islands. Comparisons use the observation's noise threshold for both peak-scaled curves. `--terminal-return-width` controls confirmation in km/s alongside the minimum-channel setting; plots and JSON identify the shared threshold and main-wing detection edges. Existing threshold and erfc methods remain available.

- LAB erfc fitting automatically selects a falling-wing window when `--terminal-fit-range` is omitted. Selection uses a smoothed guide after the dominant positive-velocity peak, while fits use original channels and retain existing validity checks. Selected windows are recorded in JSON and shown in the console and plot legend; explicit windows retain manual behaviour.

- LAB profiler accepts repeated `--baseline-window VMIN VMAX` options for explicit line-free baseline fits independent of the scoring range. Linear/constant fits use all selected channels with symmetric outlier clipping, allowing both ends of a sloping spectrum to anchor the baseline. Metadata records the windows and method; default lower-envelope behaviour is retained.

- LAB comparison plots now measure terminal velocities independently for the observation and LAB profile at their own noise-floor-plus-3-sigma thresholds. Both markers and thresholds are shown by default, and the heading and JSON report the observation-minus-LAB terminal-velocity difference when both edges are detected. Explicit source selection and disabling terminal measurements remain available.

- Fixed scan-product output paths containing `~`: scan CSV/metadata/QA saves and filterbank writers expand the home directory and record a resolved output path, preventing spectra being written to a literal `~/samples` directory beneath the launch directory. Schedule downloads validate HTTP status, CSV content type and filename before saving; missing-file and other JSON errors appear in the pane instead of becoming `spr.json` downloads.

- Completed scans in both live and historic Schedule details now offer a compact rightmost SPR CSV download button. WEB resolves the spectrum from saved scan metadata, verifies completion and confines access to the selected archive, with profile-change checks and symlink/path rejection. The authenticated UI streams the unchanged CSV as an attachment using its original filename; incomplete scans have no download action.

- Schedule now displays queued and running observations from WEB's shared TM snapshot cache, with running observations first and the remaining queue ordered by scheduled start. The table scrolls after approximately ten rows and includes observation ID, title, dish, reported status, scheduled start/end UTC, estimated duration, creation UTC and creator contact. Selecting a row expands its individual scans, with reported statuses, current-scan highlighting, tuning and sample timestamps; expansion and scroll positions survive polling. Finished observations retained in the live model are excluded.

- Added a separate Historic observations pane below the live Schedule queue, reading saved metadata recursively from the selected profile's scan store independently of TM. History shows newest observations first, scrolls after approximately ten rows, excludes IDs in the live queue and uses the newest disk copy when an ID is duplicated. Expanding an observation loads its recorded scans on demand through an authenticated, profile-guarded WEB endpoint. Completion is derived from saved scans/target progress instead of treating saved READY/IDLE/SCANNING labels as current execution; aborted, faulted, incomplete and unreadable records remain distinguishable. Disk failures retain the previous catalogue with an explicit warning.

- Dish coordinate calculations translate the known Astropy stale-IERS predictive-data error into `XSoftwareFailure`, retaining the original exception as its cause. DM returns its existing observation-correlated error response so TM cancels request retries and aborts the observation. Failed coordinate conversion or initial slew invalidates the driver's operating/pointing state, and handled target failures clear the partial target before replying, preventing a retry from falsely reporting success. DM continues to handle only `XBase` exceptions; unrelated unexpected exceptions propagate.

- Application models report the local TCP role of each interface. Status connection tables distinguish Server, Client - Persistent and Client - On demand, with Unknown for unreported roles. TCP clients reject replies addressed to obsolete sockets, and TM ignores stale WEB connection/status events after reconnection.

- Status application rows now show the expanded application type under App and the instance identifier under App ID. Dish overview rows place Feed and Driver type after the dish ID, combine actual altitude/azimuth into a one-decimal Actual AltAz column, and show Driver Failures before Health. Shared weather status appears above the dish table; static location, diameter, F/D and digitiser association move to the selected dish's details, avoiding duplicate health, pointing-state and actual-value fields in the controls below.

- Dish Entity Connections scrolls after approximately five rows. The centred pointing polar plot shows the current position plus up to five earlier positions as a trail while slewing, clearing the trail when the dish stops slewing or the selected dish changes.

- The rotation launch configuration includes `web001` on the Mac at `192.168.50.1`, with TM initiating its connection to that address. Rotation digitiser configuration updates the configured hosts and selects the `airstream` SDR driver. The rotation observation example now targets `dish001`, starts with a 10-second load scan at Alt/Az (23°, 201°), then samples Galactic longitude 20° through 170° at latitude 0° in 5° steps, using AUTO1 gain, 6 MHz sampling and 2048 channels. The rotation load example also uses AUTO1 gain.

- Added home configuration snapshots and a solar MODBUS weather-station configuration, and updated local dish/MD01 settings, digitiser associations and the local load-observation example. Added commissioning utilities for WitMotion inspection/calibration and magnetic azimuth, a BNO08x IMU diagnostic, and an Airspy observation-to-LAB azimuth/elevation fitting sweep. These standalone commissioning scripts retain device/site-specific settings.

- Added a repository `unittest` launcher using the shared environment setup and a `.python-version` selecting `dmd2000-homebrew-311`.

- Fixed gain-only DIG config events injecting `scanning: null` into SDP updates. Omitted scan state now stays unchanged; SDP and TM ignore legacy null scan-state values so a partial tuning update cannot corrupt the digitiser model and break subsequent status reports.

- Digitiser Status details now have desired/actual controls for load, gain, centre frequency, sample rate, bandwidth, frequency correction, channels, scan duration and scanning, with compact driver diagnostics on the right. Individual Set and Apply settings actions use the existing DIG config-event path through WEB to TM's queue; acknowledgements and reported actual values remain separate.

- Weather Station details use an anemometer dashboard: thresholds and DM-reported alarm-window actuals on the left, a wind-speed history chart on the right with centred legend and time-range caption, compact connection/driver diagnostics above, and optional trailing-month alarm history. The unused Entity Connections pane, generic latest-reading section and raw weather table are removed. DM forwards bounded per-station display history and authoritative alarm metrics through the telescope model, allowing instantaneous wind readings to appear even when the raw samples remain local to DM. Summaries and alarm calculations retain all original samples. Timeout warnings use DM's reported state rather than the age of a cached browser snapshot.

- Dish Manager Entity Connections now lists every dish, with Not Applicable for DRIFT drivers and configured MD01/network IMU endpoints, DM's client role and the last successful communication time. Network drivers record decoded responses in `DishModel.last_comms_success_dt`; later failures retain that timestamp, and older models show no timestamp until a successful exchange is recorded.

- Added application commands beside the command endpoint in each Status detail pane, with app-specific registry choices, required values, and dated responses. A compact command selector aligns with the endpoint text; descriptions appear beside Send until replaced by pending/results feedback, with scrolling for long responses. Read-only tracing/debug queries are labelled TRACE ? and DEBUG ?. The selected application supplies the command target; permissions and the existing direct command path apply.

- TM now initiates the WEB TCP connection when its initialization is ready, matching DM, SDP and WS. WEB listens on its existing `--tm_host`/`--tm_port`; TM connects using `--web_host`/`--web_port` and owns reconnection. Restart both applications after upgrading.

- Expanded Status details with separate App Connections and Entity Connections tables, including per-digitiser TM/SDP connection states on TM/SDP details. Dish Manager details include Queue size and processor count below Connections, an initially collapsed processor table, and an alphabetical dish engineering table. Selecting a dish opens desired Mode, Capability and Alt/Az controls, timestamped errors, target/PEC data and driver diagnostics; reported Mode/Capability/Actual AltAz remain in the overview. Operators can request Mode/Capability through TM's existing DSH configuration path or send STOP directly to Dish Manager, with dated responses, no automatic retries and desired selections preserved during polling. Configuration acknowledgements indicate scheduled actions; reported state remains separate. A compact polar plot compares actual and desired pointing with distinct markers, north-up clockwise azimuth, altitude rings, hover coordinates and explicit missing/below-horizon/stale indicators.

- Fixed confirmed TRACE/DEBUG toggles reverting during cached telescope-model refreshes. The UI retains successful command values until a non-stale snapshot contains an application report newer than the command acknowledgement, including rapid repeated toggles.

- Added an App selector and RESYNC button alongside Config subtabs, including configured digitisers. Pending/success/error button colours and an adjacent dated response reflect the target application’s actual reply. Unsaved drafts, pending editor activity and viewer permissions disable submission; in-flight RESYNC locks profile/editor changes and is sent once through the existing command path.

- Status application rows now have TRACE/DEBUG switches beside their trace/log links, with operator/admin permissions, a dated response area and confirmed state retained until a fresh app report. Commands are forwarded once through WEB's existing webhook/TCP framework; failures and unknown outcomes do not change the switch or trigger retries. App-specific command dropdowns, including RESYNC, are available in the selected application's detail pane. Last errors appear at the top of application details with their recorded UTC date/time beside the heading.

- Added authenticated log/HTML-trace viewing from the Status table: Debug and Tracing values open independent panes below Applications, ordered Log then Trace, with Refresh, Download and close controls. Files use the selected profile's log root, digitiser trace IDs and labelled shared digitiser logs. Reads are bounded, reject arbitrary paths/symlinks, and remain exclusive to the private UI. Status polling preserves open file contents; sandboxed HTML documents retain trace expand/collapse controls and use object URLs for browser compatibility.

- Fixed WEB Ctrl-C/SIGTERM shutdown racing with Waitress HTTP/UI event loops. Workers finish before socket closure is scheduled on each listener’s own thread, avoiding Bad file descriptor exceptions while retaining normal TCP, timer and OPT cleanup.

- Added profile-level `LaunchConfigModel.log_dir`, defaulting to the existing repository logs directory. The launcher supplies its resolved path before logging starts; direct launches support the same profile default, `DMD_LOG_DIR` and `--log_dir` overrides. Application, trace and telemetry files share the configured root. WEB profile responses include the selected log root for future file viewing, without redirecting running applications when the UI profile changes.

- All application HTML traces use the launch entity ID when defined (for example `tm001.html`, `ws002.html` or `dig001.html`), keeping active files and rotated archives separate for each instance. Applications without a defined entity ID retain the application-name filename. The private trace viewer follows the same naming rule; application logs retain their existing names. Restart running applications to adopt the new trace filenames; historical files are retained.

- Status application details now open below the application table at full width. Replaced the single Current Observation pane with a top-level Observation Executions widget listing all simultaneous CONFIGURING, READY and SCANNING observations from the shared TM snapshot.

- Fixed UI login rejecting accounts whose usernames contain dots, underscores or hyphens. Account creation and Flask-Security login now share the same username validation and lowercase normalization; existing accounts do not need to be recreated.

- WEB now serves the engineering console and OPT workspace in the same application process on a separate localhost-only Waitress listener (default port 8765), while the token/HMAC API remains on port 8000. Added UI port/thread/storage options, an API-only switch, and coordinated listener/OPT-worker shutdown.

- Added Flask-Security username/password authentication with profile-independent SQLite accounts, Argon2 password hashes, eight-hour sessions, CSRF protection and viewer/operator/admin permissions. The Access view supports administrator-managed accounts, password resets and disabling; users can change their own passwords. Added an interactive account/recovery CLI, persistent private signing keys, session revocation on password reset/disable, and protection for the last active administrator. Public API routes do not accept UI session cookies or expose console/authentication endpoints.

- WEB now polls TM for a full telescope-model snapshot on connection/reconnection and every 30 seconds through timer-backed `tm_web` GET requests. Authenticated HTTP clients share the cache, with source/receipt timestamps, TM profile, credential redaction and stale/disconnected indicators. The frontend Status view uses this cache and provides selectable application rows with responsive detail panes for health, availability/reliability, tracing/debug, connections, processor activity, errors and subsystem-specific data.

- Connected the frontend Admin → Digitisers tab to WEB for viewing, editing, creation and confirmed deletion. Added sampling/network, SDR, filter GPIO and temperature controls, JSON driver options, row selection/filtering, and version/profile conflict checks. Dishes and Digitisers now share profile/refresh controls that preserve tab drafts and check both editors before discarding changes; WEB credentials remain server-side in the restricted local bridge.

- Added profile-backed DELETE operations for dishes, digitisers, weather-station definitions, pipeline maps and individual steps, preserving unrelated configuration and recorded data. The dish editor offers confirmed deletion with version/profile conflict checks and refreshes the list after success; the local console forwards dish deletions with its existing authentication and origin protections.

- Connected the frontend prototype's Admin → Dishes view to WEB profile/dish APIs, with profile selection, filtering, typed configuration forms, creation and editing, unsaved-draft protection, and version/profile conflict checks. The local console forwards authenticated requests through a restricted same-origin bridge, keeping WEB credentials server-side and retaining the existing OPT workspace.

- Added authenticated profile-backed pipeline APIs to list named step maps and ordered steps, create maps, append/insert steps and edit step definitions in `PipelineConfig.json`. Operations preserve custom step keys and unrelated configuration, use atomic writes and ETag checks, and save changes independently of TM/SDP for subsequent SDP startup or RESYNC.

- Added authenticated profile-backed digitiser and weather-station list/read/create/PATCH APIs using `DigitiserList.json` and `WeatherStationList.json`. Both follow WEB profile selection, use existing model validation, preserve unrelated configuration and share atomic writes and ETag conflict checks with dish editing. Weather-station APIs manage station definitions; weather application driver settings remain in `WeatherStationModel.json`.

- Added authenticated WEB profile discovery and selection APIs (`GET /api/profiles`, `GET/PUT /api/profile`). Switching updates file-backed dish access and the default observation directory from the selected profile's `LaunchConfig.json`, clears startup storage overrides, and preserves the selection through WEB RESYNC without changing running application connections or identities.

- Added authenticated WEB APIs to list, read, create and partially edit dishes in the active profile's `DishList.json`, independently of TM. Configuration values use existing dish/driver validation, atomic file writes preserve unrelated entries, and ETags support stale-editor conflict checks. Profile edits take effect in running Dish Manager after startup or a successful RESYNC.

- The standalone web application (`web.web_server`, with its `WebServer(App)` runtime in `web.web`) has its own model, event processors, command endpoint, tracing and managed Waitress HTTP server. Added WEB models and launch definitions to the local and Alston profiles, the Waitress dependency, and web API documentation. HTTP worker counts are configurable through `--http_threads`; the retained embedded TM API supports `--web_api_threads`.

- Command webhooks execute through `web.cmd_handler` and the target application's existing TCP command API using `util.cmd_app`. Endpoint lookup uses the selected profile or configured overrides, so commands to other applications work without TM running. WEB itself supports TRACE, DEBUG and RESYNC through its command endpoint and `cmd_app`. The embedded TM HTTP API rejects CMD webhooks with guidance to use the standalone web server or command utility.

- HTTP application results use the transport-independent `ResponseEvent` envelope with `status`, `message`, optional error `code` and JSON `payload`. Completed command and configuration outcomes, including target errors and timeouts, return HTTP 200 so gateways preserve the JSON body; clients must check the application status. Invalid requests, authentication failures and internal server errors retain HTTP error statuses. The Cloudflare tunnel configuration targets the standalone web server on port 8000.

- DIG, DSH, ODT and OET configuration webhooks use the new `tm_web` TCP API on TM's `web` interface (default port 50004), with `web` registered as a protocol origin/destination. TM validates updates and processes them as serialized ConfigEvents; `rsp_future` resolves with contextual errors or confirmation that the configuration was handled and its actions scheduled, without waiting for those actions to finish.

- WEB configuration requests are sent only while connected to TM and use unique timer Actions to carry request context and match responses. Requests are not retransmitted; timeouts, disconnects and shutdown complete waiting HTTP calls, and late responses are ignored. Replies retain the originating TCP socket, supporting concurrent HTTP callers without sending an old connection's response over a replacement connection. Timer cancellation actions are removed from completed Action lists so they do not falsely indicate incomplete scheduling.

- WEB sends its `WebServerModel` status immediately after connecting and during the standard 30-second status cycle. The `tm_web` API accepts status advice without response timers or acknowledgements. TM maintains WEB connection flags and a web status model in the telescope model, and publishes it through the Google Sheets driver's configurable `web_range` (default `TM_UI_API++!V2:W`).

- Observation listing at `/api/observations` searches storage directly in WEB without TM or SDP running. It recursively discovers observation metadata, returns relative folder names in `payload.observations` alongside `payload.count`, skips symbolic links and reports unreadable subtrees. The legacy embedded TM listing endpoint remains available and shares the catalogue implementation.

- Promoted `scan_store_dir` to a profile-level `LaunchConfigModel` setting shared by SDP and WEB. Migrated existing profile values out of SDP launch arguments while retaining `--scan_store_dir` overrides and WEB's per-request `directory` override; omitted overrides use the shared value. WEB reloads its storage default on RESYNC. Existing `~` expansion is retained, and launch models now correctly deserialize timestamps when loading serialized configurations.

- `obs.inject` submits observation files to the web application's REST endpoint on port 8000, authenticates using `WEBHOOK_SECRET` or `--token`, and reports TM's `ResponseEvent` result. The HTTP timeout defaults to 20 seconds, and application errors remain failures even when returned with HTTP 200.

- The Google Sheets Apps Script clears and refreshes `ART!A20:D20` for each webhook, displaying the application name, response datetime, status and message; command controls use row 17. Non-JSON gateway errors report that the command outcome is unknown. Observation submission sends the structured `ObsList` JSON rather than the flattened rows used for sheet storage.

- WEB HTML traces include HTTP request and response entries alongside TCP messages, covering locally serviced observation listings and error responses. Matching request IDs, HTTP status, elapsed time and expandable JSON details follow the existing TRACE switch; credential fields are redacted and body details are bounded.

- TM logs Dish Manager errors using the primary error description and the advice/response `message` attribute, avoiding full model dumps.

- Fixed the shared application status loop to exit as soon as shutdown is requested. Ctrl-C no longer causes repeated status dumps while waiting for main-thread cleanup; normal pending-status diagnostics remain enabled.

- LAB profiler adds optional erfc fits to the positive-velocity wings of the observation and best-matching survey profile, including sweep results, fitted-edge overlays, formal errors and JSON comparison metadata.

- OPT's frontend adds a scrollable observation catalogue with names, dishes, completed scan counts/types and completed integration times, plus row selection, filtering and an Open action. The connection badge now turns red with “OPT Disconnected” on lost connections and recovers through periodic health checks. Updated the workspace heading and input/aggregate/plot tab labels.

- Integrated a local OPT analysis workspace into the frontend prototype's Results view, with physical-scan inclusion controls, uninterrupted background processing, completed-aggregate signal browsing, velocity/timeline/L–V views, cancellation, logs and ZIP exports. Baseline and contour changes can re-render cached spectra without replaying recorded scans. Added the localhost-only `ui.opt_service` launcher and frontend workspace documentation.

- OPT (`obs.opt`) now opens and saves a longitude–velocity (L–V) diagram after the velocity plot's Enter prompt. It uses target Galactic coordinates, native LSRK channels and baseline-subtracted power, with white/blue asinh colouring and labelled magenta contours. The view truncates columns to their shared valid velocity interval, preserves masked channels, separates frequency setups, and supports configurable contour levels and contour-only velocity smoothing. Added the OPT usage guide in `docs/opt.md`, linked from the README.

- Observation velocity plots independently subtract a linear or constant lower-envelope baseline per target/frequency scan without amplitude scaling, using the shared `util.convert.subtract_spectrum_baseline` helper. Velocity CSV exports preserve original `calibrated_power` and add `baseline` and `baseline_subtracted_power`; fitting ranges and optional line-free windows are configurable.

- LAB profiler can generate predicted L–V diagrams from a local LAB FITS cube at fixed Galactic latitude over configurable longitude offsets. Predictions support a requested final Gaussian beam with nominal native-beam correction, white/blue or inferno styling, brightness contours, colour stretches, and CSV/PNG/JSON outputs. Usage is documented in `docs/lab_profiler.md`.

- HI4PI sweep fitting can optionally remove a constant or linear lower-envelope baseline from observation power before normalization, improving vertical alignment with the near-zero HI4PI survey baseline. Documented `--observation-baseline` usage in `docs/hi4pi.md`.

- Combined HI4PI single-position extraction and Galactic-coordinate sweep fitting in `util/hi4pi.py`. The utility can generate beam-convolved HI4PI velocity plots or compare observation CSV spectra against a swept grid of convolved HI4PI profiles, with local cube reuse, cached downloads, RMS/correlation scoring, and best-fit plots. Added user documentation in `docs/hi4pi.md`.

- Streaming digitisers stop the hardware acquisition at phase boundaries, restart with a configurable settling discard, and isolate auto-gain measurements from recorded samples. Phase changes invalidate reads before resetting and preserve the read's original scan metadata. Genuine stale buffered samples and failed live stream resets propagate hardware errors; measured host-read timing and freshness handling are described above.

- HI4PI spectrum extraction downloads CAR cubes from the projection subdirectory in the CDS archive, fixing HTTP 404 errors for selected tiles, and handles scalar NumPy pixel coordinates returned by Astropy WCS.

- Galactic target coordinates (`sky_coord` with `frame: "galactic"` and longitude `l`/latitude `b` in degrees) now survive serialization and display correctly. Dish drivers transform the supplied coordinates directly to Alt/Az for sidereal tracking, offset scans, and five-point scans.

- Telescope Manager allows 40 seconds per auto-gain request attempt and rechecks observation configuration after accepting the result when no SDP gain acknowledgement is pending, preventing configuration stalls when SDP already has the measured gain and TM retains a temporary load-state snapshot.

- Telescope Manager tracks pending auto-gain requests by digitiser and request timestamp, retaining the last reported hardware gain until the matching result arrives. Unrelated configuration acknowledgements no longer report premature calibration completion using a previous gain; failures and final request timeouts clear the pending request.

- Digitiser reports SDP disconnections during an active scan as error status advice with the interrupted observation's scan identifiers and updated stopped/disconnected state, allowing Telescope Manager to abort the observation immediately instead of waiting for the scan timeout. Idle disconnections remain ordinary status updates.

- Renamed the Milky Way utility to `util.milkyway_plot` and added LAB-compatible direct `--galactic`, `--location`, and `--dt` inputs, sharing defaults and validation with LAB profiler and saving a JSON context sidecar.

- LAB profiler accepts direct `--galactic L B` coordinates without observation metadata, with optional `--location LAT LON HEIGHT` (default Jodrell Bank) and `--dt` (default current UTC). Both input modes save a JSON context sidecar alongside CSV and PNG outputs.

- Added `util.milkyway_plot` to show and save a scan's Galactic line of sight on the Milky Way illustration, using observation site, recording time and pointing metadata shared with `util.lab_profiler`.

- LAB H I profile extraction is an importable `util.lab_profiler` CLI using observation/scan metadata or direct Galactic coordinates, with local LAB cube reuse, Gaussian beam averaging, CSV/PNG/JSON output, and kelvin brightness scaling.

- Observation processing displays and saves an additional H I LSRK velocity versus calibrated power plot using the radio Doppler convention after the aggregated SKY plots, supporting multiple frequency scans and fixed Alt/Az targets using original recording times.

- Observation processing (`obs.opt`) automatically saves LSRK velocity samples to `<Obs id>_vlsr.csv` in the observation metadata directory. Columns are `target_id` (numeric target index), `freq_scan`, `velocity_km_s`, `calibrated_power`, `baseline`, and `baseline_subtracted_power`; masked channels retain their velocity with blank power values.

## [1.1.0] - 2026-10-24 - First Light Milestone

First light was achieved on the Alston Radio Telescope using the DMD2000 software. An observation of the Cygnus region of the Milky Way detected neutral hydrogen emmissions. The Alston Radio Telescope has been assembled, integrated and is undergoing verification and commissioning tests whilst temporarily located at Jodrell Bank. The Alston Radio Telescope will be moved to the Alston Observatory once a concrete pad has been prepared for its arrival. The following applications where utilised to achieve "First Light": Telescope Manager, Science Data Processor, Digitiser, Dish Manager, and the Weather Station simulating calm weather despite a storm raging towards the afternoon !

- Signal displays retain a lightweight baseline spectrum and channel flags per scan, preserving the black load overlay after calibration queue cleanup. Subsequent scans select the latest available compatible baseline; headless runs do not create display copies.

- Weather analysis accepts optional inclusive UTC `--start_date` and `--end_date` filters in YYYY-MM-DD format, defaulting to the earliest available readings through today UTC while preserving gust continuity across the start boundary.
- Local Dish Manager launch uses `python -m dsh.dm` so application packages remain importable in a new terminal without a preconfigured PYTHONPATH.
- Terminal launcher activates repository `.venv` environments as well as legacy `venv` environments, preventing a new terminal window from falling back to the system Python. Repository pyenv configuration retains precedence.
- Weather Station reports DEGRADED when either or both manager connections are unavailable, FAILED immediately when the latest poll cannot supply valid wind data, and UNKNOWN until the first poll after startup or driver replacement. Health recovers on the next successful poll.
- All five application entry points use a shared App lifecycle to handle SIGTERM and SIGINT, stop workers and network interfaces, invoke application-specific cleanup, and flush and close logging handlers. Weather Station headless runs no longer start an interactive input thread.
- Weather Station polls only when its weather polling timer expires. Polls follow monotonic deadlines so sensor processing time does not accumulate into the configured interval; overdue deadlines are skipped with a warning rather than triggering catch-up reads.
- Weather analysis ignores macOS metadata files, sorts readings on disk to handle backward timestamp jumps, and reports and removes leading NUL padding found in logs. An optional `--skip-invalid-lines` flag warns and omits malformed readings; default processing stops on malformed data.
- Weather analysis supports a configurable input `--encoding` for older plain logs and ZIP archives, and reports the source file/member and line number on decoding errors.
- Weather display includes an m/s or knots selector for wind readings, thresholds and plots. Alarm calculations and stored weather measurements remain in m/s.
- Weather Station records every poll in `logs/weather/ws.log`, including the UTC log timestamp, station ID, health and weather measurements. Logs rotate at midnight UTC into dated ZIP archives, retained without automatic deletion.
- Weather analysis tool (`src/ws/analysis.py`) reads plain weather logs and ZIP archives to produce a daily summary CSV with one row per station per UTC day. Summaries include sample counts, minimum/maximum/average wind speed, time of maximum wind, readings above a configurable `--threshold`, gust counts, and first/last reading timestamps. Usage is documented in `docs/ws_analysis.md`.
- Weather Stations using modbus drivers now attempt recovery from a read error by closing and reopening the device on the next poll timer.
- Command registry created to allow applications to create custom commands directed at their command port. Custom commands for an application need to be registered in the ~/Config/CmdRegistry.json file and the relevant application needs to implement a handler for the command before it can be processed. An application RESYNC will refresh the command registry for a given application.
- Dish Manager now supports a STOP command that transitions a specified dish to a STANDBY capability, whilst issuing a stop command on the driver. Dish movement is not possible if the dish is not in an OPERATIONAL capability such as OPERATE FULL or OPERATE DEGRADED.
- Dish Manager now supports its dish pointing log to be queried via a request message from the Telescope Manager. This is used when the Science Data Processor informs the Telescope Manager that a scan has completed. The Telescope Manager requests the pointings corresponding to the first and last scan samples so that the scan metadata can be augmented with this information. 
- MD01 Controller Driver now supports 'short way' movement if the physical controller is configured with this option enabled. Short way movement will flip the elevation past the 90 degree mark to get to a desired azimuth and elevation if this is shorter in travel distance than limiting elevation to a maximum of 90 degrees.
- MD01 Controller allows a minimum and maximum azimith range to be configured for a dish, and the driver now limits azimuth movement to this range.
- MD01 Driver configuration for dish rotation speed updated so that the default speed is 1.5 deg/sec, from 2.5 deg/sec that resulted in observations timing out and aborting.
- Dish Manager now attempts to estimate the slew duration for each new observation target, and provides this information to Telescope Manager in order to adjust its observation timeout timer appropriately.
- Digitiser updated to make the SDR stream reset idempotent instead of logging duplicate hardware errors.
- Digitiser now clears cached SDR configuration (gain, center_freq, sample_rate and bandwidth) on SDR hardware reset and immediately sends a status update to the Telescope Manager.
- Digitiser guards against fatal hardware/USB SDR errors and closes old workers cleanly before attempting reconnection. 
- Telescope Manager now does not attempt to Abort a non-active Observation (e.g. one that already completed) on receipt of an error status message from the Digitiser or when a weather alarm is triggered. 
- Observation Execution Tool lifecycle handling improvements were made:
    Resource release is permitted from READY and ABORTED.
    Duplicate START transitions are no longer queued by the observation timer.
    Active observation states are defined centrally in models.obs.
    Weather alarms only abort active observations.
    Digitiser errors only abort the associated observation when it is still active.
- Observation Execution Tool now gives precedence to a new incoming observation when resource contention exists between it and a prior aborted observation. The prior aborted observation may be reset while it has the required resources allocated, however needs to relinquish the resources if this has not happened by the time a new observation requests the same resources. 
- Science Data Processor previously did not show the signal display when the scan integration time was very short e.g. 1 sec, this has been fixed. 
 
## [1.0.0] - 2026-09-03

- Initial version of the DMD2000 application suite.

### Applications

- Dish Manager (DM)
- Digitiser (DIG)
- Telescope Manager (TM)
- Science Data Processor (SDP)
- Weather Station (WS)

### Dish Manager (DM)

The Dish Manager controls dish operating modes, capabilities, target acquisition, slewing and tracking; reports pointing and status information to the Telescope Manager; and monitors weather conditions to place the dish safely into stow when required. It supports configurable hardware and simulated dish drivers, together with live dish and weather displays for commissioning and operation.

#### Dish drivers

- [SPID MD01 Control Unit](https://www.rfhamdesign.com/products/spid-hr-antenna-rotators/index.php) for motorised azimuth and altitude control
- Drift driver for fixed dishes

### Digitiser (DIG)

The Digitiser controls the software-defined radio used to convert received radio-frequency signals into digital IQ samples. It configures frequency, bandwidth, sample rate and gain; acquires samples during observations; and streams them to the Science Data Processor. It also monitors hardware and communications health, supports automatic gain selection, and manages optional bandpass-filter, calibration-load and temperature-protection hardware.

#### Supported filters

- [Nooelec SAWbird+ H1](https://www.nooelec.com/store/sdr/sdr-addons/sawbird/sawbird-h1.html)

#### Supported temperature sensors

- [BME280 sensor](https://amzn.eu/d/0dxeseEG)

#### Supported software-defined radios

- RTL-SDR supports Realtek RTL2832U-based USB software-defined radio dongles, such as the [Nooelec NESDR SMArt](https://amzn.eu/d/0eQ5WVRa)
- SoapySDR supports a wide variety of software-defined radios, such as the [Airspy Mini](https://amzn.eu/d/0bbrA5dU)

### Telescope Manager (TM)

The Telescope Manager coordinates the DMD2000 application suite and manages the complete observation lifecycle. It schedules observations, allocates resources such as dishes and digitisers, configures targets and scans, and orchestrates the Dish Manager, Digitiser and Science Data Processor. It also monitors subsystem health, handles faults and observation aborts, and records completed scan metadata for later analysis.

### Science Data Processor (SDP)

The Science Data Processor receives IQ samples from the Digitiser and converts them into calibrated spectral data for each observation scan. Its configurable processing pipeline performs power-spectrum generation, load calibration, bandpass selection, interference flagging and quality assessment. It stores the resulting scan products and metadata, provides live signal and waterfall displays, and reports scan progress and completion to the Telescope Manager.

### Weather Station (WS)

The Weather Station collects environmental measurements—including wind speed, temperature, humidity, pressure and precipitation—and reports them to the Dish Manager and Telescope Manager. These observations support operational monitoring and allow unsafe weather conditions to trigger alarms and automatic dish stowing. It supports both physical weather sensors and simulated conditions for development and testing.
