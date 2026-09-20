"""Contact cards — the only first-contact mechanism.

Format:
  dm:v1:<handle>:<ipns_or_local>:<pubkey_b64url>
"""

from __future__ import annotations

import base64
import re

from directmail.crypto import fingerprint_hex
from directmail.models import Contact, Identity

CARD_PREFIX = "dm:v1"
HANDLE_RE = re.compile(r"^[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+$")


class CardError(ValueError):
    pass


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def validate_handle(handle: str) -> str:
    handle = handle.strip()
    if not HANDLE_RE.match(handle):
        raise CardError(
            f"Invalid handle {handle!r} — expected user@machine "
            "(letters, digits, ., _, -)"
        )
    return handle


def encode_card(*, handle: str, ipns: str, pubkey: bytes) -> str:
    handle = validate_handle(handle)
    ipns = ipns.strip()
    if not ipns:
        raise CardError("IPNS / mailbox pointer required")
    if len(pubkey) != 32:
        raise CardError("Pubkey must be 32 raw X25519 bytes")
    return f"{CARD_PREFIX}:{handle}:{ipns}:{b64url_encode(pubkey)}"


def encode_identity_card(identity: Identity) -> str:
    if not identity.ipns:
        raise CardError("Identity has no mailbox pointer yet")
    return encode_card(
        handle=identity.handle, ipns=identity.ipns, pubkey=identity.pubkey
    )


def decode_card(card: str) -> tuple[str, str, bytes]:
    raw = card.strip()
    # Allow whitespace / newlines pasted from terminals
    raw = "".join(raw.split())
    parts = raw.split(":")
    if len(parts) < 4 or f"{parts[0]}:{parts[1]}" != CARD_PREFIX:
        raise CardError(
            "Bad card — expected dm:v1:handle:ipns:pubkey "
            "(copy from the other person's Me screen)"
        )
    # ipns may contain colons rarely; pubkey is last, handle is parts[2],
    # ipns is everything between handle and pubkey.
    handle = validate_handle(parts[2])
    pubkey_b64 = parts[-1]
    ipns = ":".join(parts[3:-1])
    if not ipns:
        raise CardError("Missing mailbox pointer in card")
    try:
        pubkey = b64url_decode(pubkey_b64)
    except Exception as exc:
        raise CardError("Invalid pubkey encoding in card") from exc
    if len(pubkey) != 32:
        raise CardError("Pubkey must decode to 32 bytes")
    return handle, ipns, pubkey


def contact_from_card(card: str, *, added_at) -> Contact:
    handle, ipns, pubkey = decode_card(card)
    return Contact(handle=handle, pubkey=pubkey, ipns=ipns, added_at=added_at)


def card_summary(handle: str, pubkey: bytes) -> str:
    return f"{handle}  [{fingerprint_hex(pubkey)}]"
