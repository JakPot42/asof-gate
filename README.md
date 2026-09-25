# asof-gate

A check that has to pass before an automated system pays, denies, or files.

![Terminal demo, about 40 seconds. It shows the rulebook's two dated figures for a two-person SNAP household ($546 through 30 September 2026, $562 from 1 October). The gate is given last year's $546 for an October decision and stops, saying 546 is present but not in force, with a delta of +16. The independent verifier then accepts the real receipt and rejects a forged copy whose verdict was changed to CLEAR.](demo/asof-gate.gif)

It enforces two rules:

1. **Every material fact is entailed.** A fact counts only if a span of the case file contains
   the value, or a named rule in the published rulebook derives it. An inferred fact does not
   count.
2. **Every legal number is the one in force on the decision date.** Not last year's, not next
   year's, and cited to the document that states it for that date.

If either fails, the action stops, and the stop says what is missing or what is present but out
of date. Each decision produces a receipt, and a separate verifier recomputes the receipt from
the published rule and accepts it only if every byte matches.

This repository is a demonstration of one path, run on one real rule change: **the SNAP
maximum allotment for a household of 2 in the 48 States and D.C., on 30 September and
1 October 2026.**

| decision date | maximum allotment in force | source |
|---|---:|---|
| 30 September 2026 | **$546** | USDA FNS, *SNAP FY 2026 Maximum Allotments and Deductions*, PDF page 1 |
| 1 October 2026 | **$562** | USDA FNA, *SNAP - Fiscal Year 2027 Cost-of-Living Adjustments* (21 August 2026), PDF page 4 |

Both documents are in `sources/`, and the rulebook records their SHA-256 hashes. The authority
for how the figure is derived is the Food and Nutrition Act of 2008 (7 U.S.C. 2012(u)(2)(B) and
(u)(3)(C), and 2017(a)), which sets a 2-person household at 55 percent of the 4-person amount and
adjusts it each October 1. **The statute states no dollar figure.** Receipts cite USDA for the
number and the Act for the authority, as two separate roles.

## Run it

Python 3.10 or later, standard library only (pytest for the tests).

```
python -m asof_gate fixtures/stale_constant.json --out receipts/stale_constant.json
python verifier/verify_receipt.py receipts/stale_constant.json \
    --case fixtures/cases/case_income_stated.txt --rulebook rules/snap_max_allotment.json
python -m pytest -q
```

The gate exits 0 when the action is clear and 2 when it stops. The verifier exits 0 only when
the receipt it recomputes is byte-identical to the one it was given, and 1 otherwise.

## The four fixtures

| fixture | date | what the action supplies | verdict |
|---|---|---|---|
| `CLEAR_SEP30` | 2026-09-30 | $546 from the FY 2026 table; income 0 from the case file's closure sentence | CLEAR |
| `CLEAR_OCT01` | 2026-10-01 | $562 from the FY 2027 memo; same facts | CLEAR |
| `STALE_CONSTANT` | 2026-10-01 | last year's $546 | **STOP** |
| `INFERRED_FACT` | 2026-09-30 | income 0, inferred from zero working hours, with an empty span | **STOP** |

`STALE_CONSTANT` stops with:

> max_allotment_48dc_hh2: 546 is present but not in force on 2026-10-01; it was in force
> 2025-10-01 to 2026-09-30. In force on 2026-10-01: 562 (usda-fna-snap-cola-fy2027, PDF page 4).
> Delta +16. To clear, supply 562 from a source in force on 2026-10-01.

`INFERRED_FACT` stops with:

> monthly_net_income = 0: not entailed (no_span_and_no_rule). To clear, cite text in the case
> file that contains the value, or a named rule that derives it.

## Why a second implementation

`docs/SPEC.md` is the published rule. `asof_gate/` implements it, and
`verifier/verify_receipt.py` implements it again, separately, without importing the gate. A test
scans the verifier's imports and fails on anything outside the standard library. Anyone can
read the spec and write a third implementation.

The two are checked against each other on 4,320 generated actions (dates on both sides of each
boundary, right and wrong values, right and wrong sources, good, bad and empty spans, in-scope
and out-of-scope households). Planting a bug in either one turns the suite red: flipping the
sign of the verifier's delta fails 3 tests, and treating a version's last day as out of force
in the gate fails 5.

The receipt hash is not a signature. Anyone can recompute it, so a forger could edit a receipt
and rehash it. What stops that is recomputation: the tests edit a stop into a clear, soften a
message, change the delta, credit the statute with the dollar figure, and rewrite the action,
each time recomputing the hash, and the verifier rejects every one.

## What this is not

- **One path, one rule change.** It does not compute a benefit. It checks two things about a
  decision someone else made. It does not check that the amount paid follows from the facts
  and figures.
- **No model calls.** Nothing here asks a language model anything.
- **No rules engine on the critical path.** PolicyEngine is not used. The rulebook is a
  hand-built JSON file, with every value traced to a quoted page.
- **Entailment is literal.** A span must contain the value, digits or a number word from zero
  to ten, or a named rule must derive it. It does not read meaning: a span that contains "2"
  for an unrelated reason passes. The named-rule list is the only other route, and it has one
  entry.
- **Not a signature scheme.** Receipts are recomputable, not signed. Keeping a tamper-evident
  record of them over time is what a log such as LogChain is for, and it is out of scope here.
- **No interface.** Command line only.
