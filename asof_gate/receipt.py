"""Build the hashed receipt for a gate decision. See docs/SPEC.md, "Receipt"."""

from __future__ import annotations

import hashlib
import json

from .gate import decide

SCHEMA = "asof-gate/receipt/1"


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _citations(action: dict, rulebook: dict) -> dict:
    date = action["decision_date"]
    figures = []
    for name, param in rulebook["parameters"].items():
        for v in param["versions"]:
            if v["effective_from"] <= date <= v["effective_to"]:
                s = v["source"]
                figures.append({
                    "parameter": name, "value": v["value"],
                    "effective_from": v["effective_from"], "effective_to": v["effective_to"],
                    "source_id": s["id"], "title": s["title"], "url": s["url"],
                    "pdf_page": s["pdf_page"], "sha256": s["sha256"],
                    "role": "states the figure in force",
                })
                break
    return {
        "authority": {"citation": rulebook["authority"]["citation"],
                      "role": "derivation only; states no dollar figure"},
        "figures": figures,
    }


def build_receipt(action: dict, case_bytes: bytes, rulebook_bytes: bytes) -> dict:
    rulebook = json.loads(rulebook_bytes)
    body = {
        "receipt_schema": SCHEMA,
        "rulebook": rulebook["rulebook"],
        "rulebook_version": rulebook["rulebook_version"],
        "inputs": {
            "action": action,
            "case_file_sha256": sha256_hex(case_bytes),
            "rulebook_sha256": sha256_hex(rulebook_bytes),
        },
        "citations": _citations(action, rulebook),
        "decision": decide(action, case_bytes.decode("utf-8"), rulebook),
    }
    body["receipt_sha256"] = sha256_hex(canonical(body))
    return body
