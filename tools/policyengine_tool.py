"""Benefit impacts from PolicyEngine. Not on the gate's or the verifier's path.

    python tools/policyengine_tool.py confirm    # do PolicyEngine's FY 2027 figures match the memo?
    python tools/policyengine_tool.py impacts    # check impacts/policyengine.json against a fresh run
    python tools/policyengine_tool.py impacts --write
    python tools/policyengine_tool.py search     # stale FY 2026 figures on October 2026 cases
    python tools/policyengine_tool.py search --write

Needs policyengine-us (see impacts/policyengine.json for the version used). Exit 0 when the
check passes, 1 when it does not.

The gate and the verifier never import this. They read impacts/policyengine.json, a table of
PolicyEngine results keyed by the SHA-256 of the determination inputs (docs/SPEC.md, Rule 3).
This tool is what fills that table, and what anyone can rerun to check it.

Every input PolicyEngine is given is written out in `situation()`. Anything not written there
is a PolicyEngine default, and the ones that matter are asserted after the run: the engine may
not give the household income the case file does not state, and it may not make the household
categorically eligible through a programme the case file does not mention.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import itertools
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULEBOOK = ROOT / "rules" / "snap_fy2027_48dc.json"
TABLE = ROOT / "impacts" / "policyengine.json"
SEARCH = ROOT / "impacts" / "stale_figure_search.json"
FOREVER = "2100-12-31"

# Programmes PolicyEngine would otherwise model the household as receiving. Each counts as
# SNAP unearned income or confers categorical eligibility, and no case file here states any.
SUPPRESSED = ("tanf", "ssi", "social_security", "social_security_disability",
              "unemployment_compensation")

FIGURE_PATHS = {
    "max_allotment": "gov.usda.snap.max_allotment.main.CONTIGUOUS_US.{size}",
    "standard_deduction": "gov.usda.snap.income.deductions.standard.CONTIGUOUS_US.{ded_size}",
    "excess_shelter_cap": "gov.usda.snap.income.deductions.excess_shelter_expense.cap.CONTIGUOUS_US",
}


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


_SYSTEM = None


def system():
    global _SYSTEM
    if _SYSTEM is None:
        from policyengine_us import CountryTaxBenefitSystem
        _SYSTEM = CountryTaxBenefitSystem()
    return _SYSTEM


def _spread(var: str, year: int, annual: float) -> dict:
    """Set a value at the variable's own period, so no month is left to a formula."""
    if system().variables[var].definition_period == "month":
        return {f"{year}-{m:02d}": annual / 12 for m in range(1, 13)}
    return {str(year): annual}


def situation(inputs: dict, tag: str = "") -> dict:
    """The complete PolicyEngine household. Nothing is set anywhere else.

    `tag` prefixes every entity name, so several households can share one simulation.
    """
    year = int(inputs["benefit_month"][:4])
    y = str(year)
    members = inputs["members"]
    ids = [m["id"] for m in members]
    if inputs["household_size"] != len(members):
        raise Unsupported("household_size differs from the number of members in the case file")
    adults = [m["id"] for m in members if m["age"] >= 18]
    if not 1 <= len(adults) <= 2:
        raise Unsupported("this tool models one adult, or two adults as a married couple")
    earned = {i: 0 for i in ids}
    for e in inputs["earned_income"]:
        if e["person"] not in earned:
            raise Unsupported(f"earned income for {e['person']}, who is not a member")
        earned[e["person"]] += e["monthly"]

    people = {}
    for m in members:
        p = {
            "age": {y: m["age"]},
            "employment_income": {y: earned[m["id"]] * 12},
            "weekly_hours_worked_before_lsr": {y: m["weekly_hours"]},
            "is_in_k12_school": {y: bool(m["k12_student"])},
            "immigration_status": {y: "CITIZEN"},
            "is_disabled": {y: False},
            "is_tax_unit_head": {y: m["id"] == adults[0]},
            "is_tax_unit_spouse": {y: len(adults) == 2 and m["id"] == adults[1]},
            "is_tax_unit_dependent": {y: m["id"] not in adults},
        }
        people[tag + m["id"]] = p
    adults = [tag + i for i in adults]
    ids = [tag + i for i in ids]
    marital = {tag + "mu_adults": {"members": adults}}
    for i in ids:
        if i not in adults:
            marital[f"mu_{i}"] = {"members": [i]}
    sit = {
        "people": people,
        "tax_units": {tag + "tu": {"members": ids}},
        "families": {tag + "fam": {"members": ids}},
        "spm_units": {tag + "spm": {
            "members": ids,
            "housing_cost": {y: inputs["monthly_rent"] * 12},
            "childcare_expenses": {y: 0},
            "has_heating_cooling_expense": {y: bool(inputs["heating_or_cooling_cost"])},
            "takes_up_snap_if_eligible": {y: True},
        }},
        "households": {tag + "hh": {"members": ids, "state_name": {y: inputs["state"]}}},
        "marital_units": marital,
    }
    variables = system().variables
    groups = {"spm_unit": "spm_units", "household": "households", "tax_unit": "tax_units",
              "family": "families"}
    for var in SUPPRESSED:
        v = variables.get(var)
        if v is None:
            continue
        if v.entity.key == "person":
            for p in people.values():
                p[var] = _spread(var, year, 0.0)
        elif v.entity.key in groups:
            for unit in sit[groups[v.entity.key]].values():
                unit[var] = _spread(var, year, 0.0)
    return sit


