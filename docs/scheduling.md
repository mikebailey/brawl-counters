# Daily updates

`code/daily_update.py` collects a fresh pass of battles, rescores every
matchup, and pushes the regenerated JSON. GitHub Pages redeploys on any change
under `site/`, so the published site follows automatically.

## Why it runs on this machine and not in CI

The Brawl Stars API key is **locked to an IP address**. GitHub Actions runners
get an arbitrary IP from a large pool, so a workflow could never authenticate,
and whitelisting that pool is neither possible nor safe. Collection therefore
has to run here and push the results up. The Action in
`.github/workflows/pages.yml` only publishes what this machine produces.

## The scheduled task

Registered on this PC as **`BrawlCounters-DailyUpdate`**, daily at 04:00.

```powershell
Get-ScheduledTask -TaskName 'BrawlCounters-DailyUpdate'          # check it exists
Get-ScheduledTaskInfo -TaskName 'BrawlCounters-DailyUpdate'      # last/next run, last result
Start-ScheduledTask -TaskName 'BrawlCounters-DailyUpdate'        # run it now
Unregister-ScheduledTask -TaskName 'BrawlCounters-DailyUpdate'   # remove it
```

`StartWhenAvailable` is set, so a run missed because the PC was off fires at the
next opportunity rather than being skipped. `MultipleInstances IgnoreNew`
prevents a slow run from overlapping the next day's. The time limit is 2 hours;
a normal full-pool run takes about 30 minutes.

## What a run does

1. `collect.py --refresh-pool --max-players 20000` — re-walks the leaderboards
   for new player tags, then fetches battlelogs for a full pass of the pool.
   Players are visited least-recently-fetched first, so this rotates through
   everyone rather than re-hitting the same tags.
2. `compute.py` — rescores every matchup and rewrites `site/data/`.
3. Commits `site/data` **only if it changed**, fetches `origin/main`, rebases if
   behind, and pushes.

The rebase step matters because this repo may be worked on from another machine;
pushing from a stale local `main` would fail. If the rebase conflicts the run
aborts it and exits non-zero, leaving the tree clean for a human to look at.

The database itself is never committed. It is 77 MB and grows; it belongs on the
Drive mirror:

```bash
python ~/Projects/sync-mirror.py personal/family/brawl-counters --push
```

## Logs

Appended to `outputs/daily_update.log` (gitignored). Each run logs its steps,
the tail of each script's output, and what it pushed. Check there first when the
site looks stale.

Common failures:

- **403 from the API** — your ISP handed you a new IP. Run
  `python code/check_key.py`; it prints your current IP next to the one the key
  expects. Update the allowed addresses at <https://developer.brawlstars.com>;
  the token itself does not change.
- **Push rejected** — someone pushed from elsewhere and the rebase failed. Sort
  the conflict by hand and re-run.

## Watching modes unlock

`compute.py` ships a per-mode file only when a mode covers at least 60% of all
brawler pairs at 30+ battles. Every run prints the coverage table, so the log
shows the held-back modes creeping up. Gem Grab, Hot Zone and Bounty were in the
40s when this was set up and should clear the bar within about a week of daily
runs. Nothing needs to be edited when they do: the site reads the mode list from
the data.
