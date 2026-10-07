# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- Weather display includes an m/s or knots selector for wind readings, thresholds and plots. Alarm calculations and stored weather measurements remain in m/s.
- Weather Station records every poll in `logs/weather/ws.log`, including the UTC log timestamp, station ID, health and weather measurements. Logs rotate at midnight UTC into dated ZIP archives, retained without automatic deletion.
- Command registry created to allow applications to create custom commands directed at their command port. Custom commands for an application need to be registered in the ~/Config/CmdRegistry.json file and the relevant application needs to implement a handler for the command before it can be processed. An application RESYNC will refresh the command registry for a given application.
- Dish Manager now supports a STOP command that transitions a specified dish to a STANDBY capability, whilst issuing a stop command on the driver. Dish movement is not possible if the dish is not in an OPERATIONAL capability such as OPERATE FULL or OPERATE DEGRADED.
- Dish Manager now supports its dish pointing log to be queried via a request message from the Telescope Manager. This is used when the Science Data Processor informs the Telescope Manager that a scan has completed. The Telescope Manager requests the pointings corresponding to the first and last scan samples so that the scan metadata can be augmented with this information. 
- MD01 Controller Driver now supports 'short way' movement if the physical controller is configured with this option enabled. Short way movement will flip the elevation past the 90 degree mark to get to a desired azimuth and elevation if this is shorter in travel distance than limiting elevation to a maximum of 90 degrees.
- MD01 Controller allows a minimum and maximum azimith range to be configured for a dish, and the driver now limits azimuth movement to this range.
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
