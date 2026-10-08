"""The tax path: check the fields proposed for a Form 1040 before they enter the return.

See docs/SPEC.md, "Tax path". The same three questions as the SNAP path. Does every field
trace to the named person's own document, as it stands after any correction (Rule T1)? Is
every legal figure the one in force on the decision date (Rule T2)? What would the error have
done to the tax on the return (Rule T3)?
"""

from __future__ import annotations

import hashlib
import json

SCHEMA = "asof-gate/tax-receipt/1"


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- inputs ------------------------------------------------------------------------------

def _reject(obj, where: str, kinds: tuple) -> None:
    if isinstance(obj, kinds):
        raise ValueError(f"{type(obj).__name__} value in {where}; integers only (SPEC, Tax path)")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _reject(v, f"{where}.{k}", kinds)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _reject(v, f"{where}[{i}]", kinds)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validate(action: dict, case: dict, rulebook: dict) -> dict:
    """Reject malformed inputs (SPEC, Tax path, "Invalid inputs"); return the documents by id."""
    if not isinstance(case.get("filing_status"), str) or not _is_int(case.get("tax_year")):
        raise ValueError("case file: filing_status must be a string and tax_year an integer")
    people = set()
    for p in case["persons"]:
        if not isinstance(p.get("id"), str) or not _is_int(p.get("age")) or p["id"] in people:
            raise ValueError("case file: each person needs a unique id and an integer age")
        people.add(p["id"])
    rule = rulebook["field_rules"]["wages"]
    docs = {}
    for d in case["documents"]:
        if not all(isinstance(d.get(k), str) for k in ("id", "type", "person", "payer", "date")):
            raise ValueError("case file: a document lacks id, type, person, payer or date")
        if d["id"] in docs:
            raise ValueError(f"case file: document id {d['id']} appears twice")
        if d["person"] not in people:
            raise ValueError(f"case file: document {d['id']} names a person not on the return")
        docs[d["id"]] = d
    for d in docs.values():
        if d["type"] == rule["reads"] and not _is_int(d.get(rule["field"])):
            raise ValueError(f"case file: {d['id']} has no integer {rule['field']}")
        if d["type"] == rule["corrected_by"]:
            base = docs.get(d.get("corrects"))
            if base is None or base["type"] != rule["reads"] or \
                    (base["person"], base["payer"]) != (d["person"], d["payer"]) or \
                    d["date"] < base["date"]:
                raise ValueError(f"case file: {d['id']} does not correct an earlier "
                                 f"{rule['reads']} of the same person and payer")
            fixed = d.get("correct_information")
            if not isinstance(fixed, dict) or not all(_is_int(v) for v in fixed.values()):
                raise ValueError(f"case file: {d['id']} correct_information must hold integers")
    if not _is_int(action.get("tax_year")) or action["tax_year"] != case["tax_year"]:
        raise ValueError("action: tax_year is not the case file's tax year")
    if not isinstance(action.get("decision_date"), str):
        raise ValueError("action: decision_date must be a string")
    for e in action.get("fields", []):
        if e.get("name") not in rulebook["field_rules"]:
            continue
        if not _is_int(e.get("value")) or not isinstance(e.get("person"), str) \
                or not isinstance(e.get("document"), str):
            raise ValueError(f"action: {e['name']} needs an integer value, a person and a document")
    for n in action.get("numbers", []):
        if not isinstance(n.get("parameter"), str) or not _is_int(n.get("value")):
            raise ValueError("action: a number lacks a parameter name or an integer value")
    return docs


# --- Rule T1: every field traces to the document in force --------------------------------

def _ref(doc: dict) -> dict:
    return {"id": doc["id"], "date": doc["date"]}


def _in_force(base: dict, docs: dict, rule: dict, date: str):
    """The document whose value of the field stands for this form on the decision date, and
    that value: the latest correction stating the field, or the form itself if there is none.
    A form dated after the decision date is not in the file yet."""
    if base["date"] > date:
        return None
    fixes = [d for d in docs.values()
             if d["type"] == rule["corrected_by"] and d["corrects"] == base["id"]
             and rule["field"] in d["correct_information"] and d["date"] <= date]
    if fixes:
        last = max(fixes, key=lambda d: (d["date"], d["id"]))
        return last, last["correct_information"][rule["field"]]
    return base, base[rule["field"]]


