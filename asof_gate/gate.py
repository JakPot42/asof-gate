"""The gate: decide whether a proposed determination may proceed. See docs/SPEC.md.

Three rules. Every fact must trace to the document in force that supports it, for the person
the document names (Rule 1). Every legal number must be the version in force on the decision
date (Rule 2). Every stop is labelled with what it would have cost and whether that counts
toward the State's payment error rate (Rule 3). Any failure of Rule 1 or 2 stops the action
and says what is unsupported, or what is present but out of date.
"""

from __future__ import annotations

import hashlib
import json

FACT_ORDER = ("household_size", "monthly_earned_income", "monthly_rent", "heating_or_cooling_cost")


# --- inputs ------------------------------------------------------------------------------

def _reject(obj, where: str, kinds: tuple) -> None:
    if isinstance(obj, kinds):
        raise ValueError(f"{type(obj).__name__} value in {where}; integers only (SPEC, Inputs)")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _reject(v, f"{where}.{k}", kinds)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _reject(v, f"{where}[{i}]", kinds)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validate(action: dict, case: dict, rulebook: dict) -> dict:
    """Reject malformed inputs (SPEC, "Invalid inputs") and return the documents by id."""
    docs = {}
    for d in case["documents"]:
        if not all(isinstance(d.get(k), str) for k in ("id", "type", "person", "payer", "date")):
            raise ValueError("case file: a document lacks id, type, person, payer or date")
        if d["id"] in docs:
            raise ValueError(f"case file: document id {d['id']} appears twice")
        docs[d["id"]] = d
    rules = rulebook["fact_rules"]
    for e in action.get("facts", []):
        rule = rules.get(e.get("name"))
        if rule is None:
            continue
        if not _is_int(e.get("value")):
            raise ValueError(f"action: {e['name']} has no integer value")
        cited = e.get("documents")
        if not isinstance(cited, list) or not all(isinstance(c, str) for c in cited):
            raise ValueError(f"action: {e['name']} documents must be a list of document ids")
        if rule["person_bound"] and not isinstance(e.get("person"), str):
            raise ValueError(f"action: {e['name']} must name a person")
    for name, rule in rules.items():
        n = sum(1 for e in action.get("facts", []) if e.get("name") == name)
        if rule["cardinality"] == "one" and n > 1:
            raise ValueError(f"action: {name} is supplied {n} times")
    for n in action.get("numbers", []):
        if not isinstance(n.get("parameter"), str) or not _is_int(n.get("value")):
            raise ValueError("action: a number lacks a parameter name or an integer value")
    return docs


# --- Rule 1: every fact traces to its document ------------------------------------------

def _series(doc: dict) -> tuple:
    return (doc["type"], doc["person"], doc["payer"])


def _in_force(doc: dict, docs: dict, date: str, count: int) -> list:
    """The `count` most recent documents of this document's series on the decision date."""
    same = [d for d in docs.values() if _series(d) == _series(doc) and d["date"] <= date]
    same.sort(key=lambda d: (d["date"], d["id"]), reverse=True)
    return sorted(same[:count], key=lambda d: (d["date"], d["id"]))


def _compute(rule: dict, used: list) -> int:
    values = [d[rule["field"]] for d in used]
    if rule["compute"] == "count":
        return len(values[0])
    if rule["compute"] == "field":
        return values[0]
    if rule["compute"] == "biweekly_to_monthly":
        return sum(values) * 215 // 200
    raise ValueError(f"unknown compute {rule['compute']}")


def _refs(used: list) -> list:
    return [{"id": d["id"], "date": d["date"]} for d in used]


