"""SQLite metadata store for identity, contacts, and messages.

Safe to call from Textual worker threads: one connection with
check_same_thread=False, serialized by an RLock.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Callable, TypeVar

from directmail.models import Contact, Folder, Identity, Message

F = TypeVar("F", bound=Callable[..., object])


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _locked(method: F) -> F:
    @wraps(method)
    def wrapper(self: Store, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


class Store:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # Refresh runs in a Textual worker thread; UI reads on the main thread.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    @_locked
    def close(self) -> None:
        self._conn.close()

    @_locked
    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS identity (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              handle TEXT NOT NULL,
              pubkey BLOB NOT NULL,
              ipns TEXT
            );

            CREATE TABLE IF NOT EXISTS contacts (
              handle TEXT PRIMARY KEY,
              pubkey BLOB NOT NULL,
              ipns TEXT NOT NULL,
              added_at TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY,
              folder TEXT NOT NULL,
              from_handle TEXT NOT NULL,
              to_handle TEXT NOT NULL,
              created_at TEXT NOT NULL,
              body_cid TEXT NOT NULL,
              wrapped_key BLOB NOT NULL,
              ciphertext_sha256 TEXT NOT NULL,
              preview TEXT NOT NULL,
              read INTEGER NOT NULL DEFAULT 0,
              body_plaintext TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_folder
              ON messages(folder, created_at DESC);
            """
        )
        self._conn.commit()

    # --- identity ---

    @_locked
    def get_identity(self) -> Identity | None:
        row = self._conn.execute("SELECT * FROM identity WHERE id = 1").fetchone()
        if row is None:
            return None
        return Identity(
            handle=row["handle"],
            pubkey=bytes(row["pubkey"]),
            ipns=row["ipns"],
        )

    @_locked
    def set_identity(self, identity: Identity) -> None:
        self._conn.execute(
            """
            INSERT INTO identity (id, handle, pubkey, ipns)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              handle = excluded.handle,
              pubkey = excluded.pubkey,
              ipns = excluded.ipns
            """,
            (identity.handle, identity.pubkey, identity.ipns),
        )
        self._conn.commit()

    @_locked
    def set_identity_ipns(self, ipns: str) -> None:
        self._conn.execute("UPDATE identity SET ipns = ? WHERE id = 1", (ipns,))
        self._conn.commit()

    # --- contacts ---

    @_locked
    def upsert_contact(self, contact: Contact) -> None:
        self._conn.execute(
            """
            INSERT INTO contacts (handle, pubkey, ipns, added_at, note)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(handle) DO UPDATE SET
              pubkey = excluded.pubkey,
              ipns = excluded.ipns,
              note = excluded.note
            """,
            (
                contact.handle,
                contact.pubkey,
                contact.ipns,
                contact.added_at.isoformat(),
                contact.note,
            ),
        )
        self._conn.commit()

    @_locked
    def get_contact(self, handle: str) -> Contact | None:
        row = self._conn.execute(
            "SELECT * FROM contacts WHERE handle = ?", (handle,)
        ).fetchone()
        if row is None:
            return None
        return Contact(
            handle=row["handle"],
            pubkey=bytes(row["pubkey"]),
            ipns=row["ipns"],
            added_at=_parse_dt(row["added_at"]),
            note=row["note"] or "",
        )

    @_locked
    def list_contacts(self) -> list[Contact]:
        rows = self._conn.execute(
            "SELECT * FROM contacts ORDER BY handle COLLATE NOCASE"
        ).fetchall()
        return [
            Contact(
                handle=r["handle"],
                pubkey=bytes(r["pubkey"]),
                ipns=r["ipns"],
                added_at=_parse_dt(r["added_at"]),
                note=r["note"] or "",
            )
            for r in rows
        ]

    @_locked
    def delete_contact(self, handle: str) -> None:
        self._conn.execute("DELETE FROM contacts WHERE handle = ?", (handle,))
        self._conn.commit()

    # --- messages ---

    @_locked
    def upsert_message(self, message: Message) -> None:
        self._conn.execute(
            """
            INSERT INTO messages (
              id, folder, from_handle, to_handle, created_at, body_cid,
              wrapped_key, ciphertext_sha256, preview, read, body_plaintext
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              read = excluded.read,
              body_plaintext = COALESCE(excluded.body_plaintext, messages.body_plaintext),
              preview = excluded.preview
            """,
            (
                message.id,
                message.folder.value,
                message.from_handle,
                message.to_handle,
                message.created_at.isoformat(),
                message.body_cid,
                message.wrapped_key,
                message.ciphertext_sha256,
                message.preview,
                1 if message.read else 0,
                message.body_plaintext,
            ),
        )
        self._conn.commit()

    @_locked
    def get_message(self, message_id: str) -> Message | None:
        row = self._conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_message(row)

    @_locked
    def list_messages(self, folder: Folder) -> list[Message]:
        rows = self._conn.execute(
            """
            SELECT * FROM messages
            WHERE folder = ?
            ORDER BY created_at DESC
            """,
            (folder.value,),
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    @_locked
    def mark_read(self, message_id: str, read: bool = True) -> None:
        self._conn.execute(
            "UPDATE messages SET read = ? WHERE id = ?",
            (1 if read else 0, message_id),
        )
        self._conn.commit()

    @_locked
    def set_body_plaintext(self, message_id: str, body: str) -> None:
        preview = body.strip().replace("\n", " ")[:80]
        self._conn.execute(
            """
            UPDATE messages
            SET body_plaintext = ?, preview = ?, read = 1
            WHERE id = ?
            """,
            (body, preview, message_id),
        )
        self._conn.commit()

    @_locked
    def has_message(self, message_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        return row is not None

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"],
            folder=Folder(row["folder"]),
            from_handle=row["from_handle"],
            to_handle=row["to_handle"],
            created_at=_parse_dt(row["created_at"]),
            body_cid=row["body_cid"],
            wrapped_key=bytes(row["wrapped_key"]),
            ciphertext_sha256=row["ciphertext_sha256"],
            preview=row["preview"],
            read=bool(row["read"]),
            body_plaintext=row["body_plaintext"],
        )
