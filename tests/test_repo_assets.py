"""Repository assets and helper scripts (non-Python surface)."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_shell_scripts_parse():
    for name in ("run.sh", "check_host_prerequisites.sh"):
        script = REPO / "scripts" / name
        assert script.is_file()
        r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, timeout=10)
        assert r.returncode == 0, r.stderr


def test_static_and_templates_present():
    static_js = REPO / "src" / "iphone_cleanup" / "static" / "app.js"
    idx = REPO / "src" / "iphone_cleanup" / "templates" / "index.html"
    pre = REPO / "src" / "iphone_cleanup" / "templates" / "prerequisites.html"
    for p in (static_js, idx, pre):
        assert p.is_file() and p.stat().st_size > 50