def _check_entry(index: int, e: dict, rule: dict, docs: dict, date: str):
    """Return (finding or None, supported). `supported` is what the documents in force support
    for this entry: {"person", "value"}, or None when they support nothing definite."""
    cited = e["documents"]
    base = {"subject": e["name"], "entry": index, "supplied_value": e["value"],
            "documents": list(cited)}
    if rule["person_bound"]:
        base["person"] = e["person"]

    def stop(kind, **more):
        return {"kind": kind, **base, **more}

    def unsupported(reason):
        return stop("FACT_NOT_IN_SOURCE", reason=reason), None

    if not cited:
        return unsupported("no_document_cited")
    if len(set(cited)) != len(cited):
        return unsupported("duplicate_document")
    if any(c not in docs for c in cited):
        return unsupported("document_not_in_case_file")
    used = [docs[c] for c in cited]
    if any(d["type"] != rule["reads"] for d in used):
        return unsupported("wrong_document_type")
    if len({_series(d) for d in used}) != 1:
        return unsupported("mixed_series")

    live = _in_force(used[0], docs, date, rule["count"])
    owner = used[0]["person"]
    supported = None
    extra = {"in_force_documents": _refs(live), "supported_value": None}
    if len(live) == rule["count"]:
        supported = {"person": owner if rule["person_bound"] else "", "value": _compute(rule, live)}
        extra["supported_value"] = supported["value"]

    if rule["person_bound"] and owner != e["person"]:
        return stop("WRONG_PERSON", document_person=owner, **extra), supported
    if any(d["date"] > date for d in used):
        return stop("FACT_NOT_IN_SOURCE", reason="document_dated_after_decision", **extra), supported
    live_ids = {d["id"] for d in live}
    stale = sorted((d for d in used if d["id"] not in live_ids), key=lambda d: (d["date"], d["id"]))
    if stale:
        return stop("SUPERSEDED_DOCUMENT", superseded_documents=_refs(stale), **extra), supported
    if len(used) != rule["count"]:
        return stop("FACT_NOT_IN_SOURCE", reason="wrong_document_count", **extra), supported
    if e["value"] != supported["value"]:
        return stop("FACT_NOT_IN_SOURCE", reason="value_not_supported", **extra), supported
    return None, {"person": e["person"] if rule["person_bound"] else "", "value": e["value"]}


def _check_facts(action: dict, docs: dict, rulebook: dict):
    """Findings, plus the entries as proposed and as supported (None where unsupportable)."""
    date = action["decision_date"]
    findings, proposed, supported = [], [], []
    for name in FACT_ORDER:
        rule = rulebook["fact_rules"][name]
        mine = [(i, e) for i, e in enumerate(action.get("facts", [])) if e.get("name") == name]
        if rule["cardinality"] == "one" and not mine:
            findings.append({"kind": "FACT_MISSING", "subject": name, "entry": -1})
            proposed.append(None)
            supported.append(None)
        for i, e in mine:
            finding, sup = _check_entry(i, e, rule, docs, date)
            in_scope = name != "household_size" or \
                e["value"] in rulebook["scope"]["household_sizes"]
            if finding is None and not in_scope:
                finding = {"kind": "OUT_OF_SCOPE", "subject": name, "entry": i,
                           "supplied_value": e["value"]}
                sup = None
            if finding is not None:
                findings.append(finding)
            proposed.append({"name": name, "person": e["person"] if rule["person_bound"] else "",
                             "value": e["value"]})
            supported.append(None if sup is None else {"name": name, **sup})
    return findings, proposed, supported


# --- Rule 2: every legal number is the one in force --------------------------------------

def _live(versions: list, date: str):
    for v in versions:
        if v["effective_from"] <= date <= v["effective_to"]:
            return v
    return None


def _key(param: dict, size: int) -> str:
    return str(size) if param["by"] == "household_size" else "all"


def _check_numbers(action: dict, rulebook: dict, size: int) -> list:
    out = []
    date = action["decision_date"]
    params = rulebook["parameters"]
    supplied = {n["parameter"]: n for n in action.get("numbers", [])}
    for name in supplied:
        if name not in params:
            out.append({"kind": "UNKNOWN_PARAMETER", "subject": name, "entry": -1})
    for name, param in params.items():
        key = _key(param, size)
        num = supplied.get(name)
        if num is None:
            out.append({"kind": "NUMBER_MISSING", "subject": name, "entry": -1, "key": key,
                        "decision_date": date})
            continue
        v = _live(param["versions"], date)
        if v is None:
            out.append({"kind": "NUMBER_NO_VERSION_IN_FORCE", "subject": name, "entry": -1,
                        "key": key, "decision_date": date})
            continue
        right = v["values"][key]
        if num["value"] == right and num.get("source_id") == v["source_id"]:
            continue
        f = {"kind": "NUMBER_NOT_IN_FORCE", "subject": name, "entry": -1, "key": key,
             "decision_date": date, "supplied_value": num["value"],
             "supplied_source_id": num.get("source_id"), "in_force_value": right,
             "in_force_source_id": v["source_id"], "in_force_pdf_page": v["pdf_page"],
             "delta": right - num["value"]}
        if num["value"] == right:
            f["reason"] = "source_not_in_force"
        else:
            older = [o for o in param["versions"] if o["values"][key] == num["value"]]
            if older:
                o = max(older, key=lambda x: x["effective_from"])
                f.update(reason="value_out_of_date", supplied_effective_from=o["effective_from"],
                         supplied_effective_to=o["effective_to"])
            else:
                f["reason"] = "value_unknown"
        out.append(f)
    return out


