import json
from pathlib import Path
from models.obs import ObsModel

path = Path("/Users/r.brederode/github/dmd2000/src/config/jodrell/obs_3hr_solar_drift_scan_airspy.json")
data = json.loads(path.read_text())

obs = ObsModel().from_dict(data)
print("targets:", len(obs.targets))
print("target_configs:", len(obs.target_configs))
print("target_scans before determine:", len(obs.target_scans))
obs.determine_scans()
print("target_scans after determine:", len(obs.target_scans))
print("first scan set scans:", len(obs.target_scans[0].scans) if obs.target_scans else None)
print("current scan:", obs.get_current_tgt_scan())