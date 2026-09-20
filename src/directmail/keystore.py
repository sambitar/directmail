"""Sealed local keystore for Directmail identity private key."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from directmail.crypto import (
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    derive_keystore_key,
    new_keystore_salt,
    seal_secret,
    unseal_secret,
)


class KeystoreLockedError(Exception):
    pass


class KeystoreAuthError(Exception):
    pass


class Keystore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._kdf_path = self.root / "kdf.json"
        self._identity_path = self.root / "identity_privkey.sealed"
        self._master_key: bytes | None = None

    @property
    def is_unlocked(self) -> bool:
        return self._master_key is not None

    @property
    def is_initialized(self) -> bool:
        return self._kdf_path.is_file() and self._identity_path.is_file()

    def initialize(self, passphrase: str, identity_privkey: bytes) -> None:
        if self.is_initialized:
            raise KeystoreAuthError("Keystore already initialized")
        salt = new_keystore_salt()
        master = derive_keystore_key(passphrase, salt)
        self._write_kdf(salt)
        self._master_key = master
        self._write_sealed(
            self._identity_path,
            identity_privkey,
            context=self._identity_context(),
        )

    def unlock(self, passphrase: str) -> None:
        if not self._kdf_path.is_file():
            raise KeystoreAuthError("Keystore is not initialized")
        salt = self._read_kdf_salt()
        master = derive_keystore_key(passphrase, salt)
        try:
            unseal_secret(
                master,
                self._identity_path.read_bytes(),
                context=self._identity_context(),
            )
        except Exception as exc:
            raise KeystoreAuthError("Wrong passphrase or corrupt keystore") from exc
        self._master_key = master

    def lock(self) -> None:
        self._master_key = None

    def load_identity_privkey(self) -> bytes:
        master = self._require_unlocked()
        try:
            return unseal_secret(
                master,
                self._identity_path.read_bytes(),
                context=self._identity_context(),
            )
        except Exception as exc:
            raise KeystoreAuthError("Failed to unseal identity key") from exc

    def _require_unlocked(self) -> bytes:
        if self._master_key is None:
            raise KeystoreLockedError("Keystore locked — unlock with passphrase first")
        return self._master_key

    def _identity_context(self) -> bytes:
        return b"directmail:identity"

    def _write_kdf(self, salt: bytes) -> None:
        payload = {
            "kdf": "scrypt",
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "salt_b64": base64.b64encode(salt).decode("ascii"),
        }
        self._kdf_path.write_text(json.dumps(payload, indent=2) + "\n")
        self._kdf_path.chmod(0o600)

    def _read_kdf_salt(self) -> bytes:
        data = json.loads(self._kdf_path.read_text())
        return base64.b64decode(data["salt_b64"])

    def _write_sealed(self, path: Path, plaintext: bytes, *, context: bytes) -> None:
        master = self._require_unlocked()
        path.write_bytes(seal_secret(master, plaintext, context=context))
        path.chmod(0o600)
