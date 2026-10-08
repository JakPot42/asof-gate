"""The tax path: fixtures, sources, the independent verifier, and gate against verifier.

Every return here is synthetic. The tests never call PolicyEngine. They read the committed
impact table, and check its four tax amounts against the 2025 rate table by hand.
"""

from __future__ import annotations

import ast
import copy
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

from asof_gate import tax  # noqa: E402

RULEBOOK = ROOT / "rules" / "tax_ty2025_form1040.json"
IMPACTS = ROOT / "impacts" / "policyengine_tax.json"
CASE = ROOT / "fixtures" / "tax" / "cases" / "case_t1.json"
VERIFIER = ROOT / "verifier" / "verify_receipt.py"
NAMES = ["clear", "superseded_document", "superseded_figure", "wrong_person"]
OLD, NEW, LAW = "irs-rev-proc-2024-40", "irs-rev-proc-2025-32", "public-law-119-21"


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def verifier_module():
    spec = importlib.util.spec_from_file_location("vr_tax", VERIFIER)
    vr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vr)
    return vr


def run_verifier(receipt, case=CASE, rulebook=RULEBOOK, impacts=IMPACTS):
    return subprocess.run([sys.executable, str(VERIFIER), str(receipt), "--case", str(case),
                           "--rulebook", str(rulebook), "--impacts", str(impacts)],
                          capture_output=True, text=True, cwd=ROOT)


def receipt_for(name):
    fx = load(ROOT / "fixtures" / "tax" / f"{name}.json")
    return fx, tax.build_receipt(fx["action"], (ROOT / fx["case_file"]).read_bytes(),
                                 RULEBOOK.read_bytes(), IMPACTS.read_bytes())


