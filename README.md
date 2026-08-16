# brawl-counters

> One-line description of what this project does.

## Where this project lives

The local folder and its mirror are both category-qualified
(`scratch/brawl-counters`); the GitHub repo stays flat (`brawl-counters`).

- **Local:** `~/Projects/scratch/brawl-counters/`
- **GitHub:** `github.com/mikebailey/brawl-counters`
- **Mirror:** no mirror — see below

This project's mirror is recorded in [`.mirror`](.mirror). J-PAL/MIT-owned or
very large data goes to MIT Dropbox; personal and small J-PAL artifacts go to
Google Drive; code-only projects mirror nowhere.

## Data location

Large data files are **not stored in git**. They live on no mirror:

- **macOS:** `{{MIRROR_MACOS}}data/`
- **Windows:** `{{MIRROR_WINDOWS}}data\`
- **Linux:** {{MIRROR_LINUX}}

To get the data on a new machine, or to back up new local files:

```bash
python ~/Projects/sync-mirror.py scratch/brawl-counters          # audit, changes nothing
python ~/Projects/sync-mirror.py scratch/brawl-counters --pull   # mirror -> local
python ~/Projects/sync-mirror.py scratch/brawl-counters --push   # local -> mirror
```

## Setup

```bash
# The repo name is flat, but the local checkout belongs in its category slot.
git clone https://github.com/mikebailey/brawl-counters.git ~/Projects/scratch/brawl-counters
cd ~/Projects/scratch/brawl-counters

# Python projects:
python -m venv .venv
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows
pip install -r requirements.txt   # if present

# Project keys are the norm: put this project's labelled key in .env
# (e.g. ANTHROPIC_API_KEY_JPAL1), then call load_keys() at startup.
cp .env.example .env
python code/test_env_keys.py     # verifies the key wiring resolves
```

## Folder structure

- `code/` — all source code (Python, R, scripts)
- `docs/` — written docs, papers, notes
- `data/` — raw and intermediate data (gitignored, Drive-synced)
- `outputs/` — figures, tables, model outputs (gitignored, Drive-synced)
- `.env` — this project's secrets (gitignored, never synced, chmod 600). Holds a
  *labelled* key per provider, e.g. `ANTHROPIC_API_KEY_JPAL1`, so you can tell
  which account issued it. See `.env.example`.
- `code/env_keys.py` — binds a labelled key to the canonical name the SDK reads
  (`ANTHROPIC_API_KEY`). Call `load_keys()` once at startup; without it a
  labelled key is invisible to the SDK. Cross-project keys (personal, Parley)
  live in `~/.config/secrets/` and are inherited automatically.

## Status

Project phase: _(planning / active / wrapping up / archived)_
