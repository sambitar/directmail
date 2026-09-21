"""IPFS blob storage for encrypted message bodies."""

from __future__ import annotations

from typing import Protocol


class BlobStore(Protocol):
    def put(self, data: bytes, *, filename: str) -> str: ...
    def get(self, uri: str) -> bytes: ...


class IpfsBlobStore:
    def __init__(self, client) -> None:
        self.client = client

    def put(self, data: bytes, *, filename: str) -> str:
        cid = self.client.add_bytes(data, filename=filename, pin=True)
        return f"ipfs://{cid}"

    def get(self, uri: str) -> bytes:
        if uri.startswith("local://"):
            raise FileNotFoundError(
                f"Legacy local blob {uri!r} — unlock once to migrate to IPFS"
            )
        return self.client.cat(uri)
