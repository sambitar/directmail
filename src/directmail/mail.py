"""Directmail application service: register, contacts, send, sync, read."""

from __future__ import annotations

import getpass
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path

from directmail.blobs import IpfsBlobStore
from directmail.cards import (
    CardError,
    contact_from_card,
    encode_identity_card,
    validate_handle,
)
from directmail.crypto import (
    decrypt_aesgcm,
    fingerprint_hex,
    generate_identity_keypair,
    generate_message_key,
    message_aad,
    sha256_hex,
    unwrap_key,
    wrap_key,
    encrypt_aesgcm,
)
from directmail.keystore import Keystore, KeystoreAuthError, KeystoreLockedError
from directmail.mailbox import (
    MailboxPublisher,
    OutboxEnvelope,
    b64,
    b64d,
    build_outbox_document,
    parse_outbox_document,
)
from directmail.models import Contact, Folder, Identity, Message
from directmail.store import Store


def default_handle() -> str:
    user = getpass.getuser()
    host = socket.gethostname().split(".")[0]
    return f"{user}@{host}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MailService:
    def __init__(self, data_dir: str | Path, *, ipfs_client) -> None:
        if ipfs_client is None:
            raise ValueError("ipfs_client is required — Directmail is IPFS-only")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data_dir / "mail.db")
        self.keystore = Keystore(self.data_dir / "keystore")
        self.ipfs = ipfs_client
        self.blobs = IpfsBlobStore(ipfs_client)
        self.mailbox = MailboxPublisher(ipfs_client)
        # Legacy on-disk blobs from older local-backend installs (read-only migrate)
        self._legacy_blobs = self.data_dir / "blobs"

    def close(self) -> None:
        self.store.close()

    # --- identity ---

    def is_registered(self) -> bool:
        return self.keystore.is_initialized and self.store.get_identity() is not None

    def register(self, passphrase: str, handle: str | None = None) -> Identity:
        if self.is_registered():
            raise KeystoreAuthError("Already registered")
        handle = validate_handle(handle or default_handle())
        keys = generate_identity_keypair()
        self.keystore.initialize(passphrase, keys.private_key_bytes)
        ipns = self.ipfs.my_ipns()
        identity = Identity(handle=handle, pubkey=keys.public_key_bytes, ipns=ipns)
        self.store.set_identity(identity)
        self._publish_outbox()
        identity = self.store.get_identity()
        assert identity is not None
        return identity

    def unlock(self, passphrase: str) -> Identity:
        self.keystore.unlock(passphrase)
        identity = self.store.get_identity()
        if identity is None:
            raise KeystoreAuthError("No identity in database")
        self.migrate_local_to_ipfs()
        identity = self.store.get_identity()
        assert identity is not None
        return identity

    def migrate_local_to_ipfs(self) -> bool:
        """One-shot: republish any leftover local:// blobs/mailbox onto IPFS."""
        identity = self.store.get_identity()
        if identity is None:
            return False

        changed = False
        for folder in (Folder.SENT, Folder.INBOX):
            for msg in self.store.list_messages(folder):
                if not msg.body_cid.startswith("local://"):
                    continue
                name = msg.body_cid.removeprefix("local://")
                path = self._legacy_blobs / name
                if not path.is_file():
                    raise FileNotFoundError(
                        f"Missing legacy blob {path} — cannot migrate message {msg.id}"
                    )
                new_cid = self.blobs.put(path.read_bytes(), filename=f"{msg.id}.enc")
                self.store.set_body_cid(msg.id, new_cid)
                changed = True

        needs_ipns = not identity.ipns or identity.ipns.startswith("local://")
        if needs_ipns or changed:
            self.store.set_identity_ipns(self.ipfs.my_ipns())
            self._publish_outbox()
            return True
        return False

    def require_unlocked(self) -> Identity:
        if not self.keystore.is_unlocked:
            raise KeystoreLockedError("Unlock first")
        identity = self.store.get_identity()
        if identity is None:
            raise KeystoreAuthError("Not registered")
        return identity

    def my_card(self) -> str:
        identity = self.require_unlocked()
        return encode_identity_card(identity)

    def my_fingerprint(self) -> str:
        identity = self.require_unlocked()
        return fingerprint_hex(identity.pubkey)

    # --- contacts ---

    def add_contact_card(self, card: str, *, note: str = "") -> Contact:
        self.require_unlocked()
        contact = contact_from_card(card, added_at=utcnow())
        contact.note = note
        me = self.store.get_identity()
        if me and contact.handle == me.handle:
            raise CardError("Cannot add yourself as a contact")
        if contact.ipns.startswith("local://"):
            raise CardError(
                "That card still uses a local:// mailbox — ask them to reopen "
                "Directmail (IPFS) and copy their card again"
            )
        self.store.upsert_contact(contact)
        return contact

    def list_contacts(self) -> list[Contact]:
        self.require_unlocked()
        return self.store.list_contacts()

    def get_contact(self, handle: str) -> Contact | None:
        return self.store.get_contact(handle)

    def remove_contact(self, handle: str) -> None:
        self.require_unlocked()
        self.store.delete_contact(handle)

    # --- mail ---

    def send(self, to_handle: str, body: str) -> Message:
        identity = self.require_unlocked()
        contact = self.store.get_contact(to_handle)
        if contact is None:
            raise CardError(
                f"Unknown contact {to_handle!r} — add their contact card first"
            )
        body = body if body.endswith("\n") else body + "\n"
        message_id = uuid.uuid4().hex
        msg_key = generate_message_key()
        aad = message_aad(message_id, identity.handle, contact.handle)
        ciphertext = encrypt_aesgcm(msg_key, body.encode("utf-8"), associated_data=aad)
        body_cid = self.blobs.put(ciphertext, filename=f"{message_id}.enc")
        wrapped = wrap_key(msg_key, contact.pubkey, message_id=message_id)
        preview = body.strip().replace("\n", " ")[:80]
        message = Message(
            id=message_id,
            folder=Folder.SENT,
            from_handle=identity.handle,
            to_handle=contact.handle,
            created_at=utcnow(),
            body_cid=body_cid,
            wrapped_key=wrapped,
            ciphertext_sha256=sha256_hex(ciphertext),
            preview=preview,
            read=True,
            body_plaintext=body,
        )
        self.store.upsert_message(message)
        self._publish_outbox()
        return message

    def list_messages(self, folder: Folder) -> list[Message]:
        self.require_unlocked()
        return self.store.list_messages(folder)

    def delete_message(self, message_id: str) -> None:
        """Remove a message locally; republish outbox if it was in Sent."""
        self.require_unlocked()
        message = self.store.get_message(message_id)
        if message is None:
            raise KeyError(f"No message {message_id}")
        was_sent = message.folder == Folder.SENT
        if not self.store.delete_message(message_id):
            raise KeyError(f"No message {message_id}")
        if was_sent:
            self._publish_outbox()

    def read_message(self, message_id: str) -> Message:
        self.require_unlocked()
        message = self.store.get_message(message_id)
        if message is None:
            raise KeyError(f"No message {message_id}")
        if message.body_plaintext is not None:
            self.store.mark_read(message_id, True)
            message.read = True
            return message

        ciphertext = self.blobs.get(message.body_cid)
        priv = self.keystore.load_identity_privkey()
        msg_key = unwrap_key(message.wrapped_key, priv, message_id=message.id)
        aad = message_aad(message.id, message.from_handle, message.to_handle)
        plaintext = decrypt_aesgcm(msg_key, ciphertext, associated_data=aad).decode(
            "utf-8"
        )
        self.store.set_body_plaintext(message.id, plaintext)
        message.body_plaintext = plaintext
        message.read = True
        message.preview = plaintext.strip().replace("\n", " ")[:80]
        return message

    def sync(self) -> int:
        """Pull known contacts' outboxes; import messages addressed to us."""
        identity = self.require_unlocked()
        imported = 0
        for contact in self.store.list_contacts():
            try:
                raw = self.mailbox.resolve_outbox(contact.ipns)
            except Exception:
                continue
            for env in parse_outbox_document(raw):
                if env.to_handle != identity.handle:
                    continue
                if self.store.has_message(env.id):
                    continue
                message = Message(
                    id=env.id,
                    folder=Folder.INBOX,
                    from_handle=env.from_handle,
                    to_handle=env.to_handle,
                    created_at=datetime.fromisoformat(
                        env.created_at.replace("Z", "+00:00")
                    ),
                    body_cid=env.body_cid,
                    wrapped_key=b64d(env.wrapped_key_b64),
                    ciphertext_sha256=env.ciphertext_sha256,
                    preview="(encrypted)",
                    read=False,
                    body_plaintext=None,
                )
                self.store.upsert_message(message)
                imported += 1
        return imported

    def _publish_outbox(self) -> None:
        identity = self.store.get_identity()
        if identity is None:
            return
        sent = self.store.list_messages(Folder.SENT)
        envelopes = [
            OutboxEnvelope(
                id=m.id,
                from_handle=m.from_handle,
                to_handle=m.to_handle,
                created_at=m.created_at.isoformat(),
                body_cid=m.body_cid,
                wrapped_key_b64=b64(m.wrapped_key),
                ciphertext_sha256=m.ciphertext_sha256,
            )
            for m in sent
        ]
        doc = build_outbox_document(
            handle=identity.handle,
            pubkey_b64=b64(identity.pubkey),
            envelopes=envelopes,
        )
        published = self.mailbox.publish(doc)
        self.store.set_identity_ipns(published)

    def unread_count(self, folder: Folder = Folder.INBOX) -> int:
        return sum(1 for m in self.store.list_messages(folder) if not m.read)
