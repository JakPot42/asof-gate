"""Run the gate on a fixture and write its receipt.

    python -m asof_gate FIXTURE [--rulebook RULEBOOK] [--impacts TABLE] [--out RECEIPT]

A fixture whose action has `"program": "FORM_1040"` goes down the tax path, with the rulebook
and impact table the fixture names. Exit 0 when the action is CLEAR, 2 when it is STOPPED.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import textwrap
from pathlib import Path

from . import tax
from .receipt import build_receipt

DEFAULT_RULEBOOK = "rules/snap_fy2027_48dc.json"
DEFAULT_IMPACTS = "impacts/policyengine.json"


def _say(text: str, indent: str = "") -> None:
    """Print, wrapping at word boundaries when writing to a terminal. Pipes get one line."""
    if not sys.stdout.isatty():
        print(indent + text)
        return
    width = shutil.get_terminal_size((100, 24)).columns
    print(textwrap.fill(text, width=width, initial_indent=indent,
                        subsequent_indent=indent + "  ", break_long_words=False,
                        break_on_hyphens=False))


def _write(path, receipt: dict) -> None:
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8", newline="\n")


def _tax(fixture: dict, a) -> int:
    receipt = tax.build_receipt(fixture["action"],
                                Path(fixture["case_file"]).read_bytes(),
                                Path(a.rulebook or fixture["rulebook"]).read_bytes(),
                                Path(a.impacts or fixture["impacts"]).read_bytes())
    _write(a.out, receipt)
    act, dec, cit = fixture["action"], receipt["decision"], receipt["citations"]
    _say(f"{fixture['fixture']}: Form 1040 for tax year {act['tax_year']}, "
         f"decision date {dec['decision_date']}")
    _say(f"verdict: {dec['verdict']}", "  ")
    for f in dec["findings"]:
        _say(f"- {f['kind']}: {f['message']}", "  ")
    _say(f"impact: {dec['impact']['message']}", "  ")
    for fig in cit["figures"]:
        line = (f"figure in force: {fig['parameter']}[{fig['key']}] = {fig['value']} "
                f"({fig['source_id']}, {fig['section']}, PDF page {fig['pdf_page']})")
        if fig["enacted_by"]:
            e = fig["enacted_by"]
            line += f"; enacted by {e['source_id']}, {e['section']}, PDF page {e['pdf_page']}"
        _say(line, "  ")
    for fig in cit["superseded_figures"]:
        _say(f"superseded figure: {fig['parameter']}[{fig['key']}] = {fig['value']} "
             f"({fig['source_id']}, {fig['section']}, PDF page {fig['pdf_page']}), in force "
             f"{fig['effective_from']} to {fig['effective_to']}", "  ")
    _say(f"tax amounts: {cit['impact_engine']['name']} {cit['impact_engine']['version']} "
         f"({cit['impact_engine']['role']})", "  ")
    _say(f"authority: {cit['authority']['citation']} ({cit['authority']['role']})", "  ")
    _say(f"receipt_sha256: {receipt['receipt_sha256']}", "  ")
    return 0 if dec["verdict"] == "CLEAR" else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="asof_gate")
    ap.add_argument("fixture")
    ap.add_argument("--rulebook")
    ap.add_argument("--impacts")
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    fixture = json.loads(Path(a.fixture).read_text(encoding="utf-8"))
    if fixture["action"].get("program") == "FORM_1040":
        return _tax(fixture, a)
    a.rulebook = a.rulebook or DEFAULT_RULEBOOK
    a.impacts = a.impacts or DEFAULT_IMPACTS
    receipt = build_receipt(fixture["action"],
                            Path(fixture["case_file"]).read_bytes(),
                            Path(a.rulebook).read_bytes(),
                            Path(a.impacts).read_bytes())
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8", newline="\n")

    act, dec, cit = fixture["action"], receipt["decision"], receipt["citations"]
    _say(f"{fixture['fixture']}: SNAP determination for {act['benefit_month']}, "
         f"decision date {dec['decision_date']}")
    _say(f"verdict: {dec['verdict']}", "  ")
    for f in dec["findings"]:
        _say(f"- {f['kind']}: {f['message']}", "  ")
    _say(f"impact: {dec['impact']['message']}", "  ")
    if cit["tolerance"]:
        _say(f"tolerance: ${cit['tolerance']['value']} ({cit['tolerance']['source_id']}, "
             f"PDF page {cit['tolerance']['pdf_page']})", "  ")
    _say("figures in force: " + "; ".join(
        f"{fig['parameter']} = {fig['value']} ({fig['source_id']}, PDF page {fig['pdf_page']})"
        for fig in cit["figures"]), "  ")
    _say(f"benefit amounts: {cit['impact_engine']['name']} {cit['impact_engine']['version']} "
         f"({cit['impact_engine']['role']})", "  ")
    _say(f"authority: {cit['authority']['citation']} ({cit['authority']['role']})", "  ")
    _say(f"receipt_sha256: {receipt['receipt_sha256']}", "  ")
    return 0 if dec["verdict"] == "CLEAR" else 2


if __name__ == "__main__":
    sys.exit(main())
