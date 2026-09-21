"""End-to-end send / sync / read with in-memory IPFS."""

from __future__ import annotations

from pathlib import Path

from directmail.ipfs_client import MemoryIpfsClient
from directmail.mail import MailService
from directmail.models import Folder
from directmail.store import Store


def test_two_peers_contact_card_send_sync(tmp_path):
    alice_ipfs = MemoryIpfsClient(key_name="directmail-alice")
    bob_ipfs = MemoryIpfsClient(key_name="directmail-bob", share_with=alice_ipfs)

    alice = MailService(tmp_path / "alice", ipfs_client=alice_ipfs)
    bob = MailService(tmp_path / "bob", ipfs_client=bob_ipfs)
    try:
        alice.register("alice-pass", handle="alice@laptop")
        bob.register("bob-pass", handle="bob@studio")

        bob.add_contact_card(alice.my_card())
        alice.add_contact_card(bob.my_card())

        alice.send("bob@studio", "Secret hello from Alice\nLine two")

        n = bob.sync()
        assert n == 1
        inbox = bob.list_messages(Folder.INBOX)
        assert len(inbox) == 1
        assert inbox[0].from_handle == "alice@laptop"
        assert inbox[0].body_cid.startswith("ipfs://")
        msg = bob.read_message(inbox[0].id)
        assert "Secret hello from Alice" in (msg.body_plaintext or "")

        sent = alice.list_messages(Folder.SENT)
        assert len(sent) == 1
        assert sent[0].body_cid.startswith("ipfs://")
        assert "/ipns/" in alice.my_card()
    finally:
        alice.close()
        bob.close()


def test_rejects_local_contact_card(tmp_path):
    from directmail.cards import CardError, encode_card
    from directmail.crypto import generate_identity_keypair

    ipfs = MemoryIpfsClient(key_name="directmail-rej")
    mail = MailService(tmp_path / "user", ipfs_client=ipfs)
    try:
        mail.register("pass", handle="sam@host")
        keys = generate_identity_keypair()
        bad = encode_card(
            handle="old@host",
            ipns="local:///tmp/outbox.json",
            pubkey=keys.public_key_bytes,
        )
        try:
            mail.add_contact_card(bad)
            assert False, "should reject local:// cards"
        except CardError as exc:
            assert "local://" in str(exc)
    finally:
        mail.close()


def test_migrate_legacy_local_blob_to_ipfs(tmp_path):
    """Unlock migrates leftover local:// body CIDs onto IPFS."""
    shared = MemoryIpfsClient(key_name="directmail-mig")
    data = tmp_path / "user"
    ipfs = MemoryIpfsClient(key_name="directmail-mig-seed", share_with=shared)

    mail = MailService(data, ipfs_client=ipfs)
    try:
        mail.register("pass", handle="sam@host")
        # Simulate a legacy sent row pointing at an on-disk blob
        blob_dir = data / "blobs"
        blob_dir.mkdir(parents=True, exist_ok=True)
        name = "legacy.enc"
        ciphertext = b"\x00" * 48
        (blob_dir / name).write_bytes(ciphertext)
        store = mail.store
        from directmail.models import Message
        from datetime import datetime, timezone

        msg = Message(
            id="legacy1",
            folder=Folder.SENT,
            from_handle="sam@host",
            to_handle="peer@host",
            created_at=datetime.now(timezone.utc),
            body_cid=f"local://{name}",
            wrapped_key=b"\x01" * 80,
            ciphertext_sha256="00" * 32,
            preview="legacy",
            read=True,
            body_plaintext=None,
        )
        store.upsert_message(msg)
        # Force identity back to local:// pointer
        store.set_identity_ipns("local://outbox")
    finally:
        mail.close()

    online = MailService(data, ipfs_client=shared)
    try:
        online.unlock("pass")
        card = online.my_card()
        assert "/ipns/" in card
        assert "local://" not in card
        sent = online.list_messages(Folder.SENT)
        assert sent[0].body_cid.startswith("ipfs://")
    finally:
        online.close()
