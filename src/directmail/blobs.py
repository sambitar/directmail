"""Local and IPFS blob storage for encrypted message bodies."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol


class BlobStore(Protocol):
    def put(self, data: bytes, *, filename: str) -> str: ...
    def get(self, uri: str) -> bytes: ...


class LocalBlobStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, filename: str) -> str:
        name = filename or f"{uuid.uuid4().hex}.enc"
        # Keep path segment safe
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
        path = self.root / safe
        path.write_bytes(data)
        return f"local://{safe}"

    def get(self, uri: str) -> bytes:
        if not uri.startswith("local://"):
            raise FileNotFoundError(f"Not a local blob: {uri}")
        path = self.root / uri.removeprefix("local://")
        if not path.is_file():
            raise FileNotFoundError(uri)
        return path.read_bytes()


class IpfsBlobStore:
    def __init__(self, client) -> None:
        self.client = client

    def put(self, data: bytes, *, filename: str) -> str:
        cid = self.client.add_bytes(data, filename=filename, pin=True)
        return f"ipfs://{cid}"

    def get(self, uri: str) -> bytes:
        return self.client.cat(uri)


class HybridBlobStore:
    """Prefer IPFS put when available; get routes by URI scheme."""

    def __init__(self, local: LocalBlobStore, ipfs: IpfsBlobStore | None) -> None:
        self.local = local
        self.ipfs = ipfs

    def put(self, data: bytes, *, filename: str) -> str:
        if self.ipfs is not None:
            return self.ipfs.put(data, filename=filename)
        return self.local.put(data, filename=filename)

    def get(self, uri: str) -> bytes:
        if uri.startswith("ipfs://") or uri.startswith("/ipfs/"):
            if self.ipfs is None:
                raise FileNotFoundError(f"IPFS blob but IPFS disabled: {uri}")
            return self.ipfs.get(uri)
        return self.local.get(uri)
