"""Tests for project-local Kubo helper (no network downloads)."""

from __future__ import annotations

from pathlib import Path

import pytest

from directmail.kubo import (
    DEFAULT_KUBO_VERSION,
    _parse_sha512_sidecar,
    find_repo_root,
    ipfs_binary,
    ipfs_repo_path,
    platform_asset,
    tools_root,
)


def test_find_repo_root_from_package():
    root = find_repo_root()
    assert root is not None
    assert (root / "pyproject.toml").is_file()
    assert (root / "src" / "directmail").is_dir()


def test_tools_root_prefers_repo_dot_tools(monkeypatch):
    monkeypatch.delenv("DIRECTMAIL_TOOLS", raising=False)
    root = tools_root(data_dir=Path("/tmp/dm-data"))
    assert root.name == ".tools"
    assert root.parent == find_repo_root()


def test_tools_root_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "mytools"
    monkeypatch.setenv("DIRECTMAIL_TOOLS", str(custom))
    assert tools_root(data_dir=tmp_path / "data") == custom.resolve()


def test_platform_asset_linux_or_darwin():
    name, kind = platform_asset(DEFAULT_KUBO_VERSION)
    assert DEFAULT_KUBO_VERSION in name
    assert name.startswith("kubo_")
    assert kind in ("tar.gz", "zip")


def test_parse_sha512_sidecar_formats():
    digest = "a" * 128
    assert _parse_sha512_sidecar(f"{digest}  kubo_v1.tar.gz\n", "kubo_v1.tar.gz") == digest
    assert _parse_sha512_sidecar(digest + "\n", "kubo_v1.tar.gz") == digest
    with pytest.raises(Exception):
        _parse_sha512_sidecar("# nothing\n", "x.tar.gz")


def test_ipfs_paths(tmp_path):
    tools = tmp_path / ".tools"
    binary = ipfs_binary(tools)
    assert binary.parent == tools / "kubo" / "bin"
    assert ipfs_repo_path(tmp_path / "data") == tmp_path / "data" / "ipfs-repo"
