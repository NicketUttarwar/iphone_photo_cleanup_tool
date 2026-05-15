"""Tests for iphone_cleanup.settings."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from iphone_cleanup.settings import Settings, deep_merge, load_merged_settings, load_yaml


def test_deep_merge_nested_and_replace():
    base = {"a": 1, "b": {"x": 1, "y": 2}, "c": 3}
    over = {"b": {"y": 9}, "c": 0}
    assert deep_merge(base, over) == {"a": 1, "b": {"x": 1, "y": 9}, "c": 0}


def test_load_yaml_empty_file(tmp_path: Path):
    p = tmp_path / "e.yaml"
    p.write_text("", encoding="utf-8")
    assert load_yaml(p) == {}


def test_load_yaml_null_document(tmp_path: Path):
    p = tmp_path / "n.yaml"
    p.write_text("null\n", encoding="utf-8")
    assert load_yaml(p) == {}


def test_load_yaml_invalid_root(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("[1, 2]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_yaml(p)


def test_load_merged_settings_with_local(tmp_path: Path):
    d = tmp_path / "config"
    d.mkdir()
    defaults = d / "def.yaml"
    local = d / "loc.yaml"
    defaults.write_text(yaml.safe_dump({"server": {"port": 1}, "x": {"a": 1}}), encoding="utf-8")
    local.write_text(yaml.safe_dump({"server": {"host": "0.0.0.0"}, "x": {"b": 2}}), encoding="utf-8")
    merged = load_merged_settings(defaults, local)
    assert merged["server"]["port"] == 1
    assert merged["server"]["host"] == "0.0.0.0"
    assert merged["x"] == {"a": 1, "b": 2}


def test_load_merged_missing_local(tmp_path: Path):
    d = tmp_path / "def.yaml"
    d.write_text("server:\n  port: 55\n", encoding="utf-8")
    merged = load_merged_settings(d, tmp_path / "nope.yaml")
    assert merged["server"]["port"] == 55


def test_settings_from_dict_defaults(repo_root: Path, defaults_path: Path):
    merged = load_merged_settings(defaults_path, None)
    s = Settings.from_dict(repo_root, merged)
    assert s.server_host == "127.0.0.1"
    assert s.server_port == 18765
    assert s.data_dir == (repo_root / "_pytest_data/data").resolve()
    assert s.delete_chunk_size == 5
    assert s.duplicates_auto_face_eye is False
    assert s.phash_threshold == 6
