"""Tests for the gate, the receipt, and the independent verifier.

The verifier is only worth something if it rejects things, so most of this file tries to get
a false receipt past it: a flipped verdict with a recomputed hash, an edited delta, a receipt
that credits the statute with the dollar figure, swapped inputs, a corrupted source. Every one
must exit 1.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from asof_gate.gate import decide  # noqa: E402
from asof_gate.receipt import build_receipt, canonical, sha256_hex  # noqa: E402

RULEBOOK = ROOT / "rules" / "snap_max_allotment.json"
VERIFIER = ROOT / "verifier" / "verify_receipt.py"
FIXTURES = sorted((ROOT / "fixtures").glob("*.json"))
STDLIB_OK = {"__future__", "argparse", "hashlib", "json", "shutil", "sys", "textwrap", "pathlib"}


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def run_verifier(receipt, case, rulebook=RULEBOOK):
    return subprocess.run([sys.executable, str(VERIFIER), str(receipt), "--case", str(case),
                           "--rulebook", str(rulebook)], capture_output=True, text=True,
                          cwd=ROOT)


def receipt_for(fixture):
    fx = load(fixture)
    return fx, build_receipt(fx["action"], (ROOT / fx["case_file"]).read_bytes(),
                             RULEBOOK.read_bytes())


def write(tmp_path, receipt, name="r.json"):
    p = tmp_path / name
    p.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    return p


def rehash(receipt):
    """What a forger would do: edit the receipt, then recompute its hash."""
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    receipt["receipt_sha256"] = sha256_hex(canonical(body))
    return receipt


# --- the four fixtures -----------------------------------------------------------------

@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_fixture_verdicts(fixture):
    fx, r = receipt_for(fixture)
    assert r["decision"]["verdict"] == fx["expect_verdict"]


def test_stale_constant_says_present_but_not_in_force_with_delta_16():
    _, r = receipt_for(ROOT / "fixtures" / "stale_constant.json")
    (f,) = r["decision"]["findings"]
    assert f["kind"] == "NUMBER_NOT_IN_FORCE" and f["reason"] == "value_out_of_date"
    assert (f["supplied_value"], f["in_force_value"], f["delta"]) == (546, 562, 16)
    assert (f["supplied_effective_from"], f["supplied_effective_to"]) == ("2025-10-01", "2026-09-30")
    assert f["in_force_source_id"] == "usda-fna-snap-cola-fy2027" and f["in_force_pdf_page"] == 4
    assert "present but not in force on 2026-10-01" in f["message"]
    assert "To clear, supply 562 from a source in force on 2026-10-01." in f["message"]


def test_inferred_fact_stops_on_the_empty_span():
    _, r = receipt_for(ROOT / "fixtures" / "inferred_fact.json")
    (f,) = r["decision"]["findings"]
    assert (f["kind"], f["subject"], f["reason"]) == (
        "FACT_NOT_ENTAILED", "monthly_net_income", "no_span_and_no_rule")


def test_the_two_cells_are_the_sourced_values():
    rb = load(RULEBOOK)
    versions = rb["parameters"]["max_allotment_48dc_hh2"]["versions"]
    by_date = {v["effective_from"]: v for v in versions}
    assert by_date["2025-10-01"]["value"] == 546 and by_date["2025-10-01"]["effective_to"] == "2026-09-30"
    assert by_date["2026-10-01"]["value"] == 562 and by_date["2026-10-01"]["source"]["pdf_page"] == 4


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_receipt_never_credits_the_statute_with_the_figure(fixture):
    _, r = receipt_for(fixture)
    authority = r["citations"]["authority"]
    assert set(authority) == {"citation", "role"}
    assert "states no dollar figure" in authority["role"]
    assert not any(ch.isdigit() and ch in "0123456789" for ch in authority["role"])
    for fig in r["citations"]["figures"]:
        assert "U.S.C." not in fig["title"] and fig["source_id"].startswith("usda-")


# --- the verifier accepts genuine receipts ---------------------------------------------

@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_verifier_accepts_every_genuine_receipt(tmp_path, fixture):
    fx, r = receipt_for(fixture)
    res = run_verifier(write(tmp_path, r), ROOT / fx["case_file"])
    assert res.returncode == 0, res.stdout + res.stderr


def test_committed_receipts_verify():
    for fixture in FIXTURES:
        fx = load(fixture)
        res = run_verifier(ROOT / "receipts" / f"{fixture.stem}.json", ROOT / fx["case_file"])
        assert res.returncode == 0, res.stdout


# --- the verifier rejects forgeries, even with a recomputed hash ------------------------

def _forge(edit):
    fx, r = receipt_for(ROOT / "fixtures" / "stale_constant.json")
    edit(r)
    return fx, rehash(r)


FORGERIES = {
    "verdict_flipped_to_clear": lambda r: (r["decision"].update(verdict="CLEAR", findings=[])),
    "delta_edited": lambda r: r["decision"]["findings"][0].update(delta=0),
    "message_softened": lambda r: r["decision"]["findings"][0].update(
        message="max_allotment_48dc_hh2: minor rounding difference."),
    "statute_credited_with_figure": lambda r: r["citations"]["authority"].update(
        role="sets the figure of 562"),
    "figure_source_swapped_to_old_memo": lambda r: r["citations"]["figures"][0].update(
        source_id="usda-fns-snap-fy2026-maximum-allotments"),
    "action_rewritten_to_562": lambda r: r["inputs"]["action"]["numbers"][0].update(value=562),
}


@pytest.mark.parametrize("name", FORGERIES)
def test_verifier_rejects_forgery_with_recomputed_hash(tmp_path, name):
    fx, forged = _forge(FORGERIES[name])
    res = run_verifier(write(tmp_path, forged), ROOT / fx["case_file"])
    assert res.returncode == 1, f"{name} was accepted:\n{res.stdout}"


def test_verifier_rejects_edit_without_rehash(tmp_path):
    fx, r = receipt_for(ROOT / "fixtures" / "stale_constant.json")
    r["decision"]["verdict"] = "CLEAR"
    res = run_verifier(write(tmp_path, r), ROOT / fx["case_file"])
    assert res.returncode == 1 and "receipt_sha256" in res.stdout


def test_verifier_rejects_a_different_case_file(tmp_path):
    fx, r = receipt_for(ROOT / "fixtures" / "inferred_fact.json")
    doctored = tmp_path / "case.txt"
    doctored.write_bytes((ROOT / fx["case_file"]).read_bytes()
                         + b"Nobody in the household has any income.\n")
    res = run_verifier(write(tmp_path, r), doctored)
    assert res.returncode == 1 and "case file does not hash" in res.stdout


def test_verifier_rejects_a_different_rulebook(tmp_path):
    fx, r = receipt_for(ROOT / "fixtures" / "stale_constant.json")
    tree = tmp_path / "tree"
    shutil.copytree(ROOT / "rules", tree / "rules")
    rb = load(tree / "rules" / "snap_max_allotment.json")
    rb["parameters"]["max_allotment_48dc_hh2"]["versions"][1]["value"] = 546
    (tree / "rules" / "snap_max_allotment.json").write_text(json.dumps(rb), encoding="utf-8")
    res = run_verifier(write(tmp_path, r), ROOT / fx["case_file"],
                       tree / "rules" / "snap_max_allotment.json")
    assert res.returncode == 1 and "rulebook does not hash" in res.stdout


def test_verifier_rejects_a_corrupted_source_pdf(tmp_path):
    fx, r = receipt_for(ROOT / "fixtures" / "clear_oct01.json")
    tree = tmp_path / "tree"
    shutil.copytree(ROOT / "rules", tree / "rules")
    shutil.copytree(ROOT / "sources", tree / "sources")
    pdf = tree / "sources" / "usda-fna-snap-cola-fy2027.pdf"
    pdf.write_bytes(pdf.read_bytes() + b"%tampered")
    res = run_verifier(write(tmp_path, r), ROOT / fx["case_file"],
                       tree / "rules" / "snap_max_allotment.json")
    assert res.returncode == 1 and "does not match its recorded sha256" in res.stdout


def test_source_pdfs_match_the_rulebook():
    rb = load(RULEBOOK)
    for v in rb["parameters"]["max_allotment_48dc_hh2"]["versions"]:
        data = (ROOT / v["source"]["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == v["source"]["sha256"]


# --- the verifier is independent of the gate -------------------------------------------

def _imports(source: str) -> set:
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0] if node.level == 0 else "<relative>")
    return names


def test_verifier_imports_only_the_standard_library():
    found = _imports(VERIFIER.read_text(encoding="utf-8"))
    assert found <= STDLIB_OK, found - STDLIB_OK
    assert "asof_gate" not in VERIFIER.read_text(encoding="utf-8").replace(
        "imports nothing from `asof_gate`", "")


def test_the_import_scan_would_catch_a_planted_import():
    planted = VERIFIER.read_text(encoding="utf-8") + "\nfrom asof_gate.gate import decide\n"
    assert "asof_gate" in _imports(planted)


# --- two implementations, one answer ----------------------------------------------------

SPANS = ["Two people live together and buy and prepare food together",
         "Two people live together", "three people", "", "not in the case file at all",
         "Nobody in the household has any income"]
FACT_SHAPES = [
    lambda: {"name": "monthly_net_income", "value": 0, "rule": "closure_no_income"},
    lambda: {"name": "monthly_net_income", "value": 5, "rule": "closure_no_income"},
    lambda: {"name": "monthly_net_income", "value": 0, "rule": "made_up_rule"},
    lambda: {"name": "monthly_net_income", "value": 0, "span": ""},
    None,
]


def _actions():
    dates = ["2025-09-30", "2025-10-01", "2026-09-30", "2026-10-01", "2027-09-30", "2027-10-01"]
    numbers = [None, (546, "usda-fns-snap-fy2026-maximum-allotments"),
               (562, "usda-fna-snap-cola-fy2027"), (546, "usda-fna-snap-cola-fy2027"),
               (562, "usda-fns-snap-fy2026-maximum-allotments"), (600, "usda-fna-snap-cola-fy2027")]
    for date, num, span, hh, inc in itertools.product(dates, numbers, SPANS, (2, 3), FACT_SHAPES):
        facts = [{"name": "household_size", "value": hh, "span": span}]
        if inc is not None:
            facts.append(inc())
        nums = [] if num is None else [{"parameter": "max_allotment_48dc_hh2", "value": num[0],
                                        "source_id": num[1]}]
        yield {"action_id": "x", "action": "pay", "program": "SNAP", "benefit_month": date[:7],
               "decision_date": date, "amount": 0, "facts": facts, "numbers": nums}


def test_gate_and_verifier_agree_on_every_generated_action():
    spec = __import__("importlib.util").util.spec_from_file_location("vr", VERIFIER)
    vr = __import__("importlib.util").util.module_from_spec(spec)
    spec.loader.exec_module(vr)
    rb_bytes = RULEBOOK.read_bytes()
    n = 0
    for case in (ROOT / "fixtures" / "cases").glob("*.txt"):
        cb = case.read_bytes()
        for action in _actions():
            ours = build_receipt(copy.deepcopy(action), cb, rb_bytes)
            theirs = vr.rebuild(copy.deepcopy(action), cb, rb_bytes)
            assert canonical(ours) == vr.canonical_bytes(theirs), action
            n += 1
    assert n == 2 * 6 * 6 * 6 * 2 * 5  # cases x dates x numbers x spans x sizes x income shapes


def test_floats_are_rejected():
    fx = load(ROOT / "fixtures" / "clear_oct01.json")
    fx["action"]["numbers"][0]["value"] = 562.0
    with pytest.raises(ValueError):
        decide(fx["action"], "text", load(RULEBOOK))
