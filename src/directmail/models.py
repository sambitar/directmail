"""Data models for Directmail."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Folder(str, Enum):
    INBOX = "inbox"
    SENT = "sent"


@dataclass
class Identity:
    handle: str
    pubkey: bytes
    ipns: str | None  # set once IPFS mailbox key exists; local:// for local backend


@dataclass
class Contact:
    handle: str
    pubkey: bytes
    ipns: str
    added_at: datetime
    note: str = ""


@dataclass
class Message:
    id: str
    folder: Folder
    from_handle: str
    to_handle: str
    created_at: datetime
    body_cid: str  # ipfs://… or local://…
    wrapped_key: bytes
    ciphertext_sha256: str
    preview: str
    read: bool
    body_plaintext: str | None = None  # cached after decrypt
