"""Cryptographic primitives for Directmail.

Same construction as filerooms: X25519 key wrap + AES-256-GCM bodies,
Scrypt-sealed keystore. Domain strings use directmail prefixes.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

AES_KEY_SIZE = 32
NONCE_SIZE = 12
WRAPPED_KEY_INFO = b"directmail-message-key-wrap-v1"
KEYSTORE_KDF_INFO = b"directmail-keystore-v1"

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_SALT_SIZE = 16


@dataclass(frozen=True)
class IdentityKeyPair:
    private_key_bytes: bytes
    public_key_bytes: bytes


def generate_identity_keypair() -> IdentityKeyPair:
    private_key = X25519PrivateKey.generate()
    public_key = private_key.public_key()
    return IdentityKeyPair(
        private_key_bytes=private_key.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        ),
        public_key_bytes=public_key.public_bytes(Encoding.Raw, PublicFormat.Raw),
    )


def generate_message_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint_hex(pubkey: bytes) -> str:
    """Short human-checkable fingerprint of an identity pubkey."""
    digest = hashlib.sha256(pubkey).hexdigest()
    return ":".join(digest[i : i + 4] for i in range(0, 16, 4))


def message_aad(message_id: str, from_handle: str, to_handle: str) -> bytes:
    payload = {
        "v": 1,
        "message_id": message_id,
        "from": from_handle,
        "to": to_handle,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def wrap_aad(message_id: str) -> bytes:
    payload = {"v": 1, "message_id": message_id}
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def encrypt_aesgcm(key: bytes, plaintext: bytes, associated_data: bytes | None = None) -> bytes:
    if len(key) != AES_KEY_SIZE:
        raise ValueError(f"AES key must be {AES_KEY_SIZE} bytes")
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data)
    return nonce + ciphertext


def decrypt_aesgcm(key: bytes, blob: bytes, associated_data: bytes | None = None) -> bytes:
    if len(key) != AES_KEY_SIZE:
        raise ValueError(f"AES key must be {AES_KEY_SIZE} bytes")
    if len(blob) < NONCE_SIZE + 16:
        raise ValueError("Encrypted blob too short")
    return AESGCM(key).decrypt(blob[:NONCE_SIZE], blob[NONCE_SIZE:], associated_data)


def _load_private(private_key_bytes: bytes) -> X25519PrivateKey:
    return X25519PrivateKey.from_private_bytes(private_key_bytes)


def _load_public(public_key_bytes: bytes) -> X25519PublicKey:
    return X25519PublicKey.from_public_bytes(public_key_bytes)


def wrap_key(message_key: bytes, recipient_pubkey: bytes, *, message_id: str) -> bytes:
    """Wrap AES message key: ephemeral_pubkey (32) || nonce || ciphertext+tag."""
    if len(message_key) != AES_KEY_SIZE:
        raise ValueError(f"Message key must be {AES_KEY_SIZE} bytes")

    ephemeral = X25519PrivateKey.generate()
    recipient = _load_public(recipient_pubkey)
    shared_secret = ephemeral.exchange(recipient)
    wrapping_key = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_SIZE,
        salt=None,
        info=WRAPPED_KEY_INFO,
    ).derive(shared_secret)
    ephemeral_pub = ephemeral.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    sealed = encrypt_aesgcm(
        wrapping_key, message_key, associated_data=wrap_aad(message_id)
    )
    return ephemeral_pub + sealed


def unwrap_key(wrapped: bytes, recipient_privkey: bytes, *, message_id: str) -> bytes:
    if len(wrapped) < 32 + NONCE_SIZE + 16:
        raise ValueError("Wrapped key blob too short")
    ephemeral_pub_bytes = wrapped[:32]
    sealed = wrapped[32:]
    recipient = _load_private(recipient_privkey)
    ephemeral_pub = _load_public(ephemeral_pub_bytes)
    shared_secret = recipient.exchange(ephemeral_pub)
    wrapping_key = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_SIZE,
        salt=None,
        info=WRAPPED_KEY_INFO,
    ).derive(shared_secret)
    return decrypt_aesgcm(
        wrapping_key, sealed, associated_data=wrap_aad(message_id)
    )


def new_keystore_salt() -> bytes:
    return os.urandom(SCRYPT_SALT_SIZE)


def derive_keystore_key(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str):
        raise TypeError("passphrase must be a str")
    kdf = Scrypt(
        salt=salt,
        length=AES_KEY_SIZE,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    )
    return kdf.derive(KEYSTORE_KDF_INFO + passphrase.encode("utf-8"))


def seal_secret(master_key: bytes, plaintext: bytes, *, context: bytes) -> bytes:
    return encrypt_aesgcm(master_key, plaintext, associated_data=context)


def unseal_secret(master_key: bytes, blob: bytes, *, context: bytes) -> bytes:
    return decrypt_aesgcm(master_key, blob, associated_data=context)
