"""CLI entry — launch Textual webmail UI."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from directmail.ipfs_client import IpfsClient
from directmail.mail import MailService
from directmail.tui import run_tui


def default_data_dir() -> Path:
    env = os.environ.get("DIRECTMAIL_DATA")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "directmail"


def main(argv: list[str] | None = None) -> None:
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
        "--backend",
        choices=("ipfs", "local"),
        default=os.environ.get("DIRECTMAIL_BACKEND", "ipfs"),
        help="ipfs (default) or local blobs/outbox for offline demos",
    )
    parser.add_argument(
        "--ipfs-api",
        default=os.environ.get("IPFS_API", "http://127.0.0.1:5001"),
        help="Kubo HTTP API URL",
    )
    args = parser.parse_args(argv)

    ipfs = None
    use_ipfs = False
    if args.backend == "ipfs":
        ipfs = IpfsClient(args.ipfs_api)
        if ipfs.is_available():
            use_ipfs = True
        else:
            print(
                "IPFS daemon not reachable — falling back to local backend.\n"
                f"  Tried {args.ipfs_api}. Start Kubo or pass --backend local."
            )
            ipfs = None

    mail = MailService(args.data_dir, ipfs_client=ipfs, use_ipfs=use_ipfs)
    try:
        run_tui(mail)
    finally:
        mail.close()


if __name__ == "__main__":
    main()
