"""session_bootstrap: device + mount reconciliation."""

from __future__ import annotations

from unittest.mock import patch

from iphone_cleanup.session_bootstrap import bootstrap_runtime, sync_device, sync_mount_from_disk
from iphone_cleanup.state import Phase


@patch("iphone_cleanup.session_bootstrap.device_bridge.detect_device")
def test_sync_device_trusted_sets_device_detected(mock_det, app_ctx):
    mock_det.return_value = {"trusted": True, "udid": "u1", "name": "Phone", "error": None}
    app_ctx.state.set_phase(Phase.idle)
    dev = sync_device(app_ctx)
    assert dev["trusted"] is True
    assert app_ctx.state.phase == Phase.device_detected


@patch("iphone_cleanup.session_bootstrap.device_bridge.detect_device")
def test_sync_device_untrusted_idle(mock_det, app_ctx):
    mock_det.return_value = {"trusted": False, "udid": None, "error": "No device"}
    app_ctx.state.set_phase(Phase.idle)
    sync_device(app_ctx)
    assert app_ctx.state.phase == Phase.idle


@patch("iphone_cleanup.session_bootstrap.mount.is_mountpoint", return_value=True)
def test_sync_mount_from_disk_mounted(mock_mp, app_ctx, settings):
    app_ctx.state.set_phase(Phase.device_detected)
    app_ctx.state.device_info = {"trusted": True, "udid": "u1"}
    sync_mount_from_disk(app_ctx)
    assert app_ctx.state.mount_path == settings.mount_point.resolve()
    assert app_ctx.state.phase == Phase.mounted


@patch("iphone_cleanup.session_bootstrap.mount.is_mountpoint", return_value=False)
def test_sync_mount_preserves_mounting(mock_mp, app_ctx):
    app_ctx.state.set_phase(Phase.mounting)
    app_ctx.state.mount_path = None
    sync_mount_from_disk(app_ctx)
    assert app_ctx.state.phase == Phase.mounting


@patch("iphone_cleanup.session_bootstrap.mount.is_mountpoint", return_value=False)
def test_sync_mount_preserves_deleting(mock_mp, app_ctx, settings):
    mp = settings.mount_point.resolve()
    mp.mkdir(parents=True, exist_ok=True)
    app_ctx.state.set_phase(Phase.deleting)
    app_ctx.state.mount_path = mp
    sync_mount_from_disk(app_ctx)
    assert app_ctx.state.phase == Phase.deleting
    assert app_ctx.state.mount_path == mp


@patch("iphone_cleanup.session_bootstrap.sync_mount_from_disk")
@patch("iphone_cleanup.session_bootstrap.sync_device")
def test_bootstrap_runtime_calls_both(mock_dev, mock_mount, app_ctx):
    bootstrap_runtime(app_ctx)
    mock_dev.assert_called_once()
    mock_mount.assert_called_once()