def _forms_of(person: str, docs: dict, rule: dict, date: str) -> list:
    """What is in force for each of the person's forms, in (date, id) order of the form."""
    out = []
    forms = sorted((d for d in docs.values() if d["type"] == rule["reads"] and d["person"] == person),
                   key=lambda d: (d["date"], d["id"]))
    for form in forms:
        live = _in_force(form, docs, rule, date)
        if live is not None:
            out.append({"document": form["id"], "in_force_document": live[0]["id"],
                        "in_force_date": live[0]["date"], "value": live[1]})
    return out


def _check_fields(action: dict, case: dict, docs: dict, rulebook: dict) -> list:
    date = action["decision_date"]
    rule = rulebook["field_rules"]["wages"]
    findings, read = [], set()
    for i, e in enumerate(action.get("fields", [])):
        if e.get("name") != "wages":
            continue
        base_fields = {"subject": "wages", "entry": i, "person": e["person"],
                       "supplied_value": e["value"], "document": e["document"]}

        def stop(kind, **more):
            findings.append({"kind": kind, **base_fields, **more})

        cited = docs.get(e["document"])
        if e["document"] == "":
            stop("FACT_NOT_IN_SOURCE", reason="no_document_cited", decision_date=date)
            continue
        if cited is None:
            stop("FACT_NOT_IN_SOURCE", reason="document_not_in_case_file", decision_date=date)
            continue
        if cited["type"] not in (rule["reads"], rule["corrected_by"]):
            stop("FACT_NOT_IN_SOURCE", reason="wrong_document_type", decision_date=date)
            continue
        form = cited if cited["type"] == rule["reads"] else docs[cited["corrects"]]
        if form["person"] != e["person"]:
            own = _forms_of(e["person"], docs, rule, date)
            stop("WRONG_PERSON", document_person=form["person"], person_in_force=own,
                 supported_value=sum(x["value"] for x in own) if own else None)
            continue
        already = form["id"] in read
        read.add(form["id"])
        if cited["date"] > date:
            stop("FACT_NOT_IN_SOURCE", reason="document_dated_after_decision", decision_date=date)
            continue
        live, value = _in_force(form, docs, rule, date)
        if cited is not form and rule["field"] not in cited["correct_information"]:
            stop("FACT_NOT_IN_SOURCE", reason="field_not_in_document", decision_date=date,
                 in_force_document=_ref(live), supported_value=value)
        elif live["id"] != cited["id"]:
            stop("SUPERSEDED_DOCUMENT", decision_date=date, superseded_document=_ref(cited),
                 in_force_document=_ref(live), in_force_value=value, delta=value - e["value"])
        elif already:
            stop("FACT_NOT_IN_SOURCE", reason="document_already_used", decision_date=date,
                 form=form["id"])
        elif e["value"] != value:
            stop("FACT_NOT_IN_SOURCE", reason="value_not_supported", decision_date=date,
                 supported_value=value)

    for person in sorted(p["id"] for p in case["persons"]):
        for item in _forms_of(person, docs, rule, date):
            if item["document"] not in read:
                findings.append({"kind": "FACT_MISSING", "subject": "wages", "entry": -1,
                                 "person": person, "document": item["document"],
                                 "in_force_document": {"id": item["in_force_document"],
                                                       "date": item["in_force_date"]},
                                 "in_force_value": item["value"]})
    return findings


# --- Rule T2: every legal figure is the one in force -------------------------------------

def _live(versions: list, date: str):
    for v in versions:
        if v["effective_from"] <= date <= v["effective_to"]:
            return v
    return None


def _where(v: dict) -> dict:
    return {"source_id": v["source_id"], "section": v["section"], "pdf_page": v["pdf_page"]}


