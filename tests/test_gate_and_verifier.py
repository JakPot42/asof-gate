"""Tests for the gate, the receipt, and the independent verifier.

The verifier is only worth something if it rejects things, so much of this file tries to get
a false receipt past it: a flipped verdict with a recomputed hash, a label changed from
"counts" to "does not count", a raised tolerance, an edited benefit, a receipt that credits the
statute with the dollar figure, swapped inputs, a corrupted source. Every one must exit 1.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from asof_gate.gate import canonical, decide  # noqa: E402
from asof_gate.receipt import build_receipt, sha256_hex  # noqa: E402

RULEBOOK = ROOT / "rules" / "snap_fy2027_48dc.json"
IMPACTS = ROOT / "impacts" / "policyengine.json"
VERIFIER = ROOT / "verifier" / "verify_receipt.py"
FIXTURES = sorted((ROOT / "fixtures").glob("*.json"))
STDLIB_OK = {"__future__", "argparse", "hashlib", "json", "shutil", "sys", "textwrap", "pathlib"}
FY26, FY27 = "usda-fns-snap-fy2026-maximum-allotments", "usda-fna-snap-cola-fy2027"
PARAMS = ("max_allotment", "standard_deduction", "excess_shelter_cap", "gross_income_limit",
          "net_income_limit")


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def verifier_module():
    spec = importlib.util.spec_from_file_location("vr", VERIFIER)
    vr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vr)
    return vr


def run_verifier(receipt, case, rulebook=RULEBOOK, impacts=IMPACTS):
    return subprocess.run([sys.executable, str(VERIFIER), str(receipt), "--case", str(case),
                           "--rulebook", str(rulebook), "--impacts", str(impacts)],
                          capture_output=True, text=True, cwd=ROOT)


def receipt_for(name):
    fx = load(ROOT / "fixtures" / f"{name}.json")
    return fx, build_receipt(fx["action"], (ROOT / fx["case_file"]).read_bytes(),
                             RULEBOOK.read_bytes(), IMPACTS.read_bytes())


def write(tmp_path, obj, name="r.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
    return p


def rehash(receipt):
    """What a forger would do: edit the receipt, then recompute its hash."""
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    receipt["receipt_sha256"] = sha256_hex(canonical(body))
    return receipt


NAMES = [p.stem for p in FIXTURES]


# --- the five fixtures -----------------------------------------------------------------

def test_there_are_the_six_fixtures():
    assert NAMES == ["clear", "fact_not_in_source", "stale_figure", "stale_table",
                     "superseded_document", "wrong_person"]


@pytest.mark.parametrize("name", NAMES)
def test_fixture_verdict_class_and_label(name):
    fx, r = receipt_for(name)
    d = r["decision"]
    assert d["verdict"] == fx["expect_verdict"]
    assert d["impact"].get("class") == fx["expect_class"]
    assert d["impact"].get("counts_toward_payment_error_rate") == fx["expect_counts"]
    assert d["impact"]["status"] == ("clear" if fx["expect_verdict"] == "CLEAR" else "computed")


def test_clear_names_the_benefit_and_has_no_label():
    _, r = receipt_for("clear")
    i = r["decision"]["impact"]
    assert r["decision"]["findings"] == [] and i["proposed"]["monthly_benefit"] == 587
    assert "counts_toward_payment_error_rate" not in i and r["citations"]["tolerance"] is None


def test_fact_not_in_source_names_the_field_and_the_documents():
    _, r = receipt_for("fact_not_in_source")
    (f,) = r["decision"]["findings"]
    assert (f["kind"], f["reason"], f["subject"], f["person"]) == (
        "FACT_NOT_IN_SOURCE", "value_not_supported", "monthly_earned_income", "p1")
    assert f["documents"] == ["PS-3", "PS-4"] and (f["supplied_value"], f["supported_value"]) == (2795, 2150)
    i = r["decision"]["impact"]
    assert (i["class"], i["error"], i["counts_toward_payment_error_rate"]) == ("underpayment", -154, True)
    assert (i["proposed"]["monthly_benefit"], i["supported"]["monthly_benefit"]) == (433, 587)


def test_the_bonus_is_what_separates_the_two_values():
    case = load(ROOT / "fixtures" / "cases" / "case_a.json")
    stubs = {d["id"]: d for d in case["documents"] if d["type"] == "pay_stub"}
    assert stubs["PS-4"]["one_time_bonus"] == 600
    assert (stubs["PS-3"]["regular_gross"] + stubs["PS-4"]["regular_gross"]) * 215 // 200 == 2150
    assert (stubs["PS-3"]["gross_total"] + stubs["PS-4"]["gross_total"]) * 215 // 200 == 2795


def test_wrong_person_names_whose_document_it_is_and_is_a_wrongful_denial():
    _, r = receipt_for("wrong_person")
    (f,) = r["decision"]["findings"]
    assert (f["kind"], f["person"], f["document_person"], f["supported_value"]) == (
        "WRONG_PERSON", "p1", "p2", 602)
    i = r["decision"]["impact"]
    assert (i["class"], i["counts_toward_payment_error_rate"]) == ("wrongful_denial", False)
    assert (i["proposed"]["eligible"], i["supported"]["eligible"]) == (0, 1)
    assert i["supported"]["monthly_benefit"] == 510
    assert i["supported"]["inputs"]["earned_income"] == [{"person": "p1", "monthly": 2472},
                                                         {"person": "p2", "monthly": 602}]
    assert "outside the payment error rate" in i["message"]


def test_superseded_document_says_present_but_superseded_and_names_what_is_in_force():
    _, r = receipt_for("superseded_document")
    (f,) = r["decision"]["findings"]
    assert f["kind"] == "SUPERSEDED_DOCUMENT"
    assert [d["id"] for d in f["superseded_documents"]] == ["PS-1", "PS-2"]
    assert f["in_force_documents"] == [{"id": "PS-3", "date": "2026-09-11"},
                                       {"id": "PS-4", "date": "2026-09-25"}]
    assert "present but superseded on 2026-10-05" in f["message"]
    i = r["decision"]["impact"]
    assert (i["class"], i["error"], i["counts_toward_payment_error_rate"]) == ("overpayment", 155, True)


def test_stale_figure_is_the_16_dollar_case_and_does_not_count():
    _, r = receipt_for("stale_figure")
    (f,) = r["decision"]["findings"]
    assert f["kind"] == "NUMBER_NOT_IN_FORCE" and f["reason"] == "value_out_of_date"
    assert (f["supplied_value"], f["in_force_value"], f["delta"], f["key"]) == (546, 562, 16, "2")
    assert (f["supplied_effective_from"], f["supplied_effective_to"]) == ("2025-10-01", "2026-09-30")
    assert f["in_force_source_id"] == FY27 and f["in_force_pdf_page"] == 4
    assert "present but not in force on 2026-10-05" in f["message"]
    i = r["decision"]["impact"]
    assert (i["class"], i["error"], i["counts_toward_payment_error_rate"]) == ("underpayment", -16, False)
    assert r["decision"]["verdict"] == "STOP"  # a stop that does not count is still a stop


def test_a_whole_stale_table_on_a_household_of_eight_counts():
    _, r = receipt_for("stale_table")
    found = {f["subject"]: (f["supplied_value"], f["in_force_value"]) for f in r["decision"]["findings"]}
    assert found == {"max_allotment": (1789, 1841), "standard_deduction": (299, 308),
                     "excess_shelter_cap": (744, 769)}
    assert all(f["reason"] == "value_out_of_date" for f in r["decision"]["findings"])
    i = r["decision"]["impact"]
    assert (i["class"], i["error"], i["counts_toward_payment_error_rate"]) == ("underpayment", -62, True)


def test_the_committed_search_says_what_the_readme_says():
    rows = load(ROOT / "impacts" / "stale_figure_search.json")["rows"]
    counting = {(r["size"], r["stale_figure"]) for r in rows if r["counting_cases"]}
    assert counting == {(8, "all_figures")} | {(n, f) for n in (9, 10, 11, 12)
                                              for f in ("max_allotment", "all_figures")}
    worst = {(r["size"], r["stale_figure"]): r["largest_error"] for r in rows}
    assert (worst[(2, "max_allotment")], worst[(7, "all_figures")], worst[(8, "all_figures")],
            worst[(8, "max_allotment")], worst[(9, "max_allotment")]) == (-16, -56, -63, -52, -59)


@pytest.mark.parametrize("name", [n for n in NAMES if n != "clear"])
def test_the_tolerance_is_58_carried_forward_and_cited_to_usda(name):
    _, r = receipt_for(name)
    t = r["citations"]["tolerance"]
    assert (t["value"], t["basis"], t["pdf_page"]) == (58, "carried_forward", 1)
    assert t["source_id"] == "usda-fns-snap-qc-policy-memo-25-04" and "usda.gov" in t["url"]
    assert "none is published for 2026-10-05" in r["decision"]["impact"]["message"] \
        or r["decision"]["impact"]["class"] == "wrongful_denial"


def test_the_rulebook_cells_are_the_sourced_values():
    rb = load(RULEBOOK)
    cell = lambda p, start, k: next(v for v in rb["parameters"][p]["versions"]  # noqa: E731
                                    if v["effective_from"] == start)["values"][k]
    assert (cell("max_allotment", "2025-10-01", "2"), cell("max_allotment", "2026-10-01", "2")) == (546, 562)
    assert (cell("max_allotment", "2026-10-01", "3"), cell("max_allotment", "2026-10-01", "8")) == (808, 1841)
    assert (cell("standard_deduction", "2025-10-01", "3"), cell("standard_deduction", "2026-10-01", "3")) == (209, 217)
    assert (cell("excess_shelter_cap", "2025-10-01", "all"), cell("excess_shelter_cap", "2026-10-01", "all")) == (744, 769)
    assert (cell("gross_income_limit", "2026-10-01", "3"), cell("net_income_limit", "2026-10-01", "3")) == (2960, 2277)
    assert rb["qc_tolerance"]["versions"] == [{"value": 58, "effective_from": "2025-10-01",
                                               "effective_to": "2026-09-30",
                                               "source_id": "usda-fns-snap-qc-policy-memo-25-04",
                                               "pdf_page": 1}]


@pytest.mark.parametrize("name", NAMES)
def test_receipt_never_credits_the_statute_or_the_engine_with_a_figure(name):
    _, r = receipt_for(name)
    authority = r["citations"]["authority"]
    assert set(authority) == {"citation", "role"}
    assert "states no dollar figure" in authority["role"]
    assert not any(ch.isdigit() for ch in authority["role"])
    assert len(r["citations"]["figures"]) == 5
    for fig in r["citations"]["figures"]:
        assert "U.S.C." not in fig["title"] and fig["source_id"].startswith("usda-")
    assert "not used to reach the verdict" in r["citations"]["impact_engine"]["role"]


def test_the_verdict_does_not_depend_on_the_impact_table():
    rb = load(RULEBOOK)
    for name in NAMES:
        fx, r = receipt_for(name)
        bare = decide(fx["action"], load(ROOT / fx["case_file"]), rb, {"results": {}})
        assert (bare["verdict"], bare["findings"]) == (r["decision"]["verdict"], r["decision"]["findings"])
        if name != "clear":
            assert bare["impact"]["reason"] == "not_in_impact_table"


# --- the impact table ------------------------------------------------------------------

def test_the_impact_table_is_keyed_by_the_hash_of_its_inputs():
    table = load(IMPACTS)
    assert table["engine"]["name"] == "policyengine-us" and len(table["results"]) == 9
    for key, row in table["results"].items():
        assert hashlib.sha256(canonical(row["inputs"])).hexdigest() == key
        assert row["eligible"] in (0, 1) and (row["eligible"] or row["monthly_benefit"] == 0)


def test_the_shelter_cap_binds_wherever_a_household_is_eligible():
    """The standard utility allowance is a State figure this rulebook does not check. The
    fixtures are built so the allowance's exact amount cannot move a benefit."""
    for row in load(IMPACTS)["results"].values():
        if row["eligible"]:
            cap = row["inputs"]["figures"]["excess_shelter_cap"]
            assert row["detail"]["excess_shelter_deduction"] == f"{cap}.00"


