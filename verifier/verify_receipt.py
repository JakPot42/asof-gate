"""Independent verifier for asof-gate receipts.

    python verifier/verify_receipt.py RECEIPT --case CASE_FILE --rulebook RULEBOOK --impacts TABLE

Written from docs/SPEC.md, not from the gate. It imports nothing from `asof_gate` and uses
only the Python standard library, so it can be read, audited and run on its own. It rebuilds
the entire receipt from the inputs and accepts it only if every byte of the canonical form
matches. Exit 0 on a match, 1 otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import textwrap
from pathlib import Path

SCHEMA = "asof-gate/receipt/2"
FACTS = ["household_size", "monthly_earned_income", "monthly_rent", "heating_or_cooling_cost"]


class Invalid(ValueError):
    """The inputs are not ones the specification gives an answer for."""


# --- canonical form and hashing --------------------------------------------------------

def canonical_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def contains(obj, kinds) -> bool:
    if isinstance(obj, kinds):
        return True
    if isinstance(obj, dict):
        return any(contains(v, kinds) for v in obj.values())
    if isinstance(obj, list):
        return any(contains(v, kinds) for v in obj)
    return False


def whole_number(v) -> bool:
    return type(v) is int


# --- SPEC "Invalid inputs" -------------------------------------------------------------

def check_inputs(action, case, rb, table) -> None:
    if contains(action, (float, bool)):
        raise Invalid("floating-point or boolean value in the action")
    if contains(case, float) or contains(rb, float) or contains(table, float):
        raise Invalid("floating-point value in the case file, rulebook or impact table")
    seen = set()
    for doc in case["documents"]:
        for field in ("id", "type", "person", "payer", "date"):
            if type(doc.get(field)) is not str:
                raise Invalid("a document lacks " + field)
        if doc["id"] in seen:
            raise Invalid("document id repeated: " + doc["id"])
        seen.add(doc["id"])
    counts = {}
    for entry in action.get("facts", []):
        name = entry.get("name")
        if name not in rb["fact_rules"]:
            continue
        counts[name] = counts.get(name, 0) + 1
        if not whole_number(entry.get("value")):
            raise Invalid(name + " has no integer value")
        cited = entry.get("documents")
        if type(cited) is not list or any(type(c) is not str for c in cited):
            raise Invalid(name + " documents must be a list of ids")
        if rb["fact_rules"][name]["person_bound"] and type(entry.get("person")) is not str:
            raise Invalid(name + " must name a person")
    for name, n in counts.items():
        if rb["fact_rules"][name]["cardinality"] == "one" and n > 1:
            raise Invalid(name + " supplied more than once")
    for number in action.get("numbers", []):
        if type(number.get("parameter")) is not str or not whole_number(number.get("value")):
            raise Invalid("a number lacks a parameter name or an integer value")


# --- Rule 1 ----------------------------------------------------------------------------

def ref(doc) -> dict:
    return {"id": doc["id"], "date": doc["date"]}


def by_date(docs) -> list:
    return sorted(docs, key=lambda d: (d["date"], d["id"]))


def documents_in_force(sample, all_docs, date, count) -> list:
    series = [d for d in all_docs
              if d["type"] == sample["type"] and d["person"] == sample["person"]
              and d["payer"] == sample["payer"] and not d["date"] > date]
    ordered = by_date(series)
    return ordered[len(ordered) - count:] if len(ordered) > count else ordered


def derive(rule, documents) -> int:
    how, field = rule["compute"], rule["field"]
    if how == "count":
        return len(documents[0][field])
    if how == "field":
        return documents[0][field]
    if how == "biweekly_to_monthly":
        total = 0
        for d in documents:
            total += d[field]
        return (total * 215) // 200
    raise Invalid("unknown compute " + how)


def examine(position, entry, rule, case_docs, date):
    """One fact entry. Returns (finding or None, what the documents support or None)."""
    index = {d["id"]: d for d in case_docs}
    cited = entry["documents"]
    finding = {"subject": entry["name"], "entry": position, "supplied_value": entry["value"],
               "documents": list(cited)}
    bound = rule["person_bound"]
    if bound:
        finding["person"] = entry["person"]

    early = None
    if len(cited) == 0:
        early = "no_document_cited"
    elif len(cited) != len(set(cited)):
        early = "duplicate_document"
    elif not all(c in index for c in cited):
        early = "document_not_in_case_file"
    elif not all(index[c]["type"] == rule["reads"] for c in cited):
        early = "wrong_document_type"
    elif len({(index[c]["type"], index[c]["person"], index[c]["payer"]) for c in cited}) > 1:
        early = "mixed_series"
    if early:
        finding.update(kind="FACT_NOT_IN_SOURCE", reason=early)
        return finding, None

    chosen = [index[c] for c in cited]
    current = documents_in_force(chosen[0], case_docs, date, rule["count"])
    holder = chosen[0]["person"]
    backed = None
    if len(current) == rule["count"]:
        backed = {"person": holder if bound else "", "value": derive(rule, current)}
    finding["in_force_documents"] = [ref(d) for d in current]
    finding["supported_value"] = backed["value"] if backed else None

    current_ids = [d["id"] for d in current]
    replaced = by_date([d for d in chosen if d["id"] not in current_ids])
    if bound and holder != entry["person"]:
        finding.update(kind="WRONG_PERSON", document_person=holder)
    elif any(d["date"] > date for d in chosen):
        finding.update(kind="FACT_NOT_IN_SOURCE", reason="document_dated_after_decision")
    elif replaced:
        finding.update(kind="SUPERSEDED_DOCUMENT", superseded_documents=[ref(d) for d in replaced])
    elif len(chosen) != rule["count"]:
        finding.update(kind="FACT_NOT_IN_SOURCE", reason="wrong_document_count")
    elif backed["value"] != entry["value"]:
        finding.update(kind="FACT_NOT_IN_SOURCE", reason="value_not_supported")
    else:
        return None, backed
    return finding, backed


# --- Rule 2 ----------------------------------------------------------------------------

def version_on(versions, date):
    for v in versions:
        if v["effective_from"] <= date <= v["effective_to"]:
            return v
    return None


def cell(param, size) -> str:
    return "all" if param["by"] == "all" else str(size)


def examine_numbers(action, rb, size) -> list:
    date = action["decision_date"]
    given = {}
    for n in action.get("numbers", []):
        given[n["parameter"]] = n
    out = [{"kind": "UNKNOWN_PARAMETER", "subject": name, "entry": -1}
           for name in given if name not in rb["parameters"]]
    for name, param in rb["parameters"].items():
        key = cell(param, size)
        common = {"subject": name, "entry": -1, "key": key, "decision_date": date}
        if name not in given:
            out.append({"kind": "NUMBER_MISSING", **common})
            continue
        current = version_on(param["versions"], date)
        if current is None:
            out.append({"kind": "NUMBER_NO_VERSION_IN_FORCE", **common})
            continue
        value, source = given[name]["value"], given[name].get("source_id")
        in_force = current["values"][key]
        if value == in_force and source == current["source_id"]:
            continue
        f = {"kind": "NUMBER_NOT_IN_FORCE", **common, "supplied_value": value,
             "supplied_source_id": source, "in_force_value": in_force,
             "in_force_source_id": current["source_id"],
             "in_force_pdf_page": current["pdf_page"], "delta": in_force - value}
        if value == in_force:
            f["reason"] = "source_not_in_force"
        else:
            same = sorted((v for v in param["versions"] if v["values"][key] == value),
                          key=lambda v: v["effective_from"])
            if same:
                f["reason"] = "value_out_of_date"
                f["supplied_effective_from"] = same[-1]["effective_from"]
                f["supplied_effective_to"] = same[-1]["effective_to"]
            else:
                f["reason"] = "value_unknown"
        out.append(f)
    return out


# --- messages (SPEC, "Messages") -------------------------------------------------------

def signed(n: int) -> str:
    return ("+" if n >= 0 else "") + str(n)


def id_list(items) -> str:
    return ", ".join(i["id"] if isinstance(i, dict) else i for i in items)


def dated_list(items) -> str:
    return ", ".join("{id} ({date})".format(**i) for i in items)


NOT_IN_SOURCE = {
    "no_document_cited": "no document is cited. To clear, cite the {reads} it comes from.",
    "duplicate_document": "the same document is cited twice.",
    "document_not_in_case_file": "a cited document ({cited}) is not in the case file.",
    "wrong_document_type": "this fact is read from a {reads}, and a cited document is not one.",
    "mixed_series": "the cited documents are not all for the same person and payer.",
    "document_dated_after_decision": "a cited document is dated after the decision date {decision_date}.",
    "wrong_document_count": "this fact is read from {count} {reads} document(s), and {n_cited} cited. In force: {in_force}.",
    "value_not_supported": "not supported by {cited}, which give {supported_value}. To clear, supply {supported_value}.",
}
NUMBER = {
    "NUMBER_MISSING": "{name}: not supplied. The decision needs the value in force on {decision_date}.",
    "NUMBER_NO_VERSION_IN_FORCE": "{name}: no version in the rulebook is in force on {decision_date}.",
    "UNKNOWN_PARAMETER": "{name}: not a parameter in this rulebook.",
    "value_out_of_date": "{name}: {supplied_value} is present but not in force on {decision_date}; it was in force {supplied_effective_from} to {supplied_effective_to}. In force on {decision_date}: {in_force_value} ({in_force_source_id}, PDF page {in_force_pdf_page}). Delta {delta_signed}. To clear, supply {in_force_value} from a source in force on {decision_date}.",
    "source_not_in_force": "{name}: {supplied_value} is the value in force on {decision_date}, but the cited source {supplied_source_id} is not the one in force. To clear, cite {in_force_source_id}.",
    "value_unknown": "{name}: {supplied_value} is not a value of this parameter in any version. In force on {decision_date}: {in_force_value} ({in_force_source_id}, PDF page {in_force_pdf_page}). Delta {delta_signed}.",
}


def describe(f, rb) -> str:
    kind = f["kind"]
    if kind == "FACT_MISSING":
        return f["subject"] + ": not supplied. It is a material fact for this decision."
    if kind in ("OUT_OF_SCOPE", "FACT_NOT_IN_SOURCE", "WRONG_PERSON", "SUPERSEDED_DOCUMENT"):
        head = f["subject"]
        if "person" in f:
            head += " for " + f["person"]
        head += " = " + str(f["supplied_value"]) + ": "
        if kind == "OUT_OF_SCOPE":
            return head + "outside the household sizes this rulebook covers."
        if kind == "FACT_NOT_IN_SOURCE":
            rule = rb["fact_rules"][f["subject"]]
            return head + NOT_IN_SOURCE[f["reason"]].format(
                reads=rule["reads"], count=rule["count"], cited=id_list(f["documents"]),
                n_cited=len(f["documents"]), decision_date=f.get("decision_date"),
                in_force=dated_list(f.get("in_force_documents", [])),
                supported_value=f.get("supported_value"))
        if kind == "WRONG_PERSON":
            text = head + "{} name {}, not {}.".format(id_list(f["documents"]),
                                                      f["document_person"], f["person"])
            if f["supported_value"] is not None:
                text += " The documents in force support {} for {} of {}.".format(
                    f["subject"], f["document_person"], f["supported_value"])
            return text
        text = head + "present but superseded on {}: {}. In force: {}.".format(
            f["decision_date"], dated_list(f["superseded_documents"]),
            dated_list(f["in_force_documents"]))
        if f["supported_value"] is not None:
            text += " Those give {0}. To clear, supply {0} from them.".format(f["supported_value"])
        return text
    fields = dict(f)
    fields["name"] = f["subject"] + ("[" + f["key"] + "]" if "key" in f else "")
    if "delta" in f:
        fields["delta_signed"] = signed(f["delta"])
    return NUMBER[f["reason"] if kind == "NUMBER_NOT_IN_FORCE" else kind].format(**fields)


# --- Rule 3 ----------------------------------------------------------------------------

def determination_inputs(action, case, facts, figures) -> dict:
    single = {}
    wages = []
    for f in facts:
        if f["name"] == "monthly_earned_income":
            wages.append({"person": f["person"], "monthly": f["value"]})
        else:
            single[f["name"]] = f["value"]
    wages.sort(key=lambda w: (w["person"], w["monthly"]))
    members = [{"id": m["id"], "age": m["age"], "k12_student": m["k12_student"],
                "weekly_hours": m["weekly_hours"]} for m in case["household"]["members"]]
    members.sort(key=lambda m: m["id"])
    return {"benefit_month": action["benefit_month"], "state": case["state"],
            "members": members, "household_size": single["household_size"],
            "earned_income": wages, "monthly_rent": single["monthly_rent"],
            "heating_or_cooling_cost": single["heating_or_cooling_cost"], "figures": figures}


def looked_up(inputs, table) -> dict:
    key = digest(canonical_bytes(inputs))
    row = table["results"].get(key)
    return {"inputs": inputs, "inputs_sha256": key,
            "eligible": row["eligible"] if row else None,
            "monthly_benefit": row["monthly_benefit"] if row else None}


def tolerance_for(rb, date):
    versions = rb["qc_tolerance"]["versions"]
    chosen, basis = version_on(versions, date), "in_force"
    if chosen is None:
        past = sorted((v for v in versions if v["effective_to"] < date),
                      key=lambda v: v["effective_to"])
        if not past:
            return None
        chosen, basis = past[-1], "carried_forward"
    return {"value": chosen["value"], "basis": basis, "source_id": chosen["source_id"],
            "pdf_page": chosen["pdf_page"], "effective_from": chosen["effective_from"],
            "effective_to": chosen["effective_to"]}


COUNTS = "Counts toward the payment error rate"
NOT_COUNTED = "Does not count toward the payment error rate"


def impact_text(impact, date) -> str:
    status = impact["status"]
    if status == "clear":
        text = "No stop, so no error to measure."
        p = impact["proposed"]
        if p and p["eligible"] == 0:
            text += " On these facts the household is ineligible."
        elif p and p["eligible"] == 1:
            text += " On these facts the benefit is ${} a month.".format(p["monthly_benefit"])
        return text
    if status == "not_computed":
        return "Benefit impact not computed ({}). Not labelled.".format(impact["reason"])
    p = impact["proposed"]["monthly_benefit"]
    s = impact["supported"]["monthly_benefit"]
    t = impact["tolerance"]
    if t["basis"] == "in_force":
        tol = "the ${} quality-control tolerance in force on {}".format(t["value"], date)
    else:
        tol = ("the ${} quality-control tolerance, the last USDA has published (in force {} to "
               "{}; none is published for {})").format(t["value"], t["effective_from"],
                                                       t["effective_to"], date)
    kind = impact["class"]
    if kind == "no_benefit_impact":
        return "{}: no benefit impact (${} a month as proposed and as supported).".format(NOT_COUNTED, p)
    if kind == "payment_to_ineligible_household":
        return ("{}: ${} a month would be paid to a household that is ineligible on the supported "
                "facts. The tolerance does not apply to an ineligible household.").format(COUNTS, p)
    if kind == "wrongful_denial":
        return ("{}: a wrongful denial. As proposed the household is ineligible; on the supported "
                "facts it is eligible for ${} a month, and loses it. Denials are reviewed as "
                "negative cases, outside the payment error rate.").format(NOT_COUNTED, s)
    what = "an overpayment" if kind == "overpayment" else "an underpayment"
    amount = impact["error"] if impact["error"] > 0 else -impact["error"]
    if impact["counts_toward_payment_error_rate"]:
        return "{}: {} of ${} a month (${} as proposed, ${} as supported), above {}.".format(
            COUNTS, what, amount, p, s, tol)
    return "{}: {} of ${} a month (${} as proposed, ${} as supported), at or below {}.".format(
        NOT_COUNTED, what, amount, p, s, tol)


def measure(action, case, rb, table, findings, as_proposed, as_supported, size) -> dict:
    date = action["decision_date"]
    params = rb["parameters"]
    sizes = rb["scope"]["household_sizes"]
    given = {n["parameter"]: n["value"] for n in action.get("numbers", [])}

    proposed = None
    if size is not None and all(x is not None for x in as_proposed) \
            and all(name in given for name in params):
        proposed = looked_up(determination_inputs(
            action, case, as_proposed, {name: given[name] for name in params}), table)
    if not findings:
        return {"status": "clear", "proposed": proposed, "supported": None}

    impact = {"status": "not_computed", "reason": None, "proposed": proposed, "supported": None}
    if proposed is None:
        impact["reason"] = "proposed_inputs_incomplete"
        return impact
    supported_size = None
    for x in as_supported:
        if x is not None and x["name"] == "household_size":
            supported_size = x["value"]
    current = {name: version_on(params[name]["versions"], date) for name in params}
    if any(x is None for x in as_supported) or supported_size not in sizes \
            or any(v is None for v in current.values()):
        impact["reason"] = "supported_inputs_unavailable"
        return impact
    figures = {name: current[name]["values"][cell(params[name], supported_size)] for name in params}
    supported = looked_up(determination_inputs(action, case, as_supported, figures), table)
    impact["supported"] = supported
    if proposed["eligible"] is None or supported["eligible"] is None:
        impact["reason"] = "not_in_impact_table"
        return impact
    tolerance = tolerance_for(rb, date)
    if tolerance is None:
        impact["reason"] = "no_tolerance_published"
        return impact

    error = proposed["monthly_benefit"] - supported["monthly_benefit"]
    if error == 0:
        kind, counts = "no_benefit_impact", False
    elif proposed["eligible"] == 1 and supported["eligible"] == 0:
        kind, counts = "payment_to_ineligible_household", True
    elif proposed["eligible"] == 0 and supported["eligible"] == 1:
        kind, counts = "wrongful_denial", False
    elif error > 0:
        kind, counts = "overpayment", error > tolerance["value"]
    else:
        kind, counts = "underpayment", -error > tolerance["value"]
    return {"status": "computed", "proposed": proposed, "supported": supported, "error": error,
            "class": kind, "counts_toward_payment_error_rate": counts, "tolerance": tolerance}


# --- the whole decision, then the receipt ----------------------------------------------

def recompute_decision(action, case, rb, table) -> dict:
    check_inputs(action, case, rb, table)
    date = action["decision_date"]
    sizes = rb["scope"]["household_sizes"]
    findings, as_proposed, as_supported = [], [], []
    for name in FACTS:
        rule = rb["fact_rules"][name]
        entries = [(i, e) for i, e in enumerate(action.get("facts", [])) if e.get("name") == name]
        if not entries and rule["cardinality"] == "one":
            findings.append({"kind": "FACT_MISSING", "subject": name, "entry": -1})
            as_proposed.append(None)
            as_supported.append(None)
        for i, e in entries:
            finding, backed = examine(i, e, rule, case["documents"], date)
            if finding is None and name == "household_size" and e["value"] not in sizes:
                finding = {"kind": "OUT_OF_SCOPE", "subject": name, "entry": i,
                           "supplied_value": e["value"]}
                backed = None
            if finding is not None:
                if finding["kind"] in ("FACT_NOT_IN_SOURCE", "SUPERSEDED_DOCUMENT"):
                    finding["decision_date"] = date
                findings.append(finding)
            as_proposed.append({"name": name, "person": e["person"] if rule["person_bound"] else "",
                                "value": e["value"]})
            as_supported.append(None if backed is None else
                                {"name": name, "person": backed["person"], "value": backed["value"]})

    size = None
    for e in action.get("facts", []):
        if e.get("name") == "household_size":
            size = e["value"] if e["value"] in sizes else None
            break
    if size is not None:
        findings.extend(examine_numbers(action, rb, size))
    for f in findings:
        f["message"] = describe(f, rb)
    findings = sorted(findings, key=lambda f: (f["kind"], f["subject"], f["entry"]))

    impact = measure(action, case, rb, table, findings, as_proposed, as_supported, size)
    impact["message"] = impact_text(impact, date)
    return {"decision_date": date, "verdict": "STOP" if findings else "CLEAR",
            "findings": findings, "impact": impact}


def citation(rb, version, role) -> dict:
    source = rb["sources"][version["source_id"]]
    return {"source_id": version["source_id"], "title": source["title"], "url": source["url"],
            "pdf_page": version["pdf_page"], "sha256": source["sha256"],
            "effective_from": version["effective_from"], "effective_to": version["effective_to"],
            "role": role}


def rebuild(action: dict, case_bytes: bytes, rb_bytes: bytes, table_bytes: bytes) -> dict:
    rb, case, table = json.loads(rb_bytes), json.loads(case_bytes), json.loads(table_bytes)
    decision = recompute_decision(action, case, rb, table)
    date = action["decision_date"]

    figures = []
    size = next((e.get("value") for e in action.get("facts", [])
                 if e.get("name") == "household_size"), None)
    if size in rb["scope"]["household_sizes"]:
        for name, param in rb["parameters"].items():
            v = version_on(param["versions"], date)
            if v is not None:
                key = cell(param, size)
                entry = {"parameter": name, "key": key, "value": v["values"][key]}
                entry.update(citation(rb, v, "states the figure in force"))
                figures.append(entry)
    tolerance = None
    used = decision["impact"].get("tolerance")
    if used:
        v = [x for x in rb["qc_tolerance"]["versions"]
             if x["effective_from"] == used["effective_from"]][0]
        tolerance = {"value": used["value"], "basis": used["basis"]}
        tolerance.update(citation(rb, v, "states the tolerance"))

    body = {
        "receipt_schema": SCHEMA,
        "rulebook": rb["rulebook"],
        "rulebook_version": rb["rulebook_version"],
        "inputs": {"action": action, "case_file_sha256": digest(case_bytes),
                   "rulebook_sha256": digest(rb_bytes), "impact_table_sha256": digest(table_bytes)},
        "citations": {
            "authority": {"citation": rb["authority"]["citation"],
                          "role": "derivation only; states no dollar figure"},
            "figures": figures,
            "tolerance": tolerance,
            "impact_engine": {"name": table["engine"]["name"], "version": table["engine"]["version"],
                              "role": "computed the benefit amounts; not used to reach the verdict"},
        },
        "decision": decision,
    }
    body["receipt_sha256"] = digest(canonical_bytes(body))
    return body


def verify(receipt_path: Path, case_path: Path, rulebook_path: Path, impacts_path: Path) -> list:
    """Return a list of failed checks; empty means the receipt verifies."""
    problems = []
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    case_bytes, rb_bytes = case_path.read_bytes(), rulebook_path.read_bytes()
    table_bytes = impacts_path.read_bytes()
    recorded = receipt.get("inputs", {})

    if digest(case_bytes) != recorded.get("case_file_sha256"):
        problems.append("case file does not hash to the value recorded in the receipt")
    if digest(rb_bytes) != recorded.get("rulebook_sha256"):
        problems.append("rulebook does not hash to the value recorded in the receipt")
    if digest(table_bytes) != recorded.get("impact_table_sha256"):
        problems.append("impact table does not hash to the value recorded in the receipt")

    rb = json.loads(rb_bytes)
    root = rulebook_path.resolve().parent.parent
    for source in rb["sources"].values():
        if not source["file"]:
            continue
        src = root / source["file"]
        if src.exists() and digest(src.read_bytes()) != source["sha256"]:
            problems.append(f"source file {source['file']} does not match its recorded sha256")

    stated = dict(receipt)
    stated_hash = stated.pop("receipt_sha256", None)
    if digest(canonical_bytes(stated)) != stated_hash:
        problems.append("receipt_sha256 does not match the receipt's own contents")

    try:
        rebuilt = rebuild(receipt["inputs"]["action"], case_bytes, rb_bytes, table_bytes)
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        problems.append(f"could not recompute the receipt: {exc}")
        return problems
    if canonical_bytes(rebuilt) != canonical_bytes(receipt):
        theirs = receipt.get("decision", {})
        ours = rebuilt["decision"]
        if ours["verdict"] != theirs.get("verdict"):
            problems.append(f"verdict differs: recomputed {ours['verdict']}, "
                            f"receipt says {theirs.get('verdict')}")
        if ours["findings"] != theirs.get("findings"):
            problems.append("recomputed findings differ from the receipt's findings")
        if ours["impact"] != theirs.get("impact"):
            problems.append("recomputed impact and error-rate label differ from the receipt's")
        if rebuilt["citations"] != receipt.get("citations"):
            problems.append("recomputed citations differ from the receipt's citations")
        if not problems:
            problems.append("recomputed receipt is not byte-identical to the receipt")
    return problems


def say(text: str, indent: str = "") -> None:
    """Print one line; on a terminal, wrap it at spaces to the window width."""
    lines = [indent + text]
    if sys.stdout.isatty():
        columns = shutil.get_terminal_size(fallback=(100, 24)).columns
        wrapper = textwrap.TextWrapper(width=columns, initial_indent=indent,
                                       subsequent_indent=indent + "  ",
                                       break_long_words=False, break_on_hyphens=False)
        lines = wrapper.wrap(text)
    for line in lines:
        print(line)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify an asof-gate receipt by recomputing it.")
    ap.add_argument("receipt")
    ap.add_argument("--case", required=True)
    ap.add_argument("--rulebook", required=True)
    ap.add_argument("--impacts", required=True)
    a = ap.parse_args(argv)
    problems = verify(Path(a.receipt), Path(a.case), Path(a.rulebook), Path(a.impacts))
    receipt = json.loads(Path(a.receipt).read_text(encoding="utf-8"))
    if problems:
        say(f"FAIL {a.receipt}")
        for p in problems:
            say(f"- {p}", "  ")
        return 1
    d = receipt["decision"]
    label = d["impact"].get("counts_toward_payment_error_rate")
    counted = {True: "counts toward the payment error rate",
               False: "does not count toward the payment error rate"}.get(label, "no error-rate label")
    say(f"OK   {a.receipt}: recomputed independently, byte-identical. verdict {d['verdict']}, "
        f"{len(d['findings'])} finding(s), {counted}, receipt_sha256 "
        f"{receipt['receipt_sha256'][:16]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