def _check_numbers(action: dict, rulebook: dict, key: str) -> list:
    out = []
    date = action["decision_date"]
    params = rulebook["parameters"]
    supplied = {n["parameter"]: n for n in action.get("numbers", [])}
    for name in supplied:
        if name not in params:
            out.append({"kind": "UNKNOWN_PARAMETER", "subject": name, "entry": -1, "document": ""})
    for name, param in params.items():
        common = {"subject": name, "entry": -1, "document": "", "key": key, "decision_date": date}
        num = supplied.get(name)
        if num is None:
            out.append({"kind": "NUMBER_MISSING", **common})
            continue
        v = _live(param["versions"], date)
        if v is None:
            out.append({"kind": "NUMBER_NO_VERSION_IN_FORCE", **common})
            continue
        right = v["values"][key]
        if num["value"] == right and num.get("source_id") == v["source_id"]:
            continue
        f = {"kind": "NUMBER_NOT_IN_FORCE", **common, "supplied_value": num["value"],
             "supplied_source_id": num.get("source_id"), "in_force_value": right,
             "in_force": _where(v),
             "enacted_by": None if v["enacted_by"] is None else _where(v["enacted_by"]),
             "delta": right - num["value"]}
        if num["value"] == right:
            f["reason"] = "source_not_in_force"
        else:
            older = [o for o in param["versions"] if o["values"][key] == num["value"]]
            if older:
                o = max(older, key=lambda x: x["effective_from"])
                f.update(reason="value_out_of_date", supplied_effective_from=o["effective_from"],
                         supplied_effective_to=o["effective_to"], superseded=_where(o))
            else:
                f["reason"] = "value_unknown"
        out.append(f)
    return out


# --- Rule T3: what the stop would have done to the tax ------------------------------------

def _return_inputs(action: dict, case: dict, wages: dict, figures: dict) -> dict:
    people = sorted(({"id": p["id"], "age": p["age"]} for p in case["persons"]),
                    key=lambda p: p["id"])
    return {"tax_year": action["tax_year"], "filing_status": case["filing_status"],
            "persons": people,
            "wages": [{"person": p["id"], "amount": wages.get(p["id"], 0)} for p in people],
            "figures": figures}


def _side(inputs: dict, table: dict) -> dict:
    key = sha256_hex(canonical(inputs))
    row = table["results"].get(key)
    return {"inputs": inputs, "inputs_sha256": key,
            "income_tax": None if row is None else row["income_tax"]}


def _impact(action, case, docs, rulebook, table, findings, in_scope: bool) -> dict:
    date = action["decision_date"]
    params = rulebook["parameters"]
    rule = rulebook["field_rules"]["wages"]
    people = {p["id"] for p in case["persons"]}
    out = {"status": "not_computed", "reason": None, "proposed": None, "supported": None}

    entries = [e for e in action.get("fields", []) if e.get("name") == "wages"]
    supplied = {n["parameter"]: n["value"] for n in action.get("numbers", [])}
    if in_scope and all(e["person"] in people for e in entries) and all(p in supplied for p in params):
        wages = {}
        for e in entries:
            wages[e["person"]] = wages.get(e["person"], 0) + e["value"]
        out["proposed"] = _side(_return_inputs(action, case, wages, {p: supplied[p] for p in params}),
                                table)
    if not findings:
        out["status"] = "clear"
        del out["reason"]
        return out
    if out["proposed"] is None:
        out["reason"] = "proposed_inputs_incomplete"
        return out

    live = {p: _live(params[p]["versions"], date) for p in params}
    if not in_scope or None in live.values():
        out["reason"] = "supported_inputs_unavailable"
        return out
    wages = {p: sum(x["value"] for x in _forms_of(p, docs, rule, date)) for p in people}
    figures = {p: live[p]["values"][case["filing_status"]] for p in params}
    out["supported"] = _side(_return_inputs(action, case, wages, figures), table)

    p, s = out["proposed"]["income_tax"], out["supported"]["income_tax"]
    if p is None or s is None:
        out["reason"] = "not_in_impact_table"
        return out
    del out["reason"]
    error = p - s
    out.update(status="computed", error=error)
    out["class"] = "no_tax_impact" if error == 0 else \
        "tax_overstated" if error > 0 else "tax_understated"
    return out


# --- messages ----------------------------------------------------------------------------

def _signed(n: int) -> str:
    return f"+{n}" if n >= 0 else str(n)


def _cite(w: dict) -> str:
    return f"{w['source_id']}, {w['section']}, PDF page {w['pdf_page']}"