# --- the verifier accepts genuine receipts ---------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_verifier_accepts_every_genuine_receipt(tmp_path, name):
    fx, r = receipt_for(name)
    res = run_verifier(write(tmp_path, r), ROOT / fx["case_file"])
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize("name", NAMES)
def test_committed_receipts_are_current_and_verify(name):
    fx, r = receipt_for(name)
    assert load(ROOT / "receipts" / f"{name}.json") == r
    res = run_verifier(ROOT / "receipts" / f"{name}.json", ROOT / fx["case_file"])
    assert res.returncode == 0, res.stdout


# --- the verifier rejects forgeries, even with a recomputed hash ------------------------

def _impact(r):
    return r["decision"]["impact"]


FORGERIES = {
    # fixture, edit
    "verdict_flipped_to_clear": ("fact_not_in_source", lambda r: r["decision"].update(
        verdict="CLEAR", findings=[])),
    "label_flipped_to_does_not_count": ("superseded_document", lambda r: _impact(r).update(
        counts_toward_payment_error_rate=False)),
    "label_flipped_and_message_rewritten": ("superseded_document", lambda r: _impact(r).update(
        counts_toward_payment_error_rate=False,
        message=_impact(r)["message"].replace("Counts toward", "Does not count toward")
        .replace("above", "at or below"))),
    "tolerance_raised_to_200": ("fact_not_in_source", lambda r: (
        _impact(r)["tolerance"].update(value=200), r["citations"]["tolerance"].update(value=200),
        _impact(r).update(counts_toward_payment_error_rate=False))),
    "error_shrunk_under_the_tolerance": ("fact_not_in_source", lambda r: (
        _impact(r).update(error=-40, counts_toward_payment_error_rate=False),
        _impact(r)["proposed"].update(monthly_benefit=547))),
    "wrongful_denial_relabelled_no_impact": ("wrong_person", lambda r: _impact(r).update(
        {"class": "no_benefit_impact", "error": 0})),
    "denied_benefit_edited": ("wrong_person", lambda r: _impact(r)["supported"].update(
        monthly_benefit=12)),
    "stale_16_relabelled_as_counting": ("stale_figure", lambda r: _impact(r).update(
        counts_toward_payment_error_rate=True)),
    "delta_edited": ("stale_figure", lambda r: r["decision"]["findings"][0].update(delta=0)),
    "message_softened": ("stale_figure", lambda r: r["decision"]["findings"][0].update(
        message="max_allotment[2]: minor rounding difference.")),
    "supported_value_edited": ("fact_not_in_source", lambda r: r["decision"]["findings"][0].update(
        supported_value=2795)),
    "wrong_person_finding_dropped": ("wrong_person", lambda r: r["decision"].update(
        verdict="CLEAR", findings=[], impact={"status": "clear", "proposed": _impact(r)["proposed"],
                                              "supported": None, "message": "No stop."})),
    "statute_credited_with_figure": ("clear", lambda r: r["citations"]["authority"].update(
        role="sets the figure of 808")),
    "figure_source_swapped_to_old_table": ("clear", lambda r: r["citations"]["figures"][0].update(
        source_id=FY26)),
    "engine_credited_with_the_verdict": ("clear", lambda r: r["citations"]["impact_engine"].update(
        role="decided the case")),
    "action_rewritten_to_the_supported_value": ("fact_not_in_source", lambda r: [
        e.update(value=2150) for e in r["inputs"]["action"]["facts"]
        if e["name"] == "monthly_earned_income"]),
    "action_rewritten_to_562": ("stale_figure", lambda r: r["inputs"]["action"]["numbers"][0].update(
        value=562)),
}


