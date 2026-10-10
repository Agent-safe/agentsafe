"""Verify an AgentSafe journal export without the running controller.

Usage: uv run python verify_export.py journal.json
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify(document: dict) -> list[str]:
    errors: list[str] = []
    public = Ed25519PublicKey.from_public_bytes(decode(document["public_key"]))
    previous = "0" * 64
    for index, event in enumerate(document["events"], start=1):
        body = json.loads(event["body"])
        digest = hashlib.sha256(bytes.fromhex(previous) + canonical(body)).hexdigest()
        if event["prev_hash"] != previous:
            errors.append(f"event {index}: previous hash mismatch")
        if event["hash"] != digest:
            errors.append(f"event {index}: content hash mismatch")
        try:
            public.verify(decode(event["signature"]), canonical({"hash": digest, "event_id": event["event_id"]}))
        except Exception:
            errors.append(f"event {index}: Ed25519 signature invalid")
        previous = event["hash"]
    return errors


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: uv run python verify_export.py journal.json")
    data = json.loads(Path(sys.argv[1]).read_text())
    failures = verify(data)
    print(json.dumps({"valid": not failures, "events": len(data["events"]), "errors": failures}, indent=2))
    raise SystemExit(bool(failures))
