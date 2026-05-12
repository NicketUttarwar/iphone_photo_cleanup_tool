"""Tests for iphone_cleanup.__main__ CLI parsing."""

from __future__ import annotations

import sys
from unittest.mock import patch

from iphone_cleanup import __main__ as mainmod


def test_parse_args_required():
    with patch.object(sys, "argv", ["prog", "--repo-root", "/r", "--defaults-config", "/d/app.yaml"]):
        ns = mainmod.parse_args()
        assert ns.repo_root == "/r"
        assert ns.defaults_config == "/d/app.yaml"
        assert ns.local_config is None
        assert ns.no_open_browser is False


def test_parse_args_optional_local_and_no_browser():
    with patch.object(
        sys,
        "argv",
        [
            "prog",
            "--repo-root",
            "/r",
            "--defaults-config",
            "/d.yaml",
            "--local-config",
            "/l.yaml",
            "--no-open-browser",
        ],
    ):
        ns = mainmod.parse_args()
        assert ns.local_config == "/l.yaml"
        assert ns.no_open_browser is True


def test_package_version():
    import iphone_cleanup

    assert isinstance(iphone_cleanup.__version__, str)
    assert iphone_cleanup.__version__
