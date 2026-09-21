"""Outbox mailbox publish / pull over IPFS + IPNS."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


OUTBOX_SCHEMA = "directmail-outbox-v1"


@dataclass
class OutboxEnvelope:
    id: str
    from_handle: str
    to_handle: str
    created_at: str  # ISO
    body_cid: str
    wrapped_key_b64: str
    ciphertext_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "from": self.from_handle,
            "to": self.to_handle,
            "created_at": self.created_at,
            "body_cid": self.body_cid,
            "wrapped_key_b64": self.wrapped_key_b64,
            "ciphertext_sha256": self.ciphertext_sha256,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OutboxEnvelope:
        return cls(
            id=data["id"],
            from_handle=data["from"],
            to_handle=data["to"],
            created_at=data["created_at"],
            body_cid=data["body_cid"],
            wrapped_key_b64=data["wrapped_key_b64"],
            ciphertext_sha256=data["ciphertext_sha256"],
        )


def build_outbox_document(
    *,
    handle: str,
    pubkey_b64: str,
    envelopes: list[OutboxEnvelope],
) -> dict[str, Any]:
    return {
        "schema": OUTBOX_SCHEMA,
        "handle": handle,
        "pubkey_b64": pubkey_b64,
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "messages": [e.to_dict() for e in envelopes],
    }


def parse_outbox_document(raw: bytes) -> list[OutboxEnvelope]:
    data = json.loads(raw.decode("utf-8"))
    if data.get("schema") != OUTBOX_SCHEMA:
        return []
    return [OutboxEnvelope.from_dict(m) for m in data.get("messages") or []]


class MailboxPublisher:
    """Publishes the outbox snapshot under our IPNS name."""

    def __init__(self, client) -> None:
        if client is None:
            raise ValueError("IPFS client is required")
        self.client = client

    def publish(self, document: dict[str, Any]) -> str:
        raw = json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")
        cid = self.client.add_bytes(raw, filename="outbox.json", pin=True)
        return self.client.name_publish(cid)

    def resolve_outbox(self, ipns: str) -> bytes:
        if ipns.startswith("local://"):
            raise FileNotFoundError(
                f"Contact still uses a local:// mailbox ({ipns}) — they must "
                "re-open Directmail with IPFS and share a new card"
            )
        path = self.client.name_resolve(ipns)
        return self.client.cat(path)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(text: str) -> bytes:
    return base64.b64decode(text)
