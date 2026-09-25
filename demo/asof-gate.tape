# Demo of asof-gate, rendered with VHS (https://github.com/charmbracelet/vhs).
#
#   vhs demo/asof-gate.tape
#
# Clones the public repository into a temporary folder, so the result depends only on what is
# published. To render from another copy, set ASOF_GATE_REPO to its path or URL first.
#
# Setup and captions are typed while hidden: the fresh clone, and forged_clear.json, which is
# the committed stale-constant receipt with only its verdict edited to CLEAR and its hash
# recomputed. Everything run on screen is a real command whose output is shown as produced.

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
Type `python -c 'import json,hashlib;r=json.load(open("receipts/stale_constant.json"));r["decision"]["verdict"]="CLEAR";b={k:v for k,v in r.items() if k!="receipt_sha256"};r["receipt_sha256"]=hashlib.sha256(json.dumps(b,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest();json.dump(r,open("forged_clear.json","w"),indent=2,sort_keys=True)'` Enter
Type `cap(){ printf '\e[1A\e[2K\r\e[1;36m'; printf '%s\n' "$1" | fold -s -w "$(tput cols)"; printf '\e[0m\n'; }` Enter
Type `c1(){ cap $'SNAP maximum allotment, household of two. \x24546 through 30 September 2026. \x24562 from 1 October, per USDA\'s FY 2027 memo, page 4.'; }` Enter
Type `c2(){ cap $'Given last year\'s figure, it stops and says what is out of date.'; }` Enter
Type `c3(){ cap $'A separate verifier, sharing no code, rebuilds the receipt from the published rule.'; }` Enter
Sleep 1s
Type "clear" Enter
Sleep 500ms

# ---- beat 1: the rule change ------------------------------------------------------------
Type "c1" Enter
Sleep 300ms
Show
Sleep 1500ms
Type `grep -E '^ *"(value|effective_from|effective_to|pdf_page)"' rules/snap_max_allotment.json` Enter
Sleep 7s

# ---- beat 2: the stale constant stops ---------------------------------------------------
Hide
Type "clear" Enter
Sleep 300ms
Show
Type "python -m asof_gate fixtures/stale_constant.json --out receipts/stale_constant.json" Enter
Sleep 2s
Hide
Type "c2" Enter
Sleep 300ms
Show
Sleep 9s

# ---- beat 3: anyone can recompute the stop ----------------------------------------------
Hide
Type "clear" Enter
Sleep 300ms
Show
Type `python verifier/verify_receipt.py receipts/stale_constant.json \` Enter
Type "    --case fixtures/cases/case_income_stated.txt --rulebook rules/snap_max_allotment.json" Enter
Sleep 3s
Type `python verifier/verify_receipt.py forged_clear.json \` Enter
Type "    --case fixtures/cases/case_income_stated.txt --rulebook rules/snap_max_allotment.json" Enter
Sleep 2500ms
Hide
Type "c3" Enter
Sleep 300ms
Show
Sleep 8s
