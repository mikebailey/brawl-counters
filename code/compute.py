"""Turn stored battles into counter scores, and write the site's data files.

    python code/compute.py
    python code/compute.py --min-n 50            # stricter confidence bar
    python code/compute.py --mode-coverage 0.45  # admit thinner game modes

OUTPUT
------
    site/data/data.json          all modes combined, plus a manifest of the
                                 per-mode files that qualified
    site/data/mode-<name>.json   one per game mode that clears the coverage bar

Per-mode files are separate rather than one large payload so the front end
loads a mode only when it is asked for.

WHICH MODES SHIP
----------------
A mode qualifies when at least `--mode-coverage` of all possible brawler pairs
have `--min-n` battles behind them. That is a data test, not a hand-maintained
list, so modes appear on their own as collection accumulates.

The bar is 0.40 rather than something stricter because the two things the site
shows need very different amounts of data. Brawler rankings only need per-brawler
win rates, and every mode down to Bounty has a median of 1,200+ battles per
brawler -- rock solid. Counters need per-PAIR data, which is roughly a hundred
times scarcer. At 0.40 the five biggest modes ship; their thinner matchups are
simply filtered out by the site's minimum-battles control rather than shown as
if they were trustworthy. Heist (12%) and below stay out entirely.

METHOD
------
Every stored battle has a winning team and a losing team. For each brawler `w`
on the winning side and `b` on the losing side we record one observation: w
beat b. A 3v3 battle therefore yields 9 observations, one per cross-team pair.

    counter_score(A -> B) = (A's win rate against B - 0.50) * 100

So +12 means "A beats B 62% of the time". The score is signed and exactly
antisymmetric, score(A->B) == -score(B->A), which is what lets one stored list
serve both the "counters" and "is countered by" views.

Uncertainty is a Wilson score interval on the win rate, reported in the same
units. Wilson rather than the textbook normal interval because it stays
sensible at small n and near 0 or 1, which is exactly where thin matchup data
lives.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "battles.db"
OUT_DIR = ROOT / "site" / "data"

IMG_URL = "https://cdn.brawlify.com/brawlers/borderless/{}.png"

Z = 1.96  # 95% confidence

# Display names for the API's camelCase mode ids.
MODE_LABELS = {
    "brawlBall": "Brawl Ball",
    "knockout": "Knockout",
    "gemGrab": "Gem Grab",
    "bounty": "Bounty",
    "hotZone": "Hot Zone",
    "heist": "Heist",
    "siege": "Siege",
    "wipeout": "Wipeout",
    "basketBrawl": "Basket Brawl",
    "duels": "Duels",
    "lastStand": "Last Stand",
}


def logit(p: float) -> float:
    """Log-odds, clamped so a 0% or 100% brawler cannot produce infinity."""
    p = min(max(p, 0.01), 0.99)
    return math.log(p / (1 - p))


def expected_rate(overall_a: float, overall_b: float) -> float:
    """P(A beats B) predicted by overall strength alone (Bradley-Terry).

    Strength is additive in log-odds, not in probability. Treating it as
    additive in probability -- expected = 0.5 + (wr_a - wr_b)/2 -- badly
    under-corrects at the extremes, because a jump from 50% to 60% overall is a
    far smaller strength gain than one from 85% to 95%. Under the linear
    version a dominant brawler still "counters" almost everyone after
    adjustment, which defeats the point of adjusting.

    Writing each brawler's overall rate as its strength against an average
    opponent, logit(overall) = s - s_avg, the average cancels in the difference:

        P(A beats B) = sigmoid(logit(overall_a) - logit(overall_b))
    """
    diff = logit(overall_a) - logit(overall_b)
    return 1 / (1 + math.exp(-diff))


def wilson(wins: int, n: int):
    """Wilson score interval for a binomial proportion, as (low, high).

    Degrades gracefully at n=0 and never leaves [0, 1], unlike the normal
    approximation which can produce negative win rates on thin data.
    """
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    denom = 1 + Z * Z / n
    center = (p + Z * Z / (2 * n)) / denom
    margin = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def tally(rows):
    """Accumulate pairwise and per-brawler win counts from battle rows.

    rows is an iterable of (winners_json, losers_json).
    """
    pair_wins = defaultdict(int)   # (a, b) -> times a's team beat b's team
    pair_n = defaultdict(int)      # (a, b) -> times they met (symmetric)
    solo_wins = defaultdict(int)
    solo_n = defaultdict(int)

    for winners_json, losers_json in rows:
        winners = json.loads(winners_json)
        losers = json.loads(losers_json)

        for w in winners:
            solo_wins[w] += 1
            solo_n[w] += 1
        for b in losers:
            solo_n[b] += 1

        for w in winners:
            for b in losers:
                if w == b:
                    continue  # mirror matchup carries no information
                pair_wins[(w, b)] += 1
                pair_n[(w, b)] += 1
                pair_n[(b, w)] += 1

    return pair_wins, pair_n, solo_wins, solo_n


def build(rows, names, min_n):
    """Build the payload fragment (brawlers + matchups) for one slice."""
    pair_wins, pair_n, solo_wins, solo_n = tally(rows)
    overall = {i: solo_wins[i] / solo_n[i] for i in solo_n if solo_n[i]}

    matchups = defaultdict(list)
    for (a, b), n in pair_n.items():
        if a > b:
            continue  # handle each unordered pair once, emit both directions
        wins_a = pair_wins.get((a, b), 0)
        wr = wins_a / n if n else 0.5
        low, high = wilson(wins_a, n)

        expected = expected_rate(overall.get(a, 0.5), overall.get(b, 0.5))
        adj = (wr - expected) * 100
        score = (wr - 0.5) * 100
        ci = round((high - low) * 50, 1)

        # [opponent id, raw score, n, ci half-width, adjusted score]
        matchups[a].append([b, round(score, 1), n, ci, round(adj, 1)])
        matchups[b].append([a, round(-score, 1), n, ci, round(-adj, 1)])

    for bid in matchups:
        matchups[bid].sort(key=lambda r: -r[1])

    brawlers = [
        {
            "id": bid,
            "name": names.get(bid, "Brawler %d" % bid),
            "img": IMG_URL.format(bid),
            "winRate": round(overall.get(bid, 0.5) * 100, 1),
            "n": solo_n[bid],
        }
        for bid in sorted(solo_n, key=lambda i: names.get(i, ""))
    ]

    n_brawlers = len(brawlers)
    possible = n_brawlers * (n_brawlers - 1) // 2
    reliable = sum(1 for n in pair_n.values() if n >= min_n) // 2

    return {
        "brawlers": brawlers,
        "matchups": {str(k): v for k, v in matchups.items()},
        "stats": {
            "brawlers": n_brawlers,
            "pairsSeen": len(pair_n) // 2,
            "pairsPossible": possible,
            "pairsReliable": reliable,
            "coverage": round(reliable / possible, 3) if possible else 0.0,
        },
    }


def write_json(path: Path, payload: dict) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return path.stat().st_size / 1024


def compute(min_n: int, mode_coverage: float) -> None:
    if not DB_PATH.exists():
        raise SystemExit("No database yet. Run: python code/collect.py")

    con = sqlite3.connect(DB_PATH)
    names = dict(con.execute("SELECT id, name FROM brawlers").fetchall())

    all_rows = con.execute("SELECT winners, losers FROM battles").fetchall()
    if not all_rows:
        raise SystemExit("No battles stored yet.")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # --- every mode combined -------------------------------------------
    combined = build(all_rows, names, min_n)
    print("All modes: %s battles, %s of %s pairs reliable (%.0f%%)"
          % (format(len(all_rows), ","),
             format(combined["stats"]["pairsReliable"], ","),
             format(combined["stats"]["pairsPossible"], ","),
             100 * combined["stats"]["coverage"]))

    # --- each mode, largest first --------------------------------------
    mode_counts = con.execute(
        "SELECT mode, COUNT(*) FROM battles GROUP BY mode ORDER BY COUNT(*) DESC"
    ).fetchall()

    manifest = []
    print("\nPer mode (bar: %.0f%% of pairs with %d+ battles):"
          % (100 * mode_coverage, min_n))

    for mode, count in mode_counts:
        rows = con.execute(
            "SELECT winners, losers FROM battles WHERE mode = ?", (mode,)
        ).fetchall()
        payload = build(rows, names, min_n)
        cov = payload["stats"]["coverage"]
        qualifies = cov >= mode_coverage

        print("  %-14s %8s battles  coverage %5.1f%%  %s"
              % (MODE_LABELS.get(mode, mode), format(count, ","), 100 * cov,
                 "SHIPS" if qualifies else "held back"))

        if not qualifies:
            continue

        payload["generated"] = stamp
        payload["minN"] = min_n
        payload["mode"] = mode
        payload["label"] = MODE_LABELS.get(mode, mode)
        payload["stats"]["battles"] = count

        size = write_json(OUT_DIR / ("mode-%s.json" % mode), payload)
        manifest.append({
            "mode": mode,
            "label": MODE_LABELS.get(mode, mode),
            "file": "mode-%s.json" % mode,
            "battles": count,
            "coverage": cov,
            "sizeKb": round(size),
        })

    combined.update({
        "generated": stamp,
        "minN": min_n,
        "mode": "all",
        "label": "All modes",
        "modes": manifest,
    })
    combined["stats"]["battles"] = len(all_rows)
    combined["stats"]["observations"] = sum(
        len(v) for v in combined["matchups"].values()) // 2

    size = write_json(OUT_DIR / "data.json", combined)

    print("\nWrote data.json (%.0f KB) + %d mode file%s"
          % (size, len(manifest), "" if len(manifest) == 1 else "s"))
    for m in manifest:
        print("  mode-%s.json (%d KB)" % (m["mode"], m["sizeKb"]))
    if not manifest:
        print("  No mode cleared the bar. Collect more, or lower "
              "--mode-coverage.")


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--min-n", type=int, default=30,
                   help="battles needed before a matchup counts as reliable "
                        "(default 30)")
    p.add_argument("--mode-coverage", type=float, default=0.40,
                   help="share of pairs a mode must cover to ship its own "
                        "file (default 0.60)")
    a = p.parse_args()
    compute(a.min_n, a.mode_coverage)


if __name__ == "__main__":
    main()
