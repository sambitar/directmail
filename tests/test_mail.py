"""End-to-end send / sync / read with in-memory IPFS."""

from __future__ import annotations

from directmail.ipfs_client import MemoryIpfsClient
from directmail.mail import MailService
from directmail.models import Folder


def test_two_peers_contact_card_send_sync(tmp_path):
    alice_ipfs = MemoryIpfsClient(key_name="directmail-alice")
    bob_ipfs = MemoryIpfsClient(key_name="directmail-bob", share_with=alice_ipfs)

    alice = MailService(tmp_path / "alice", ipfs_client=alice_ipfs, use_ipfs=True)
    bob = MailService(tmp_path / "bob", ipfs_client=bob_ipfs, use_ipfs=True)
    try:
        alice.register("alice-pass", handle="alice@laptop")
        bob.register("bob-pass", handle="bob@studio")

        # First contact: exchange cards only
        bob.add_contact_card(alice.my_card())
        alice.add_contact_card(bob.my_card())

        alice.send("bob@studio", "Secret hello from Alice\nLine two")

        n = bob.sync()
        assert n == 1
        inbox = bob.list_messages(Folder.INBOX)
        assert len(inbox) == 1
        assert inbox[0].from_handle == "alice@laptop"
        msg = bob.read_message(inbox[0].id)
        assert "Secret hello from Alice" in (msg.body_plaintext or "")

        sent = alice.list_messages(Folder.SENT)
        assert len(sent) == 1
        assert sent[0].to_handle == "bob@studio"
    finally:
        alice.close()
        bob.close()


def test_local_backend_send_and_read(tmp_path):
    mail = MailService(tmp_path / "solo", use_ipfs=False)
    try:
        mail.register("pass", handle="solo@host")
        # Can't send without contact — add a fake local peer mailbox
        peer = MailService(tmp_path / "peer", use_ipfs=False)
        peer.register("pass2", handle="peer@host")
        mail.add_contact_card(peer.my_card())
        peer.add_contact_card(mail.my_card())

        mail.send("peer@host", "hi peer")
        # Local absolute outbox path on card allows sync of metadata,
        # but body CID is local to sender — peer needs shared storage.
        # For local backend we only assert sender sent folder works.
        sent = mail.list_messages(Folder.SENT)
        assert len(sent) == 1
        read = mail.read_message(sent[0].id)
        assert "hi peer" in (read.body_plaintext or "")
        peer.close()
    finally:
        mail.close()
