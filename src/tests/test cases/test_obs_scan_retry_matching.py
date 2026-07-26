from models.obs import ObsModel
from models.scan import ScanModel
from models.target import TargetScanSet


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


def test_target_scan_set_finds_sparse_persisted_iteration():
    persisted_scan = ScanModel(
        obs_id="obs001",
        tgt_idx=0,
        freq_scan=0,
        scan_iter=18,
    )
    scans = TargetScanSet(
        obs_id="obs001",
        tgt_idx=0,
        freq_scans=1,
        scan_iterations=1,
        scans=[persisted_scan],
    )

    assert scans.get_scan_by_index(0, 18) is persisted_scan
    assert scans.get_scan_by_index(0, 0) is None
