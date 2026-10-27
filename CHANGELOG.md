# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- HI4PI spectrum extraction downloads CAR cubes from the projection subdirectory in the CDS archive, fixing HTTP 404 errors for selected tiles, and handles scalar NumPy pixel coordinates returned by Astropy WCS.

- Galactic target coordinates (`sky_coord` with `frame: "galactic"` and longitude `l`/latitude `b` in degrees) now survive serialization and display correctly. Dish drivers transform the supplied coordinates directly to Alt/Az for sidereal tracking, offset scans, and five-point scans.

- Telescope Manager allows 40 seconds per auto-gain request attempt and rechecks observation configuration after accepting the result when no SDP gain acknowledgement is pending, preventing configuration stalls when SDP already has the measured gain and TM retains a temporary load-state snapshot.

- Telescope Manager tracks pending auto-gain requests by digitiser and request timestamp, retaining the last reported hardware gain until the matching result arrives. Unrelated configuration acknowledgements no longer report premature calibration completion using a previous gain; failures and final request timeouts clear the pending request.

- Digitiser reports SDP disconnections during an active scan as error status advice with the interrupted observation's scan identifiers and updated stopped/disconnected state, allowing Telescope Manager to abort the observation immediately instead of waiting for the scan timeout. Idle disconnections remain ordinary status updates.

- Renamed the Milky Way utility to `util.milkyway_plot` and added LAB-compatible direct `--galactic`, `--location`, and `--dt` inputs, sharing defaults and validation with LAB profiler and saving a JSON context sidecar.

- LAB profiler accepts direct `--galactic L B` coordinates without observation metadata, with optional `--location LAT LON HEIGHT` (default Jodrell Bank) and `--dt` (default current UTC). Both input modes save a JSON context sidecar alongside CSV and PNG outputs.

- Added `util.milkyway_plot` to show and save a scan's Galactic line of sight on the Milky Way illustration, using observation site, recording time and pointing metadata shared with `util.lab_profiler`.

- LAB H I profile extraction is now an importable `util.lab_profiler` CLI using observation and scan metadata, with local survey reuse, automatic temporary download/extraction, CSV/PNG output, and corrected kelvin brightness scaling.

- Observation processing displays and saves an additional H I LSRK velocity versus calibrated power plot using the radio Doppler convention after the aggregated SKY plots, supporting multiple frequency scans and fixed Alt/Az targets using original recording times.

- Observation processing (`obs.opt`) automatically saves the plotted LSRK velocity and calibrated power samples to `<Obs id>_vlsr.csv` in the observation metadata directory. Columns are `target_id` (numeric target index), `freq_scan`, `velocity_km_s`, and `calibrated_power`; masked channels retain their velocity with a blank power value.

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
