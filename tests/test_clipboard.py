"""Clipboard helper unit tests (no GUI required)."""

from __future__ import annotations

from directmail.clipboard import _popen_write, copy_via_system, read_via_system


def test_popen_write_false_on_missing_binary():
    assert _popen_write(["definitely-not-a-real-bin-xyz"], b"hi") is False


def test_copy_via_system_returns_bool():
    assert isinstance(copy_via_system("dm:v1:test"), bool)


def test_read_via_system_returns_str_or_none():
    result = read_via_system()
    assert result is None or isinstance(result, str)
