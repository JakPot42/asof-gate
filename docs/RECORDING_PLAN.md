# Recording plan (for review before recording)

**Length:** 30 to 45 seconds. **Format:** terminal only, one window, large font, width at least
110 columns so no message wraps mid-word. Captions are on-screen text. Nothing is typed live that
is not in this plan. Recorded from a fresh clone.

## Beat 1: the rule change (8 to 10 s)

Show `rules/snap_max_allotment.json`, scrolled to the two versions.

Caption: *"SNAP maximum allotment, household of two. $546 through 30 September 2026. $562 from
1 October, per USDA's FY 2027 memo, page 4."*

## Beat 2: the stale constant stops (12 to 15 s)

    python -m asof_gate fixtures/stale_constant.json --out receipts/stale_constant.json

On screen: `verdict: STOP` and the message: 546 is present but not in force on 2026-10-01,
delta +16, supply 562 from a source in force on that date.

Caption: *"Given last year's figure, it stops and says what is out of date."*

## Beat 3: anyone can recompute the stop (12 to 15 s)

    python verifier/verify_receipt.py receipts/stale_constant.json --case fixtures/cases/case_income_stated.txt --rulebook rules/snap_max_allotment.json

On screen: `OK ... recomputed independently, byte-identical. verdict STOP`.

Then the same command on `forged_clear.json`: a copy with the verdict edited to CLEAR and the
hash recomputed. On screen: `FAIL` and `verdict differs: recomputed STOP, receipt says CLEAR`.

Caption: *"A separate verifier, sharing no code, rebuilds the receipt from the published rule."*

## Optional beat: the inferred fact (5 s, only if the cut runs under 40 s)

    python -m asof_gate fixtures/inferred_fact.json

On screen: `STOP` and `monthly_net_income = 0: not entailed`. No caption.

## Before recording

- `forged_clear.json` is made off camera by a one-line script kept out of the repository, which
  edits only the verdict and recomputes the hash. It is not committed.

## Claims the recording must not make

- That the statute sets $562. It sets the method; USDA publishes the figure.
- That this computes or checks a benefit amount.
- That the receipt hash proves authenticity. Recomputation does.
- That this is a product or has users.
