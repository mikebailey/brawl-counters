"""Verify the Brawl Stars API key works from this machine.

Makes one cheap request to /brawlers. Prints only status information --
never the key itself, so the output is safe to paste anywhere.

Run:  python code/check_key.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = "https://api.brawlstars.com/v1"


def load_key() -> str:
    """Read BRAWL_API_KEY from the project .env.

    Parsed by hand rather than via python-dotenv so this diagnostic has zero
    dependencies -- it needs to work even in a bare interpreter.
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        sys.exit(f"No .env at {env_path}. Copy .env.example and add your key.")

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == "BRAWL_API_KEY":
            # Strip whichever quote style wraps the value, if any.
            return value.strip().strip("'\"")

    sys.exit("BRAWL_API_KEY not found in .env")


def public_ip() -> str:
    """This machine's outbound IP, for diagnosing whitelist mismatches."""
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=10) as r:
            return r.read().decode().strip()
    except Exception:
        return "(could not determine)"


def main() -> None:
    key = load_key()
    if not key:
        sys.exit("BRAWL_API_KEY is empty -- paste the token into .env.")

    # Show enough to confirm the right key is loaded, not enough to leak it.
    print(f"Key loaded: {len(key)} chars, starts {key[:6]}...")

    req = urllib.request.Request(
        f"{API_BASE}/brawlers",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"\nHTTP {e.code} -- request rejected.")
        if e.code == 403:
            # The API's own message does not mention IPs, which makes this the
            # single most confusing failure mode. Say it plainly.
            print("\n403 almost always means the key's IP whitelist does not match.")
            print(f"  Key was created for : 104.1.94.46")
            print(f"  This machine is now : {public_ip()}")
            print("\nIf those differ, edit the key's allowed IPs at")
            print("https://developer.brawlstars.com (the token itself stays the same).")
        print(f"\nAPI said: {body[:300]}")
        sys.exit(1)
    except urllib.error.URLError as e:
        sys.exit(f"Network error: {e.reason}")

    brawlers = data.get("items", [])
    print(f"\nOK -- API reachable and key accepted.")
    print(f"{len(brawlers)} brawlers returned.")
    if brawlers:
        names = [b["name"].title() for b in brawlers[:5]]
        print(f"First few: {', '.join(names)}")


if __name__ == "__main__":
    main()
