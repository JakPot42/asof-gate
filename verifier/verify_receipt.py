"""Independent verifier for asof-gate receipts.

    python verifier/verify_receipt.py RECEIPT --case CASE_FILE --rulebook RULEBOOK

Written from docs/SPEC.md, not from the gate. It imports nothing from `asof_gate` and uses
only the Python standard library, so it can be read, audited and run on its own. It rebuilds
the entire receipt from the inputs and accepts it only if every byte of the canonical form
matches. Exit 0 on a match, 1 otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


# --- text matching (SPEC, "Text matching") ---------------------------------------------

def normalise(text: str) -> str:
    return " ".join(text.lower().split())


def contains(haystack: str, needle: str) -> bool:
    return normalise(needle) in normalise(haystack)


def has_whole(text: str, token: str) -> bool:
    start = 0
    while True:
        i = text.find(token, start)
        if i < 0:
            return False
        before = text[i - 1] if i > 0 else ""
        after = text[i + len(token)] if i + len(token) < len(text) else ""
        if not (before.isalnum() and before.isascii()) and not (after.isalnum() and after.isascii()):
            return True
        start = i + 1


def renders(value: int, span: str) -> bool:
    text = normalise(span)
    for form in (str(value), f"{value:,}"):
        if has_whole(text, form) or has_whole(text, "$" + form):
            return True
    return 0 <= value <= 10 and has_whole(text, WORDS[value])


# --- the two rules ---------------------------------------------------------------------

def entailment_failure(fact: dict, case_text: str, named_rules: dict):
    if fact.get("rule"):
        rule = named_rules.get(fact["rule"])
        if rule is None:
            return "unknown_rule"
        if not contains(case_text, rule["requires_text"]):
            return "rule_text_absent"
        if rule["derives"].get("fact") != fact["name"] or rule["derives"].get("value") != fact.get("value"):
            return "rule_derives_other_value"
        return None
    span = fact.get("span") or ""
    if span.strip() == "":
        return "no_span_and_no_rule"
    if not contains(case_text, span):
        return "span_not_in_case_file"
    if not renders(fact.get("value"), span):
        return "span_does_not_contain_value"
    return None


def signed(n: int) -> str:
    return ("+" if n >= 0 else "") + str(n)


TEMPLATES = {
    "FACT_MISSING": "{subject}: not supplied. It is a material fact for this decision.",
    "FACT_NOT_ENTAILED": "{subject} = {supplied_value}: not entailed ({reason}). To clear, cite text in the case file that contains the value, or a named rule that derives it.",
    "OUT_OF_SCOPE": "{subject} = {supplied_value}: outside this rulebook's scope ({scope_value}).",
    "NUMBER_MISSING": "{subject}: not supplied. The decision needs the value in force on {decision_date}.",
    "NUMBER_NO_VERSION_IN_FORCE": "{subject}: no version in the rulebook is in force on {decision_date}.",
    "UNKNOWN_PARAMETER": "{subject}: not a parameter in this rulebook.",
    ("NUMBER_NOT_IN_FORCE", "value_out_of_date"): "{subject}: {supplied_value} is present but not in force on {decision_date}; it was in force {supplied_effective_from} to {supplied_effective_to}. In force on {decision_date}: {in_force_value} ({in_force_source_id}, PDF page {in_force_pdf_page}). Delta {delta_signed}. To clear, supply {in_force_value} from a source in force on {decision_date}.",
    ("NUMBER_NOT_IN_FORCE", "source_not_in_force"): "{subject}: {supplied_value} is the value in force on {decision_date}, but the cited source {supplied_source_id} is not the one in force. To clear, cite {in_force_source_id}.",
    ("NUMBER_NOT_IN_FORCE", "value_unknown"): "{subject}: {supplied_value} is not a value of this parameter in any version. In force on {decision_date}: {in_force_value} ({in_force_source_id}, PDF page {in_force_pdf_page}). Delta {delta_signed}.",
}


def with_message(finding: dict) -> dict:
    key = (finding["kind"], finding["reason"]) if finding["kind"] == "NUMBER_NOT_IN_FORCE" else finding["kind"]
    fields = dict(finding)
    if "delta" in finding:
        fields["delta_signed"] = signed(finding["delta"])
    finding["message"] = TEMPLATES[key].format(**fields)
    return finding


def recompute_findings(action: dict, case_text: str, rb: dict) -> list:
    findings = []
    date = action["decision_date"]
    facts = {f["name"]: f for f in action.get("facts", [])}
    for name in rb["material_facts"]:
        if name not in facts:
            findings.append({"kind": "FACT_MISSING", "subject": name})
            continue
        why = entailment_failure(facts[name], case_text, rb["named_rules"])
        if why:
            findings.append({"kind": "FACT_NOT_ENTAILED", "subject": name,
                             "supplied_value": facts[name].get("value"), "reason": why})
        elif name == "household_size" and facts[name]["value"] != rb["scope"]["household_size"]:
            findings.append({"kind": "OUT_OF_SCOPE", "subject": name,
                             "supplied_value": facts[name]["value"],
                             "scope_value": rb["scope"]["household_size"]})

    numbers = {n["parameter"]: n for n in action.get("numbers", [])}
    for name in numbers:
        if name not in rb["parameters"]:
            findings.append({"kind": "UNKNOWN_PARAMETER", "subject": name})
    for name, param in rb["parameters"].items():
        if name not in numbers:
            findings.append({"kind": "NUMBER_MISSING", "subject": name, "decision_date": date})
            continue
        given = numbers[name]
        current = next((v for v in param["versions"]
                        if v["effective_from"] <= date <= v["effective_to"]), None)
        if current is None:
            findings.append({"kind": "NUMBER_NO_VERSION_IN_FORCE", "subject": name,
                             "decision_date": date})
            continue
        same_value = given["value"] == current["value"]
        if same_value and given.get("source_id") == current["source"]["id"]:
            continue
        f = {"kind": "NUMBER_NOT_IN_FORCE", "subject": name, "decision_date": date,
             "supplied_value": given["value"], "supplied_source_id": given.get("source_id"),
             "in_force_value": current["value"], "in_force_source_id": current["source"]["id"],
             "in_force_pdf_page": current["source"]["pdf_page"],
             "delta": current["value"] - given["value"]}
        if same_value:
            f["reason"] = "source_not_in_force"
        else:
            matches = sorted((v for v in param["versions"] if v["value"] == given["value"]),
                             key=lambda v: v["effective_from"])
            if matches:
                f["reason"] = "value_out_of_date"
                f["supplied_effective_from"] = matches[-1]["effective_from"]
                f["supplied_effective_to"] = matches[-1]["effective_to"]
            else:
                f["reason"] = "value_unknown"
        findings.append(f)

    findings = [with_message(f) for f in findings]
    return sorted(findings, key=lambda f: (f["kind"], f["subject"]))


# --- receipt ---------------------------------------------------------------------------

def canonical_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def contains_float(obj) -> bool:
    if isinstance(obj, float):
        return True
    if isinstance(obj, dict):
        return any(contains_float(v) for v in obj.values())
    if isinstance(obj, list):
        return any(contains_float(v) for v in obj)
    return False


def rebuild(action: dict, case_bytes: bytes, rb_bytes: bytes) -> dict:
    rb = json.loads(rb_bytes)
    if contains_float(action) or contains_float(rb):
        raise ValueError("floating-point value in inputs; SPEC allows integers only")
    date = action["decision_date"]
    figures = []
    for name, param in rb["parameters"].items():
        v = next((v for v in param["versions"]
                  if v["effective_from"] <= date <= v["effective_to"]), None)
        if v is not None:
            s = v["source"]
            figures.append({"parameter": name, "value": v["value"],
                            "effective_from": v["effective_from"],
                            "effective_to": v["effective_to"], "source_id": s["id"],
                            "title": s["title"], "url": s["url"], "pdf_page": s["pdf_page"],
                            "sha256": s["sha256"], "role": "states the figure in force"})
    findings = recompute_findings(action, case_bytes.decode("utf-8"), rb)
    body = {
        "receipt_schema": "asof-gate/receipt/1",
        "rulebook": rb["rulebook"],
        "rulebook_version": rb["rulebook_version"],
        "inputs": {"action": action, "case_file_sha256": digest(case_bytes),
                   "rulebook_sha256": digest(rb_bytes)},
        "citations": {"authority": {"citation": rb["authority"]["citation"],
                                    "role": "derivation only; states no dollar figure"},
                      "figures": figures},
        "decision": {"decision_date": date,
                     "verdict": "STOP" if findings else "CLEAR",
                     "findings": findings},
    }
    body["receipt_sha256"] = digest(canonical_bytes(body))
    return body


def verify(receipt_path: Path, case_path: Path, rulebook_path: Path) -> list:
    """Return a list of failed checks; empty means the receipt verifies."""
    problems = []
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    case_bytes, rb_bytes = case_path.read_bytes(), rulebook_path.read_bytes()

    if digest(case_bytes) != receipt.get("inputs", {}).get("case_file_sha256"):
        problems.append("case file does not hash to the value recorded in the receipt")
    if digest(rb_bytes) != receipt.get("inputs", {}).get("rulebook_sha256"):
        problems.append("rulebook does not hash to the value recorded in the receipt")

    rb = json.loads(rb_bytes)
    root = rulebook_path.resolve().parent.parent
    for param in rb["parameters"].values():
        for v in param["versions"]:
            src = root / v["source"]["file"]
            if src.exists() and digest(src.read_bytes()) != v["source"]["sha256"]:
                problems.append(f"source file {v['source']['file']} does not match its recorded sha256")

    stated = dict(receipt)
    stated_hash = stated.pop("receipt_sha256", None)
    if digest(canonical_bytes(stated)) != stated_hash:
        problems.append("receipt_sha256 does not match the receipt's own contents")

    try:
        rebuilt = rebuild(receipt["inputs"]["action"], case_bytes, rb_bytes)
    except (KeyError, ValueError, TypeError) as exc:
        problems.append(f"could not recompute the receipt: {exc}")
        return problems
    if canonical_bytes(rebuilt) != canonical_bytes(receipt):
        if rebuilt["decision"]["verdict"] != receipt.get("decision", {}).get("verdict"):
            problems.append(f"verdict differs: recomputed {rebuilt['decision']['verdict']}, "
                            f"receipt says {receipt.get('decision', {}).get('verdict')}")
        if rebuilt["decision"] != receipt.get("decision"):
            problems.append("recomputed findings differ from the receipt's findings")
        if rebuilt["citations"] != receipt.get("citations"):
            problems.append("recomputed citations differ from the receipt's citations")
        if not problems:
            problems.append("recomputed receipt is not byte-identical to the receipt")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify an asof-gate receipt by recomputing it.")
    ap.add_argument("receipt")
    ap.add_argument("--case", required=True)
    ap.add_argument("--rulebook", required=True)
    a = ap.parse_args(argv)
    problems = verify(Path(a.receipt), Path(a.case), Path(a.rulebook))
    receipt = json.loads(Path(a.receipt).read_text(encoding="utf-8"))
    if problems:
        print(f"FAIL {a.receipt}")
        for p in problems:
            print(f"  - {p}")
        return 1
    d = receipt["decision"]
    print(f"OK   {a.receipt}: recomputed independently, byte-identical. verdict {d['verdict']}, "
          f"{len(d['findings'])} finding(s), receipt_sha256 {receipt['receipt_sha256'][:16]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