# --- Rule 3: what the stop would have cost -----------------------------------------------

def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _inputs(action: dict, case: dict, facts: list, figures: dict) -> dict:
    one = {f["name"]: f["value"] for f in facts if f["name"] != "monthly_earned_income"}
    earned = sorted(({"person": f["person"], "monthly": f["value"]} for f in facts
                     if f["name"] == "monthly_earned_income"),
                    key=lambda x: (x["person"], x["monthly"]))
    members = sorted(({k: m[k] for k in ("id", "age", "k12_student", "weekly_hours")}
                      for m in case["household"]["members"]), key=lambda m: m["id"])
    return {"benefit_month": action["benefit_month"], "state": case["state"], "members": members,
            "household_size": one["household_size"], "earned_income": earned,
            "monthly_rent": one["monthly_rent"],
            "heating_or_cooling_cost": one["heating_or_cooling_cost"], "figures": figures}


def _side(inputs: dict, table: dict) -> dict:
    key = hashlib.sha256(canonical(inputs)).hexdigest()
    row = table["results"].get(key)
    return {"inputs": inputs, "inputs_sha256": key,
            "eligible": None if row is None else row["eligible"],
            "monthly_benefit": None if row is None else row["monthly_benefit"]}


def _tolerance(rulebook: dict, date: str):
    versions = rulebook["qc_tolerance"]["versions"]
    v, basis = _live(versions, date), "in_force"
    if v is None:
        earlier = [x for x in versions if x["effective_to"] < date]
        if not earlier:
            return None
        v, basis = max(earlier, key=lambda x: x["effective_to"]), "carried_forward"
    return {"value": v["value"], "basis": basis, "source_id": v["source_id"],
            "pdf_page": v["pdf_page"], "effective_from": v["effective_from"],
            "effective_to": v["effective_to"]}


def _impact(action, case, rulebook, table, findings, proposed, supported, size) -> dict:
    date = action["decision_date"]
    params = rulebook["parameters"]
    out = {"status": "not_computed", "reason": None, "proposed": None, "supported": None}

    supplied = {n["parameter"]: n["value"] for n in action.get("numbers", [])}
    if size is not None and None not in proposed and all(p in supplied for p in params):
        out["proposed"] = _side(_inputs(action, case, proposed, {p: supplied[p] for p in params}),
                                table)
    if not findings:
        out["status"] = "clear"
        del out["reason"]
        return out
    if out["proposed"] is None:
        out["reason"] = "proposed_inputs_incomplete"
        return out

    sup_size = next((s["value"] for s in supported if s and s["name"] == "household_size"), None)
    live = {p: _live(params[p]["versions"], date) for p in params}
    if None in supported or sup_size not in rulebook["scope"]["household_sizes"] \
            or None in live.values():
        out["reason"] = "supported_inputs_unavailable"
        return out
    figures = {p: live[p]["values"][_key(params[p], sup_size)] for p in params}
    out["supported"] = _side(_inputs(action, case, supported, figures), table)

    p, s = out["proposed"], out["supported"]
    if p["eligible"] is None or s["eligible"] is None:
        out["reason"] = "not_in_impact_table"
        return out
    tol = _tolerance(rulebook, date)
    if tol is None:
        out["reason"] = "no_tolerance_published"
        return out

    error = p["monthly_benefit"] - s["monthly_benefit"]
    if error == 0:
        kind, counts = "no_benefit_impact", False
    elif p["eligible"] == 1 and s["eligible"] == 0:
        kind, counts = "payment_to_ineligible_household", True
    elif p["eligible"] == 0 and s["eligible"] == 1:
        kind, counts = "wrongful_denial", False
    else:
        kind, counts = ("overpayment" if error > 0 else "underpayment"), abs(error) > tol["value"]
    del out["reason"]
    out.update(status="computed", error=error, tolerance=tol,
               counts_toward_payment_error_rate=counts)
    out["class"] = kind
    return out