def write(tmp_path, obj, name="r.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
    return p


def rehash(receipt):
    """What a forger would do: edit the receipt, then recompute its hash."""
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    receipt["receipt_sha256"] = tax.sha256_hex(tax.canonical(body))
    return receipt


def finding(receipt, kind):
    return next(f for f in receipt["decision"]["findings"] if f["kind"] == kind)


# --- the four fixtures -----------------------------------------------------------------

def test_there_are_the_four_tax_fixtures():
    assert sorted(p.stem for p in (ROOT / "fixtures" / "tax").glob("*.json")) == NAMES


@pytest.mark.parametrize("name", NAMES)
def test_fixture_verdict_and_class(name):
    fx, r = receipt_for(name)
    assert r["receipt_schema"] == "asof-gate/tax-receipt/1"
    assert r["decision"]["verdict"] == fx["expect_verdict"]
    assert r["decision"]["impact"].get("class") == fx["expect_class"]


def test_clear_passes_on_the_corrected_wages_and_the_deduction_in_force():
    fx, r = receipt_for("clear")
    assert r["decision"]["findings"] == []
    fields = {e["person"]: (e["value"], e["document"]) for e in fx["action"]["fields"]}
    assert fields == {"taxpayer": (82000, "W2C-1"), "spouse": (68000, "W2-2")}
    assert fx["action"]["numbers"] == [{"parameter": "standard_deduction", "value": 31500,
                                        "source_id": NEW}]
    assert r["decision"]["impact"]["message"].endswith("the federal income tax is $15898.")


def test_wrong_person_says_present_in_a_source_but_not_the_named_persons_document():
    _, r = receipt_for("wrong_person")
    f = finding(r, "WRONG_PERSON")
    assert (f["person"], f["document"], f["document_person"]) == ("taxpayer", "W2-2", "spouse")
    assert "present in a source, but not in taxpayer's document: W2-2 names spouse." in f["message"]
    assert f["person_in_force"] == [{"document": "W2-1", "in_force_document": "W2C-1",
                                     "in_force_date": "2026-02-17", "value": 82000}]
    assert "In force for taxpayer: 82000 (W2C-1, correcting W2-1)." in f["message"]
    # The taxpayer's own form went unread, and the gate says that too.
    missing = finding(r, "FACT_MISSING")
    assert (missing["person"], missing["document"], missing["in_force_value"]) == \
        ("taxpayer", "W2-1", 82000)
    assert len(r["decision"]["findings"]) == 2


def test_superseded_document_names_the_field_the_document_and_what_is_in_force():
    _, r = receipt_for("superseded_document")
    assert [f["kind"] for f in r["decision"]["findings"]] == ["SUPERSEDED_DOCUMENT"]
    f = r["decision"]["findings"][0]
    assert f["superseded_document"] == {"id": "W2-1", "date": "2026-01-22"}
    assert f["in_force_document"] == {"id": "W2C-1", "date": "2026-02-17"}
    assert (f["supplied_value"], f["in_force_value"], f["delta"]) == (76000, 82000, 6000)
    assert f["message"] == (
        "wages for taxpayer = 76000: Box 1 of W2-1 (2026-01-22) is present but superseded on "
        "2026-03-10, corrected by W2C-1 (2026-02-17). In force: 82000 (W2C-1). Delta +6000. "
        "To clear, supply 82000 from W2C-1.")


def test_superseded_figure_says_present_but_not_in_force_and_cites_both_sources():
    _, r = receipt_for("superseded_figure")
    assert [f["kind"] for f in r["decision"]["findings"]] == ["NUMBER_NOT_IN_FORCE"]
    f = r["decision"]["findings"][0]
    assert (f["reason"], f["key"], f["supplied_value"], f["in_force_value"], f["delta"]) == \
        ("value_out_of_date", "joint", 30000, 31500, 1500)
    assert f["superseded"] == {"source_id": OLD, "section": "section 2.15(1)", "pdf_page": 12}
    assert f["in_force"] == {"source_id": NEW, "section": "section 3.01", "pdf_page": 9}
    assert f["enacted_by"]["source_id"] == LAW and f["enacted_by"]["pdf_page"] == 88
    assert f["tax_year"] == 2025 and f["decision_date"] == "2026-03-10"
    assert f["message"] == (
        "standard_deduction[joint] for tax year 2025: 30000 is present but not in force as the "
        "law reads on 2026-03-10. It was the published figure for tax year 2025 from 2024-10-22 "
        "to 2025-07-03 (irs-rev-proc-2024-40, section 2.15(1), PDF page 12). As read on "
        "2026-03-10, the figure for tax year 2025 is 31500 (irs-rev-proc-2025-32, section 3.01, "
        "PDF page 9), enacted by public-law-119-21, section 70102(b) and (c), 139 Stat. 158-159, "
        "PDF page 88. Delta +1500. To clear, supply 31500 from the source in force on 2026-03-10.")
    for source in (OLD, NEW, LAW):
        assert source in f["message"]
    cit = r["citations"]
    assert [c["source_id"] for c in cit["figures"]] == [NEW]
    assert [c["source_id"] for c in cit["superseded_figures"]] == [OLD]
    assert cit["figures"][0]["enacted_by"]["source_id"] == LAW
    sources = load(RULEBOOK)["sources"]
    for c in cit["figures"] + cit["superseded_figures"] + [cit["figures"][0]["enacted_by"]]:
        assert c["sha256"] == sources[c["source_id"]]["sha256"]


TAX = {"clear": 15898, "wrong_person": 12818, "superseded_document": 14578,
       "superseded_figure": 16228}


@pytest.mark.parametrize("name", NAMES)
def test_every_stop_is_material_and_prints_its_tax_impact(name):
    _, r = receipt_for(name)
    impact = r["decision"]["impact"]
    assert impact["proposed"]["income_tax"] == TAX[name]
    if name == "clear":
        return
    assert impact["supported"]["income_tax"] == TAX["clear"]
    assert impact["error"] == TAX[name] - TAX["clear"]
    assert abs(impact["error"]) >= 300, "each stop must move the tax by several hundred dollars"
    way = "overstated" if impact["error"] > 0 else "understated"
    assert impact["message"] == (
        f"Federal income tax would be {way} by ${abs(impact['error'])}: ${TAX[name]} as "
        f"proposed, ${TAX['clear']} on the documents and figures in force.")


def joint_tax_2025(taxable: int) -> int:
    """Rev. Proc. 2024-40, section 2.01, Table 1 (PDF page 5), the row these returns use:
    over $96,950 but not over $206,700, $11,157 plus 22% of the excess over $96,950."""
    if 96950 < taxable <= 206700:
        return round(11157 + 0.22 * (taxable - 96950))
    raise AssertionError("outside the row transcribed here")


def test_the_impact_table_matches_the_rate_table_by_hand():
    table = load(IMPACTS)
    assert table["engine"]["name"] == "policyengine-us"
    assert tuple(int(x) for x in table["engine"]["version"].split(".")) >= (2, 24, 5)
    assert len(table["results"]) == 4
    for key, row in table["results"].items():
        inputs = row["inputs"]
        assert tax.sha256_hex(tax.canonical(inputs)) == key
        taxable = sum(w["amount"] for w in inputs["wages"]) - inputs["figures"]["standard_deduction"]
        assert row["income_tax"] == joint_tax_2025(taxable)


def test_the_case_file_says_it_is_synthetic():
    case = load(CASE)
    assert "Synthetic" in case["note"] and "No real person" in case["note"]
    assert all("synthetic" in d["payer"] for d in case["documents"])


# --- the figures and their sources -----------------------------------------------------

def test_the_rulebook_cells_are_the_sourced_values():
    old, new = load(RULEBOOK)["parameters"]["standard_deduction"]["versions"]
    assert old["values"] == {"joint": 30000, "head_of_household": 22500, "single": 15000,
                             "separate": 15000}
    assert (old["source_id"], old["section"], old["pdf_page"]) == (OLD, "section 2.15(1)", 12)
    assert (old["effective_from"], old["effective_to"]) == ("2024-10-22", "2025-07-03")
    assert new["values"] == {"joint": 31500, "head_of_household": 23625, "single": 15750,
                             "separate": 15750}
    assert (new["source_id"], new["section"], new["pdf_page"]) == (NEW, "section 3.01", 9)
    assert (new["effective_from"], new["effective_to"]) == ("2025-07-04", "9999-12-31")
    assert (new["enacted_by"]["source_id"], new["enacted_by"]["pdf_page"]) == (LAW, 88)
    assert "does not print $31,500" in new["enacted_by"]["states"]


def test_source_pdfs_match_the_rulebook():
    sources = load(RULEBOOK)["sources"]
    assert set(sources) == {OLD, NEW, LAW}
    for s in sources.values():
        assert tax.sha256_hex((ROOT / s["file"]).read_bytes()) == s["sha256"]


PRINTED = [
    (OLD, 12, ["$30,000", "$22,500", "$15,000"]),
    (OLD, 4, ["as in effect on October 22, 2024"]),
    (OLD, 5, ["Over $96,950 but", "$11,157 plus 22%"]),
    (NEW, 9, ["$31,500", "$23,625", "$15,750", "section 2.15(1) of Rev. Proc. 2024-40 is removed"]),
    (NEW, 6, ["$31,500 for married individuals filing a joint return"]),
    (LAW, 88, ["SEC. 70102", "$23,625"]),
    (LAW, 89, ["$15,750", "taxable years beginning after December 31, 2024"]),
]


@pytest.mark.parametrize("source,page,phrases", PRINTED)
def test_each_figure_is_printed_on_the_page_cited(source, page, phrases):
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(ROOT / load(RULEBOOK)["sources"][source]["file"]))
    text = " ".join(reader.pages[page - 1].extract_text().split())
    for phrase in phrases:
        assert phrase in text, f"{phrase!r} is not on PDF page {page} of {source}"


