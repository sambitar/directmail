"""Minimal Kubo HTTP RPC client + in-memory fake for tests."""

from __future__ import annotations

import json
import os
import threading
import time
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
        timeout: float | None = None,
    ) -> bytes:
        qs = f"?{urlencode(params)}" if params else ""
        url = f"{self.api_url}/api/v0/{path.lstrip('/')}{qs}"
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        req = Request(url, data=data if data is not None else b"", headers=headers, method="POST")
        try:
            with urlopen(req, timeout=self.timeout if timeout is None else timeout) as resp:
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
        return self._post_json("id", timeout=5.0)

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
            timeout=120.0,
        )
        line = raw.decode("utf-8").strip().splitlines()[-1]
        result = json.loads(line)
        cid = result.get("Hash")
        if not cid:
            raise IpfsError(f"Unexpected add response: {result}")
        return cid

    def cat(self, cid: str) -> bytes:
        return self._post("cat", params={"arg": _strip_ipfs_prefix(cid)}, timeout=120.0)

    def key_list(self) -> list[dict[str, str]]:
        result = self._post_json("key/list", timeout=30.0)
        return list(result.get("Keys") or [])

    def ensure_key(self, name: str = DIRECTMAIL_IPNS_KEY) -> str:
        for key in self.key_list():
            if key.get("Name") == name:
                return key["Id"]
        result = self._post_json(
            "key/gen", params={"arg": name, "type": "ed25519"}, timeout=60.0
        )
        kid = result.get("Id")
        if not kid:
            raise IpfsError(f"key/gen failed: {result}")
        return kid

    def my_ipns(self, key: str = DIRECTMAIL_IPNS_KEY) -> str:
        return f"/ipns/{self.ensure_key(key)}"

    def name_publish(
        self, cid: str, *, key: str = DIRECTMAIL_IPNS_KEY, lifetime: str = "168h"
    ) -> str:
        """Publish IPNS without blocking on DHT.

        Kubo writes the local record quickly, then can hang for minutes
        announcing to the DHT when many peers are connected. We return as
        soon as local resolve sees the new CID; DHT finishes in a daemon thread.
        """
        cid = _strip_ipfs_prefix(cid)
        key_id = self.ensure_key(key)
        target = f"/ipfs/{cid}"
        params = {
            "arg": target,
            "key": key,
            "lifetime": lifetime,
            "allow-offline": "true",
            "resolve": "false",
        }

        errors: list[BaseException] = []

        def _publish() -> None:
            try:
                self._post_json("name/publish", params=params, timeout=600.0)
            except BaseException as exc:  # noqa: BLE001 — background best-effort
                errors.append(exc)

        thread = threading.Thread(target=_publish, name="ipns-publish", daemon=True)
        thread.start()

        deadline = time.time() + 8.0
        while time.time() < deadline:
            try:
                path = self.name_resolve(f"/ipns/{key_id}", nocache=True, timeout=3.0)
                if _strip_ipfs_prefix(path) == cid or path.rstrip("/").endswith(cid):
                    return f"/ipns/{key_id}"
            except IpfsError:
                pass
            if not thread.is_alive() and errors:
                raise IpfsError(f"name/publish failed: {errors[0]}") from errors[0]
            if not thread.is_alive():
                break
            time.sleep(0.25)

        try:
            path = self.name_resolve(f"/ipns/{key_id}", nocache=True, timeout=3.0)
            if _strip_ipfs_prefix(path) == cid or path.rstrip("/").endswith(cid):
                return f"/ipns/{key_id}"
        except IpfsError:
            pass

        if errors:
            raise IpfsError(f"name/publish failed: {errors[0]}") from errors[0]
        return f"/ipns/{key_id}"

    def name_resolve(
        self,
        ipns_name: str,
        *,
        nocache: bool = True,
        timeout: float = 30.0,
    ) -> str:
        name = ipns_name.strip()
        if not name.startswith("/ipns/"):
            name = f"/ipns/{name}"
        params: dict[str, Any] = {"arg": name}
        if nocache:
            params["nocache"] = "true"
        result = self._post_json("name/resolve", params=params, timeout=timeout)
        path = result.get("Path")
        if not path:
            raise IpfsError(f"name/resolve failed: {result}")
        return path


class MemoryIpfsClient:
    """In-process IPFS stand-in for unit tests.

    Share one instance across peers so blobs + IPNS resolve across identities.
    Pass distinct ``key_name`` per mailbox (e.g. directmail-alice).
    """

    def __init__(
        self, *, key_name: str = DIRECTMAIL_IPNS_KEY, share_with: MemoryIpfsClient | None = None
    ) -> None:
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

    def name_resolve(
        self, ipns_name: str, *, nocache: bool = True, timeout: float = 30.0
    ) -> str:
        _ = timeout
        kid = _strip_ipfs_prefix(ipns_name)
        if kid not in self._ipns:
            raise IpfsError(f"unresolved: {ipns_name}")
        return f"/ipfs/{self._ipns[kid]}"