@pytest.mark.parametrize("forgery", FORGERIES)
def test_verifier_rejects_forgery_with_recomputed_hash(tmp_path, forgery):
    name, edit = FORGERIES[forgery]
    fx, r = receipt_for(name)
    before = copy.deepcopy(r)
    edit(r)
    assert r != before, "the forgery changed nothing"
    res = run_verifier(write(tmp_path, rehash(r)), ROOT / fx["case_file"])
    assert res.returncode == 1, f"{forgery} was accepted:\n{res.stdout}"


def test_verifier_rejects_edit_without_rehash(tmp_path):
    fx, r = receipt_for("superseded_document")
    r["decision"]["impact"]["counts_toward_payment_error_rate"] = False
    res = run_verifier(write(tmp_path, r), ROOT / fx["case_file"])
    assert res.returncode == 1 and "receipt_sha256" in res.stdout


def test_verifier_rejects_a_different_case_file(tmp_path):
    fx, r = receipt_for("fact_not_in_source")
    case = load(ROOT / fx["case_file"])
    next(d for d in case["documents"] if d["id"] == "PS-4").update(regular_gross=1600, one_time_bonus=0)
    res = run_verifier(write(tmp_path, r), write(tmp_path, case, "case.json"))
    assert res.returncode == 1 and "case file does not hash" in res.stdout


