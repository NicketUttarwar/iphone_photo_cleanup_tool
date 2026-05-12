"""Tests for iphone_cleanup.mount and device_bridge (mocked subprocess)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from iphone_cleanup import device_bridge, mount


def test_mount_tool_helper():
    assert mount._tool(None, "ifuse") == "ifuse"
    assert mount._tool("/opt/ifuse", "ifuse") == "/opt/ifuse"


def test_is_mountpoint_true_when_stdout_matches(tmp_path: Path):
    mp = tmp_path / "mp"
    mp.mkdir()
    needle = str(mp.resolve())
    fake_out = f"foo on {needle} (osxfuse, ...)\n"
    with patch("iphone_cleanup.mount.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=fake_out)
        assert mount.is_mountpoint(mp) is True


def test_is_mountpoint_false_on_bad_return(tmp_path: Path):
    with patch("iphone_cleanup.mount.subprocess.run") as run:
        run.return_value = MagicMock(returncode=1, stdout="")
        assert mount.is_mountpoint(tmp_path) is False


@patch("iphone_cleanup.mount.is_mountpoint", return_value=True)
def test_mount_media_already_mounted(_im, tmp_path: Path):
    ok, msg, proc = mount.mount_media(None, tmp_path, "udid")
    assert ok and "Already" in msg and proc is None


@patch("iphone_cleanup.mount.is_mountpoint", side_effect=[False, False])
@patch("iphone_cleanup.mount.subprocess.Popen")
def test_mount_media_failure_reads_stderr(popen, _im, tmp_path: Path):
    proc = MagicMock()
    proc.stderr.read.return_value = "fuse failed"
    proc.stdout.read.return_value = ""
    popen.return_value = proc
    ok, msg, p = mount.mount_media(None, tmp_path, None)
    assert ok is False
    assert "fuse failed" in msg
    proc.terminate.assert_called_once()
    assert p is None


@patch("iphone_cleanup.mount.is_mountpoint", return_value=True)
@patch("iphone_cleanup.mount.subprocess.run")
def test_unmount_path_success_first_try(run, _im, tmp_path: Path):
    run.return_value = MagicMock(returncode=0, stdout="disk4s1 unmounted.", stderr="")
    ok, msg = mount.unmount_path(tmp_path)
    assert ok and "unmounted" in msg.lower()


@patch("iphone_cleanup.mount.is_mountpoint", return_value=False)
@patch("iphone_cleanup.mount.subprocess.run")
def test_unmount_path_skip_when_not_mountpoint(run, _im, tmp_path: Path):
    ok, msg = mount.unmount_path(tmp_path)
    assert ok is True
    assert "Nothing is mounted" in msg
    run.assert_not_called()


@patch("iphone_cleanup.mount.is_mountpoint", return_value=True)
@patch("iphone_cleanup.mount.subprocess.run")
def test_unmount_path_force_third_try(run, _im, tmp_path: Path):
    run.side_effect = [
        MagicMock(returncode=1, stdout="", stderr="fail1"),
        MagicMock(returncode=1, stdout="", stderr="fail2"),
        MagicMock(returncode=0, stdout="forced", stderr=""),
    ]
    ok, msg = mount.unmount_path(tmp_path)
    assert ok and "forced" in msg.lower()


def test_device_bridge_tool():
    assert device_bridge._tool(None, "idevice_id") == "idevice_id"
    assert device_bridge._tool("/x/idevice_id", "idevice_id") == "/x/idevice_id"


@patch("iphone_cleanup.device_bridge.subprocess.run")
def test_list_udids_success(run):
    run.return_value = MagicMock(returncode=0, stdout="aaa\nbbb\n", stderr="")
    assert device_bridge.list_udids(None) == ["aaa", "bbb"]


@patch("iphone_cleanup.device_bridge.subprocess.run")
def test_list_udids_strips_usb_suffix(run):
    run.return_value = MagicMock(returncode=0, stdout="AAA-UUID (USB)\n", stderr="")
    assert device_bridge.list_udids(None) == ["AAA-UUID"]


@patch("iphone_cleanup.device_bridge.subprocess.run")
def test_ideviceinfo_dict_parses_lines(run):
    run.return_value = MagicMock(
        returncode=0,
        stdout="DeviceName: iPhone\nProductVersion: 17.0\n",
        stderr="",
    )
    info, err = device_bridge.ideviceinfo_dict(None, None)
    assert err == ""
    assert info["DeviceName"] == "iPhone"


@patch("iphone_cleanup.device_bridge.subprocess.run")
def test_detect_device_no_udids_uses_info_udid(run):
    def side_effect(cmd, **kwargs):
        if len(cmd) >= 2 and cmd[1] == "-l":
            return MagicMock(returncode=1, stdout="", stderr="")
        if "-u" not in cmd:
            return MagicMock(
                returncode=0,
                stdout="UniqueDeviceID: THE-UDID\nDeviceName: P\n",
                stderr="",
            )
        return MagicMock(returncode=0, stdout="DeviceName: P\nProductVersion: 1\n", stderr="")

    run.side_effect = side_effect
    d = device_bridge.detect_device(None, None)
    assert d["trusted"] is True
    assert d["udid"] == "THE-UDID"


@patch("iphone_cleanup.device_bridge.subprocess.run")
def test_detect_device_idevice_id_usb_suffix_passes_clean_udid_to_ideviceinfo(run):
    def side_effect(cmd, **kwargs):
        if len(cmd) >= 2 and cmd[1] == "-l":
            return MagicMock(returncode=0, stdout="THE-UDID (USB)\n", stderr="")
        if "ideviceinfo" in cmd[0]:
            joined = " ".join(cmd)
            assert " (USB)" not in joined
            assert "THE-UDID" in joined
            return MagicMock(returncode=0, stdout="DeviceName: Phone\nProductVersion: 18\n", stderr="")
        return MagicMock(returncode=1, stderr="unexpected cmd")

    run.side_effect = side_effect
    d = device_bridge.detect_device(None, None)
    assert d["trusted"] is True
    assert d["udid"] == "THE-UDID"
    assert d["name"] == "Phone"
