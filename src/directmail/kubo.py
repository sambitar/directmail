"""Project-local Kubo (go-ipfs) install and daemon lifecycle."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from directmail.ipfs_client import DEFAULT_API, IpfsClient

# Pin for reproducible installs; override with DIRECTMAIL_KUBO_VERSION (e.g. v0.43.1).
DEFAULT_KUBO_VERSION = "v0.43.1"
RELEASE_BASE = "https://github.com/ipfs/kubo/releases/download"


class KuboError(Exception):
    pass


def kubo_version() -> str:
    v = os.environ.get("DIRECTMAIL_KUBO_VERSION", DEFAULT_KUBO_VERSION).strip()
    if not v.startswith("v"):
        v = f"v{v}"
    return v


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk up from start (or this file) looking for the directmail checkout."""
    here = start or Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        pyproject = parent / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            text = pyproject.read_text(encoding="utf-8")
        except OSError:
            continue
        if 'name = "directmail"' in text or "name = 'directmail'" in text:
            return parent
    return None


def tools_root(*, data_dir: Path | None = None) -> Path:
    """Where Kubo binaries live: DIRECTMAIL_TOOLS, else <repo>/.tools, else <data>/tools."""
    env = os.environ.get("DIRECTMAIL_TOOLS")
    if env:
        return Path(env).expanduser().resolve()
    repo = find_repo_root()
    if repo is not None:
        return (repo / ".tools").resolve()
    if data_dir is None:
        env_data = os.environ.get("DIRECTMAIL_DATA")
        data_dir = Path(env_data) if env_data else Path.home() / ".local" / "share" / "directmail"
    return (Path(data_dir) / "tools").resolve()


def kubo_home(tools: Path) -> Path:
    return tools / "kubo"


def kubo_bin_dir(tools: Path) -> Path:
    return kubo_home(tools) / "bin"


def ipfs_binary(tools: Path) -> Path:
    name = "ipfs.exe" if platform.system() == "Windows" else "ipfs"
    return kubo_bin_dir(tools) / name


def ipfs_repo_path(data_dir: Path) -> Path:
    return Path(data_dir) / "ipfs-repo"


def pid_file(data_dir: Path) -> Path:
    return Path(data_dir) / "ipfs-daemon.pid"


def log_file(data_dir: Path) -> Path:
    return Path(data_dir) / "ipfs-daemon.log"


def platform_asset(version: str | None = None) -> tuple[str, str]:
    """Return (archive_filename, kind) where kind is 'tar.gz' or 'zip'."""
    version = version or kubo_version()
    system = platform.system()
    machine = platform.machine().lower()

    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        raise KuboError(f"Unsupported CPU architecture: {platform.machine()}")

    if system == "Linux":
        return f"kubo_{version}_linux-{arch}.tar.gz", "tar.gz"
    if system == "Darwin":
        return f"kubo_{version}_darwin-{arch}.tar.gz", "tar.gz"
    if system == "Windows":
        if arch != "amd64":
            raise KuboError("Windows Kubo builds are amd64-only in this helper")
        return f"kubo_{version}_windows-amd64.zip", "zip"
    raise KuboError(f"Unsupported OS: {system}")