def test_verifier_rejects_a_different_rulebook(tmp_path):
    fx, r = receipt_for("stale_figure")
    tree = tmp_path / "tree"
    shutil.copytree(ROOT / "rules", tree / "rules")
    rb = load(tree / "rules" / RULEBOOK.name)
    rb["parameters"]["max_allotment"]["versions"][1]["values"]["2"] = 546
    (tree / "rules" / RULEBOOK.name).write_text(json.dumps(rb), encoding="utf-8")
    res = run_verifier(write(tmp_path, r), ROOT / fx["case_file"], tree / "rules" / RULEBOOK.name)
    assert res.returncode == 1 and "rulebook does not hash" in res.stdout


def test_verifier_rejects_a_different_impact_table(tmp_path):
    fx, r = receipt_for("superseded_document")
    table = load(IMPACTS)
    table["results"][r["decision"]["impact"]["proposed"]["inputs_sha256"]]["monthly_benefit"] = 600
    res = run_verifier(write(tmp_path, r), ROOT / fx["case_file"],
                       impacts=write(tmp_path, table, "table.json"))
    assert res.returncode == 1 and "impact table does not hash" in res.stdout


def test_what_the_verifier_does_not_establish(tmp_path):
    """SPEC, "What the verifier does and does not establish". The benefit amounts are
    PolicyEngine's and the verifier does not recompute them. A receipt built honestly from a
    doctored table verifies against that doctored table, and against no other. The check on
    the amounts themselves is tools/policyengine_tool.py impacts."""
    fx, honest = receipt_for("superseded_document")
    table = load(IMPACTS)
    table["results"][honest["decision"]["impact"]["proposed"]["inputs_sha256"]]["monthly_benefit"] = 600
    doctored = write(tmp_path, table, "table.json")
    r = build_receipt(fx["action"], (ROOT / fx["case_file"]).read_bytes(), RULEBOOK.read_bytes(),
                      doctored.read_bytes())
    assert r["decision"]["impact"]["counts_toward_payment_error_rate"] is False
    assert run_verifier(write(tmp_path, r), ROOT / fx["case_file"], impacts=doctored).returncode == 0
    assert run_verifier(write(tmp_path, r), ROOT / fx["case_file"]).returncode == 1


