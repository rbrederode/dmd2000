# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-09-03
- Initial version of the DMD2000 application suite.

Applications
- Dish Manager (DM)
- Digitiser (DIG)
- Telescope Manager (TM)
- Science Data Processor (SDP)
- Weather Station (WS)

Dish Manager
The Dish Manager controls dish operating modes, capabilities, target acquisition, slewing and tracking; reports pointing and status information to the Telescope Manager; and monitors weather conditions to place the dish safely into stow when required. It supports configurable hardware and simulated dish drivers, together with live dish and weather displays for commissioning and operation.

Dish Drivers
- [SPID MD01 Control Unit] (https://www.rfhamdesign.com/products/spid-hr-antenna-rotators/index.php
) for motorised azimuth and altitude control. 

- Drift for fixed dishes

Digitiser
The Digitiser controls the software-defined radio used to convert received radio-frequency signals into digital IQ samples. It configures frequency, bandwidth, sample rate and gain; acquires samples during observations; and streams them to the Science Data Processor. It also monitors hardware and communications health, supports automatic gain selection, and manages optional bandpass-filter, calibration-load and temperature-protection hardware.

Filters
- [Noolelec Sawbird HI] (https://www.nooelec.com/store/sdr/sdr-addons/sawbird/sawbird-h1.html)

Temperature
- [BME280 Sensor] (https://amzn.eu/d/0dxeseEG)

Software Defined Radios
- RtlSdr,  supports Realtek RTL2832U-based USB software-defined radio (SDR) dongles e.g. [Nooelec SMARTee SDR] (https://amzn.eu/d/0eQ5WVRa)
- Soapy, supports a wide variety of SDRs e.g. [AirSpy Mini] (https://amzn.eu/d/0bbrA5dU)

Telescope Manager
The Telescope Manager coordinates the DMD2000 application suite and manages the complete observation lifecycle. It schedules observations, allocates resources such as dishes and digitisers, configures targets and scans, and orchestrates the Dish Manager, Digitiser and Science Data Processor. It also monitors subsystem health, handles faults and observation aborts, and records completed scan metadata for later analysis.

Science Data Processor
The Science Data Processor receives IQ samples from the Digitiser and converts them into calibrated spectral data for each observation scan. Its configurable processing pipeline performs power-spectrum generation, load calibration, bandpass selection, interference flagging and quality assessment. It stores the resulting scan products and metadata, provides live signal and waterfall displays, and reports scan progress and completion to the Telescope Manager.

Weather Station
The Weather Station collects environmental measurements—including wind speed, temperature, humidity, pressure and precipitation—and reports them to the Dish Manager and Telescope Manager. These observations support operational monitoring and allow unsafe weather conditions to trigger alarms and automatic dish stowing. It supports both physical weather sensors and simulated conditions for development and testing.
