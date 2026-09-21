"""CLI entry — launch Textual webmail UI or manage project-local Kubo."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from directmail.ipfs_client import IpfsClient
from directmail.kubo import KuboError, run_ipfs_command, start_daemon
from directmail.mail import MailService
from directmail.tui import run_tui


def default_data_dir() -> Path:
    env = os.environ.get("DIRECTMAIL_DATA")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "directmail"


def ensure_ipfs_client(*, api_url: str, data_dir: Path) -> IpfsClient:
    """Ensure Kubo is reachable — download + start project-local daemon if needed.

    Running ``directmail`` is enough; users should not need a separate
    ``directmail ipfs start`` for normal use.
    """
    client = IpfsClient(api_url, timeout=3.0)
    if client.is_available():
        return client

    print("Starting IPFS (Kubo) for Directmail…", file=sys.stderr)
    try:
        start_daemon(data_dir=data_dir, api_url=api_url, install_if_missing=True)
    except KuboError as exc:
        print(f"error: could not start IPFS: {exc}", file=sys.stderr)
        print(
            "  Manual recovery: directmail ipfs install && directmail ipfs start",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    client = IpfsClient(api_url, timeout=5.0)
    if not client.is_available():
        print(
            f"error: IPFS still unreachable at {api_url} after start",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print("IPFS ready.", file=sys.stderr)
    return client


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="directmail",
        description="Encrypted peer mail over IPFS — terminal webmail",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=default_data_dir(),
        help="Local data directory (default: ~/.local/share/directmail)",
    )
    parser.add_argument(
        "--ipfs-api",
        default=os.environ.get("IPFS_API", "http://127.0.0.1:5001"),
        help="Kubo HTTP API URL",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("ui", help="Launch the Textual webmail UI (default)")

    ipfs_p = sub.add_parser("ipfs", help="Install/start/stop project-local Kubo")
    ipfs_sub = ipfs_p.add_subparsers(dest="ipfs_cmd", required=True)
    install_p = ipfs_sub.add_parser("install", help="Download Kubo into .tools/kubo")
    install_p.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the pinned version is already installed",
    )
    ipfs_sub.add_parser("start", help="Start the project-local ipfs daemon")
    ipfs_sub.add_parser("stop", help="Stop the project-local ipfs daemon")
    ipfs_sub.add_parser("status", help="Show Kubo install + daemon status")

    return parser


def _run_ui(args: argparse.Namespace) -> None:
    ipfs = ensure_ipfs_client(api_url=args.ipfs_api, data_dir=args.data_dir)
    mail = MailService(args.data_dir, ipfs_client=ipfs)
    try:
        run_tui(mail)
    finally:
        mail.close()


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "ipfs":
        code = run_ipfs_command(
            args.ipfs_cmd,
            data_dir=args.data_dir,
            api_url=args.ipfs_api,
            force=bool(getattr(args, "force", False)),
        )
        raise SystemExit(code)

    _run_ui(args)


if __name__ == "__main__":
    main()
