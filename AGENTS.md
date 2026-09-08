# Brawl Counters project guidance

## Architecture and state

- `code/collect.py` gathers short-lived Brawl Stars battle logs into the ignored
  SQLite store; `code/compute.py` produces the tracked static payload under `site/data/`.
- `site/` is the deployable static application. It has no build transform, so changes
  there are production changes.
- The API key is IP-locked. A 403 usually means the home IP changed; use
  `code/check_key.py` before replacing the key.
- Accumulated raw battles belong in the configured Drive mirror, never Git.

## Checks and deployment

- Run `uv run --with python-dotenv python code/test_env_keys.py`, compile the Python
  sources, and run `node --check site/app.js` before committing.
- Do not attempt API collection in CI; the provider key is deliberately IP-restricted.
- Pushing `main` deploys the static site through Cloudflare. Confirm the live page and
  security headers after a change to `site/`.
