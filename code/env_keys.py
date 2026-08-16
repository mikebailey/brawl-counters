"""Resolve descriptively-named API keys to the canonical names SDKs actually read.

The problem this solves
-----------------------
Every provider SDK reads exactly one variable name: the Anthropic SDK reads
ANTHROPIC_API_KEY, the OpenAI SDK reads OPENAI_API_KEY. But a single generic name
per machine makes it impossible to tell which key a project is using, or which one
to rotate when an org reissues credentials.

So project .env files label their keys:

    ANTHROPIC_API_KEY_JPAL1='sk-ant-...'      # J-PAL org key, issued 2026-08
    OPENAI_API_KEY_JPAL1='sk-...'

and this module binds the labelled key to the canonical name at import time. The
label survives in the file as documentation; the SDK still finds what it needs.

Usage
-----
    from env_keys import load_keys
    report = load_keys()          # call once, early, before constructing clients
    print(report.describe())      # optional: shows which variable supplied each key

Resolution order per provider (first match wins)
------------------------------------------------
    1. CANONICAL set in the project .env            (explicit beats inferred)
    2. exactly one CANONICAL_<LABEL> in .env        (the normal case)
    3. CANONICAL_ACTIVE=<LABEL> names which to use  (required if >1 candidate)
    4. inherited from the environment               (~/.config/secrets, CI secrets)

Ambiguity is an error, never a guess: two labelled keys with no ACTIVE selector
raises rather than silently picking one. A wrong key is worse than no key -- it
bills the wrong account and the failure surfaces far from the cause.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

# Providers to resolve. Add to this tuple as needed -- the canonical name is the
# variable the provider's own SDK reads.
DEFAULT_PROVIDERS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "PARLEY_API_KEY",
)

# A label may contain letters, digits and underscores: ANTHROPIC_API_KEY_JPAL1,
# ANTHROPIC_API_KEY_ORG_1234. Anchored so it cannot match unrelated variables.
_LABEL_RE = r"^{canonical}_(?P<label>[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)$"

# Suffixes that are configuration, not key labels, so they must never be treated
# as a candidate key.
_RESERVED_SUFFIXES = frozenset({"ACTIVE", "FILE", "PATH", "ID", "VAR"})


class KeyResolutionError(RuntimeError):
    """Raised when a provider's key cannot be resolved unambiguously."""


@dataclass
class Resolution:
    """Where a single provider's key came from."""

    canonical: str
    source_var: str | None = None
    origin: str = "missing"  # "dotenv" | "inherited" | "missing"


@dataclass
class KeyReport:
    resolutions: dict[str, Resolution] = field(default_factory=dict)

    def describe(self) -> str:
        """Human-readable summary. Never includes key values."""
        lines = []
        for canonical, r in self.resolutions.items():
            if r.origin == "missing":
                lines.append(f"  {canonical:20} -- not set")
            else:
                via = "" if r.source_var == canonical else f" (from {r.source_var})"
                lines.append(f"  {canonical:20} <- {r.origin}{via}")
        return "\n".join(lines) or "  (no providers checked)"

    def resolved(self) -> list[str]:
        return [c for c, r in self.resolutions.items() if r.origin != "missing"]


def _find_project_root(start: Path | None) -> Path:
    """Walk up from `start` looking for a .env; fall back to the caller's dir.

    Derived rather than hardcoded so the same code works on macOS, Windows and a
    Linux server, and regardless of the current working directory.
    """
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".env").exists() or (candidate / ".git").exists():
            return candidate
    return here


def _candidates(canonical: str, values: dict[str, str | None]) -> dict[str, str]:
    """Labelled keys for one provider, e.g. {'JPAL1': 'sk-ant-...'}."""
    pattern = re.compile(_LABEL_RE.format(canonical=re.escape(canonical)))
    found = {}
    for name, value in values.items():
        match = pattern.match(name or "")
        if not match or not value:
            continue
        label = match.group("label")
        if label.upper() in _RESERVED_SUFFIXES:
            continue
        found[label] = value
    return found


def load_keys(
    project_root: str | Path | None = None,
    providers: tuple[str, ...] = DEFAULT_PROVIDERS,
    *,
    strict: bool = True,
) -> KeyReport:
    """Load the project .env and bind labelled keys to canonical SDK names.

    Args:
        project_root: directory holding .env. Auto-detected if omitted.
        providers:    canonical variable names to resolve.
        strict:       raise on ambiguity. Set False to skip ambiguous providers.

    Returns:
        KeyReport describing where each key came from (never the values).
    """
    root = Path(project_root) if project_root else _find_project_root(None)
    env_path = root / ".env"
    file_values = dotenv_values(env_path) if env_path.exists() else {}

    report = KeyReport()

    for canonical in providers:
        # 1. Explicit canonical in the project file always wins. override=True
        #    semantics: the project file beats anything inherited.
        explicit = file_values.get(canonical)
        if explicit:
            os.environ[canonical] = explicit
            report.resolutions[canonical] = Resolution(canonical, canonical, "dotenv")
            continue

        found = _candidates(canonical, file_values)

        # 3. An ACTIVE selector disambiguates, and is required when >1 candidate.
        active = (file_values.get(f"{canonical}_ACTIVE") or "").strip()
        if active:
            if active not in found:
                raise KeyResolutionError(
                    f"{canonical}_ACTIVE='{active}' but no {canonical}_{active} "
                    f"in {env_path}. Available labels: {sorted(found) or 'none'}"
                )
            os.environ[canonical] = found[active]
            report.resolutions[canonical] = Resolution(
                canonical, f"{canonical}_{active}", "dotenv"
            )
            continue

        if len(found) == 1:
            # 2. The normal case: one labelled key, unambiguous.
            label, value = next(iter(found.items()))
            os.environ[canonical] = value
            report.resolutions[canonical] = Resolution(
                canonical, f"{canonical}_{label}", "dotenv"
            )
            continue

        if len(found) > 1:
            message = (
                f"{len(found)} candidate keys for {canonical} in {env_path}: "
                f"{sorted(f'{canonical}_{lbl}' for lbl in found)}. "
                f"Add {canonical}_ACTIVE=<LABEL> to choose one."
            )
            if strict:
                raise KeyResolutionError(message)
            report.resolutions[canonical] = Resolution(canonical, None, "missing")
            continue

        # 4. Nothing in the project file -- inherit whatever the environment has
        #    (~/.config/secrets for cross-project keys, or CI-injected secrets).
        if os.environ.get(canonical):
            report.resolutions[canonical] = Resolution(
                canonical, canonical, "inherited"
            )
        else:
            report.resolutions[canonical] = Resolution(canonical, None, "missing")

    return report


if __name__ == "__main__":
    print(load_keys(strict=False).describe())
