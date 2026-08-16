"""Collect Brawl Stars battle data into a local SQLite database.

    python code/collect.py                    # default run (~8k players)
    python code/collect.py --max-players 500  # quick test
    python code/collect.py --refresh-pool     # re-walk leaderboards first

WHY THIS RUNS REPEATEDLY
------------------------
A player's battlelog holds only their ~25 most recent battles and expires after
a day or two. One run is therefore a snapshot of very recent play, not a
history. Running this on a schedule accumulates into the same database; the
fingerprint primary key makes re-runs idempotent, so overlapping runs cannot
double-count a battle.

WHAT GETS KEPT
--------------
Only battles with exactly two teams and a win/loss result, since that is what
makes "A was on the side that beat B" well defined. Showdown (placement, not
win/loss) and friendly matches (no stakes, often testing) are counted as
skipped and reported, never silently dropped.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

from bs_api import COUNTRY_CODES, BrawlStarsAPI, NotFound

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "battles.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    tag           TEXT PRIMARY KEY,
    name          TEXT,
    trophies      INTEGER,
    country       TEXT,
    last_fetched  REAL
);
CREATE INDEX IF NOT EXISTS idx_players_fetched ON players(last_fetched);

CREATE TABLE IF NOT EXISTS battles (
    fingerprint   TEXT PRIMARY KEY,
    battle_time   TEXT,
    mode          TEXT,
    battle_type   TEXT,
    map           TEXT,
    team_size     INTEGER,
    winners       TEXT,
    losers        TEXT
);
CREATE INDEX IF NOT EXISTS idx_battles_mode ON battles(mode);

CREATE TABLE IF NOT EXISTS brawlers (
    id    INTEGER PRIMARY KEY,
    name  TEXT
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    # WAL keeps reads working while a long collection run writes.
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def fingerprint(battle_time: str, tags: list) -> str:
    """Stable id for a battle, identical across every participant's log.

    Without this, one battle is counted once per participant who happens to be
    in our player pool, inflating sample sizes several-fold and making every
    confidence number wrong.
    """
    raw = battle_time + "|" + "|".join(sorted(tags))
    return hashlib.sha1(raw.encode()).hexdigest()


def parse_battle(entry: dict, owner_tag: str):
    """Turn one battlelog entry into (reason, row).

    row is None when the battle is unusable; reason always explains why, so the
    run summary can account for every entry seen.
    """
    battle = entry.get("battle", {})

    if battle.get("type") == "friendly":
        return ("skip:friendly", None)

    teams = battle.get("teams")
    if not teams:
        return ("skip:no_teams", None)          # showdown-style placement list
    if len(teams) != 2:
        return ("skip:multi_team", None)        # duo/trio showdown brackets

    result = battle.get("result")
    if result not in ("victory", "defeat"):
        return ("skip:no_result", None)         # draws, placement modes

    # `result` is from the perspective of the player whose log this is, so we
    # must locate their team before we know which side actually won.
    owner_team = None
    for i, team in enumerate(teams):
        if any(p.get("tag") == owner_tag for p in team):
            owner_team = i
            break
    if owner_team is None:
        return ("skip:owner_missing", None)

    win_idx = owner_team if result == "victory" else 1 - owner_team
    winners = [p["brawler"]["id"] for p in teams[win_idx] if p.get("brawler")]
    losers = [p["brawler"]["id"] for p in teams[1 - win_idx] if p.get("brawler")]
    if not winners or not losers:
        return ("skip:no_brawlers", None)

    all_tags = [p.get("tag", "") for team in teams for p in team]
    event = entry.get("event", {})
    row = (
        fingerprint(entry.get("battleTime", ""), all_tags),
        entry.get("battleTime"),
        battle.get("mode"),
        battle.get("type"),
        event.get("map"),
        len(winners),
        json.dumps(winners),
        json.dumps(losers),
    )
    return ("kept", row)


def refresh_pool(api: BrawlStarsAPI, con: sqlite3.Connection, countries: list) -> None:
    """Walk global plus country leaderboards, adding any new player tags."""
    targets = ["global"] + countries
    print("Building player pool from %d leaderboards..." % len(targets))
    for i, cc in enumerate(targets, 1):
        try:
            items = api.rankings(cc)
        except (NotFound, RuntimeError) as e:
            print("  %s: skipped (%s)" % (cc, type(e).__name__))
            continue
        rows = [(p["tag"], p.get("name"), p.get("trophies"), cc) for p in items]
        con.executemany(
            "INSERT OR IGNORE INTO players(tag, name, trophies, country) "
            "VALUES (?,?,?,?)", rows)
        if i % 20 == 0 or i == len(targets):
            con.commit()
            total = con.execute("SELECT COUNT(*) FROM players").fetchone()[0]
            print("  %d/%d leaderboards -> %s unique players"
                  % (i, len(targets), format(total, ",")))
    con.commit()


def collect(max_players: int, rate: float, workers: int, refresh: bool,
            countries: list) -> None:
    api = BrawlStarsAPI(per_second=rate)
    con = connect()

    # Cache the brawler list so compute.py can name ids without an API call.
    con.executemany("INSERT OR REPLACE INTO brawlers(id, name) VALUES (?,?)",
                    [(b["id"], b["name"].title()) for b in api.brawlers()])
    con.commit()

    have_players = con.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    if refresh or have_players == 0:
        refresh_pool(api, con, countries)

    # Prefer never-fetched players, then least-recently-fetched. On repeat runs
    # this rotates through the pool instead of re-hitting the same tags.
    tags = [r[0] for r in con.execute(
        "SELECT tag FROM players "
        "ORDER BY last_fetched IS NOT NULL, last_fetched ASC LIMIT ?",
        (max_players,))]
    if not tags:
        sys.exit("Player pool is empty. Run with --refresh-pool.")

    before = con.execute("SELECT COUNT(*) FROM battles").fetchone()[0]
    print("\nFetching battlelogs for %s players (%.0f req/s, %d workers)..."
          % (format(len(tags), ","), rate, workers))
    print("Estimated time: %.0f min\n" % (len(tags) / rate / 60))

    reasons = Counter()
    started = time.time()
    done = 0

    def fetch(tag):
        try:
            return tag, api.battlelog(tag)
        except (NotFound, RuntimeError):
            return tag, None

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fetch, t) for t in tags]
        for fut in cf.as_completed(futures):
            tag, entries = fut.result()
            done += 1
            if entries is None:
                reasons["skip:fetch_failed"] += 1
            else:
                rows = []
                for entry in entries:
                    reason, row = parse_battle(entry, tag)
                    reasons[reason] += 1
                    if row:
                        rows.append(row)
                if rows:
                    # All DB writes happen here on the main thread; sqlite
                    # connections are not safe to share across threads.
                    con.executemany(
                        "INSERT OR IGNORE INTO battles VALUES (?,?,?,?,?,?,?,?)",
                        rows)
            con.execute("UPDATE players SET last_fetched=? WHERE tag=?",
                        (time.time(), tag))

            if done % 250 == 0:
                con.commit()
                per_s = done / max(time.time() - started, 1e-9)
                total = con.execute("SELECT COUNT(*) FROM battles").fetchone()[0]
                eta = (len(tags) - done) / max(per_s, 1e-9)
                print("  %s/%s players | %s battles | %.1f players/s | ETA %.0f min"
                      % (format(done, ","), format(len(tags), ","),
                         format(total, ","), per_s, eta / 60))
    con.commit()

    after = con.execute("SELECT COUNT(*) FROM battles").fetchone()[0]
    elapsed = time.time() - started

    print("\n--- Run complete in %.1f min ---" % (elapsed / 60))
    print("API requests: %s (%d retried)" % (format(api.n_requests, ","), api.n_retries))
    print("Battles in DB: %s -> %s  (+%s new)"
          % (format(before, ","), format(after, ","), format(after - before, ",")))
    print("\nBattle entries seen, by disposition:")
    for reason, n in reasons.most_common():
        print("  %-22s %8s" % (reason, format(n, ",")))
    kept = reasons.get("kept", 0)
    if kept:
        dup = kept - (after - before)
        print("\n%s of %s usable entries were duplicates of battles already "
              "stored\n(expected: one battle appears in every participant's log)."
              % (format(dup, ","), format(kept, ",")))
    print("\nNext: python code/compute.py")


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--max-players", type=int, default=8000,
                   help="players to fetch this run (default 8000, ~13 min)")
    p.add_argument("--rate", type=float, default=10.0,
                   help="requests per second (default 10)")
    p.add_argument("--workers", type=int, default=8,
                   help="concurrent requests (default 8)")
    p.add_argument("--refresh-pool", action="store_true",
                   help="re-walk leaderboards to add new player tags")
    p.add_argument("--countries", default=",".join(COUNTRY_CODES),
                   help="comma-separated country codes for the pool")
    a = p.parse_args()
    collect(a.max_players, a.rate, a.workers, a.refresh_pool,
            [c.strip() for c in a.countries.split(",") if c.strip()])


if __name__ == "__main__":
    main()
