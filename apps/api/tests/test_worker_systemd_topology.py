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
    video_unit = heavy
    assert "VIDEO_PROXY_HOST_SOURCE_CEILING_BYTES=1500000000" in video_unit
    assert "VIDEO_PROXY_MIN_FREE_DISK_BYTES=8589934592" in video_unit
    assert "CPUQuota=100%" in video_unit
    assert "MemoryHigh=1G" in video_unit
    assert "MemoryMax=1536M" in video_unit
    assert "creative-asset-manager-image-worker-4.service" in deploy
    assert 'CAM_IMAGE_WORKER_4_HEALTH_PORT:-8085' in deploy
    assert 'CAM_VIDEO_DELIVERY_WORKER_HEALTH_PORT:-8088' in deploy
    assert 'CAM_BACKEND_MIN_FREE_MIB:-2048' in deploy
    assert 'CAM_BACKEND_RELEASE_HEADROOM_PERCENT:-125' in deploy
    assert "check_disk_headroom" in deploy
