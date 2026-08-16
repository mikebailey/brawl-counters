"""Behavioural tests for env_keys.load_keys. Run: uv run python code/test_env_keys.py"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from env_keys import KeyResolutionError, load_keys  # noqa: E402

PROVIDERS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
passed = failed = 0


def case(name, env_body, ambient=None, expect=None, expect_error=False):
    """Write a .env, clear the environment, resolve, and compare."""
    global passed, failed
    d = Path(tempfile.mkdtemp())
    (d / ".env").write_text(env_body)

    for v in list(os.environ):
        if v.startswith(("ANTHROPIC_", "OPENAI_")):
            del os.environ[v]
    for k, v in (ambient or {}).items():
        os.environ[k] = v

    try:
        load_keys(d, PROVIDERS)
        got = {p: os.environ.get(p) for p in PROVIDERS}
        ok = (not expect_error) and got == expect
        detail = "" if ok else f"\n      expected {expect}\n      got      {got}"
    except KeyResolutionError as e:
        ok = expect_error
        detail = "" if ok else f"\n      unexpected error: {e}"

    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{detail}")


print("=" * 66)
print("env_keys resolution tests")
print("=" * 66)

# The real-world j-wiki / jrfp shape: one labelled key, no canonical anywhere.
case(
    "labelled key binds to canonical",
    "ANTHROPIC_API_KEY_JPAL1='sk-ant-jpal1'\n",
    expect={"ANTHROPIC_API_KEY": "sk-ant-jpal1", "OPENAI_API_KEY": None},
)

# Explicit canonical is an escape hatch and must beat a labelled sibling.
case(
    "explicit canonical beats labelled",
    "ANTHROPIC_API_KEY='sk-ant-explicit'\nANTHROPIC_API_KEY_JPAL1='sk-ant-jpal1'\n",
    expect={"ANTHROPIC_API_KEY": "sk-ant-explicit", "OPENAI_API_KEY": None},
)

# Two labelled keys with no selector must FAIL rather than guess.
case(
    "two labelled keys, no selector -> error",
    "ANTHROPIC_API_KEY_JPAL1='a'\nANTHROPIC_API_KEY_JPAL2='b'\n",
    expect_error=True,
)

case(
    "two labelled keys + ACTIVE selector",
    "ANTHROPIC_API_KEY_JPAL1='a'\nANTHROPIC_API_KEY_JPAL2='b'\nANTHROPIC_API_KEY_ACTIVE=JPAL2\n",
    expect={"ANTHROPIC_API_KEY": "b", "OPENAI_API_KEY": None},
)

case(
    "ACTIVE pointing at a missing label -> error",
    "ANTHROPIC_API_KEY_JPAL1='a'\nANTHROPIC_API_KEY_ACTIVE=NOPE\n",
    expect_error=True,
)

# The cross-project inheritance case: nothing in .env, key comes from
# ~/.config/secrets via the shell.
case(
    "inherits from environment when .env is silent",
    "SOMETHING_ELSE=x\n",
    ambient={"ANTHROPIC_API_KEY": "sk-ant-inherited"},
    expect={"ANTHROPIC_API_KEY": "sk-ant-inherited", "OPENAI_API_KEY": None},
)

# The whole point of override=True: a project key must beat the inherited one.
case(
    "project labelled key OVERRIDES inherited",
    "ANTHROPIC_API_KEY_JPAL1='sk-ant-project'\n",
    ambient={"ANTHROPIC_API_KEY": "sk-ant-inherited"},
    expect={"ANTHROPIC_API_KEY": "sk-ant-project", "OPENAI_API_KEY": None},
)

# _FILE / _ACTIVE style suffixes are configuration, not candidate keys.
case(
    "reserved suffixes are not treated as keys",
    "ANTHROPIC_API_KEY_FILE=/tmp/x\nANTHROPIC_API_KEY_JPAL1='sk-ant-real'\n",
    expect={"ANTHROPIC_API_KEY": "sk-ant-real", "OPENAI_API_KEY": None},
)

case(
    "multi-provider, independent labels",
    "ANTHROPIC_API_KEY_JPAL1='sk-ant-x'\nOPENAI_API_KEY_JPAL1='sk-oai-y'\n",
    expect={"ANTHROPIC_API_KEY": "sk-ant-x", "OPENAI_API_KEY": "sk-oai-y"},
)

case(
    "empty labelled value is ignored, not bound",
    "ANTHROPIC_API_KEY_JPAL1=''\n",
    expect={"ANTHROPIC_API_KEY": None, "OPENAI_API_KEY": None},
)

case(
    "underscored multi-part label",
    "ANTHROPIC_API_KEY_ORG_1234='sk-ant-org'\n",
    expect={"ANTHROPIC_API_KEY": "sk-ant-org", "OPENAI_API_KEY": None},
)

print("=" * 66)
print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