def _reform(overrides: dict, start: str):
    from policyengine_core.reforms import Reform
    return Reform.from_dict({path: {f"{start}.{FOREVER}": value}
                             for path, value in overrides.items()}, country_id="us")


def simulate_many(many: list, overrides: dict | None = None) -> list:
    """Run PolicyEngine once over several households (same benefit month) and return each
    one's raw monthly values, or the reason the leak checks refuse it."""
    from policyengine_us import Simulation
    month = many[0]["benefit_month"]
    merged = {}
    for n, inputs in enumerate(many):
        for group, units in situation(inputs, f"h{n}_" if len(many) > 1 else "").items():
            merged.setdefault(group, {}).update(units)
    kwargs = {"situation": merged}
    if overrides:
        kwargs["reform"] = _reform(overrides, f"{month}-01")
    sim = Simulation(**kwargs)
    floats = ("snap", "snap_unit_size", "snap_earned_income", "snap_unearned_income",
              "snap_gross_income", "snap_net_income", "snap_max_allotment",
              "snap_standard_deduction", "snap_excess_shelter_expense_deduction",
              "snap_utility_allowance", "snap_fpg")
    bools = ("is_snap_eligible", "meets_snap_gross_income_test", "meets_snap_net_income_test",
             "meets_snap_asset_test", "meets_snap_categorical_eligibility",
             "meets_snap_work_requirements")
    cols = {name: sim.calculate(name, month) for name in floats + bools}
    outs = []
    for n, inputs in enumerate(many):
        out = {name: float(cols[name][n]) for name in floats}
        out.update({name: bool(cols[name][n]) for name in bools})
        countable = sum(e["monthly"] for e in inputs["earned_income"]
                        if not any(m["id"] == e["person"] and m["age"] < 18 and m["k12_student"]
                                   for m in inputs["members"]))
        problems = []
        if out["snap_unit_size"] != inputs["household_size"]:
            problems.append("unit size is not the stated household size")
        if abs(out["snap_earned_income"] - countable) > 0.005:
            problems.append(f"engine counted earned income {out['snap_earned_income']}, "
                            f"the inputs state {countable} countable")
        if out["snap_unearned_income"] != 0:
            problems.append(f"engine gave the household unearned income {out['snap_unearned_income']}")
        if out["meets_snap_categorical_eligibility"]:
            problems.append("engine made the household categorically eligible")
        if not out["meets_snap_work_requirements"]:
            problems.append("the household fails a work requirement the case file says nothing about")
        if not out["meets_snap_asset_test"]:
            problems.append("the household fails an asset test the case file says nothing about")
        out["refused"] = "; ".join(problems)
        outs.append(out)
    return outs


def simulate(inputs: dict, overrides: dict | None = None) -> dict:
    """One household. Raises if the engine assumed something the inputs do not state."""
    out = simulate_many([inputs], overrides)[0]
    if out["refused"]:
        raise Unsupported(out["refused"])
    return out