def _message(f: dict, rulebook: dict) -> str:
    k = f["kind"]
    rule = rulebook["field_rules"]["wages"]
    if k in ("FACT_NOT_IN_SOURCE", "WRONG_PERSON", "SUPERSEDED_DOCUMENT"):
        who = f"{f['subject']} for {f['person']} = {f['supplied_value']}"
    if k == "FACT_MISSING":
        return (f"{f['subject']} for {f['person']}: {f['document']} is in the case file and no "
                f"entry reads it. In force: {f['in_force_value']} ({f['in_force_document']['id']}).")
    if k == "OUT_OF_SCOPE":
        return f"filing_status = {f['supplied_value']}: outside the filing statuses this rulebook covers."
    if k == "FACT_NOT_IN_SOURCE":
        r = f["reason"]
        if r == "no_document_cited":
            why = f"no document is cited. To clear, cite the {rule['label']} it comes from."
        elif r == "document_not_in_case_file":
            why = f"the cited document ({f['document']}) is not in the case file."
        elif r == "wrong_document_type":
            why = (f"this field is read from a {rule['label']} or a correction of one, and "
                   f"{f['document']} is neither.")
        elif r == "document_dated_after_decision":
            why = f"{f['document']} is dated after the decision date {f['decision_date']}."
        elif r == "field_not_in_document":
            why = (f"{f['document']} does not state {rule['box']}. In force: "
                   f"{f['supported_value']} ({f['in_force_document']['id']}).")
        elif r == "document_already_used":
            why = f"{f['form']} is already read by an earlier entry."
        else:
            why = (f"not supported by {f['document']}, which gives {f['supported_value']}. "
                   f"To clear, supply {f['supported_value']}.")
        return f"{who}: {why}"
    if k == "WRONG_PERSON":
        m = (f"{who}: present in a source, but not in {f['person']}'s document: "
             f"{f['document']} names {f['document_person']}.")
        if not f["person_in_force"]:
            return m + f" The case file has no {rule['label']} for {f['person']}."
        parts = []
        for x in f["person_in_force"]:
            if x["in_force_document"] == x["document"]:
                parts.append(f"{x['value']} ({x['document']})")
            else:
                parts.append(f"{x['value']} ({x['in_force_document']}, correcting {x['document']})")
        return m + f" In force for {f['person']}: {'; '.join(parts)}."
    if k == "SUPERSEDED_DOCUMENT":
        old, new = f["superseded_document"], f["in_force_document"]
        return (f"{who}: {rule['box']} of {old['id']} ({old['date']}) is present but superseded "
                f"on {f['decision_date']}, corrected by {new['id']} ({new['date']}). In force: "
                f"{f['in_force_value']} ({new['id']}). Delta {_signed(f['delta'])}. To clear, "
                f"supply {f['in_force_value']} from {new['id']}.")
    subject = f"{f['subject']}[{f['key']}]" if "key" in f else f["subject"]
    if k == "NUMBER_MISSING":
        return (f"{subject}: not supplied. The return needs the value in force on "
                f"{f['decision_date']}.")
    if k == "NUMBER_NO_VERSION_IN_FORCE":
        return f"{subject}: no version in the rulebook is in force on {f['decision_date']}."
    if k == "UNKNOWN_PARAMETER":
        return f"{subject}: not a parameter in this rulebook."
    if k == "NUMBER_NOT_IN_FORCE":
        d = f["decision_date"]
        now = f"{f['in_force_value']} ({_cite(f['in_force'])})"
        if f["enacted_by"] is not None:
            now += f", enacted by {_cite(f['enacted_by'])}"
        if f["reason"] == "value_out_of_date":
            return (f"{subject}: {f['supplied_value']} is present but not in force on {d}; it was "
                    f"in force {f['supplied_effective_from']} to {f['supplied_effective_to']} "
                    f"({_cite(f['superseded'])}). In force on {d}: {now}. Delta "
                    f"{_signed(f['delta'])}. To clear, supply {f['in_force_value']} from a source "
                    f"in force on {d}.")
        if f["reason"] == "source_not_in_force":
            return (f"{subject}: {f['supplied_value']} is the value in force on {d}, but the "
                    f"cited source {f['supplied_source_id']} is not the one in force. To clear, "
                    f"cite {f['in_force']['source_id']}.")
        return (f"{subject}: {f['supplied_value']} is not a value of this parameter in any "
                f"version. In force on {d}: {now}. Delta {_signed(f['delta'])}.")
    raise ValueError(f"unknown finding kind {k}")


def _impact_message(i: dict) -> str:
    if i["status"] == "clear":
        p = i["proposed"]
        if p is None or p["income_tax"] is None:
            return "No stop, so no error to measure."
        return (f"No stop, so no error to measure. On these fields the federal income tax is "
                f"${p['income_tax']}.")
    if i["status"] == "not_computed":
        return f"Tax impact not computed ({i['reason']})."
    p, s = i["proposed"]["income_tax"], i["supported"]["income_tax"]
    if i["class"] == "no_tax_impact":
        return (f"No tax impact: federal income tax of ${p} as proposed and on the documents and "
                "figures in force.")
    way = "overstated" if i["class"] == "tax_overstated" else "understated"
    return (f"Federal income tax would be {way} by ${abs(i['error'])}: ${p} as proposed, ${s} on "
            "the documents and figures in force.")


