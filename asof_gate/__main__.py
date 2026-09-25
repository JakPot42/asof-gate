"""Run the gate on a fixture and write its receipt.

    python -m asof_gate FIXTURE [--rulebook RULEBOOK] [--out RECEIPT]

Exit 0 when the action is CLEAR, 2 when it is STOPPED.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .receipt import build_receipt

DEFAULT_RULEBOOK = "rules/snap_max_allotment.json"


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
    print(f"{fixture['fixture']}: {act['action']} ${act['amount']} SNAP for "
          f"{act['benefit_month']}, decision date {dec['decision_date']}")
    print(f"  verdict: {dec['verdict']}")
    for f in dec["findings"]:
        print(f"  - {f['kind']}: {f['message']}")
    for fig in receipt["citations"]["figures"]:
        print(f"  figure in force: {fig['parameter']} = {fig['value']} "
              f"({fig['source_id']}, PDF page {fig['pdf_page']})")
    print(f"  authority: {receipt['citations']['authority']['citation']} "
          f"({receipt['citations']['authority']['role']})")
    print(f"  receipt_sha256: {receipt['receipt_sha256']}")
    return 0 if dec["verdict"] == "CLEAR" else 2


if __name__ == "__main__":
    sys.exit(main())