def _download(url: str, dest: Path, *, timeout: float = 120.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urlopen(url, timeout=timeout) as resp, dest.open("wb") as out:
            shutil.copyfileobj(resp, out)
    except (URLError, TimeoutError, OSError) as exc:
        raise KuboError(f"Download failed ({url}): {exc}") from exc


def _sha512_file(path: Path) -> str:
    h = hashlib.sha512()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_sha512_sidecar(text: str, archive_name: str) -> str:
    """Accept `hash  filename` or `hash` alone."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 1 and len(parts[0]) == 128:
            return parts[0].lower()
        if len(parts) >= 2 and parts[-1].endswith(archive_name):
            return parts[0].lower()
        if len(parts) >= 2 and len(parts[0]) == 128:
            return parts[0].lower()
    raise KuboError("Could not parse sha512 sidecar from release")


def _extract_ipfs_binary(archive: Path, kind: str, dest_bin: Path) -> None:
    dest_bin.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="directmail-kubo-") as tmp:
        tmp_path = Path(tmp)
        if kind == "tar.gz":
            with tarfile.open(archive, "r:gz") as tf:
                # filter= is 3.12+; keep extractall compatible on 3.11
                try:
                    tf.extractall(tmp_path, filter="data")
                except TypeError:
                    tf.extractall(tmp_path)
        elif kind == "zip":
            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(tmp_path)
        else:
            raise KuboError(f"Unknown archive kind: {kind}")

        candidates = list(tmp_path.rglob("ipfs.exe" if dest_bin.suffix == ".exe" else "ipfs"))
        # Prefer a file named exactly ipfs / ipfs.exe, not directories
        files = [p for p in candidates if p.is_file()]
        if not files:
            raise KuboError("ipfs binary not found inside Kubo archive")
        src = files[0]
        if dest_bin.exists():
            dest_bin.unlink()
        shutil.copy2(src, dest_bin)
        if dest_bin.suffix != ".exe":
            dest_bin.chmod(dest_bin.stat().st_mode | 0o111)


def install_kubo(
    *,
    data_dir: Path,
    tools: Path | None = None,
    version: str | None = None,
    force: bool = False,
) -> Path:
    """Download + verify + extract Kubo into tools/kubo/bin. Returns binary path."""
    version = version or kubo_version()
    tools = tools or tools_root(data_dir=data_dir)
    binary = ipfs_binary(tools)
    version_stamp = kubo_home(tools) / "VERSION"

    if binary.is_file() and not force:
        if version_stamp.is_file() and version_stamp.read_text(encoding="utf-8").strip() == version:
            return binary

    archive_name, kind = platform_asset(version)
    base = f"{RELEASE_BASE}/{version}"
    archive_url = f"{base}/{archive_name}"
    sha_url = f"{base}/{archive_name}.sha512"

    kubo_home(tools).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="directmail-kubo-dl-") as tmp:
        tmp_path = Path(tmp)
        archive_path = tmp_path / archive_name
        sha_path = tmp_path / f"{archive_name}.sha512"
        print(f"Downloading {archive_url} …")
        _download(archive_url, archive_path)
        print(f"Downloading {sha_url} …")
        _download(sha_url, sha_path)
        expected = _parse_sha512_sidecar(sha_path.read_text(encoding="utf-8"), archive_name)
        actual = _sha512_file(archive_path)
        if actual != expected:
            raise KuboError(
                f"SHA-512 mismatch for {archive_name}:\n  expected {expected}\n  got      {actual}"
            )
        print("Checksum OK — extracting ipfs binary …")
        _extract_ipfs_binary(archive_path, kind, binary)

    version_stamp.write_text(version + "\n", encoding="utf-8")
    print(f"Installed Kubo {version} → {binary}")
    return binary


def _read_pid(data_dir: Path) -> int | None:
    path = pid_file(data_dir)
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_daemon_running(*, data_dir: Path, api_url: str = DEFAULT_API) -> bool:
    if IpfsClient(api_url).is_available():
        return True
    pid = _read_pid(data_dir)
    return pid is not None and _pid_alive(pid)


def ensure_repo_initialized(binary: Path, repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    config = repo / "config"
    if config.is_file():
        return
    env = os.environ.copy()
    env["IPFS_PATH"] = str(repo)
    print(f"Initializing IPFS repo at {repo} …")
    result = subprocess.run(
        [str(binary), "init"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise KuboError(f"ipfs init failed:\n{result.stderr or result.stdout}")


def start_daemon(
    *,
    data_dir: Path,
    api_url: str = DEFAULT_API,
    tools: Path | None = None,
    install_if_missing: bool = True,
) -> None:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    tools = tools or tools_root(data_dir=data_dir)
    binary = ipfs_binary(tools)

    if not binary.is_file():
        if not install_if_missing:
            raise KuboError(f"Kubo not installed at {binary}. Run: directmail ipfs install")
        install_kubo(data_dir=data_dir, tools=tools)

    if IpfsClient(api_url).is_available():
        print(f"IPFS API already reachable at {api_url}")
        return

    pid = _read_pid(data_dir)
    if pid is not None and _pid_alive(pid):
        print(f"Daemon PID {pid} is alive but API not ready yet — waiting …")
        _wait_for_api(api_url, timeout=60.0)
        return

    repo = ipfs_repo_path(data_dir)
    ensure_repo_initialized(binary, repo)

    env = os.environ.copy()
    env["IPFS_PATH"] = str(repo)
    log_path = log_file(data_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 — kept open for daemon lifetime
    print(f"Starting ipfs daemon (IPFS_PATH={repo}) …")
    print(f"  logs → {log_path}")
    proc = subprocess.Popen(
        [str(binary), "daemon", "--migrate=true", "--routing=dhtclient"],
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    pid_file(data_dir).write_text(str(proc.pid) + "\n", encoding="utf-8")
    try:
        _wait_for_api(api_url, timeout=90.0)
    except KuboError:
        if proc.poll() is not None:
            raise KuboError(
                f"ipfs daemon exited early (code {proc.returncode}). See {log_path}"
            ) from None
        raise
    print(f"IPFS daemon ready at {api_url} (pid {proc.pid})")


def _wait_for_api(api_url: str, *, timeout: float) -> None:
    deadline = time.time() + timeout
    client = IpfsClient(api_url, timeout=2.0)
    while time.time() < deadline:
        if client.is_available():
            return
        time.sleep(0.4)
    raise KuboError(f"Timed out waiting for IPFS API at {api_url}")


def stop_daemon(*, data_dir: Path, api_url: str = DEFAULT_API) -> None:
    data_dir = Path(data_dir)
    client = IpfsClient(api_url, timeout=5.0)
    if client.is_available():
        try:
            client._post("shutdown")  # noqa: SLF001 — Kubo RPC
            print("Sent ipfs shutdown via API")
        except Exception as exc:  # noqa: BLE001 — fall through to signal
            print(f"API shutdown failed ({exc}); trying SIGTERM …")

    pid = _read_pid(data_dir)
    deadline = time.time() + 15.0
    while time.time() < deadline:
        if not client.is_available() and (pid is None or not _pid_alive(pid)):
            break
        time.sleep(0.3)

    if pid is not None and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Sent SIGTERM to pid {pid}")
        except ProcessLookupError:
            pass
        deadline = time.time() + 10.0
        while time.time() < deadline and _pid_alive(pid):
            time.sleep(0.2)
        if _pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
            print(f"Sent SIGKILL to pid {pid}")

    pf = pid_file(data_dir)
    if pf.exists():
        pf.unlink()
    print("IPFS daemon stopped")


def status_report(*, data_dir: Path, api_url: str = DEFAULT_API) -> dict[str, object]:
    tools = tools_root(data_dir=data_dir)
    binary = ipfs_binary(tools)
    version_stamp = kubo_home(tools) / "VERSION"
    installed_version = (
        version_stamp.read_text(encoding="utf-8").strip() if version_stamp.is_file() else None
    )
    client = IpfsClient(api_url, timeout=3.0)
    api_ok = client.is_available()
    peer_id = None
    if api_ok:
        try:
            peer_id = client.id().get("ID")
        except Exception:  # noqa: BLE001
            peer_id = None
    pid = _read_pid(data_dir)
    return {
        "tools_root": str(tools),
        "binary": str(binary),
        "binary_exists": binary.is_file(),
        "kubo_version": installed_version,
        "ipfs_repo": str(ipfs_repo_path(data_dir)),
        "api_url": api_url,
        "api_reachable": api_ok,
        "peer_id": peer_id,
        "pid": pid,
        "pid_alive": pid is not None and _pid_alive(pid),
        "log_file": str(log_file(data_dir)),
    }


def print_status(*, data_dir: Path, api_url: str = DEFAULT_API) -> None:
    info = status_report(data_dir=data_dir, api_url=api_url)
    for key, value in info.items():
        print(f"{key}: {value}")


def run_ipfs_command(cmd: str, *, data_dir: Path, api_url: str, force: bool = False) -> int:
    try:
        if cmd == "install":
            install_kubo(data_dir=data_dir, force=force)
        elif cmd == "start":
            start_daemon(data_dir=data_dir, api_url=api_url)
        elif cmd == "stop":
            stop_daemon(data_dir=data_dir, api_url=api_url)
        elif cmd == "status":
            print_status(data_dir=data_dir, api_url=api_url)
        else:
            print(f"Unknown ipfs command: {cmd}", file=sys.stderr)
            return 2
    except KuboError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
