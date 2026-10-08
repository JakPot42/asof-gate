"""Tax impacts from PolicyEngine. Not on the gate's or the verifier's path.

    python tools/policyengine_tax_tool.py confirm    # do PolicyEngine's 2025 figures match the rulebook?
    python tools/policyengine_tax_tool.py impacts    # check impacts/policyengine_tax.json against a fresh run
    python tools/policyengine_tax_tool.py impacts --write

Needs policyengine-us (see impacts/policyengine_tax.json for the version used). Exit 0 when the
check passes, 1 when it does not.

The gate and the verifier never import this. They read impacts/policyengine_tax.json, a table
of PolicyEngine results keyed by the SHA-256 of the return inputs (docs/SPEC.md, Rule T3).

Every input PolicyEngine is given is written out in `situation()`. The standard deduction the
return uses is set by a reform to the figure in the inputs, so a superseded figure can be
priced. Everything else is PolicyEngine's 2025 law, including the rate tables.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULEBOOK = ROOT / "rules" / "tax_ty2025_form1040.json"
TABLE = ROOT / "impacts" / "policyengine_tax.json"
STATUS = {"joint": "JOINT", "single": "SINGLE", "head_of_household": "HEAD_OF_HOUSEHOLD",
          "separate": "SEPARATE"}
# A State with no income tax, so nothing but federal law is in play. The State does not enter
# the federal figure reported here.
STATE = "TX"


class Unsupported(ValueError):
    """The inputs are outside what this tool will ask PolicyEngine about."""


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def key_of(inputs: dict) -> str:
    return hashlib.sha256(canonical(inputs)).hexdigest()


def engine() -> dict:
    return {"name": "policyengine-us",
            "version": importlib.metadata.version("policyengine-us"),
            "core_version": importlib.metadata.version("policyengine-core")}


def situation(inputs: dict) -> dict:
    """The complete PolicyEngine household. Nothing is set anywhere else."""
    y = str(inputs["tax_year"])
    ids = [p["id"] for p in inputs["persons"]]
    wanted = {"joint": 2, "single": 1}.get(inputs["filing_status"])
    if wanted is None or len(ids) != wanted:
        raise Unsupported("this tool prices a joint return of two people or a single return of one")
    if any(p["age"] >= 65 or p["age"] < 18 for p in inputs["persons"]):
        raise Unsupported("this tool prices adults under 65 only")
    wages = {w["person"]: w["amount"] for w in inputs["wages"]}
    people = {p["id"]: {"age": {y: p["age"]}, "employment_income": {y: wages[p["id"]]},
                        "is_blind": {y: False}} for p in inputs["persons"]}
    group = {"members": ids}
    return {"people": people, "tax_units": {"t": dict(group)}, "marital_units": {"m": dict(group)},
            "families": {"f": dict(group)}, "spm_units": {"s": dict(group)},
            "households": {"h": {"members": ids, "state_name": {y: STATE}}}}


def evaluate(inputs: dict) -> dict:
    from policyengine_core.reforms import Reform
    from policyengine_us import Simulation
    year = inputs["tax_year"]
    path = f"gov.irs.deductions.standard.amount.{STATUS[inputs['filing_status']]}"
    reform = Reform.from_dict(
        {path: {f"{year}-01-01.{year}-12-31": inputs["figures"]["standard_deduction"]}},
        country_id="us")
    sim = Simulation(situation=situation(inputs), reform=reform)

    def get(var):
        return float(sim.calculate(var, year)[0])

    # The engine must have used the inputs it was given and nothing it invented.
    total = sum(w["amount"] for w in inputs["wages"])
    assert get("adjusted_gross_income") == total, "PolicyEngine's AGI is not the wages supplied"
    assert get("standard_deduction") == inputs["figures"]["standard_deduction"], \
        "PolicyEngine did not use the standard deduction supplied"
    assert get("taxable_income") == max(0, total - inputs["figures"]["standard_deduction"])
    tax = get("income_tax")
    assert tax == get("income_tax_before_credits"), "a credit was applied that no input states"
    return {"income_tax": int(tax + 0.5),
            "detail": {"adjusted_gross_income": f"{get('adjusted_gross_income'):.2f}",
                       "taxable_income": f"{get('taxable_income'):.2f}",
                       "income_tax_unrounded": f"{tax:.2f}"},
            "inputs": inputs}


def confirm() -> int:
    """PolicyEngine's 2025 standard deduction against the rulebook's figure in force."""
    from policyengine_us import CountryTaxBenefitSystem
    rb = json.loads(RULEBOOK.read_text(encoding="utf-8"))
    node = CountryTaxBenefitSystem().parameters.gov.irs.deductions.standard.amount
    old, live = rb["parameters"]["standard_deduction"]["versions"]
    bad = 0
    print(f"{engine()['name']} {engine()['version']}, standard deduction for tax year 2025")
    for status, pe_name in STATUS.items():
        pe = getattr(node, pe_name)("2025-12-31")
        ok = pe == live["values"][status]
        bad += not ok
        print(f"  {status:18s} rulebook in force {live['values'][status]:>6}  "
              f"PolicyEngine {pe:>8.0f}  {'match' if ok else 'MISMATCH'}   "
              f"(superseded figure in the rulebook: {old['values'][status]})")
    print("all four match" if not bad else f"{bad} mismatch(es)")
    return 1 if bad else 0


def table_inputs() -> list:
    """Every set of return inputs the committed fixtures need, as proposed and as supported,
    taken from the gate's own impact blocks so there is one definition of the inputs."""
    sys.path.insert(0, str(ROOT))
    from asof_gate.tax import decide
    rb = json.loads(RULEBOOK.read_text(encoding="utf-8"))
    found = {}
    for fx_path in sorted((ROOT / "fixtures" / "tax").glob("*.json")):
        fx = json.loads(fx_path.read_text(encoding="utf-8"))
        case = json.loads((ROOT / fx["case_file"]).read_text(encoding="utf-8"))
        impact = decide(fx["action"], case, rb, {"results": {}})["impact"]
        for side in ("proposed", "supported"):
            if impact.get(side):
                found[impact[side]["inputs_sha256"]] = impact[side]["inputs"]
    return list(found.values())


def impacts(write: bool) -> int:
    results = {}
    for inputs in table_inputs():
        try:
            results[key_of(inputs)] = evaluate(inputs)
        except Unsupported as exc:
            print(f"  declined {key_of(inputs)[:12]}: {exc}")
    table = {
        "what_this_is": "PolicyEngine results keyed by the SHA-256 of the canonical return inputs "
                        "(docs/SPEC.md, Rule T3). Written by tools/policyengine_tax_tool.py.",
        "engine": engine(),
        "results": dict(sorted(results.items())),
    }
    if write:
        TABLE.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n", encoding="utf-8",
                         newline="\n")
        print(f"wrote {len(results)} results to {TABLE.relative_to(ROOT)}")
        return 0
    old = json.loads(TABLE.read_text(encoding="utf-8"))
    same = old["results"] == table["results"]
    print(f"{len(results)} results recomputed with {table['engine']['name']} "
          f"{table['engine']['version']} (table was written with {old['engine']['version']}): "
          + ("identical" if same else "DIFFERENT"))
    return 0 if same else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("confirm", "impacts"))
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    return confirm() if a.command == "confirm" else impacts(a.write)


if __name__ == "__main__":
    sys.exit(main())
