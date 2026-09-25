# Recording plan (for review before recording)

**Length:** about 90 seconds. **Format:** terminal only, one window, large font, no narration
over the typing; captions below are on-screen text or voice-over, decided at recording time.
**Nothing is typed live that is not in this plan.**

## Shot list

1. **The rule change (10 s).** Show `rules/snap_max_allotment.json`, scrolled to the two
   versions. Caption: *"SNAP maximum allotment, household of 2. $546 through 30 September 2026.
   $562 from 1 October, per USDA's FY 2027 memo, page 4."*

2. **The clear cell (15 s).**
   `python -m asof_gate fixtures/clear_oct01.json --out receipts/clear_oct01.json`
   Shows CLEAR, the figure in force with its source and page, and the authority line reading
   "derivation only; states no dollar figure".

3. **The stale constant (20 s).**
   `python -m asof_gate fixtures/stale_constant.json --out receipts/stale_constant.json`
   Shows STOP and the full message: present but not in force, delta +16, what clears it.
   Caption: *"The newest model we tested did this with next year's numbers. Here it is last
   year's. Either way, it stops."*

4. **The inferred fact (15 s).**
   `python -m asof_gate fixtures/inferred_fact.json --out receipts/inferred_fact.json`
   Shows STOP: income 0 is not entailed; nothing in the case file states it.

5. **Anyone can check the stop (20 s).**
   `python verifier/verify_receipt.py receipts/stale_constant.json --case fixtures/cases/case_income_stated.txt --rulebook rules/snap_max_allotment.json`
   Shows OK, recomputed independently, byte-identical. Then the same command on a forged copy
   (`receipts/forged_clear.json`, prepared off camera: verdict edited to CLEAR, hash
   recomputed) shows FAIL with "verdict differs". Caption: *"The verifier doesn't import the
   gate. It rebuilds the receipt from the published rule."*

6. **Close (10 s).** `python -m pytest -q` showing the pass count. Caption: *"The law stays the
   authority. The check can be recomputed by anyone."*

## Before recording

- Fresh clone, so the recorded run matches what a viewer would get.
- Generate `receipts/forged_clear.json` with a one-line script kept out of the repo, and show
  on screen that it is a copy of `stale_constant.json` with only the verdict edited and the
  hash recomputed.
- Terminal width at least 110 columns so no message wraps mid-word.

## Claims the recording must not make

- That the statute sets $562. It sets the method; USDA publishes the figure.
- That this computes or checks a benefit amount. It checks facts and figures only.
- That the receipt hash proves authenticity. Recomputation does.
- That this is a product or has users.