def test_verifier_rejects_a_corrupted_source_pdf(tmp_path):
    fx, r = receipt_for("clear")
    tree = tmp_path / "tree"
    shutil.copytree(ROOT / "rules", tree / "rules")
    shutil.copytree(ROOT / "sources", tree / "sources")
    pdf = tree / "sources" / "usda-fna-snap-cola-fy2027.pdf"
    pdf.write_bytes(pdf.read_bytes() + b"%tampered")
    res = run_verifier(write(tmp_path, r), ROOT / fx["case_file"], tree / "rules" / RULEBOOK.name)
    assert res.returncode == 1 and "does not match its recorded sha256" in res.stdout


def test_source_pdfs_match_the_rulebook():
    vendored = 0
    for s in load(RULEBOOK)["sources"].values():
        if s["file"]:
            assert hashlib.sha256((ROOT / s["file"]).read_bytes()).hexdigest() == s["sha256"]
            vendored += 1
        else:
            assert s["not_vendored"]
    assert vendored == 2


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


def test_the_gate_imports_neither_the_verifier_nor_policyengine():
    for path in (ROOT / "asof_gate").glob("*.py"):
        found = _imports(path.read_text(encoding="utf-8"))
        assert found <= STDLIB_OK | {"<relative>"}, (path.name, found)