def test_the_public_law_does_not_print_the_joint_figure():
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(ROOT / load(RULEBOOK)["sources"][LAW]["file"]))
    assert not any("31,500" in reader.pages[p].extract_text() for p in (87, 88))


# --- the verifier ----------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_verifier_accepts_every_genuine_receipt(tmp_path, name):
    _, r = receipt_for(name)
    out = run_verifier(write(tmp_path, r))
    assert out.returncode == 0, out.stdout + out.stderr


@pytest.mark.parametrize("name", NAMES)
def test_committed_receipts_are_current_and_verify(name):
    _, r = receipt_for(name)
    committed = ROOT / "receipts" / "tax" / f"{name}.json"
    assert load(committed) == r
    assert run_verifier(committed).returncode == 0


def _drop_missing(r):
    r["decision"]["findings"] = [f for f in r["decision"]["findings"] if f["kind"] != "FACT_MISSING"]


FORGERIES = {
    "a stop relabelled as clear": ("superseded_document", lambda r: r["decision"].update(
        verdict="CLEAR", findings=[])),
    "the tax impact shrunk": ("wrong_person", lambda r: r["decision"]["impact"].update(error=-8)),
    "understated relabelled as no impact": ("superseded_document", lambda r: r["decision"]["impact"]
                                             .__setitem__("class", "no_tax_impact")),
    "the value in force changed": ("superseded_document", lambda r: r["decision"]["findings"][0]
                                   .update(in_force_value=76000, delta=0)),
    "a finding removed": ("wrong_person", _drop_missing),
    "the superseded figure cited as in force": ("superseded_figure", lambda r: r["citations"]["figures"][0]
                                                .update(value=30000)),
    "the cited page changed": ("superseded_figure", lambda r: r["citations"]["figures"][0]
                               .update(pdf_page=1)),
    "the message softened": ("wrong_person", lambda r: r["decision"]["findings"][1]
                             .update(message="wages for taxpayer = 68000: fine.")),
    "a clear receipt for other wages": ("clear", lambda r: r["inputs"]["action"]["fields"][0]
                                        .update(value=76000, document="W2-1")),
    "the engine renamed": ("clear", lambda r: r["citations"]["impact_engine"].update(version="0")),
}


