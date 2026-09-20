"""Minimal Kubo HTTP RPC client + in-memory fake for tests."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_API = "http://127.0.0.1:5001"
DIRECTMAIL_IPNS_KEY = "directmail"


class IpfsError(Exception):
    pass


def _strip_ipfs_prefix(cid: str) -> str:
    cid = cid.strip()
    for prefix in ("ipfs://", "/ipfs/", "/ipns/"):
        if cid.startswith(prefix):
            return cid[len(prefix) :]
    return cid


class IpfsClient:
    def __init__(self, api_url: str | None = None, timeout: float = 60.0) -> None:
        self.api_url = (api_url or os.environ.get("IPFS_API") or DEFAULT_API).rstrip("/")
        self.timeout = timeout

    def _post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> bytes:
        qs = f"?{urlencode(params)}" if params else ""
        url = f"{self.api_url}/api/v0/{path.lstrip('/')}{qs}"
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        req = Request(url, data=data if data is not None else b"", headers=headers, method="POST")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise IpfsError(f"IPFS {path} failed ({exc.code}): {body}") from exc
        except (URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise IpfsError(
                f"Cannot reach IPFS daemon at {self.api_url}: {reason}"
            ) from exc

    def _post_json(self, path: str, **kwargs: Any) -> dict[str, Any]:
        raw = self._post(path, **kwargs)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def is_available(self) -> bool:
        try:
            self.id()
            return True
        except IpfsError:
            return False

    def id(self) -> dict[str, Any]:
        return self._post_json("id")

    def add_bytes(self, data: bytes, *, filename: str = "blob", pin: bool = True) -> str:
        boundary = "----directmailboundary7MA4YWxkTrZu0gW"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        raw = self._post(
            "add",
            params={"pin": str(pin).lower(), "cid-version": "1"},
            data=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        line = raw.decode("utf-8").strip().splitlines()[-1]
        result = json.loads(line)
        cid = result.get("Hash")
        if not cid:
            raise IpfsError(f"Unexpected add response: {result}")
        return cid

    def cat(self, cid: str) -> bytes:
        return self._post("cat", params={"arg": _strip_ipfs_prefix(cid)})

    def key_list(self) -> list[dict[str, str]]:
        result = self._post_json("key/list")
        return list(result.get("Keys") or [])

    def ensure_key(self, name: str = DIRECTMAIL_IPNS_KEY) -> str:
        for key in self.key_list():
            if key.get("Name") == name:
                return key["Id"]
        result = self._post_json("key/gen", params={"arg": name, "type": "ed25519"})
        kid = result.get("Id")
        if not kid:
            raise IpfsError(f"key/gen failed: {result}")
        return kid

    def my_ipns(self, key: str = DIRECTMAIL_IPNS_KEY) -> str:
        return f"/ipns/{self.ensure_key(key)}"

    def name_publish(
        self, cid: str, *, key: str = DIRECTMAIL_IPNS_KEY, lifetime: str = "168h"
    ) -> str:
        cid = _strip_ipfs_prefix(cid)
        self.ensure_key(key)
        result = self._post_json(
            "name/publish",
            params={
                "arg": cid,
                "key": key,
                "lifetime": lifetime,
                "allow-offline": "true",
            },
        )
        name = result.get("Name")
        if not name:
            raise IpfsError(f"name/publish failed: {result}")
        return f"/ipns/{name}"

    def name_resolve(self, ipns_name: str, *, nocache: bool = True) -> str:
        name = ipns_name.strip()
        if not name.startswith("/ipns/") and not name.startswith("local://"):
            name = f"/ipns/{name}"
        params: dict[str, Any] = {"arg": name}
        if nocache:
            params["nocache"] = "true"
        result = self._post_json("name/resolve", params=params)
        path = result.get("Path")
        if not path:
            raise IpfsError(f"name/resolve failed: {result}")
        return path


class MemoryIpfsClient:
    """In-process IPFS stand-in for unit tests.

    Share one instance across peers so blobs + IPNS resolve across identities.
    Pass distinct ``key_name`` per mailbox (e.g. directmail-alice).
    """

    def __init__(self, *, key_name: str = DIRECTMAIL_IPNS_KEY, share_with: MemoryIpfsClient | None = None) -> None:
        if share_with is not None:
            self._blobs = share_with._blobs
            self._keys = share_with._keys
            self._ipns = share_with._ipns
        else:
            self._blobs: dict[str, bytes] = {}
            self._keys: dict[str, str] = {}
            self._ipns: dict[str, str] = {}
        self.key_name = key_name
        self._peer_id = "QmMemory" + uuid.uuid4().hex[:16]

    def is_available(self) -> bool:
        return True

    def id(self) -> dict[str, Any]:
        return {"ID": self._peer_id}

    def add_bytes(self, data: bytes, *, filename: str = "blob", pin: bool = True) -> str:
        cid = "bafy" + uuid.uuid4().hex
        self._blobs[cid] = data
        return cid

    def cat(self, cid: str) -> bytes:
        key = _strip_ipfs_prefix(cid)
        if key not in self._blobs:
            raise IpfsError(f"not found: {cid}")
        return self._blobs[key]

    def ensure_key(self, name: str | None = None) -> str:
        name = name or self.key_name
        if name not in self._keys:
            self._keys[name] = "k51" + uuid.uuid4().hex
        return self._keys[name]

    def my_ipns(self, key: str | None = None) -> str:
        return f"/ipns/{self.ensure_key(key)}"

    def name_publish(
        self, cid: str, *, key: str | None = None, lifetime: str = "168h"
    ) -> str:
        kid = self.ensure_key(key)
        self._ipns[kid] = _strip_ipfs_prefix(cid)
        return f"/ipns/{kid}"

    def name_resolve(self, ipns_name: str, *, nocache: bool = True) -> str:
        kid = _strip_ipfs_prefix(ipns_name)
        if kid not in self._ipns:
            raise IpfsError(f"unresolved: {ipns_name}")
        return f"/ipfs/{self._ipns[kid]}"
