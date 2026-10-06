# asof-gate

A check that runs after an AI intake step proposes the facts of a SNAP case and before the
benefit is computed. It stops the case when a fact does not trace to the household's own
documents, or a legal figure is not the one in force, and it says whether the mistake it
caught is the kind that counts toward a State's SNAP payment error rate.

Everything here is a demonstration on synthetic households. No real person, employer or
landlord appears in it.

> **Version 1 receipts.** This is version 2, and its receipts use a new format
> (`asof-gate/receipt/2`). The four receipts published with version 1
> (`asof-gate/receipt/1`: `clear_sep30`, `clear_oct01`, `stale_constant`, `inferred_fact`) do
> not verify here, and are not meant to. They are not invalid. They verify, unchanged, with
> the version 1 verifier at the git tag `v1`:
>
> ```
> git checkout v1
> python verifier/verify_receipt.py receipts/stale_constant.json >     --case fixtures/cases/case_income_stated.txt --rulebook rules/snap_max_allotment.json
> ```
>
> Given a version 1 receipt, the version 2 verifier checks nothing, says which format it is,
> names the tag, and exits 1. It does not report it as tampered.

## The errors it stops

Each case file holds the documents an intake step would read: an application, pay stubs, a
lease and a utility bill. The intake step proposes facts and names the documents they came
from. The gate checks each fact against those documents and each legal figure against USDA's
published tables.

| fixture | what the intake step did | verdict | benefit as proposed | as the documents support | label |
|---|---|---|---:|---:|---|
| `CLEAR` | nothing wrong | CLEAR | $587 | | none |
| `FACT_NOT_IN_SOURCE` | counted a one-time $600 bonus as regular pay | **STOP** | $433 | $587 | underpayment of $154, **counts** |
| `SUPERSEDED_DOCUMENT` | used two August pay stubs when two September ones were in the file | **STOP** | $742 | $587 | overpayment of $155, **counts** |
| `WRONG_PERSON` | attributed a 17-year-old student's pay stubs to the parent | **STOP** | denied | $510 | wrongful denial, does not count |
| `STALE_TABLE` | used last fiscal year's allotment, deduction and shelter cap for a household of eight | **STOP** | $1,379 | $1,441 | underpayment of $62, **counts** |
| `STALE_FIGURE` | used last fiscal year's $546 allotment for a household of two | **STOP** | $546 | $562 | underpayment of $16, does not count |

All six are October 2026 determinations decided on 5 October 2026, for households in
Tennessee.

`FACT_NOT_IN_SOURCE` stops with:

> monthly_earned_income for p1 = 2795: not supported by PS-3, PS-4, which give 2150. To clear,
> supply 2150.
>
> Counts toward the payment error rate: an underpayment of $154 a month ($433 as proposed, $587
> as supported), above the $58 quality-control tolerance, the last USDA has published (in force
> 2025-10-01 to 2026-09-30; none is published for 2026-10-05).

`SUPERSEDED_DOCUMENT` stops with:

> monthly_earned_income for p1 = 1505: present but superseded on 2026-10-05: PS-1 (2026-08-14),
> PS-2 (2026-08-28). In force: PS-3 (2026-09-11), PS-4 (2026-09-25). Those give 2150. To clear,
> supply 2150 from them.

`WRONG_PERSON` stops with:

> monthly_earned_income for p1 = 602: PS-3, PS-4 name p2, not p1. The documents in force
> support monthly_earned_income for p2 of 602.
>
> Does not count toward the payment error rate: a wrongful denial. As proposed the household is
> ineligible; on the supported facts it is eligible for $510 a month, and loses it. Denials are
> reviewed as negative cases, outside the payment error rate.

In that case the household has three members and a gross income limit of $2,960. The parent
earns $2,472 a month. The 17-year-old is in high school and earns $602, which SNAP does not
count (7 CFR 273.9(c)(7)). Counted as the parent's second job, it puts the household at
$3,074 and over the limit.

## What "counts" means, and where the $58 comes from

A State's payment error rate is measured by quality-control review of cases that were paid.
An overpayment or underpayment is left out of the rate when it is at or below a tolerance
threshold, unless the household was ineligible, in which case it is counted whatever its
size. A denial is not in the payment error rate at all. Denials are reviewed separately, as
negative cases (7 CFR 275.13). So a wrongful denial costs the household its whole benefit and
carries no payment-error consequence for the State. The gate stops it all the same.

The threshold is **$58**, from USDA Food and Nutrition Service, *SNAP - Quality Control (QC)
Error Tolerance Threshold for Fiscal Year (FY) 2026 - QC Policy Memo 25-04*, 13 August 2025,
PDF page 1 (the memo is one page):

> For FY 2026, all State agencies must use $58 as the QC tolerance level. Errors less than or
> equal to the threshold must be excluded from payment error rate calculations unless
> associated with a determination that the household was ineligible for the sample month's
> benefits.

Two things about that figure are stated on every receipt that uses it:

