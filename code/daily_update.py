"""Daily refresh: collect new battles, rescore, and publish.

    python code/daily_update.py              # the scheduled run
    python code/daily_update.py --dry-run    # do everything except push

Run by a Windows Scheduled Task (see docs/scheduling.md). It has to run on this
machine rather than a cloud runner because the Brawl Stars API key is locked to
this machine's IP address, so a GitHub Action could never authenticate.

The push is what makes the site update: Cloudflare Pages is connected to the
repo and redeploys on every push.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "outputs" / "daily_update.log"

# One full pass over the player pool. Collection rotates through
# least-recently-fetched players, so a full-pool run refreshes everyone.
MAX_PLAYERS = 20000
RATE = 12


def log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run(cmd: list[str], what: str) -> str:
    """Run a step, logging and aborting the whole update if it fails."""
    log(f"START {what}: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    tail = (proc.stdout or "").strip().splitlines()[-12:]
    for line in tail:
        log("  | " + line)
    if proc.returncode != 0:
        log(f"FAILED {what} (exit {proc.returncode})")
        err = (proc.stderr or "").strip().splitlines()[-8:]
        for line in err:
            log("  ! " + line)
        raise SystemExit(proc.returncode)
    log(f"OK {what}")
    return proc.stdout or ""


def git(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return (proc.stdout or "").strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="collect and rescore, but do not commit or push")
    ap.add_argument("--max-players", type=int, default=MAX_PLAYERS)
    args = ap.parse_args()

    log("=" * 64)
    log("Daily update starting")

    py = sys.executable  # the interpreter running us, so the task needs no PATH

    run([py, "code/collect.py", "--refresh-pool",
         "--max-players", str(args.max_players), "--rate", str(RATE)],
        "collect")
    run([py, "code/compute.py"], "compute")

    # Only site data is committed; the database stays local and goes to Drive.
    changed = git("status", "--porcelain", "site/data")
    if not changed:
        log("No change in site/data — nothing to publish.")
        log("Daily update finished (no-op)")
        return

    log("Changed files:\n" + changed)
    if args.dry_run:
        log("--dry-run set, stopping before commit.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    git("add", "site/data")
    git("commit", "-m", f"Refresh battle data ({stamp})")

    # Re-fetch immediately before pushing: this repo is also worked on from
    # another machine, and a stale local main would make the push fail.
    git("fetch", "origin", "main")
    behind = git("rev-list", "--count", "HEAD..origin/main")
    if behind and behind != "0":
        log(f"Local is {behind} commit(s) behind origin/main; rebasing.")
        proc = subprocess.run(["git", "rebase", "origin/main"], cwd=ROOT,
                              capture_output=True, text=True)
        if proc.returncode != 0:
            log("Rebase failed; aborting so the tree is left clean.")
            subprocess.run(["git", "rebase", "--abort"], cwd=ROOT)
            raise SystemExit(1)

    proc = subprocess.run(["git", "push", "origin", "main"], cwd=ROOT,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        log("Push FAILED:\n" + (proc.stderr or "").strip())
        raise SystemExit(1)

    log("Pushed. Cloudflare Pages will redeploy automatically.")
    log("Daily update finished")


if __name__ == "__main__":
    main()