@pytest.mark.parametrize("forgery", FORGERIES)
def test_verifier_rejects_forgery_with_recomputed_hash(tmp_path, forgery):
    name, edit = FORGERIES[forgery]
    _, r = receipt_for(name)
    before = copy.deepcopy(r)
    edit(r)
    assert r != before, "the forgery changed nothing"
    out = run_verifier(write(tmp_path, rehash(r)))
    assert out.returncode == 1, out.stdout


def test_verifier_rejects_edit_without_rehash(tmp_path):
    _, r = receipt_for("superseded_figure")
    r["decision"]["verdict"] = "CLEAR"
    out = run_verifier(write(tmp_path, r))
    assert out.returncode == 1 and "receipt_sha256 does not match" in out.stdout


def test_verifier_rejects_a_different_case_file(tmp_path):
    _, r = receipt_for("superseded_document")
    case = load(CASE)
    case["documents"][2]["correct_information"]["box1_wages"] = 76000
    out = run_verifier(write(tmp_path, r), case=write(tmp_path, case, "case.json"))
    assert out.returncode == 1 and "case file does not hash" in out.stdout


def test_verifier_rejects_a_different_rulebook(tmp_path):
    _, r = receipt_for("superseded_figure")
    rb = load(RULEBOOK)
    rb["parameters"]["standard_deduction"]["versions"][1]["values"]["joint"] = 30000
    out = run_verifier(write(tmp_path, r), rulebook=write(tmp_path, rb, "rb.json"))
    assert out.returncode == 1 and "rulebook does not hash" in out.stdout


def test_verifier_rejects_a_different_impact_table(tmp_path):
    _, r = receipt_for("wrong_person")
    table = load(IMPACTS)
    for row in table["results"].values():
        row["income_tax"] = 15898
    out = run_verifier(write(tmp_path, r), impacts=write(tmp_path, table, "t.json"))
    assert out.returncode == 1 and "impact table does not hash" in out.stdout