def _function_bodies(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {ast.dump(ast.Module(body=n.body, type_ignores=[]))
            for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and len(n.body) > 3}


def test_gate_and_verifier_share_no_function_body():
    gate = set().union(*(_function_bodies(p) for p in (ROOT / "asof_gate").glob("*.py")))
    assert not gate & _function_bodies(VERIFIER)


# --- two implementations, one answer ----------------------------------------------------

def _numbers(size, rb, **swap):
    out = []
    for p in PARAMS:
        v27 = rb["parameters"][p]["versions"][-1]
        key = str(size) if rb["parameters"][p]["by"] == "household_size" and str(size) in v27["values"] else \
            ("all" if rb["parameters"][p]["by"] == "all" else "3")
        value, source = swap.get(p, (v27["values"][key], FY27))
        if value is not None:
            out.append({"parameter": p, "value": value, "source_id": source})
    return out


def _variants(case, rb):
    """Actions over one case file: every fact and number right, wrong in each way the
    specification names, and missing."""
    docs = case["documents"]
    stubs = sorted((d for d in docs if d["type"] == "pay_stub"), key=lambda d: (d["person"], d["date"]))
    people = sorted({d["person"] for d in stubs})
    mine = [d["id"] for d in stubs if d["person"] == "p1"]
    earned = [[]]
    if mine:
        last2, first2 = mine[-2:], mine[:2]
        right = sum(d["regular_gross"] for d in stubs if d["id"] in last2) * 215 // 200
        e = lambda person, value, cited: [{"name": "monthly_earned_income", "person": person,  # noqa: E731
                                           "value": value, "documents": cited}]
        earned = [e("p1", right, last2), e("p1", right + 645, last2), e("p1", right, first2),
                  e("p1", right, last2[:1]), e("p1", right, last2 + last2[:1]),
                  e("p1", right, ["PS-99", last2[0]]), e("p1", right, ["LEASE-1", last2[0]]),
                  e("p1", right, []), e("p2", right, last2), []]
        if len(people) > 1:
            other = [d["id"] for d in stubs if d["person"] == people[1]][-2:]
            earned += [e("p1", right, last2) + e("p1", 602, other),
                       e("p1", right, last2) + e("p2", 602, other),
                       e("p1", right, [last2[0], other[0]])]
    n = len(case["household"]["members"])
    rent = next(d["monthly_rent"] for d in docs if d["type"] == "lease")
    sizes = [[{"name": "household_size", "value": v, "documents": ["APP-1"]}] for v in (n, n + 1, 9)] + [[]]
    rents = [[{"name": "monthly_rent", "value": v, "documents": c}]
             for v, c in ((rent, ["LEASE-1"]), (rent - 300, ["LEASE-1"]), (rent, ["UTIL-1"]))]
    heats = [[{"name": "heating_or_cooling_cost", "value": v, "documents": ["UTIL-1"]}] for v in (1, 0)]
    cell26 = lambda p, k: rb["parameters"][p]["versions"][0]["values"][k]  # noqa: E731
    k = str(n)
    numbers = [
        _numbers(n, rb),
        _numbers(n, rb, max_allotment=(cell26("max_allotment", k), FY26)),
        _numbers(n, rb, max_allotment=(cell26("max_allotment", k), FY26),
                 standard_deduction=(cell26("standard_deduction", k), FY26),
                 excess_shelter_cap=(cell26("excess_shelter_cap", "all"), FY26)),
        _numbers(n, rb, excess_shelter_cap=(769, FY26)),
        _numbers(n, rb, gross_income_limit=(1, FY27)),
        _numbers(n, rb, net_income_limit=(None, None)),
        _numbers(n, rb) + [{"parameter": "homeless_shelter_deduction", "value": 205, "source_id": FY27}],
    ]
    dates = ["2026-09-05", "2026-09-30", "2026-10-01", "2026-10-05", "2027-10-01"]
    for date, s, e_, r, h, num in itertools.product(dates, sizes, earned, rents, heats, numbers):
        yield {"action_id": "x", "action": "determine", "program": "SNAP",
               "benefit_month": date[:7], "decision_date": date,
               "facts": copy.deepcopy(s + e_ + r + h), "numbers": copy.deepcopy(num)}


def _synthetic_table(decisions):
    """An impact table that answers every lookup, so every class of label is reached. The
    amounts are made up from the key; this tests the two implementations against each other,
    not PolicyEngine."""
    results = {}
    for d in decisions:
        for side in ("proposed", "supported"):
            s = d["impact"].get(side)
            if s:
                h = int(s["inputs_sha256"][:8], 16)
                eligible = 0 if h % 5 == 0 else 1
                # Benefits on a coarse grid, so errors of exactly the tolerance occur.
                results[s["inputs_sha256"]] = {"eligible": eligible,
                                               "monthly_benefit": eligible * (200 + 29 * (h % 7))}
    return {"engine": {"name": "synthetic", "version": "0"}, "results": results}


def test_gate_and_verifier_agree_on_every_generated_action():
    vr = verifier_module()
    rb_bytes = RULEBOOK.read_bytes()
    rb = json.loads(rb_bytes)
    real = IMPACTS.read_bytes()
    n, seen, kinds = 0, set(), set()
    cases = [(p.read_bytes(), rb_bytes)
             for p in sorted((ROOT / "fixtures" / "cases").glob("*.json"))]
    # A test-only pair, to reach the branch where a tolerance is in force on the decision
    # date: the first household applying in August, under a rulebook that pretends the $58
    # threshold runs through fiscal year 2027. USDA has published no such thing.
    early = json.loads(cases[0][0])
    early["documents"][0]["date"] = "2026-08-01"
    pretend = json.loads(rb_bytes)
    pretend["qc_tolerance"]["versions"][0]["effective_to"] = "2027-09-30"
    cases.append((json.dumps(early).encode(), json.dumps(pretend).encode()))
    for cb, rb_bytes in cases:
        case = json.loads(cb)
        actions = list(_variants(case, rb))
        first = [build_receipt(copy.deepcopy(a), cb, rb_bytes, real) for a in actions]
        table = json.dumps(_synthetic_table([r["decision"] for r in first])).encode()
        for action, with_real in zip(actions, first):
            assert canonical(with_real) == vr.canonical_bytes(
                vr.rebuild(copy.deepcopy(action), cb, rb_bytes, real)), action
            ours = build_receipt(copy.deepcopy(action), cb, rb_bytes, table)
            theirs = vr.rebuild(copy.deepcopy(action), cb, rb_bytes, table)
            assert canonical(ours) == vr.canonical_bytes(theirs), action
            i = ours["decision"]["impact"]
            seen.add((i["status"], i.get("reason"), i.get("class"),
                      i.get("counts_toward_payment_error_rate"),
                      (i.get("tolerance") or {}).get("basis")))
            if i.get("class") in ("overpayment", "underpayment") and abs(i["error"]) == 58:
                seen.add("error_equal_to_the_tolerance")
            kinds |= {(f["kind"], f.get("reason")) for f in ours["decision"]["findings"]}
            n += 2
    assert n == 2 * 5 * 4 * 3 * 2 * 7 * (10 + 13 + 1 + 10 + 10)  # tables x dates x sizes x rents x heats x numbers x earned
    classes = {s[2] for s in seen if isinstance(s, tuple)}
    assert classes >= {"no_benefit_impact", "payment_to_ineligible_household", "wrongful_denial",
                       "overpayment", "underpayment"}
    for c in ("overpayment", "underpayment"):
        assert {s[3] for s in seen if isinstance(s, tuple) and s[2] == c} == {True, False}
    assert {s[4] for s in seen if isinstance(s, tuple)} >= {"in_force", "carried_forward"}
    assert {s[1] for s in seen if isinstance(s, tuple)} >= {
        "proposed_inputs_incomplete", "supported_inputs_unavailable"}
    assert "error_equal_to_the_tolerance" in seen
    assert kinds >= {
        ("FACT_MISSING", None), ("WRONG_PERSON", None),
        ("SUPERSEDED_DOCUMENT", None), ("UNKNOWN_PARAMETER", None), ("NUMBER_MISSING", None),
        ("NUMBER_NO_VERSION_IN_FORCE", None), ("FACT_NOT_IN_SOURCE", "no_document_cited"),
        ("FACT_NOT_IN_SOURCE", "duplicate_document"), ("FACT_NOT_IN_SOURCE", "document_not_in_case_file"),
        ("FACT_NOT_IN_SOURCE", "wrong_document_type"), ("FACT_NOT_IN_SOURCE", "mixed_series"),
        ("FACT_NOT_IN_SOURCE", "wrong_document_count"), ("FACT_NOT_IN_SOURCE", "value_not_supported"),
        ("FACT_NOT_IN_SOURCE", "document_dated_after_decision"),
        ("NUMBER_NOT_IN_FORCE", "value_out_of_date"), ("NUMBER_NOT_IN_FORCE", "source_not_in_force"),
        ("NUMBER_NOT_IN_FORCE", "value_unknown")}


def test_a_household_of_nine_is_out_of_scope_in_both():
    case = load(ROOT / "fixtures" / "cases" / "case_c.json")
    case["household"]["members"] = [{"id": f"p{i}", "name": "x", "age": 30 if i == 1 else 5,
                                     "k12_student": 0, "weekly_hours": 0} for i in range(1, 10)]
    case["documents"][0]["members"] = [f"p{i}" for i in range(1, 10)]
    fx = load(ROOT / "fixtures" / "stale_figure.json")
    fx["action"]["facts"][0]["value"] = 9
    cb, rb, tb = json.dumps(case).encode(), RULEBOOK.read_bytes(), IMPACTS.read_bytes()
    ours = build_receipt(fx["action"], cb, rb, tb)
    assert canonical(ours) == verifier_module().canonical_bytes(
        verifier_module().rebuild(copy.deepcopy(fx["action"]), cb, rb, tb))
    (f,) = ours["decision"]["findings"]
    assert f["kind"] == "OUT_OF_SCOPE" and ours["citations"]["figures"] == []
    assert ours["decision"]["impact"]["reason"] == "proposed_inputs_incomplete"


# --- the boundary: an error equal to the tolerance does not count -----------------------

def _with_benefits(name, proposed, supported, eligible=(1, 1)):
    fx, r = receipt_for(name)
    i = r["decision"]["impact"]
    table = {"engine": {"name": "synthetic", "version": "0"}, "results": {
        i["proposed"]["inputs_sha256"]: {"eligible": eligible[0], "monthly_benefit": proposed},
        i["supported"]["inputs_sha256"]: {"eligible": eligible[1], "monthly_benefit": supported}}}
    tb = json.dumps(table).encode()
    cb, rb = (ROOT / fx["case_file"]).read_bytes(), RULEBOOK.read_bytes()
    ours = build_receipt(fx["action"], cb, rb, tb)
    assert canonical(ours) == verifier_module().canonical_bytes(
        verifier_module().rebuild(copy.deepcopy(fx["action"]), cb, rb, tb))
    return ours["decision"]["impact"]


@pytest.mark.parametrize("proposed,supported,klass,counts", [
    (558, 500, "overpayment", False), (559, 500, "overpayment", True),
    (500, 558, "underpayment", False), (500, 559, "underpayment", True),
    (500, 500, "no_benefit_impact", False), (501, 500, "overpayment", False)])
def test_an_error_equal_to_the_tolerance_does_not_count(proposed, supported, klass, counts):
    i = _with_benefits("fact_not_in_source", proposed, supported)
    assert (i["class"], i["counts_toward_payment_error_rate"]) == (klass, counts)


def test_a_small_payment_to_an_ineligible_household_counts_whatever_its_size():
    i = _with_benefits("fact_not_in_source", 25, 0, eligible=(1, 0))
    assert (i["class"], i["counts_toward_payment_error_rate"]) == ("payment_to_ineligible_household", True)
    assert "does not apply" in i["message"]


def test_a_wrongful_denial_never_counts_whatever_its_size():
    i = _with_benefits("fact_not_in_source", 0, 808, eligible=(0, 1))
    assert (i["class"], i["counts_toward_payment_error_rate"]) == ("wrongful_denial", False)


# --- in force means in force on the decision date --------------------------------------

def _decide(name, **change):
    fx = load(ROOT / "fixtures" / f"{name}.json")
    fx["action"].update(change)
    return decide(fx["action"], load(ROOT / fx["case_file"]), load(RULEBOOK), load(IMPACTS))


def test_the_august_stubs_were_in_force_before_the_september_ones_existed():
    d = _decide("superseded_document", decision_date="2026-09-05", benefit_month="2026-09")
    assert not [f for f in d["findings"] if f["kind"] == "SUPERSEDED_DOCUMENT"]


def test_a_stub_dated_on_the_decision_date_is_in_force():
    d = _decide("clear", decision_date="2026-09-25", benefit_month="2026-09")
    assert not [f for f in d["findings"] if f["subject"] == "monthly_earned_income"]
    d = _decide("clear", decision_date="2026-09-24", benefit_month="2026-09")
    (f,) = [f for f in d["findings"] if f["subject"] == "monthly_earned_income"]
    assert (f["kind"], f["reason"]) == ("FACT_NOT_IN_SOURCE", "document_dated_after_decision")


def test_546_was_in_force_on_30_september_and_not_on_1_october():
    for date, stale in (("2026-09-30", False), ("2026-10-01", True)):
        d = _decide("stale_figure", decision_date=date, benefit_month=date[:7])
        found = [f for f in d["findings"] if f["subject"] == "max_allotment"]
        assert bool(found) is stale
    d = _decide("stale_figure", decision_date="2026-09-30", benefit_month="2026-09")
    # On 30 September the fiscal year 2027 figures are the ones not yet in force, and the
    # application, dated 2 October, does not exist yet.
    assert {f["subject"] for f in d["findings"]} == {
        "household_size", "standard_deduction", "excess_shelter_cap", "gross_income_limit",
        "net_income_limit"}


# --- invalid inputs --------------------------------------------------------------------

@pytest.mark.parametrize("edit", [
    lambda a: a["numbers"][0].update(value=808.0),
    lambda a: a["facts"][0].update(value=True),
    lambda a: a["facts"][0].update(value="3"),
    lambda a: a["facts"][1].pop("person"),
    lambda a: a["facts"][1].update(documents="PS-3"),
    lambda a: a["facts"].append(dict(a["facts"][0])),
])
def test_invalid_inputs_are_rejected_by_both(tmp_path, edit):
    fx, r = receipt_for("clear")
    edit(fx["action"])
    with pytest.raises(ValueError):
        decide(fx["action"], load(ROOT / fx["case_file"]), load(RULEBOOK), load(IMPACTS))
    r["inputs"]["action"] = fx["action"]
    res = run_verifier(write(tmp_path, rehash(r)), ROOT / fx["case_file"])
    assert res.returncode == 1 and "could not recompute" in res.stdout


# --- the command line ------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_gate_exit_code(name):
    fx = load(ROOT / "fixtures" / f"{name}.json")
    res = subprocess.run([sys.executable, "-m", "asof_gate", f"fixtures/{name}.json"],
                         capture_output=True, text=True, cwd=ROOT)
    assert res.returncode == (0 if fx["expect_verdict"] == "CLEAR" else 2), res.stderr
    assert f"verdict: {fx['expect_verdict']}" in res.stdout
