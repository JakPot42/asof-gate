"""Plant one bug at a time in a copy of the repository and require the suite to go red.

A suite that stays green whatever is done to the code proves nothing. Each case below edits
one line of the gate or of the verifier in a temporary copy, runs the main test file there,
and passes only if pytest exits 1 (tests ran and failed). An untouched copy must exit 0, so a
broken copy cannot make every case pass by accident.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE, VERIFIER = "asof_gate/gate.py", "verifier/verify_receipt.py"

BUGS = {
    "gate: an error equal to the tolerance counts": (
        GATE, 'abs(error) > tol["value"]', 'abs(error) >= tol["value"]'),
    "verifier: an overpayment equal to the tolerance counts": (
        VERIFIER, '"overpayment", error > tolerance["value"]', '"overpayment", error >= tolerance["value"]'),
    "gate: a document dated on the decision date is not in force": (
        GATE, 'and d["date"] <= date]', 'and d["date"] < date]'),
    "verifier: biweekly pay is multiplied by 2.16": (
        VERIFIER, "(total * 215) // 200", "(total * 216) // 200"),
    "gate: whose document it is goes unchecked": (
        GATE, 'if rule["person_bound"] and owner != e["person"]:', 'if False:'),
    "verifier: a wrongful denial counts": (
        VERIFIER, 'kind, counts = "wrongful_denial", False', 'kind, counts = "wrongful_denial", True'),
    "gate: a figure's last day is out of force": (
        GATE, 'if v["effective_from"] <= date <= v["effective_to"]:',
        'if v["effective_from"] <= date < v["effective_to"]:'),
    "verifier: the delta has the wrong sign": (
        VERIFIER, '"delta": in_force - value}', '"delta": value - in_force}'),
    "gate: a payment to an ineligible household does not count": (
        GATE, 'kind, counts = "payment_to_ineligible_household", True',
        'kind, counts = "payment_to_ineligible_household", False'),
    "verifier: superseded documents pass": (
        VERIFIER, "    elif replaced:\n", "    elif False:\n"),
    "gate: the tolerance is never carried forward": (
        GATE, 'v, basis = max(earlier, key=lambda x: x["effective_to"]), "carried_forward"',
        'return None'),
}


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "repo"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".pytest_cache", "demo", "test_planted_bugs.py"))
    return dst


def _suite(repo: Path, *extra: str) -> int:
    return subprocess.run([sys.executable, "-m", "pytest", "tests/test_gate_and_verifier.py",
                           "-q", "-x", "-p", "no:cacheprovider", *extra],
                          cwd=repo, capture_output=True, text=True).returncode


def test_an_untouched_copy_is_green(tmp_path):
    assert _suite(_copy(tmp_path), "-k", "not agree_on_every_generated_action") == 0


@pytest.mark.parametrize("bug", BUGS)
def test_a_planted_bug_turns_the_suite_red(tmp_path, bug):
    path, old, new = BUGS[bug]
    repo = _copy(tmp_path)
    source = (repo / path).read_text(encoding="utf-8")
    assert source.count(old) == 1, f"the line to break is not in {path} exactly once: {old!r}"
    (repo / path).write_text(source.replace(old, new), encoding="utf-8", newline="\n")
    assert _suite(repo) == 1, f"the suite did not fail with this bug planted: {bug}"
