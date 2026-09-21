"""Copy text to the real OS clipboard (OSC 52 alone often fails on Linux)."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.app import App


def _popen_write(cmd: list[str], data: bytes, *, wait: float = 1.0) -> bool:
    """Write stdin to a clipboard helper. xclip forks and may not exit — that is OK."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    try:
        assert proc.stdin is not None
        proc.stdin.write(data)
        proc.stdin.close()
    except OSError:
        proc.kill()
        return False
    try:
        proc.wait(timeout=wait)
    except subprocess.TimeoutExpired:
        # xclip -silent keeps a child alive to serve the selection
        pass
    return True


def copy_via_system(text: str) -> bool:
    """Best-effort write to the desktop clipboard. Returns True on success."""
    data = text.encode("utf-8")

    # macOS
    if shutil.which("pbcopy") and _popen_write(["pbcopy"], data):
        return True

    # Wayland
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        if _popen_write(["wl-copy", "--type", "text/plain"], data):
            return True

    # X11 — prefer clipboard; also fill primary for middle-click paste
    if shutil.which("xclip"):
        ok = _popen_write(["xclip", "-selection", "clipboard", "-in", "-silent"], data)
        _popen_write(["xclip", "-selection", "primary", "-in", "-silent"], data)
        if ok:
            return True
    if shutil.which("xsel"):
        if _popen_write(["xsel", "--clipboard", "--input"], data):
            return True

    # Windows
    clip = shutil.which("clip.exe") or shutil.which("clip")
    if clip and _popen_write([clip], data):
        return True

    return False


def read_via_system() -> str | None:
    """Best-effort read from the desktop clipboard."""
    try:
        if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
            out = subprocess.run(
                ["wl-paste", "-n"],
                check=True,
                timeout=3,
                capture_output=True,
            )
            return out.stdout.decode("utf-8", errors="replace")
        if shutil.which("xclip"):
            out = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                check=True,
                timeout=3,
                capture_output=True,
            )
            return out.stdout.decode("utf-8", errors="replace")
        if shutil.which("xsel"):
            out = subprocess.run(
                ["xsel", "--clipboard", "--output"],
                check=True,
                timeout=3,
                capture_output=True,
            )
            return out.stdout.decode("utf-8", errors="replace")
        if shutil.which("pbpaste"):
            out = subprocess.run(
                ["pbpaste"],
                check=True,
                timeout=3,
                capture_output=True,
            )
            return out.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def copy_text(app: App, text: str) -> bool:
    """Copy via system clipboard first, then Textual OSC 52 as a secondary path."""
    ok = copy_via_system(text)
    try:
        app.copy_to_clipboard(text)
    except Exception:  # noqa: BLE001 — OSC 52 is best-effort
        pass
    return ok
