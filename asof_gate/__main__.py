"""Run the gate on a fixture and write its receipt.

    python -m asof_gate FIXTURE [--rulebook RULEBOOK] [--out RECEIPT]

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

DEFAULT_RULEBOOK = "rules/snap_max_allotment.json"


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
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    fixture = json.loads(Path(a.fixture).read_text(encoding="utf-8"))
    receipt = build_receipt(fixture["action"],
                            Path(fixture["case_file"]).read_bytes(),
                            Path(a.rulebook).read_bytes())
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8", newline="\n")

    act, dec = fixture["action"], receipt["decision"]
    _say(f"{fixture['fixture']}: {act['action']} ${act['amount']} SNAP for "
         f"{act['benefit_month']}, decision date {dec['decision_date']}")
    _say(f"verdict: {dec['verdict']}", "  ")
    for f in dec["findings"]:
        _say(f"- {f['kind']}: {f['message']}", "  ")
    for fig in receipt["citations"]["figures"]:
        _say(f"figure in force: {fig['parameter']} = {fig['value']} "
             f"({fig['source_id']}, PDF page {fig['pdf_page']})", "  ")
    _say(f"authority: {receipt['citations']['authority']['citation']} "
         f"({receipt['citations']['authority']['role']})", "  ")
    _say(f"receipt_sha256: {receipt['receipt_sha256']}", "  ")
    return 0 if dec["verdict"] == "CLEAR" else 2


if __name__ == "__main__":
    sys.exit(main())
