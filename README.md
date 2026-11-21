# DMD2000

This repository contains the source code for the software to be deployed at the Alston Radio Telescope. The Alston Radio Telescope will be constructed during 2025-2026 as part of a MSc programme undertaken with the University of Lancashire. 

# Mission Statement

“To design, build and commission a radio telescope located at the teaching and public outreach facility of the Jeremiah Horrocks Institute that enables future generations of students to gain practical experience in observing and analysing radio frequency emissions from space. 

Observations of known radio sources are conducted to characterise the performance of the radio telescope and achieve a set of science goals.“

# Installation Instructions

pip install -r src/requirements.txt

Shared Python packages live under `src/sdk`: `sdk.env` provides the application
runtime, `sdk.ipc` provides messaging and TCP transport, `sdk.models` defines
shared data contracts, and `sdk.util` contains shared helpers and command-line
utilities. Keep `src` on `PYTHONPATH` when
launching modules from the repository root. For example:

```bash
PYTHONPATH=src python -m sdk.util.hi4pi --lon 90 --lat 0
```

From `src`, the equivalent script command is
`python sdk/util/hi4pi.py --lon 90 --lat 0`. The root `./launch` and `./stop`
wrappers configure the Python path automatically.

## Documentation

- [Offline observation editor: copy, move and delete archived SKY scans](docs/observation_editor.md)

- [Moonhut network: static Mac/Pi/MD01 addresses and iPhone internet sharing](src/config/rotation/scripts/observation_network.md)

- [Making releases: versions, changelog, testing, release branches and maintenance patches](docs/releasing.md)

- [Observation Processing Tool (OPT): usage, baseline subtraction, and longitude–velocity plots](docs/opt.md)

- [Authenticated local console and OPT workspace served by WEB](src/ui/frontend_prototype/README.md)

- [Web application: TCP configuration updates, commands, observation catalogue and authentication](docs/web_api.md)
