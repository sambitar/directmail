"""Crypto and contact card tests."""

from __future__ import annotations

from directmail.cards import decode_card, encode_card
from directmail.crypto import (
    decrypt_aesgcm,
    encrypt_aesgcm,
    generate_identity_keypair,
    generate_message_key,
    message_aad,
    unwrap_key,
    wrap_key,
)
from directmail.keystore import Keystore, KeystoreAuthError


def test_message_encrypt_roundtrip():
    alice = generate_identity_keypair()
    bob = generate_identity_keypair()
    mid = "msg1"
    key = generate_message_key()
    aad = message_aad(mid, "alice@a", "bob@b")
    blob = encrypt_aesgcm(key, b"hello body\n", associated_data=aad)
    wrapped = wrap_key(key, bob.public_key_bytes, message_id=mid)
    opened = unwrap_key(wrapped, bob.private_key_bytes, message_id=mid)
    plain = decrypt_aesgcm(opened, blob, associated_data=aad)
    assert plain == b"hello body\n"
    # Alice cannot unwrap Bob's wrap
    try:
        unwrap_key(wrapped, alice.private_key_bytes, message_id=mid)
        assert False, "should fail"
    except Exception:
        pass


def test_contact_card_roundtrip():
    keys = generate_identity_keypair()
    card = encode_card(
        handle="sam@studio",
        ipns="/ipns/k51qzi5uqu5dexample",
        pubkey=keys.public_key_bytes,
    )
    handle, ipns, pubkey = decode_card(card)
    assert handle == "sam@studio"
    assert ipns == "/ipns/k51qzi5uqu5dexample"
    assert pubkey == keys.public_key_bytes


def test_keystore_wrong_passphrase(tmp_path):
    keys = generate_identity_keypair()
    ks = Keystore(tmp_path / "ks")
    ks.initialize("secret", keys.private_key_bytes)
    ks.lock()
    try:
        ks.unlock("wrong")
        assert False
    except KeystoreAuthError:
        pass
    ks.unlock("secret")
    assert ks.load_identity_privkey() == keys.private_key_bytes