def test_verifier_rejects_a_corrupted_source_pdf(tmp_path):
    (tmp_path / "rules").mkdir()
    shutil.copytree(ROOT / "sources", tmp_path / "sources")
    rb = tmp_path / "rules" / RULEBOOK.name
    shutil.copy(RULEBOOK, rb)
    _, r = receipt_for("clear")
    assert run_verifier(write(tmp_path, r), rulebook=rb).returncode == 0
    pdf = tmp_path / "sources" / "irs-rev-proc-2025-32.pdf"
    pdf.write_bytes(pdf.read_bytes() + b"\n")
    out = run_verifier(write(tmp_path, r), rulebook=rb)
    assert out.returncode == 1 and "does not match its recorded sha256" in out.stdout


def test_a_tax_receipt_is_not_checked_as_a_snap_receipt_or_the_reverse(tmp_path):
    _, r = receipt_for("clear")
    r["receipt_schema"] = "asof-gate/receipt/2"
    assert run_verifier(write(tmp_path, rehash(r))).returncode == 1
    snap = load(ROOT / "receipts" / "clear.json")
    snap["receipt_schema"] = "asof-gate/tax-receipt/1"
    out = subprocess.run([sys.executable, str(VERIFIER), str(write(tmp_path, snap, "s.json")),
                          "--case", str(ROOT / "fixtures" / "cases" / "case_a.json"),
                          "--rulebook", str(ROOT / "rules" / "snap_fy2027_48dc.json"),
                          "--impacts", str(ROOT / "impacts" / "policyengine.json")],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 1


def _imports(path: Path) -> set:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0] if node.level == 0 else "<relative>")
    return names


