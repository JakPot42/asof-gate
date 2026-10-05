"""Run the gate on a fixture and write its receipt.

    python -m asof_gate FIXTURE [--rulebook RULEBOOK] [--impacts TABLE] [--out RECEIPT]

Exit 0 when the action is CLEAR, 2 when it is STOPPED.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import textwrap
from pathlib import Path

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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="asof_gate")
    ap.add_argument("fixture")
    ap.add_argument("--rulebook", default=DEFAULT_RULEBOOK)
    ap.add_argument("--impacts", default=DEFAULT_IMPACTS)
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    fixture = json.loads(Path(a.fixture).read_text(encoding="utf-8"))
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