# --- decide and receipt ------------------------------------------------------------------

def decide(action: dict, case: dict, rulebook: dict, impacts: dict) -> dict:
    """Apply the three tax rules and return the decision block of the receipt."""
    _reject(action, "action", (float, bool))
    for obj, where in ((case, "case file"), (rulebook, "rulebook"), (impacts, "impact table")):
        _reject(obj, where, (float,))
    docs = _validate(action, case, rulebook)
    date = action["decision_date"]
    status = case["filing_status"]
    in_scope = status in rulebook["scope"]["filing_statuses"]

    findings = _check_fields(action, case, docs, rulebook)
    if in_scope:
        findings += _check_numbers(action, rulebook, status)
    else:
        findings.append({"kind": "OUT_OF_SCOPE", "subject": "filing_status", "entry": -1,
                         "document": "", "supplied_value": status})
    for f in findings:
        f["message"] = _message(f, rulebook)
    findings.sort(key=lambda f: (f["kind"], f["subject"], f["entry"], f["document"]))

    impact = _impact(action, case, docs, rulebook, impacts, findings, in_scope)
    impact["message"] = _impact_message(impact)
    return {"decision_date": date, "verdict": "STOP" if findings else "CLEAR",
            "findings": findings, "impact": impact}


def _source(rulebook: dict, where: dict) -> dict:
    s = rulebook["sources"][where["source_id"]]
    return {"source_id": where["source_id"], "title": s["title"], "url": s["url"],
            "section": where["section"], "pdf_page": where["pdf_page"], "sha256": s["sha256"]}


def _citations(action: dict, case: dict, rulebook: dict, impacts: dict, decision: dict) -> dict:
    date, status = action["decision_date"], case["filing_status"]
    figures, superseded = [], []
    if status in rulebook["scope"]["filing_statuses"]:
        for name, param in rulebook["parameters"].items():
            v = _live(param["versions"], date)
            if v is None:
                continue
            enacted = v["enacted_by"]
            figures.append({
                "parameter": name, "key": status, "value": v["values"][status],
                **_source(rulebook, v), "effective_from": v["effective_from"],
                "effective_to": v["effective_to"], "role": "states the figure in force",
                "enacted_by": None if enacted is None else
                {**_source(rulebook, enacted), "states": enacted["states"]}})
            for f in decision["findings"]:
                if f["kind"] == "NUMBER_NOT_IN_FORCE" and f["subject"] == name \
                        and f["reason"] == "value_out_of_date":
                    o = next(x for x in param["versions"]
                             if x["effective_from"] == f["supplied_effective_from"])
                    superseded.append({
                        "parameter": name, "key": status, "value": o["values"][status],
                        **_source(rulebook, o), "effective_from": o["effective_from"],
                        "effective_to": o["effective_to"],
                        "role": "stated the figure before it was changed"})
    return {
        "authority": {"citation": rulebook["authority"]["citation"],
                      "role": "the statute that sets the amounts; each figure is cited to the "
                              "page that prints it"},
        "figures": figures,
        "superseded_figures": superseded,
        "impact_engine": {"name": impacts["engine"]["name"], "version": impacts["engine"]["version"],
                          "role": "computed the tax amounts; not used to reach the verdict"},
    }


def build_receipt(action: dict, case_bytes: bytes, rulebook_bytes: bytes,
                  impacts_bytes: bytes) -> dict:
    rulebook, impacts, case = (json.loads(b) for b in (rulebook_bytes, impacts_bytes, case_bytes))
    decision = decide(action, case, rulebook, impacts)
    body = {
        "receipt_schema": SCHEMA,
        "rulebook": rulebook["rulebook"],
        "rulebook_version": rulebook["rulebook_version"],
        "inputs": {
            "action": action,
            "case_file_sha256": sha256_hex(case_bytes),
            "rulebook_sha256": sha256_hex(rulebook_bytes),
            "impact_table_sha256": sha256_hex(impacts_bytes),
        },
        "citations": _citations(action, case, rulebook, impacts, decision),
        "decision": decision,
    }
    body["receipt_sha256"] = sha256_hex(canonical(body))
    return body