def _function_bodies(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {ast.dump(ast.Module(body=n.body, type_ignores=[]))
            for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and len(n.body) > 3}


def test_the_tax_gate_and_the_verifier_share_no_code():
    assert _imports(ROOT / "asof_gate" / "tax.py") == {"__future__", "hashlib", "json"}
    assert "asof_gate" not in _imports(VERIFIER) and "tax" not in _imports(VERIFIER)
    assert not _function_bodies(ROOT / "asof_gate" / "tax.py") & _function_bodies(VERIFIER)


# --- two implementations, one answer ----------------------------------------------------

FORM_1099 = {"id": "INT-1", "type": "1099_int", "person": "taxpayer", "payer": "Bank (synthetic)",
             "date": "2026-01-30", "box1_interest": 40}


def _cases():
    base = load(CASE)
    base["documents"].append(FORM_1099)
    out = {"as committed": base}

    second = copy.deepcopy(base)
    second["documents"].append({
        "id": "W2C-2", "type": "w2c", "person": "taxpayer", "payer": base["documents"][0]["payer"],
        "date": "2026-03-02", "tax_year": 2025, "corrects": "W2-1",
        "previously_reported": {"box1_wages": 82000}, "correct_information": {"box1_wages": 85000}})
    out["a second correction"] = second

    other_box = copy.deepcopy(base)
    other_box["documents"][2]["correct_information"] = {"box2_federal_income_tax_withheld": 51000}
    out["a correction that does not state Box 1"] = other_box

    widow = copy.deepcopy(base)
    widow["filing_status"] = "qualifying_surviving_spouse"
    out["a filing status outside the rulebook"] = widow

    two_jobs = copy.deepcopy(base)
    two_jobs["documents"].append({"id": "W2-3", "type": "w2", "person": "spouse",
                                  "payer": "Second Employer (synthetic)", "date": "2026-01-29",
                                  "tax_year": 2025, "box1_wages": 19000})
    out["the spouse has two employers"] = two_jobs
    return out


TAXPAYER = [None] + [{"name": "wages", "person": "taxpayer", "value": v, "document": d}
                     for d in ("W2-1", "W2C-1", "W2-2", "", "NOPE", "INT-1")
                     for v in (76000, 82000, 68000)]
SPOUSE = [
    [],
    [{"name": "wages", "person": "spouse", "value": 68000, "document": "W2-2"}],
    [{"name": "wages", "person": "spouse", "value": 68001, "document": "W2-2"}],
    [{"name": "wages", "person": "spouse", "value": 76000, "document": "W2-1"}],
    [{"name": "wages", "person": "spouse", "value": 68000, "document": "W2-2"},
     {"name": "wages", "person": "spouse", "value": 68000, "document": "W2-2"}],
    [{"name": "wages", "person": "spouse", "value": 68000, "document": "W2-2"},
     {"name": "wages", "person": "spouse", "value": 19000, "document": "W2-3"},
     {"name": "tips", "person": "spouse", "value": 5, "document": "W2-2"}],
    [{"name": "wages", "person": "dependent", "value": 68000, "document": "W2-2"}],
]
NUMBERS = [
    [],
    [{"parameter": "standard_deduction", "value": 31500, "source_id": NEW}],
    [{"parameter": "standard_deduction", "value": 30000, "source_id": OLD}],
    [{"parameter": "standard_deduction", "value": 31500, "source_id": OLD}],
    [{"parameter": "standard_deduction", "value": 29200, "source_id": NEW}],
    [{"parameter": "standard_deduction", "value": 31500},
     {"parameter": "personal_exemption", "value": 0, "source_id": NEW}],
]
DATES = ["2024-10-21", "2025-07-03", "2026-01-25", "2026-02-16", "2026-02-17", "2026-03-10"]


def _actions():
    for t, s, n, d in itertools.product(TAXPAYER, SPOUSE, NUMBERS, DATES):
        fields = ([t] if t else []) + s
        yield {"action_id": "generated", "action": "prepare_return", "program": "FORM_1040",
               "tax_year": 2025, "decision_date": d, "fields": copy.deepcopy(fields),
               "numbers": copy.deepcopy(n)}


def _synthetic_table(decisions):
    """A table with an answer for every return the decisions ask about, so the impact
    arithmetic is exercised and not just the `not_in_impact_table` branch."""
    results = {}
    for d in decisions:
        for side in ("proposed", "supported"):
            s = d["impact"].get(side)
            if s:
                inputs = s["inputs"]
                total = sum(w["amount"] for w in inputs["wages"])
                results[s["inputs_sha256"]] = {
                    "income_tax": max(0, total - inputs["figures"]["standard_deduction"]) // 4}
    return {"engine": {"name": "synthetic", "version": "0"}, "results": results}


def test_gate_and_verifier_agree_on_every_generated_return():
    vr = verifier_module()
    rb_bytes = RULEBOOK.read_bytes()
    rb = json.loads(rb_bytes)
    empty = {"engine": {"name": "synthetic", "version": "0"}, "results": {}}
    seen, total = {}, 0
    for label, case in _cases().items():
        case_bytes = json.dumps(case).encode("utf-8")
        actions = list(_actions())
        table = _synthetic_table([tax.decide(a, case, rb, empty) for a in actions])
        table_bytes = json.dumps(table).encode("utf-8")
        for action in actions:
            ours = tax.build_receipt(action, case_bytes, rb_bytes, table_bytes)
            theirs = vr.tax_rebuild(action, case_bytes, rb_bytes, table_bytes)
            assert tax.canonical(ours) == vr.canonical_bytes(theirs), (label, action)
            total += 1
            d = ours["decision"]
            for f in d["findings"]:
                seen[(f["kind"], f.get("reason"))] = seen.get((f["kind"], f.get("reason")), 0) + 1
            seen[("impact", d["impact"].get("class", d["impact"].get("reason", "clear")))] = 1
            if d["verdict"] == "CLEAR":
                # A clear return is exactly the return the documents in force support.
                rule = rb["field_rules"]["wages"]
                want = [{"person": p, "amount": sum(x["value"] for x in vr.standing_for_person(
                    p, case, rule, action["decision_date"]))}
                    for p in sorted(x["id"] for x in case["persons"])]
                assert d["impact"]["proposed"]["inputs"]["wages"] == want, (label, action)
    assert total == len(_cases()) * len(TAXPAYER) * len(SPOUSE) * len(NUMBERS) * len(DATES)
    assert total > 20000
    # Every kind of finding, every reason and every impact class turned up at least once.
    for expected in [("FACT_MISSING", None), ("WRONG_PERSON", None), ("SUPERSEDED_DOCUMENT", None),
                     ("OUT_OF_SCOPE", None), ("NUMBER_MISSING", None), ("UNKNOWN_PARAMETER", None),
                     ("NUMBER_NO_VERSION_IN_FORCE", None),
                     ("FACT_NOT_IN_SOURCE", "no_document_cited"),
                     ("FACT_NOT_IN_SOURCE", "document_not_in_case_file"),
                     ("FACT_NOT_IN_SOURCE", "wrong_document_type"),
                     ("FACT_NOT_IN_SOURCE", "document_dated_after_decision"),
                     ("FACT_NOT_IN_SOURCE", "document_already_used"),
                     ("FACT_NOT_IN_SOURCE", "field_not_in_document"),
                     ("FACT_NOT_IN_SOURCE", "value_not_supported"),
                     ("NUMBER_NOT_IN_FORCE", "value_out_of_date"),
                     ("NUMBER_NOT_IN_FORCE", "source_not_in_force"),
                     ("NUMBER_NOT_IN_FORCE", "value_unknown"),
                     ("impact", "clear"), ("impact", "no_tax_impact"), ("impact", "tax_overstated"),
                     ("impact", "tax_understated"), ("impact", "proposed_inputs_incomplete"),
                     ("impact", "supported_inputs_unavailable")]:
        assert expected in seen, expected


# --- dates -----------------------------------------------------------------------------

def _decide(name, **change):
    fx = load(ROOT / "fixtures" / "tax" / f"{name}.json")
    fx["action"].update(change)
    return tax.decide(fx["action"], load(CASE), load(RULEBOOK), load(IMPACTS))


def test_a_correction_dated_on_the_decision_date_is_in_force():
    d = _decide("superseded_document", decision_date="2026-02-17")
    assert [f["kind"] for f in d["findings"]] == ["SUPERSEDED_DOCUMENT"]


def test_the_original_w2_was_in_force_the_day_before_its_correction():
    assert _decide("superseded_document", decision_date="2026-02-16")["verdict"] == "CLEAR"
    d = _decide("clear", decision_date="2026-02-16")
    assert [(f["kind"], f.get("reason")) for f in d["findings"]] == \
        [("FACT_NOT_IN_SOURCE", "document_dated_after_decision")]


def test_30000_was_in_force_on_3_july_2025_and_not_on_4_july():
    rb = load(RULEBOOK)
    case = load(CASE)
    for date, in_force, stale in (("2025-07-03", (30000, OLD), (31500, NEW)),
                                  ("2025-07-04", (31500, NEW), (30000, OLD))):
        def numbers(pair):
            action = {"action": "prepare_return", "tax_year": 2025, "decision_date": date,
                      "fields": [], "numbers": [{"parameter": "standard_deduction",
                                                 "value": pair[0], "source_id": pair[1]}]}
            return [f for f in tax.decide(action, case, rb, load(IMPACTS))["findings"]
                    if f["subject"] == "standard_deduction"]
        assert numbers(in_force) == []
        assert [(f["kind"], f["reason"]) for f in numbers(stale)] == \
            [("NUMBER_NOT_IN_FORCE", "value_out_of_date")]


def test_the_same_tax_year_read_on_two_dates_gives_two_figures():
    """Two dates, kept apart. The figure applies to tax year 2025 in both readings. What
    differs is the day the law is read: before Public Law 119-21 was enacted, and after."""
    case, rb = load(CASE), load(RULEBOOK)
    assert rb["parameters"]["standard_deduction"]["applies_to_tax_year"] == 2025

    def read_on(date, value, source):
        action = {"action": "prepare_return", "program": "FORM_1040", "tax_year": 2025,
                  "decision_date": date, "fields": [],
                  "numbers": [{"parameter": "standard_deduction", "value": value,
                               "source_id": source}]}
        r = tax.build_receipt(action, json.dumps(case).encode("utf-8"), RULEBOOK.read_bytes(),
                              IMPACTS.read_bytes())
        figure = r["citations"]["figures"][0]
        assert (figure["tax_year"], figure["read_as_of"]) == (2025, date)
        stops = [f for f in r["decision"]["findings"] if f["subject"] == "standard_deduction"]
        return figure["value"], figure["source_id"], stops

    assert read_on("2025-06-01", 30000, OLD) == (30000, OLD, [])
    assert read_on("2026-03-10", 31500, NEW) == (31500, NEW, [])

    # The same $30,000, for the same tax year, is in force on one reading and not on the other.
    value, _, stops = read_on("2026-03-10", 30000, OLD)
    assert value == 31500 and stops[0]["tax_year"] == 2025
    assert "It was the published figure for tax year 2025 from 2024-10-22 to 2025-07-03" \
        in stops[0]["message"]
    assert "As read on 2026-03-10, the figure for tax year 2025 is 31500" in stops[0]["message"]
    # And $31,500 did not exist yet for a return prepared on 1 June 2025.
    value, _, stops = read_on("2025-06-01", 31500, NEW)
    assert value == 30000
    assert "As read on 2025-06-01, the figure for tax year 2025 is 30000" in stops[0]["message"]


def test_the_right_value_from_the_superseded_document_is_still_stopped():
    fx = load(ROOT / "fixtures" / "tax" / "clear.json")
    fx["action"]["fields"][0]["document"] = "W2-1"
    d = tax.decide(fx["action"], load(CASE), load(RULEBOOK), load(IMPACTS))
    f = d["findings"][0]
    assert (f["kind"], f["delta"], d["impact"]["class"]) == ("SUPERSEDED_DOCUMENT", 0, "no_tax_impact")


# --- invalid inputs --------------------------------------------------------------------

def _float_wage(a, c):
    a["fields"][0]["value"] = 82000.0


def _bool_field(a, c):
    a["fields"][0]["value"] = True


def _other_year(a, c):
    a["tax_year"] = 2024


def _correction_of_another_person(a, c):
    c["documents"][2]["corrects"] = "W2-2"


def _correction_before_its_form(a, c):
    c["documents"][2]["date"] = "2026-01-01"


def _repeated_document(a, c):
    c["documents"].append(dict(c["documents"][0]))


def _stranger(a, c):
    c["documents"][1]["person"] = "neighbour"


def _no_document_key(a, c):
    del a["fields"][0]["document"]


INVALID = [_float_wage, _bool_field, _other_year, _correction_of_another_person,
           _correction_before_its_form, _repeated_document, _stranger, _no_document_key]


@pytest.mark.parametrize("edit", INVALID)
def test_invalid_inputs_are_rejected_by_both(tmp_path, edit):
    fx = load(ROOT / "fixtures" / "tax" / "clear.json")
    action, case = fx["action"], load(CASE)
    edit(action, case)
    case_bytes = json.dumps(case).encode("utf-8")
    with pytest.raises(ValueError):
        tax.build_receipt(action, case_bytes, RULEBOOK.read_bytes(), IMPACTS.read_bytes())
    with pytest.raises(ValueError):
        verifier_module().tax_rebuild(action, case_bytes, RULEBOOK.read_bytes(), IMPACTS.read_bytes())


# --- command line ----------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_gate_exit_code_and_printed_impact(name):
    fx = load(ROOT / "fixtures" / "tax" / f"{name}.json")
    out = subprocess.run([sys.executable, "-m", "asof_gate", f"fixtures/tax/{name}.json"],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == (0 if fx["expect_verdict"] == "CLEAR" else 2), out.stderr
    assert f"verdict: {fx['expect_verdict']}" in out.stdout
    if name != "clear":
        assert f"by ${abs(TAX[name] - TAX['clear'])}" in out.stdout
    assert "irs-rev-proc-2025-32, section 3.01, PDF page 9" in out.stdout