def evaluate(inputs: dict) -> dict:
    """One row of the impact table: PolicyEngine's answer for these determination inputs.

    The three dollar figures PolicyEngine holds as parameters are forced to the values in the
    inputs, so the answer follows the figures the action supplied (or the gate corrected), not
    whatever PolicyEngine has on file. The two income limits are not parameters in PolicyEngine
    (it derives them from the poverty guideline), so they must equal PolicyEngine's own, rounded
    up to the dollar as USDA publishes them; otherwise this tool declines to answer.
    """
    fig = inputs["figures"]
    size = inputs["household_size"]
    overrides = {FIGURE_PATHS[name].format(size=size, ded_size=min(size, 6)): fig[name]
                 for name in FIGURE_PATHS}
    raw = simulate(inputs, overrides)
    gross_limit = math.ceil(round(raw["snap_fpg"] * 1.3, 6))
    net_limit = math.ceil(round(raw["snap_fpg"], 6))
    if (fig["gross_income_limit"], fig["net_income_limit"]) != (gross_limit, net_limit):
        raise Unsupported(
            f"income limits {fig['gross_income_limit']}/{fig['net_income_limit']} are not the "
            f"limits in force ({gross_limit}/{net_limit}); PolicyEngine cannot be given others")
    if raw["snap_max_allotment"] != fig["max_allotment"] or \
            raw["snap_standard_deduction"] != fig["standard_deduction"]:
        raise Unsupported("PolicyEngine did not take the supplied figures")
    eligible = 1 if raw["is_snap_eligible"] else 0
    benefit = math.floor(raw["snap"] + 1e-9) if eligible else 0
    return {
        "inputs": inputs,
        "eligible": eligible,
        "monthly_benefit": benefit,
        # Strings, because the table is read by code that rejects floating-point numbers.
        "detail": {
            "snap_unrounded": f"{raw['snap']:.2f}",
            "gross_income": f"{raw['snap_gross_income']:.2f}",
            "net_income": f"{raw['snap_net_income']:.2f}",
            "excess_shelter_deduction": f"{raw['snap_excess_shelter_expense_deduction']:.2f}",
            "utility_allowance": f"{raw['snap_utility_allowance']:.2f}",
            "passes_gross_income_test": str(raw["meets_snap_gross_income_test"]).lower(),
            "passes_net_income_test": str(raw["meets_snap_net_income_test"]).lower(),
        },
    }


# --- confirm: PolicyEngine's FY 2027 parameters against the USDA memo ---------------------

def confirm() -> int:
    rb = json.loads(RULEBOOK.read_text(encoding="utf-8"))
    p = system().parameters
    rows, bad = [], 0
    for date, label in (("2026-09-30", "FY 2026"), ("2026-10-01", "FY 2027")):
        snap = p.gov.usda.snap(date)
        fpg = p.gov.hhs.fpg(date)
        mine = {
            "max_allotment": {str(n): snap.max_allotment.main.CONTIGUOUS_US[str(n)]
                              for n in range(1, 9)},
            "standard_deduction": {str(n): snap.income.deductions.standard.CONTIGUOUS_US[str(min(n, 6))]
                                   for n in range(1, 9)},
            "excess_shelter_cap": {"all": snap.income.deductions.excess_shelter_expense.cap.CONTIGUOUS_US},
        }
        if label == "FY 2027":
            base = [fpg.first_person.CONTIGUOUS_US + fpg.additional_person.CONTIGUOUS_US * (n - 1)
                    for n in range(1, 9)]
            mine["net_income_limit"] = {str(n): math.ceil(round(b / 12, 6))
                                        for n, b in zip(range(1, 9), base)}
            mine["gross_income_limit"] = {str(n): math.ceil(round(b * 1.3 / 12, 6))
                                          for n, b in zip(range(1, 9), base)}
        for name, values in mine.items():
            version = next(v for v in rb["parameters"][name]["versions"]
                           if v["effective_from"] <= date <= v["effective_to"])
            for k, usda in version["values"].items():
                ok = float(values[k]) == float(usda)
                bad += not ok
                rows.append((label, name, k, usda, values[k], "ok" if ok else "DIFFERS"))
    e = engine()
    print(f"{e['name']} {e['version']} against the rulebook's USDA figures")
    for r in rows:
        if r[5] != "ok":
            print("  {} {}[{}]: USDA {} PolicyEngine {} {}".format(*r))
    print(f"  {len(rows) - bad} of {len(rows)} figures match" + ("" if bad else ", none differ"))
    return 1 if bad else 0


