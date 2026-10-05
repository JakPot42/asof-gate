"""Build the hashed receipt for a gate decision. See docs/SPEC.md, "Receipt"."""

from __future__ import annotations

import hashlib
import json

from .gate import canonical, decide

SCHEMA = "asof-gate/receipt/2"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cite(rulebook: dict, version: dict, role: str) -> dict:
    s = rulebook["sources"][version["source_id"]]
    return {"source_id": version["source_id"], "title": s["title"], "url": s["url"],
            "pdf_page": version["pdf_page"], "sha256": s["sha256"],
            "effective_from": version["effective_from"], "effective_to": version["effective_to"],
            "role": role}


def _citations(action: dict, rulebook: dict, impacts: dict, decision: dict) -> dict:
    date = action["decision_date"]
    sizes = [e.get("value") for e in action.get("facts", []) if e.get("name") == "household_size"]
    figures = []
    if sizes and sizes[0] in rulebook["scope"]["household_sizes"]:
        for name, param in rulebook["parameters"].items():
            key = str(sizes[0]) if param["by"] == "household_size" else "all"
            for v in param["versions"]:
                if v["effective_from"] <= date <= v["effective_to"]:
                    figures.append({"parameter": name, "key": key, "value": v["values"][key],
                                    **_cite(rulebook, v, "states the figure in force")})
                    break
    tolerance = None
    t = decision["impact"].get("tolerance")
    if t is not None:
        version = next(v for v in rulebook["qc_tolerance"]["versions"]
                       if v["effective_from"] == t["effective_from"])
        tolerance = {"value": t["value"], "basis": t["basis"],
                     **_cite(rulebook, version, "states the tolerance")}
    return {
        "authority": {"citation": rulebook["authority"]["citation"],
                      "role": "derivation only; states no dollar figure"},
        "figures": figures,
        "tolerance": tolerance,
        "impact_engine": {"name": impacts["engine"]["name"], "version": impacts["engine"]["version"],
                          "role": "computed the benefit amounts; not used to reach the verdict"},
    }


def build_receipt(action: dict, case_bytes: bytes, rulebook_bytes: bytes,
                  impacts_bytes: bytes) -> dict:
    rulebook = json.loads(rulebook_bytes)
    impacts = json.loads(impacts_bytes)
    decision = decide(action, json.loads(case_bytes), rulebook, impacts)
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
        "citations": _citations(action, rulebook, impacts, decision),
        "decision": decision,
    }
    body["receipt_sha256"] = sha256_hex(canonical(body))
    return body
