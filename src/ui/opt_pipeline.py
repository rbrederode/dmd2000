"""Validate and snapshot per-run pipeline settings without changing profiles."""
from models.pipeline import PipelineConfig, StepConfig, StepType
from ui.pipeline_help import step_help


def pipeline_snapshot(profile_path, override=None):
    config = PipelineConfig.load_from_disk(input_dir=str(profile_path), filename="PipelineConfig.json")
    if override is None:
        return config.to_dict()
    if not isinstance(override, dict) or not override or len(override) > 64:
        raise ValueError("Pipeline must map digitiser IDs (or default) to lists of steps")
    steps_map = {}
    for digitiser, entries in override.items():
        if not isinstance(digitiser, str) or not isinstance(entries, list) or len(entries) > 64:
            raise ValueError("Each pipeline entry must contain at most 64 steps")
        steps = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) - {"step", "params"}:
                raise ValueError("Each step needs step and params fields")
            name, params = entry.get("step"), entry.get("params", {})
            if not isinstance(name, str) or name not in StepType.__members__ or not isinstance(params, dict):
                raise ValueError("Choose a built-in pipeline step and JSON parameters")
            if params.get("context") not in ("spr", "cal", "mpr"):
                raise ValueError("Step context must be spr, cal or mpr")
            if any(key in params for key in ("scan", "sky_q", "cal_q")):
                raise ValueError("Runtime scan and queue references cannot be supplied")
            if "enabled" in params and not isinstance(params["enabled"], bool):
                raise ValueError("Step enabled must be true or false")
            step = StepConfig(step=StepType[name], params=params)
            if name == "BASELINE":
                from sdp.pipeline.steps.baseline import Baseline
                Baseline(step)
            steps.append(step)
        steps_map[digitiser] = steps
    return PipelineConfig(steps_map=steps_map).to_dict()


def editable_pipeline(snapshot):
    config = PipelineConfig.from_dict(snapshot)
    return {key: [{"step": step.step.name if isinstance(step.step, StepType) else str(step.step),
                   "params": step.to_dict()["params"]} for step in steps]
            for key, steps in config.steps_map.items()}


def baseline_applied(snapshot, digitiser):
    config = PipelineConfig.from_dict(snapshot)
    return any(step.get_step_key() == "baseline" and step.params.get("context") == "cal"
               and step.params.get("enabled", True) and step.params.get("method", "flatten") != "none"
               for step in config.get_steps(digitiser))


def qa_metric_settings(snapshot, digitiser):
    """The enabled MPR QA policy used for the displayed aggregate."""
    policy = dict(noise_estimator="median_mad", window_frac=.2, smooth_window=10)
    if snapshot:
        steps_map = snapshot.get("steps_map", {})
        for step in steps_map.get(digitiser, steps_map.get("default", [])):
            name = step.get("step")
            if isinstance(name, dict):
                name = name.get("value")
            params = step.get("params", {})
            if name in ("QA", "qa", StepType.QA) and params.get("context") == "mpr" and params.get("enabled", True):
                policy.update({key: params[key] for key in policy if key in params})
    return policy


def available_pipeline_steps():
    """Built-in steps with working implementations and safe initial parameters."""
    from sdp.pipeline.pipeline_factory import ProcessingPipelineFactory
    factory = ProcessingPipelineFactory(PipelineConfig())
    defaults = {
        StepType.DC_SPIKE: {"context": "spr"},
        StepType.LOAD: {"context": "cal"},
        StepType.BANDPASS_FILTER: {"context": "cal", "ranges_pct": [[0, 100]]},
        StepType.RFI_FLAG: {"context": "cal", "threshold": 5, "window_size": 21},
        StepType.BASELINE: {"context": "cal", "method": "flatten", "ranges_pct": [[0, 10], [90, 100]]},
        StepType.QA: {"context": "mpr", "window_frac": .2, "smooth_window": 10, "noise_estimator": "median_mad"},
    }
    # GAIN_CAL and TSYS_CAL currently return inputs unchanged; do not offer them
    # as implemented calibration algorithms. Existing profile entries are retained.
    return [{"step": step.name, "params": params,
             "description": factory.get_step_class(step).describe(), "help": step_help(step.name)}
            for step, params in defaults.items()]
