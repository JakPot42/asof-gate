# Recording plan

**Length:** about 60 seconds. **Format:** terminal only, one window, 1920 by 1080, large font.
Captions are on-screen text. Nothing is typed that is not in `demo/asof-gate.tape`, which is
the script. Recorded from a fresh clone.

The plan and tape for the earlier version (one figure, $546 to $562) are at the git tag `v1`.

## Beat 1: the document (about 9 s)

    grep -A9 '"PS-4"' fixtures/cases/case_a.json | grep -E 'id|regular_gross|one_time_bonus|gross_total'

On screen: the latest pay stub, regular gross 1000, one-time bonus 600, gross total 1600.

Caption: *"In this synthetic case, an AI intake step counts the one-time $600 bonus as regular
pay."*

## Beat 2: an error that counts (about 13 s)

    python -m asof_gate fixtures/fact_not_in_source.json | head -4 | fold -s -w 100

On screen: `verdict: STOP`; 2795 is not supported by PS-3, PS-4, which give 2150; an
underpayment of $154 a month that counts toward the payment error rate, against the $58
tolerance, which the output says is the last USDA has published.

Caption: *"The gate stops it before the benefit is computed: $154 a month short, an error that
counts toward the State's payment error rate."*

## Beat 3: a harm that does not count (about 14 s)

    python -m asof_gate fixtures/wrong_person.json | head -4 | fold -s -w 100

On screen: `verdict: STOP`; PS-3, PS-4 name p2, not p1; a wrongful denial of $510 a month that
does not count toward the payment error rate.

Caption: *"A student's pay stubs attributed to the parent: the family is wrongly denied $510 a
month. It never shows in the error rate. The gate stops it anyway."*

## Beat 4: anyone can recompute the label (about 15 s)

    python verifier/verify_receipt.py receipts/fact_not_in_source.json --case fixtures/cases/case_a.json --rulebook rules/snap_fy2027_48dc.json --impacts impacts/policyengine.json

On screen: `OK ... recomputed independently, byte-identical. verdict STOP ... counts toward the
payment error rate`.

Then the same command on `forged_label.json`. On screen: `FAIL` and `recomputed impact and
error-rate label differ from the receipt's`.

Caption: *"A separate verifier, sharing no code, recomputes the verdict and the label. Change
the label and it fails."*

## Not in the recording

The $16 stale-figure case (`fixtures/stale_figure.json`) and the stale-table case. They are in
the README.

## Off camera

- The fresh clone.
- `forged_label.json`, made by a one-line script in the tape. It is the committed
  `fact_not_in_source` receipt with its label flipped to "does not count", its message reworded
  to match, and its hash recomputed. Nothing else differs. It is not committed.
- The caption functions.

## Claims the recording must not make

- That the statute sets any dollar figure. It sets the method; USDA publishes the figures.
- That $58 is the fiscal year 2027 threshold. It is the fiscal year 2026 one, carried forward
  because none is published for these dates.
- That the verifier recomputes the dollar amounts. It recomputes the verdict, the findings and
  the label; the amounts are PolicyEngine's.
- That these are real households.
- That a real AI model produced these errors. The intake step is a fixture written by hand.
- That the receipt hash proves authenticity. Recomputation does.
- That this is a product or has users.
