#!/usr/bin/env python3
"""Docker Hub login helper with MFA/TOTP support.

Reads from environment:
  DOCKERHUB_ADMIN_USER       — Docker Hub username (required)
  DOCKERHUB_ADMIN_PASSWORD   — Docker Hub password (required)
  DOCKERHUB_TOTP_SECRET      — TOTP base32 secret for MFA (optional; errors if MFA required and missing)

Prints the JWT to stdout on success, exits non-zero on failure.
"""

import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request


def generate_totp(secret: str, period: int = 30, digits: int = 6) -> str:
    """Generate a TOTP code from a base32-encoded secret (RFC 6238)."""
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    counter = struct.pack(">Q", int(time.time()) // period)
    mac = hmac.new(key, counter, hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def post_json(url: str, payload: dict) -> dict:
    """POST JSON and return parsed response."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"ERROR: HTTP {e.code} from {url}: {body}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    user = os.environ.get("DOCKERHUB_ADMIN_USER", "")
    password = os.environ.get("DOCKERHUB_ADMIN_PASSWORD", "")
    totp_secret = os.environ.get("DOCKERHUB_TOTP_SECRET", "")

    if not user or not password:
        print("ERROR: DOCKERHUB_ADMIN_USER and DOCKERHUB_ADMIN_PASSWORD must be set", file=sys.stderr)
        sys.exit(1)

    # Step 1: login with username/password
    # Docker Hub returns HTTP 401 with login_2fa_token when MFA is required
    login_url = "https://hub.docker.com/v2/users/login/"
    data = json.dumps({"username": user, "password": password}).encode()
    req = urllib.request.Request(login_url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if e.code == 401:
            resp = json.loads(body)
        else:
            print(f"ERROR: HTTP {e.code} from {login_url}: {body}", file=sys.stderr)
            sys.exit(1)

    token = resp.get("token")
    if token:
        print(token)
        return

    # Step 2: MFA required — complete 2FA with TOTP
    login_2fa_token = resp.get("login_2fa_token")
    if not login_2fa_token:
        print(f"ERROR: unexpected login response: {json.dumps(resp)}", file=sys.stderr)
        sys.exit(1)

    if not totp_secret:
        print("ERROR: MFA required but DOCKERHUB_TOTP_SECRET is not set", file=sys.stderr)
        sys.exit(1)

    code = generate_totp(totp_secret)
    print(f"2FA: submitting TOTP code for {user}", file=sys.stderr)
    resp = post_json(
        "https://hub.docker.com/v2/users/2fa-login/",
        {"login_2fa_token": login_2fa_token, "code": code},
    )

    token = resp.get("token")
    if not token:
        print(f"ERROR: 2FA login failed: {json.dumps(resp)}", file=sys.stderr)
        sys.exit(1)

    print(token)


if __name__ == "__main__":
    main()
