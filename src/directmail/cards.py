"""Contact cards — the only first-contact mechanism.

Format:
  dm:v1:<handle>:<mailbox>:<pubkey_b64url>

Pubkey is always 43 urlsafe-base64 chars (32 raw X25519 bytes, no padding).
Mailbox may contain colons; we anchor on the fixed-length pubkey at the end.
"""

from __future__ import annotations

import base64
import re

from directmail.crypto import fingerprint_hex
from directmail.models import Contact, Identity

CARD_PREFIX = "dm:v1"
HANDLE_RE = re.compile(r"^[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+$")
# 32 bytes → 43 chars urlsafe b64 without padding
PUBKEY_B64_LEN = 43
CARD_EXTRACT_RE = re.compile(
    rf"{re.escape(CARD_PREFIX)}:"
    r"(?P<handle>[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+):"
    r"(?P<mailbox>.+?):"
    rf"(?P<pk>[A-Za-z0-9_-]{{{PUBKEY_B64_LEN}}})"
)


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
    pk = b64url_encode(pubkey)
    if len(pk) != PUBKEY_B64_LEN:
        raise CardError("Internal error: unexpected pubkey encoding length")
    return f"{CARD_PREFIX}:{handle}:{ipns}:{pk}"


def encode_identity_card(identity: Identity) -> str:
    if not identity.ipns:
        raise CardError("Identity has no mailbox pointer yet")
    return encode_card(
        handle=identity.handle, ipns=identity.ipns, pubkey=identity.pubkey
    )


def decode_card(card: str) -> tuple[str, str, bytes]:
    # Drop whitespace/newlines from terminal wraps; keep extracting a full card
    raw = "".join(card.strip().split())
    match = CARD_EXTRACT_RE.search(raw)
    if not match:
        if raw.startswith(CARD_PREFIX) and ":" in raw:
            tail = raw.rsplit(":", 1)[-1]
            if tail and len(tail) != PUBKEY_B64_LEN:
                raise CardError(
                    f"Card looks truncated — pubkey is {len(tail)} chars, "
                    f"need {PUBKEY_B64_LEN}. Use Copy my card and paste the "
                    "entire string."
                )
        raise CardError(
            "Bad card — expected dm:v1:handle:mailbox:pubkey "
            "(copy from the other person's Contacts screen)"
        )

    handle = validate_handle(match.group("handle"))
    mailbox = match.group("mailbox")
    if not mailbox:
        raise CardError("Missing mailbox pointer in card")
    try:
        pubkey = b64url_decode(match.group("pk"))
    except Exception as exc:
        raise CardError("Invalid pubkey encoding in card") from exc
    if len(pubkey) != 32:
        raise CardError("Pubkey must decode to 32 bytes")
    return handle, mailbox, pubkey


def contact_from_card(card: str, *, added_at) -> Contact:
    handle, ipns, pubkey = decode_card(card)
    return Contact(handle=handle, pubkey=pubkey, ipns=ipns, added_at=added_at)


def card_summary(handle: str, pubkey: bytes) -> str:
    return f"{handle}  [{fingerprint_hex(pubkey)}]"
