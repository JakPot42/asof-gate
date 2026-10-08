"""Plant one bug at a time in the tax path and require the tax suite to go red.

The same check as tests/test_planted_bugs.py makes for the SNAP path. Each case edits one line
of the tax gate or of the verifier's tax recomputation in a temporary copy of the repository,
runs tests/test_tax.py there, and passes only if pytest exits 1. An untouched copy must exit 0.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE, VERIFIER = "asof_gate/tax.py", "verifier/verify_receipt.py"

BUGS = {
    "gate: whose W-2 it is goes unchecked": (
        GATE, 'if form["person"] != e["person"]:', 'if False:'),
    "verifier: whose W-2 it is goes unchecked": (
        VERIFIER, 'if form["person"] != entry["person"]:', 'if False:'),
    "gate: a corrected W-2 passes": (
        GATE, 'elif live["id"] != cited["id"]:', 'elif False:'),
    "verifier: a corrected W-2 passes": (
        VERIFIER, 'elif current["id"] != doc["id"]:', 'elif False:'),
    "gate: a correction dated on the decision date is not in force": (
        GATE, 'and rule["field"] in d["correct_information"] and d["date"] <= date]',
        'and rule["field"] in d["correct_information"] and d["date"] < date]'),
    "verifier: a correction dated on the decision date is not in force": (
        VERIFIER, 'if doc["date"] <= date and rule["field"] in doc["correct_information"]:',
        'if doc["date"] < date and rule["field"] in doc["correct_information"]:'),
    "gate: the delta has the wrong sign": (
        GATE, 'delta=value - e["value"])', 'delta=e["value"] - value)'),
    "verifier: the delta has the wrong sign": (
        VERIFIER, 'delta=amount - entry["value"])', 'delta=entry["value"] - amount)'),
    "gate: an overstatement is called an understatement": (
        GATE, "error = p - s", "error = s - p"),
    "verifier: an overstatement is called an understatement": (
        VERIFIER, 'gap = proposed["income_tax"] - supported["income_tax"]',
        'gap = supported["income_tax"] - proposed["income_tax"]'),
    "gate: a W-2 nobody read goes unreported": (
        GATE, 'if item["document"] not in read:', 'if False:'),
    "verifier: a W-2 nobody read goes unreported": (
        VERIFIER, 'if row["document"] in claimed:', 'if True:'),
    "gate: the same W-2 can be counted twice": (
        GATE, "elif already:", "elif False:"),
    "verifier: the same W-2 can be counted twice": (
        VERIFIER, "elif seen_before:", "elif False:"),
    "gate: a figure's first day is out of force": (
        GATE, 'if v["effective_from"] <= date <= v["effective_to"]:',
        'if v["effective_from"] < date <= v["effective_to"]:'),
    "gate: a superseded figure passes when its own source is cited": (
        GATE, 'if num["value"] == right and num.get("source_id") == v["source_id"]:',
        'if num.get("source_id") in rulebook["sources"]:'),
    "verifier: a superseded figure passes when its own source is cited": (
        VERIFIER, 'if value == correct and cited == current["source_id"]:',
        'if cited in rb["sources"]:'),
}


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "repo"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".pytest_cache", "demo", "test_planted_bugs.py",
        "test_tax_planted_bugs.py"))
    return dst


def _suite(repo: Path) -> int:
    return subprocess.run([sys.executable, "-m", "pytest", "tests/test_tax.py", "-q", "-x",
                           "-p", "no:cacheprovider"],
                          cwd=repo, capture_output=True, text=True).returncode


def test_an_untouched_copy_is_green(tmp_path):
    assert _suite(_copy(tmp_path)) == 0


@pytest.mark.parametrize("bug", BUGS)
def test_a_planted_bug_turns_the_tax_suite_red(tmp_path, bug):
    path, old, new = BUGS[bug]
    repo = _copy(tmp_path)
    source = (repo / path).read_text(encoding="utf-8")
    assert source.count(old) == 1, f"the line to break is not in {path} exactly once: {old!r}"
    (repo / path).write_text(source.replace(old, new), encoding="utf-8", newline="\n")
    assert _suite(repo) == 1, f"the tax suite did not fail with this bug planted: {bug}"