- **It is the fiscal year 2026 threshold, and these are fiscal year 2027 cases.** On
  5 October 2026, USDA's own threshold table (fna.usda.gov/snap/qc/ett, last updated
  24 November 2025) ended at fiscal year 2026, and no fiscal year 2027 memo was found on USDA's
  guidance portal. The gate carries the last published threshold forward and says so. The
  threshold is adjusted each year with the Thrifty Food Plan, so a fiscal year 2027 figure, when
  published, may be a dollar or two higher. Every label above holds at $59, $60
  and $61. `STALE_TABLE`, at $62, would stop counting at a threshold of $62 or more.
- **The memo is cited, not vendored.** usda.gov refuses scripted downloads, so the PDF was read
  in a browser and its SHA-256 recorded in the rulebook. The file is not in `sources/`.

## Do stale figures count?

The original version of this repository was about one figure: the maximum allotment for a
household of two, $546 through 30 September 2026 and $562 from 1 October. Applying the old one
in October underpays by $16. That is well under the tolerance, so on its own it would never
appear in a payment error rate. It is kept as the `STALE_FIGURE` fixture, as a footnote on
dating.

To find out whether any wrong-year figure crosses the threshold,
`tools/policyengine_tool.py search` applied each fiscal year 2026 figure, one at a time and all
together, to 3,594 synthetic October 2026 households of 1 to 12 people. The largest error for
each:

| household size | old maximum allotment | old standard deduction | old shelter cap | all three old |
|---:|---:|---:|---:|---:|
| 1 | $8 | $4 | $8 | $18 |
| 2 | $16 | $4 | $8 | $26 |
| 3 | $23 | $4 | $8 | $33 |
| 4 | $29 | $3 | $8 | $38 |
| 5 | $34 | $4 | $8 | $44 |
| 6 | $42 | $5 | $8 | $53 |
| 7 | $45 | $5 | $8 | $56 |
| 8 | $52 | $5 | $8 | **$63** |
| 9 | **$59** | $5 | $8 | **$70** |
| 10 | **$66** | $5 | $8 | **$77** |
| 11 | **$73** | $5 | $8 | **$84** |
| 12 | **$80** | $4 | $8 | **$91** |

Bold is above $58. All of these are underpayments.

- **For households of 1 to 7, no stale figure counts**, alone or combined.
- **No single stale figure counts for a household of 8 or fewer.** The old maximum allotment
  alone first crosses at 9 people, by one dollar ($59), and would not at a threshold of $59.
- **A whole stale table counts from 8 people up.** `STALE_TABLE` is that case: all three
  figures a year old, $62 short.
- **Last year's income limits produce no payment error and do produce wrongful denials.** The
  old limits are lower, so a household whose income falls between the old and new limit is
  turned away. In the search the benefit lost that way ran up to $194 a month for one person
  and $725 for eight. The band is narrow ($33 wide for a household of one), and the search
  sampled it deliberately, so it says the harm exists and not how common it is.

The search uses a grid of earnings and rents in Tennessee, with working-age adults and
children. It is not a sample of real caseloads. Full results are in
`impacts/stale_figure_search.json`.

## How the pieces fit

- `docs/SPEC.md` is the published rule. Rule 1: every fact traces to the document in force
  that supports it, for the person the document names. Rule 2: every legal figure is the
  version in force on the decision date. Rule 3: every stop is labelled with what it would
  have cost and whether that counts. The label never changes the verdict.
- `asof_gate/` implements the specification. Standard library only.
- `verifier/verify_receipt.py` implements it again, separately. It imports nothing from the
  gate, and a test compares the two files' function bodies and fails on any match.
- `rules/snap_fy2027_48dc.json` is the rulebook: each figure for household sizes 1 to 8 with
  the dates it is in force and the USDA page that states it, and the tolerance.
- `impacts/policyengine.json` holds the benefit amounts, computed by PolicyEngine and keyed
  by the SHA-256 of the inputs they were computed from.
- `tools/policyengine_tool.py` fills that table, checks it, and runs the search. It is the
  only file that needs PolicyEngine.

The figures and their sources:

| figure | fiscal year 2026 | fiscal year 2027 |
|---|---|---|
| maximum allotment, standard deduction, shelter cap | USDA FNS, *SNAP FY 2026 Maximum Allotments and Deductions*, PDF pages 1 and 2 | USDA FNA, *SNAP - Fiscal Year 2027 Cost-of-Living Adjustments* (21 August 2026), PDF pages 4 and 6 |
| gross and net income limits | not in the rulebook | the same memo, PDF page 3 |

Both documents are in `sources/` and the rulebook records their hashes. The Food and Nutrition
Act of 2008 is cited as the authority for how the figures are derived. **The statute states no
dollar figure**, and the receipts never say it does.

### PolicyEngine and the USDA memo

Before computing anything, `tools/policyengine_tool.py confirm` compares PolicyEngine's own
parameters with the rulebook's USDA figures for both fiscal years: 50 values.