# --- impacts: the table the gate reads ----------------------------------------------------

def table_inputs() -> list:
    """Every determination-input set the committed fixtures need: as proposed and as supported.

    Taken from the receipts' own `impact` blocks, which the gate fills whether or not the table
    has the answer yet, so there is one definition of the inputs (the gate's and the
    verifier's), and this tool only evaluates them.
    """
    sys.path.insert(0, str(ROOT))
    from asof_gate.gate import decide
    rb = json.loads(RULEBOOK.read_text(encoding="utf-8"))
    found = {}
    for fx_path in sorted((ROOT / "fixtures").glob("*.json")):
        fx = json.loads(fx_path.read_text(encoding="utf-8"))
        case = json.loads((ROOT / fx["case_file"]).read_text(encoding="utf-8"))
        impact = decide(fx["action"], case, rb, {"results": {}})["impact"]
        for side in ("proposed", "supported"):
            if impact.get(side):
                found[impact[side]["inputs_sha256"]] = impact[side]["inputs"]
    return list(found.values())


def impacts(write: bool) -> int:
    results, declined = {}, []
    for inputs in table_inputs():
        try:
            results[key_of(inputs)] = evaluate(inputs)
        except Unsupported as exc:
            declined.append((key_of(inputs)[:12], str(exc)))
    table = {
        "what_this_is": "PolicyEngine results keyed by the SHA-256 of the canonical determination "
                        "inputs (docs/SPEC.md, Rule 3). Written by tools/policyengine_tool.py.",
        "engine": engine(),
        "results": dict(sorted(results.items())),
    }
    for k, why in declined:
        print(f"  declined {k}: {why}")
    if write:
        TABLE.parent.mkdir(exist_ok=True)
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


# --- search: does a stale FY 2026 figure ever produce an error that counts? ---------------

def _household(size: int, monthly_earned: int, rent: int, month: str) -> dict:
    """One or two working adults and children under 14, so no work rule is in play."""
    adults = 1 if size <= 3 else 2
    members = [{"id": f"p{i + 1}", "age": 34 + i, "k12_student": 0, "weekly_hours": 40}
               for i in range(adults)]
    members += [{"id": f"p{adults + i + 1}", "age": 3 + (i % 10), "k12_student": 1 if 3 + (i % 10) >= 6 else 0,
                 "weekly_hours": 0} for i in range(size - adults)]
    return {"benefit_month": month, "state": "TN", "members": members, "household_size": size,
            "earned_income": [{"person": "p1", "monthly": monthly_earned}] if monthly_earned else [],
            "monthly_rent": rent, "heating_or_cooling_cost": 1, "figures": {}}