# --- messages ----------------------------------------------------------------------------

def _signed(n: int) -> str:
    return f"+{n}" if n >= 0 else str(n)


def _ids(refs) -> str:
    return ", ".join(r if isinstance(r, str) else r["id"] for r in refs)


def _dated(refs) -> str:
    return ", ".join(f"{r['id']} ({r['date']})" for r in refs)


def _who(f: dict) -> str:
    who = f"{f['subject']} for {f['person']}" if "person" in f else f["subject"]
    return f"{who} = {f['supplied_value']}"


def _message(f: dict, rulebook: dict) -> str:
    k = f["kind"]
    if k == "FACT_MISSING":
        return f"{f['subject']}: not supplied. It is a material fact for this decision."
    if k == "OUT_OF_SCOPE":
        return f"{_who(f)}: outside the household sizes this rulebook covers."
    if k == "FACT_NOT_IN_SOURCE":
        rule = rulebook["fact_rules"][f["subject"]]
        r = f["reason"]
        if r == "no_document_cited":
            why = f"no document is cited. To clear, cite the {rule['reads']} it comes from."
        elif r == "duplicate_document":
            why = "the same document is cited twice."
        elif r == "document_not_in_case_file":
            why = f"a cited document ({_ids(f['documents'])}) is not in the case file."
        elif r == "wrong_document_type":
            why = f"this fact is read from a {rule['reads']}, and a cited document is not one."
        elif r == "mixed_series":
            why = "the cited documents are not all for the same person and payer."
        elif r == "document_dated_after_decision":
            why = f"a cited document is dated after the decision date {f['decision_date']}."
        elif r == "wrong_document_count":
            why = (f"this fact is read from {rule['count']} {rule['reads']} document(s), and "
                   f"{len(f['documents'])} cited. In force: {_dated(f['in_force_documents'])}.")
        else:
            why = (f"not supported by {_ids(f['documents'])}, which give {f['supported_value']}. "
                   f"To clear, supply {f['supported_value']}.")
        return f"{_who(f)}: {why}"
    if k == "WRONG_PERSON":
        m = (f"{_who(f)}: {_ids(f['documents'])} name {f['document_person']}, not "
             f"{f['person']}.")
        if f["supported_value"] is not None:
            m += (f" The documents in force support {f['subject']} for {f['document_person']} "
                  f"of {f['supported_value']}.")
        return m
    if k == "SUPERSEDED_DOCUMENT":
        m = (f"{_who(f)}: present but superseded on {f['decision_date']}: "
             f"{_dated(f['superseded_documents'])}. In force: {_dated(f['in_force_documents'])}.")
        if f["supported_value"] is not None:
            m += f" Those give {f['supported_value']}. To clear, supply {f['supported_value']} from them."
        return m
    subject = f"{f['subject']}[{f['key']}]" if "key" in f else f["subject"]
    if k == "NUMBER_MISSING":
        return (f"{subject}: not supplied. The decision needs the value in force on "
                f"{f['decision_date']}.")
    if k == "NUMBER_NO_VERSION_IN_FORCE":
        return f"{subject}: no version in the rulebook is in force on {f['decision_date']}."
    if k == "UNKNOWN_PARAMETER":
        return f"{subject}: not a parameter in this rulebook."
    if k == "NUMBER_NOT_IN_FORCE":
        d = f["decision_date"]
        if f["reason"] == "value_out_of_date":
            return (f"{subject}: {f['supplied_value']} is present but not in force on {d}; "
                    f"it was in force {f['supplied_effective_from']} to "
                    f"{f['supplied_effective_to']}. In force on {d}: {f['in_force_value']} "
                    f"({f['in_force_source_id']}, PDF page {f['in_force_pdf_page']}). Delta "
                    f"{_signed(f['delta'])}. To clear, supply {f['in_force_value']} from a "
                    f"source in force on {d}.")
        if f["reason"] == "source_not_in_force":
            return (f"{subject}: {f['supplied_value']} is the value in force on {d}, but the "
                    f"cited source {f['supplied_source_id']} is not the one in force. To clear, "
                    f"cite {f['in_force_source_id']}.")
        return (f"{subject}: {f['supplied_value']} is not a value of this parameter in any "
                f"version. In force on {d}: {f['in_force_value']} ({f['in_force_source_id']}, "
                f"PDF page {f['in_force_pdf_page']}). Delta {_signed(f['delta'])}.")
    raise ValueError(f"unknown finding kind {k}")


