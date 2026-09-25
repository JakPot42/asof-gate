"""The gate: decide whether a proposed action may proceed. See docs/SPEC.md.

Two rules, nothing else. Every material fact must be entailed by the case file (a span that
contains the value, or a named rule that derives it), and every legal number must be the
version in force on the decision date. Any failure stops the action and says what is missing
or what is present but out of date.
"""

from __future__ import annotations

import re

_NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five",
                 "six", "seven", "eight", "nine", "ten"]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def appears_in(a: str, b: str) -> bool:
    return norm(a) in norm(b)


def _whole(token: str, text: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", text) is not None


def value_rendered_in(value: int, span: str) -> bool:
    text = norm(span)
    forms = {str(value), f"{value:,}"}
    if any(_whole(f, text) or _whole("$" + f, text) for f in forms):
        return True
    return 0 <= value <= 10 and _whole(_NUMBER_WORDS[value], text)


def _fact_reason(fact: dict, case_text: str, rulebook: dict) -> str | None:
    """None if the fact is entailed, otherwise the first failed condition (SPEC, Rule 1)."""
    value = fact.get("value")
    if fact.get("rule"):
        rule = rulebook["named_rules"].get(fact["rule"])
        if rule is None:
            return "unknown_rule"
        if not appears_in(rule["requires_text"], case_text):
            return "rule_text_absent"
        if rule["derives"] != {"fact": fact["name"], "value": value}:
            return "rule_derives_other_value"
        return None
    span = fact.get("span") or ""
    if not span.strip():
        return "no_span_and_no_rule"
    if not appears_in(span, case_text):
        return "span_not_in_case_file"
    if not value_rendered_in(value, span):
        return "span_does_not_contain_value"
    return None


def _in_force(version: dict, date: str) -> bool:
    return version["effective_from"] <= date <= version["effective_to"]


def _signed(n: int) -> str:
    return f"+{n}" if n >= 0 else str(n)


def _message(f: dict) -> str:
    k = f["kind"]
    if k == "FACT_MISSING":
        return f"{f['subject']}: not supplied. It is a material fact for this decision."
    if k == "FACT_NOT_ENTAILED":
        return (f"{f['subject']} = {f['supplied_value']}: not entailed ({f['reason']}). To clear, "
                "cite text in the case file that contains the value, or a named rule that "
                "derives it.")
    if k == "OUT_OF_SCOPE":
        return (f"{f['subject']} = {f['supplied_value']}: outside this rulebook's scope "
                f"({f['scope_value']}).")
    if k == "NUMBER_MISSING":
        return (f"{f['subject']}: not supplied. The decision needs the value in force on "
                f"{f['decision_date']}.")
    if k == "NUMBER_NO_VERSION_IN_FORCE":
        return f"{f['subject']}: no version in the rulebook is in force on {f['decision_date']}."
    if k == "UNKNOWN_PARAMETER":
        return f"{f['subject']}: not a parameter in this rulebook."
    if k == "NUMBER_NOT_IN_FORCE":
        d = f["decision_date"]
        if f["reason"] == "value_out_of_date":
            return (f"{f['subject']}: {f['supplied_value']} is present but not in force on {d}; "
                    f"it was in force {f['supplied_effective_from']} to "
                    f"{f['supplied_effective_to']}. In force on {d}: {f['in_force_value']} "
                    f"({f['in_force_source_id']}, PDF page {f['in_force_pdf_page']}). Delta "
                    f"{_signed(f['delta'])}. To clear, supply {f['in_force_value']} from a "
                    f"source in force on {d}.")
        if f["reason"] == "source_not_in_force":
            return (f"{f['subject']}: {f['supplied_value']} is the value in force on {d}, but the "
                    f"cited source {f['supplied_source_id']} is not the one in force. To clear, "
                    f"cite {f['in_force_source_id']}.")
        return (f"{f['subject']}: {f['supplied_value']} is not a value of this parameter in any "
                f"version. In force on {d}: {f['in_force_value']} ({f['in_force_source_id']}, "
                f"PDF page {f['in_force_pdf_page']}). Delta {_signed(f['delta'])}.")
    raise ValueError(f"unknown finding kind {k}")


def _check_facts(action: dict, case_text: str, rulebook: dict) -> list[dict]:
    out = []
    supplied = {f["name"]: f for f in action.get("facts", [])}
    for name in rulebook["material_facts"]:
        fact = supplied.get(name)
        if fact is None:
            out.append({"kind": "FACT_MISSING", "subject": name})
            continue
        reason = _fact_reason(fact, case_text, rulebook)
        if reason is not None:
            out.append({"kind": "FACT_NOT_ENTAILED", "subject": name,
                        "supplied_value": fact.get("value"), "reason": reason})
        elif name == "household_size" and fact["value"] != rulebook["scope"]["household_size"]:
            out.append({"kind": "OUT_OF_SCOPE", "subject": name, "supplied_value": fact["value"],
                        "scope_value": rulebook["scope"]["household_size"]})
    return out


def _check_numbers(action: dict, rulebook: dict) -> list[dict]:
    out = []
    date = action["decision_date"]
    params = rulebook["parameters"]
    supplied = {n["parameter"]: n for n in action.get("numbers", [])}
    for name in supplied:
        if name not in params:
            out.append({"kind": "UNKNOWN_PARAMETER", "subject": name})
    for name, param in params.items():
        num = supplied.get(name)
        if num is None:
            out.append({"kind": "NUMBER_MISSING", "subject": name, "decision_date": date})
            continue
        live = [v for v in param["versions"] if _in_force(v, date)]
        if not live:
            out.append({"kind": "NUMBER_NO_VERSION_IN_FORCE", "subject": name,
                        "decision_date": date})
            continue
        v = live[0]
        if num["value"] == v["value"] and num.get("source_id") == v["source"]["id"]:
            continue
        f = {"kind": "NUMBER_NOT_IN_FORCE", "subject": name, "decision_date": date,
             "supplied_value": num["value"], "supplied_source_id": num.get("source_id"),
             "in_force_value": v["value"], "in_force_source_id": v["source"]["id"],
             "in_force_pdf_page": v["source"]["pdf_page"], "delta": v["value"] - num["value"]}
        if num["value"] == v["value"]:
            f["reason"] = "source_not_in_force"
        else:
            older = [o for o in param["versions"] if o["value"] == num["value"]]
            if older:
                o = max(older, key=lambda x: x["effective_from"])
                f.update(reason="value_out_of_date", supplied_effective_from=o["effective_from"],
                         supplied_effective_to=o["effective_to"])
            else:
                f["reason"] = "value_unknown"
        out.append(f)
    return out


def _reject_floats(obj, where: str) -> None:
    if isinstance(obj, float):
        raise ValueError(f"floating-point value in {where}; integers only (SPEC, Inputs)")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _reject_floats(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _reject_floats(v, f"{where}[{i}]")


def decide(action: dict, case_text: str, rulebook: dict) -> dict:
    """Apply both rules and return the decision block of the receipt."""
    _reject_floats(action, "action")
    _reject_floats(rulebook, "rulebook")
    findings = _check_facts(action, case_text, rulebook) + _check_numbers(action, rulebook)
    for f in findings:
        f["message"] = _message(f)
    findings.sort(key=lambda f: (f["kind"], f["subject"]))
    return {"decision_date": action["decision_date"],
            "verdict": "STOP" if findings else "CLEAR",
            "findings": findings}