def search(write: bool) -> int:
    rb = json.loads(RULEBOOK.read_text(encoding="utf-8"))
    tol = rb["qc_tolerance"]["versions"][-1]["value"]
    p = system().parameters
    old, new = p.gov.usda.snap("2026-09-30"), p.gov.usda.snap("2026-10-01")
    fpg_old = p.gov.hhs.fpg("2025-10-01")
    month = "2026-10"

    def stale_sets(size):
        d = str(min(size, 6))
        allot = "gov.usda.snap.max_allotment"
        one = {
            # The whole FY 2026 allotment table: sizes 1 to 8, the per-person addition above 8,
            # and the minimum allotment, which is derived from the 1-person maximum.
            "max_allotment": {
                **{f"{allot}.main.CONTIGUOUS_US.{n}": old.max_allotment.main.CONTIGUOUS_US[str(n)]
                   for n in range(1, 9)},
                f"{allot}.additional.CONTIGUOUS_US": old.max_allotment.additional.CONTIGUOUS_US,
                "gov.usda.snap.min_allotment.published_adjustment.CONTIGUOUS_US":
                    old.min_allotment.published_adjustment.CONTIGUOUS_US},
            "standard_deduction": {FIGURE_PATHS["standard_deduction"].format(size=size, ded_size=d):
                                   old.income.deductions.standard.CONTIGUOUS_US[d]},
            "excess_shelter_cap": {FIGURE_PATHS["excess_shelter_cap"]:
                                   old.income.deductions.excess_shelter_expense.cap.CONTIGUOUS_US},
            # The income limits are the poverty guideline, so last year's limits are last
            # year's guideline.
            "income_limits": {
                "gov.hhs.fpg.first_person.CONTIGUOUS_US": fpg_old.first_person.CONTIGUOUS_US,
                "gov.hhs.fpg.additional_person.CONTIGUOUS_US": fpg_old.additional_person.CONTIGUOUS_US},
        }
        every = {}
        for v in one.values():
            every.update(v)
        one["all_figures"] = every
        return one

    sizes = list(range(1, 13))
    rents = [0, 600, 1000, 1400, 1800, 2400]
    rows, cases = [], 0
    for size in sizes:
        fpg_now = (p.gov.hhs.fpg("2026-10-01").first_person.CONTIGUOUS_US
                   + p.gov.hhs.fpg("2026-10-01").additional_person.CONTIGUOUS_US * (size - 1)) / 12
        top = int(fpg_now * 1.3) + 60
        incomes = sorted(set(list(range(0, top, 150)) + list(range(max(0, top - 140), top + 1, 10))))
        best = {}
        grid = [_household(size, income, rent, month)
                for income, rent in itertools.product(incomes, rents)]
        rights = simulate_many(grid)
        keep = [n for n, r in enumerate(rights) if not r["refused"]]
        cases += len(keep)
        for name, overrides in stale_sets(size).items():
            wrongs = simulate_many(grid, overrides)
            cur = best[name] = {"size": size, "stale_figure": name, "largest_error": 0,
                                "counting_cases": 0, "wrongful_denials": 0,
                                "largest_denied_benefit": 0, "example": None}
            for n in keep:
                right, wrong, hh = rights[n], wrongs[n], grid[n]
                b_right = math.floor(right["snap"] + 1e-9) if right["is_snap_eligible"] else 0
                b_wrong = math.floor(wrong["snap"] + 1e-9) if wrong["is_snap_eligible"] else 0
                err = b_wrong - b_right
                denial = bool(right["is_snap_eligible"] and not wrong["is_snap_eligible"])
                to_ineligible = bool(wrong["is_snap_eligible"] and not right["is_snap_eligible"]
                                     and b_wrong > 0)
                counts = to_ineligible or (not denial and abs(err) > tol)
                cur["counting_cases"] += int(counts)
                if denial:
                    cur["wrongful_denials"] += 1
                    cur["largest_denied_benefit"] = max(cur["largest_denied_benefit"], b_right)
                elif abs(err) > abs(cur["largest_error"]):
                    cur["largest_error"] = err
                    cur["example"] = {
                        "monthly_earned": hh["earned_income"][0]["monthly"] if hh["earned_income"] else 0,
                        "monthly_rent": hh["monthly_rent"],
                        "benefit_with_stale_figure": b_wrong,
                        "benefit_with_figure_in_force": b_right}
        rows.extend(best.values())
        print(f"  size {size}: " + ", ".join(
            f"{r['stale_figure']} {r['largest_error']:+d}" + (f" ({r['counting_cases']} count)"
                                                              if r["counting_cases"] else "")
            for r in best.values()), flush=True)
    out = {
        "what_this_is": "Every FY 2026 figure applied, one at a time and all together, to synthetic "
                        "October 2026 households, against the same household with the FY 2027 "
                        "figures in force. Written by tools/policyengine_tool.py search.",
        "engine": engine(), "benefit_month": month, "state": "TN", "tolerance": tol,
        "grid": {"household_sizes": sizes, "monthly_rents": rents,
                 "monthly_earned": "0 to just past the gross income limit, in steps of 150, and "
                                   "in steps of 10 across the last 140 below the limit",
                 "households": cases},
        "rows": rows,
    }
    if write:
        SEARCH.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {SEARCH.relative_to(ROOT)}")
        return 0
    old_rows = json.loads(SEARCH.read_text(encoding="utf-8"))["rows"]
    print("identical to the committed search" if old_rows == rows else "DIFFERENT from the committed search")
    return 0 if old_rows == rows else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=("confirm", "impacts", "search"))
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    if a.command == "confirm":
        return confirm()
    return impacts(a.write) if a.command == "impacts" else search(a.write)


if __name__ == "__main__":
    sys.exit(main())