- **policyengine-us 2.24.5 matches all 50.** That is the version used.
- **policyengine-us 1.821.4 does not.** Its fiscal year 2026 values match, and its fiscal year
  2027 values are projections made before the memo: $565.28 against $562 for a household of
  two, $216.38 against $217 for the standard deduction, $770.27 against $769 for the shelter
  cap. Seventeen of its 50 values differ.

Whatever PolicyEngine holds, the tool forces the allotment, standard deduction and shelter cap
to the values in the determination being priced, so the memo governs. PolicyEngine derives the
income limits from the poverty guideline and cannot be handed others, so the tool declines to
price a determination whose limits are not the ones in force.

## Run it

Python 3.10 or later, standard library only (pytest for the tests).

```
python -m asof_gate fixtures/fact_not_in_source.json --out receipts/fact_not_in_source.json
python verifier/verify_receipt.py receipts/fact_not_in_source.json \
    --case fixtures/cases/case_a.json --rulebook rules/snap_fy2027_48dc.json \
    --impacts impacts/policyengine.json
python -m pytest -q
```

The gate exits 0 when the action is clear and 2 when it stops. The verifier exits 0 only when
the receipt it recomputes is byte-identical to the one it was given, and 1 otherwise. The
test suite takes about nine minutes, most of it the planted-bug runs.

To recheck the benefit amounts, with `policyengine-us==2.24.5` installed:

```
python tools/policyengine_tool.py confirm
python tools/policyengine_tool.py impacts
python tools/policyengine_tool.py search
```

Each exits 0 when it reproduces what is committed.

## Why a second implementation

The two implementations are checked against each other on 36,960 generated actions, each
under two impact tables: facts right, wrong in each way the specification names, and missing;
figures right, a year old, wrongly sourced, unknown and missing; decision dates on both sides
of the fiscal year and of each pay date. One of the two tables is made-up benefit amounts, so
that every label is reached, including an error of exactly the tolerance.

Planting a bug in either one turns the suite red. `tests/test_planted_bugs.py` does it eleven
times in a temporary copy (counting an error equal to the tolerance, treating a document dated
on the decision day as not yet in force, skipping the whose-document-is-it check, counting a
wrongful denial, and so on) and passes only when pytest exits 1 each time. An untouched copy
must exit 0.

The receipt hash is not a signature. Anyone can recompute it, so a forger could edit a receipt
and rehash it. What stops that is recomputation: the tests flip a stop to a clear, change
"counts" to "does not count", raise the tolerance to $200, shrink the error, relabel the
wrongful denial as no impact, credit the statute with a figure, and rewrite the action, each
time recomputing the hash. The verifier rejects all seventeen.

## What this is not

- **A demonstration.** Six fixtures on four synthetic households in one State, one benefit
  month.
- **The verifier does not recompute the benefit amounts.** It recomputes the verdict, every
  finding, the two sets of inputs, their hashes and the label. The dollar amounts are
  PolicyEngine's, read from a table pinned by hash. A wrong amount in that table, used
  consistently, would verify; a test shows exactly that. The check on the amounts is rerunning
  the tool.
- **It checks what the intake step claims.** A fact is checked against the documents it
  cites. If the intake step leaves an income source out altogether, the gate does not notice.
  It catches a newer document in the same series, and nothing else that was ignored.
- **Tracing is mechanical.** Documents are structured records, not scanned pages. Reading a
  real pay stub is the intake step's job and is not attempted here.
- **A narrow slice of SNAP.** Wages, rent and whether heating or cooling is billed, for
  households of 1 to 8 with no elderly or disabled member, no unearned income, no resources
  and no other deductions. The standard utility allowance is a State figure and is not
  checked. The fixtures are built so the shelter cap binds, which means the allowance's exact
  amount cannot move any benefit shown here; a test holds that in place.
- **Monthly income is rounded down to the dollar.** States choose their own rounding.
- **PolicyEngine is given facts the case files state and nothing else.** Everyone is a U.S.
  citizen, nobody is disabled, and no other programme is received; the application in each
  case file says so. The tool refuses any run in which PolicyEngine gives the household income
  or categorical eligibility the case file does not state.
- **No model calls.** Nothing here asks a language model anything. The "intake step" is a
  fixture.
- **Not a signature scheme, and no interface.** Receipts are recomputable, not signed. Command
  line only.

## Demo recording

![Terminal demo of the earlier version, about 40 seconds. It shows the two dated figures for a two-person SNAP household ($546 through 30 September 2026, $562 from 1 October). The gate is given last year's $546 for an October decision and stops, saying 546 is present but not in force, with a delta of +16. The independent verifier then accepts the real receipt and rejects a forged copy whose verdict was changed to CLEAR.](demo/asof-gate.gif)

This recording is of the earlier version (commit `1872ca7`), which checked only the $546 to
$562 change. The commands in it no longer exist under those names. It has not been
re-recorded for the fixtures above.