def _impact_message(i: dict, date: str) -> str:
    if i["status"] == "clear":
        p = i["proposed"]
        if p is None or p["eligible"] is None:
            return "No stop, so no error to measure."
        if p["eligible"] == 0:
            return "No stop, so no error to measure. On these facts the household is ineligible."
        return (f"No stop, so no error to measure. On these facts the benefit is "
                f"${p['monthly_benefit']} a month.")
    if i["status"] == "not_computed":
        return f"Benefit impact not computed ({i['reason']}). Not labelled."
    p, s, t = i["proposed"]["monthly_benefit"], i["supported"]["monthly_benefit"], i["tolerance"]
    if t["basis"] == "in_force":
        tol = f"the ${t['value']} quality-control tolerance in force on {date}"
    else:
        tol = (f"the ${t['value']} quality-control tolerance, the last USDA has published (in "
               f"force {t['effective_from']} to {t['effective_to']}; none is published for {date})")
    yes, no = ("Counts toward the payment error rate", "Does not count toward the payment error rate")
    c = i["class"]
    if c == "no_benefit_impact":
        return f"{no}: no benefit impact (${p} a month as proposed and as supported)."
    if c == "payment_to_ineligible_household":
        return (f"{yes}: ${p} a month would be paid to a household that is ineligible on the "
                "supported facts. The tolerance does not apply to an ineligible household.")
    if c == "wrongful_denial":
        return (f"{no}: a wrongful denial. As proposed the household is ineligible; on the "
                f"supported facts it is eligible for ${s} a month, and loses it. Denials are "
                "reviewed as negative cases, outside the payment error rate.")
    amount = abs(i["error"])
    article = "an overpayment" if c == "overpayment" else "an underpayment"
    if i["counts_toward_payment_error_rate"]:
        return (f"{yes}: {article} of ${amount} a month (${p} as proposed, ${s} as supported), "
                f"above {tol}.")
    return (f"{no}: {article} of ${amount} a month (${p} as proposed, ${s} as supported), at or "
            f"below {tol}.")


# --- decide ------------------------------------------------------------------------------

def decide(action: dict, case: dict, rulebook: dict, impacts: dict) -> dict:
    """Apply the three rules and return the decision block of the receipt."""
    _reject(action, "action", (float, bool))
    for obj, where in ((case, "case file"), (rulebook, "rulebook"), (impacts, "impact table")):
        _reject(obj, where, (float,))
    docs = _validate(action, case, rulebook)
    date = action["decision_date"]

    findings, proposed, supported = _check_facts(action, docs, rulebook)
    sizes = [e["value"] for e in action.get("facts", []) if e.get("name") == "household_size"]
    size = sizes[0] if sizes and sizes[0] in rulebook["scope"]["household_sizes"] else None
    if size is not None:
        findings += _check_numbers(action, rulebook, size)
    for f in findings:
        if f["kind"] in ("FACT_NOT_IN_SOURCE", "SUPERSEDED_DOCUMENT"):
            f["decision_date"] = date
        f["message"] = _message(f, rulebook)
    findings.sort(key=lambda f: (f["kind"], f["subject"], f["entry"]))

    impact = _impact(action, case, rulebook, impacts, findings, proposed, supported, size)
    impact["message"] = _impact_message(impact, date)
    return {"decision_date": date, "verdict": "STOP" if findings else "CLEAR",
            "findings": findings, "impact": impact}
