from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_production_video_workers_are_split_into_heavy_and_delivery_lanes():
    heavy = (ROOT / "deploy/systemd/creative-asset-manager-video-worker.service").read_text()
    delivery = (
        ROOT / "deploy/systemd/creative-asset-manager-video-delivery-worker.service"
    ).read_text()
    deploy = (ROOT / "scripts/cam-rebuild-backend.sh").read_text()

    assert "Environment=WORKER_ROLE=video-heavy" in heavy
    assert "Environment=WORKER_ID=creativeasset-video-heavy" in heavy
    assert "Environment=WORKER_HEALTH_PORT=8082" in heavy

    assert "Environment=WORKER_ROLE=video-delivery" in delivery
    assert "Environment=WORKER_ID=creativeasset-video-delivery" in delivery
    assert "Environment=WORKER_HEALTH_PORT=8088" in delivery

    assert "creative-asset-manager-video-worker.service" in deploy
    assert "creative-asset-manager-video-delivery-worker.service" in deploy
    assert 'CAM_VIDEO_DELIVERY_WORKER_HEALTH_PORT:-8088' in deploy
    assert 'CAM_BACKEND_MIN_FREE_MIB:-2048' in deploy
    assert 'CAM_BACKEND_RELEASE_HEADROOM_PERCENT:-125' in deploy
    assert "check_disk_headroom" in deploy
