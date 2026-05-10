from models.obs import ObsModel


def test_retry_scan_iteration_matches_current_planned_scan():
    obs = ObsModel.load_from_disk(
        input_dir="src/config/jodrell",
        filename="obs_3hr_solar_drift_scan.json",
    )
    obs.determine_scans()

    obs.tgt_idx = 47
    obs.tgt_scan = 0
    planned_scan = obs.get_current_tgt_scan()

    retry_scan = obs.get_target_scan_by_id(f"{obs.obs_id}-47-0-159")

    assert retry_scan is planned_scan
    assert retry_scan.scan_id == f"{obs.obs_id}-47-0-0"
