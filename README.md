# brawl-counters

A static site that shows, for any Brawl Stars brawler, which brawlers counter
them and which they counter, ranked by a counter score derived from real
battles pulled from the official Brawl Stars API.

## Quick start

```bash
python code/check_key.py                  # confirm the API key + IP whitelist
python code/collect.py --max-players 500  # gather battles (repeatable)
python code/compute.py                    # score them, write the site data
cd site && python -m http.server 8791     # then open http://127.0.0.1:8791
```

The site must be served over HTTP. Opening `site/index.html` directly with
`file://` fails, because browsers block `fetch` of local files.

## How the counter score works

Every stored battle has a winning team and a losing team. For each brawler on
the winning side and each brawler on the losing side, we record one
observation: this one beat that one. A 3v3 battle yields 9 such observations.

    counter_score(A -> B) = (A's win rate against B - 0.50) * 100

So **+12 means "A beats B 62% of the time."** The score is signed and exactly
antisymmetric, `score(A->B) == -score(B->A)`, which is why a single stored list
per brawler serves both the "counters" and "countered by" views.

Uncertainty is a Wilson score interval on the win rate, reported in the same
units. Wilson rather than the textbook normal interval because it stays sane at
small n and near 0 or 1, which is exactly where thin matchup data lives.

## The two scores

The site has a toggle, and the difference matters.

**Raw** is the head-to-head win rate above a coin flip, as above. It answers
"what actually happens when these two meet." Its weakness is that a strong
brawler appears to counter everyone: at 66.5% overall, Wendy wins all 105 of
her matchups, and Shelly wins 3.

**Adjusted for strength** subtracts what the two brawlers' overall win rates
alone predict, leaving the matchup itself:

    expected = sigmoid(logit(overall_A) - logit(overall_B))
    adjusted = (actual_win_rate - expected) * 100

Strength is additive in **log-odds**, not in probability, which is the whole
reason for the sigmoid. The obvious linear version, `0.5 + (wr_A - wr_B)/2`,
was tried first and badly under-corrects: Wendy still won 104 of 105 matchups
after "adjustment," which defeats the point. Under the log-odds form the share
of winning matchups per brawler centers on 0.50 with a standard deviation of
0.065, and weak brawlers get a real answer — Shelly goes from 3 winning
matchups to 49, and genuinely handles Darryl.

This is Bradley-Terry with no fitted parameters. A mild residual correlation
with overall strength survives (r ≈ +0.33), because a brawler's individual
contribution is diluted by two teammates in 3v3, so overall win rate slightly
understates pairwise strength. Multiplying the log-odds difference by a
constant could zero that correlation out, but it overshoots hard (r ≈ −0.91 at
1.5) and there is no theory picking the value, so the untuned model ships.
Wendy still wins 80% of her adjusted matchups, which at that point is a fact
about Wendy rather than an artifact.

**Only 2-team win/loss battles are counted.** Showdown is placement-based, not
win/loss, so "who beat whom" is not well defined there and it is excluded.
Friendly matches are excluded too (no stakes, often testing). Every skipped
battle is counted and reported at the end of a collection run rather than
silently dropped.

**5v5 wipeout battles contribute more observations than 3v3** (25 cross-team
pairs versus 9), so they carry more weight per battle. `compute.py
--max-team-size 3` excludes them if you would rather they did not.

**The player pool is top-ranked players**, walked from the global leaderboard
plus 105 country leaderboards. Roughly 21,000 unique tags. That is good data
quality but it skews to high-skill play, so these are not the counters a
beginner would experience.

**Battlelogs expire.** The API returns only a player's ~25 most recent battles
and drops them after a day or two. One collection run is a snapshot of very
recent play, not history. Run `collect.py` repeatedly to accumulate; battles
are keyed by a fingerprint of their timestamp plus sorted player tags, so
re-runs are idempotent and cannot double-count. That fingerprint matters more
than it looks: without it, one battle is counted once per participant who
happens to be in the pool, inflating every sample size several-fold.

## Layout

```
code/check_key.py   verify key + IP whitelist, with a 403 diagnostic
code/bs_api.py      API client: rate limiting, retries, clear failures
code/collect.py     leaderboards -> player pool -> battlelogs -> SQLite
code/compute.py     SQLite -> counter scores -> site/data/data.json
site/               static site, no build step
data/battles.db     accumulated battles (gitignored)
```

## The API key

From <https://developer.brawlstars.com>, stored in a gitignored `.env` as
`BRAWL_API_KEY`. Keys are **locked to an IP address**, and the resulting 403
says nothing about IPs, which makes it a confusing failure. When collection
starts returning 403, your ISP most likely handed you a new address: run
`python code/check_key.py`, which prints the current IP next to the one the key
expects, then update the key's allowed addresses on the developer portal. The
token itself does not change.

## Design notes

The site uses a diverging blue/red scale with a neutral gray midpoint: red for
"this brawler beats the one you selected", blue for the reverse. The pair was
checked with a colorblind-safety validator and passes lightness, chroma, CVD
separation, normal-vision separation, and contrast in both light and dark mode.
Direction is never carried by color alone, since the panel headings and the
table view both state it in words.

Rows with fewer battles than the confidence threshold render faded but are
still shown, with the sample size and confidence interval on hover. That was a
deliberate choice over hiding them: the site is useful from day one, and the
uncertainty is visible rather than implied.

Not affiliated with Supercell. Brawler portraits are served from the Brawlify
CDN.
