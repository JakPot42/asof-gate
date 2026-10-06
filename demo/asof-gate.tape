# Demo of asof-gate version 2, rendered with VHS (https://github.com/charmbracelet/vhs).
#
#   vhs demo/asof-gate.tape
#
# Clones the public repository into a temporary folder, so the result depends only on what is
# published. To render from another copy, set ASOF_GATE_REPO to its path or URL first.
#
# Setup and captions are typed while hidden: the fresh clone, and forged_label.json, which is
# the committed fact_not_in_source receipt with only its error-rate label flipped to "does not
# count", its message reworded to match, and its hash recomputed. Everything run on screen is
# a real command whose output is shown as produced. The gate's output is cut to its first four
# lines by a visible `| head -4`, and wrapped at spaces by a visible `| fold -s -w 100`,
# because output sent down a pipe is not wrapped by the gate itself.

Output demo/asof-gate.mp4
Output demo/asof-gate.gif

Set Shell "bash"
Set FontFamily "DejaVu Sans Mono"
Set FontSize 27
Set Width 1920
Set Height 1080
Set Padding 36
Set TypingSpeed 18ms
Set Framerate 24

# ---- setup, off camera ------------------------------------------------------------------
Hide
Type `export PS1='$ ' PS2=''` Enter
Type `cd "$(mktemp -d)" && git clone -q "${ASOF_GATE_REPO:-https://github.com/JakPot42/asof-gate.git}" asof-gate && cd asof-gate` Enter
Sleep 8s
Type `python -c 'import json,hashlib;r=json.load(open("receipts/fact_not_in_source.json"));i=r["decision"]["impact"];i["counts_toward_payment_error_rate"]=False;i["message"]=i["message"].replace("Counts toward","Does not count toward").replace("above","at or below");b={k:v for k,v in r.items() if k!="receipt_sha256"};r["receipt_sha256"]=hashlib.sha256(json.dumps(b,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest();json.dump(r,open("forged_label.json","w"),indent=2,sort_keys=True)'` Enter
Type `cap(){ printf '\e[1A\e[2K\r\e[1;36m'; printf '%s\n' "$1" | fold -s -w "$(tput cols)"; printf '\e[0m\n'; }` Enter
Type `c1(){ cap $'In this synthetic case, an AI intake step counts the one-time \x24600 bonus as regular pay.'; }` Enter
Type `c2(){ cap $'The gate stops it before the benefit is computed: \x24154 a month short, an error that counts toward the State\'s payment error rate.'; }` Enter
Type `c3(){ cap $'A student\'s pay stubs attributed to the parent: the family is wrongly denied \x24510 a month. It never shows in the error rate. The gate stops it anyway.'; }` Enter
Type `c4(){ cap $'A separate verifier, sharing no code, recomputes the verdict and the label. Change the label and it fails.'; }` Enter
Sleep 1s
Type "clear" Enter
Sleep 500ms

# ---- beat 1: the document ---------------------------------------------------------------
Type "c1" Enter
Sleep 300ms
Show
Sleep 1500ms
Type `grep -A9 '"PS-4"' fixtures/cases/case_a.json | grep -E 'id|regular_gross|one_time_bonus|gross_total'` Enter
Sleep 7s

# ---- beat 2: an error that counts -------------------------------------------------------
Hide
Type "clear" Enter
Sleep 300ms
Show
Type "python -m asof_gate fixtures/fact_not_in_source.json | head -4 | fold -s -w 100" Enter
Sleep 2s
Hide
Type "c2" Enter
Sleep 300ms
Show
Sleep 10s

# ---- beat 3: a harm that does not count -------------------------------------------------
Hide
Type "clear" Enter
Sleep 300ms
Show
Type "python -m asof_gate fixtures/wrong_person.json | head -4 | fold -s -w 100" Enter
Sleep 2s
Hide
Type "c3" Enter
Sleep 300ms
Show
Sleep 11s

# ---- beat 4: anyone can recompute the label ---------------------------------------------
Hide
Type "clear" Enter
Sleep 300ms
Show
Type `python verifier/verify_receipt.py receipts/fact_not_in_source.json --case fixtures/cases/case_a.json \` Enter
Type "    --rulebook rules/snap_fy2027_48dc.json --impacts impacts/policyengine.json" Enter
Sleep 3s
Type `python verifier/verify_receipt.py forged_label.json --case fixtures/cases/case_a.json \` Enter
Type "    --rulebook rules/snap_fy2027_48dc.json --impacts impacts/policyengine.json" Enter
Sleep 2500ms
Hide
Type "c4" Enter
Sleep 300ms
Show
Sleep 8s
