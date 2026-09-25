# Specification

This is the published rule. The gate (`asof_gate/`) and the verifier
(`verifier/verify_receipt.py`) are two separate implementations of it. The verifier does not
import the gate, so a receipt can be checked by anyone who reads this document and writes a
third implementation.

## Inputs

1. **An action**: what an automated system proposes to do (`pay`, `deny` or `file`), the date
   the decision covers (`decision_date`, `YYYY-MM-DD`), the facts it relies on, and the legal
   numbers it used.
2. **The case file**: the text the facts must come from.
3. **The rulebook**: every legal number with the dates it is in force and the document that
   states it, the material facts for this path, and the named rules that may derive a fact.

Integers only. The gate and the verifier reject a floating-point number anywhere in the
action or the rulebook, because two implementations can serialise the same float differently.

## Text matching

`norm(s)`: lowercase, then replace every run of whitespace with a single space, then strip.

A text `a` **appears in** a text `b` when `norm(a)` is a substring of `norm(b)`.

An integer value `v` **is rendered in** a span when either:
- the decimal form of `v` (with or without thousands commas, optionally after `$`) appears as a
  whole token, or
- `v` is 0 to 10 and its English word (`zero`, `one`, ... `ten`) appears as a whole word.

A whole token or word is bounded by the start or end of the span or by a character that is not
a letter or digit.

## Rule 1: every material fact is entailed

For each material fact named in the rulebook:

- If the action does not supply it: finding `FACT_MISSING`.
- If the action supplies it with a `rule`:
  - the rule must exist in `named_rules` (else reason `unknown_rule`),
  - the rule's `requires_text` must appear in the case file (else reason `rule_text_absent`),
  - the rule must derive this fact and this value (else reason `rule_derives_other_value`).
- Otherwise the action must supply a `span`:
  - empty or absent: reason `no_span_and_no_rule`,
  - the span must appear in the case file (else reason `span_not_in_case_file`),
  - the value must be rendered in the span (else reason `span_does_not_contain_value`).

Any failed condition gives finding `FACT_NOT_ENTAILED` with that reason. Only the first failed
condition, in the order above, is reported.

If `household_size` is entailed but is not the rulebook's scope value: finding `OUT_OF_SCOPE`.

Facts the action supplies that are not material are ignored.

## Rule 2: every legal number is the one in force on the decision date

A version is **in force** on date `d` when `effective_from <= d <= effective_to`.

For each parameter in the rulebook:

- If the action does not supply it: finding `NUMBER_MISSING`.
- If no version is in force on the decision date: finding `NUMBER_NO_VERSION_IN_FORCE`.
- Otherwise, with `v` the version in force:
  - supplied value equals `v.value` and supplied `source_id` equals `v.source.id`: passes.
  - supplied value equals `v.value` but the source differs: `NUMBER_NOT_IN_FORCE`, reason
    `source_not_in_force`.
  - supplied value equals another version's value: `NUMBER_NOT_IN_FORCE`, reason
    `value_out_of_date`, recording that version's dates (the most recent such version, by
    `effective_from`, if more than one has the value).
  - otherwise: `NUMBER_NOT_IN_FORCE`, reason `value_unknown`.

`delta` is `v.value - supplied value`.

A number the action supplies for a parameter the rulebook does not contain: finding
`UNKNOWN_PARAMETER`.

## Verdict

`STOP` if there is at least one finding, otherwise `CLEAR`. Findings are sorted by
`(kind, subject)`.

## Messages

Each finding carries a `message`, built from its fields with exactly these templates (`{x}` is
the field value, and `{delta}` is written with a sign, `+16` or `-16`):

- `FACT_MISSING`: `{subject}: not supplied. It is a material fact for this decision.`
- `FACT_NOT_ENTAILED`: `{subject} = {supplied_value}: not entailed ({reason}). To clear, cite text in the case file that contains the value, or a named rule that derives it.`
- `OUT_OF_SCOPE`: `{subject} = {supplied_value}: outside this rulebook's scope ({scope_value}).`
- `NUMBER_MISSING`: `{subject}: not supplied. The decision needs the value in force on {decision_date}.`
- `NUMBER_NO_VERSION_IN_FORCE`: `{subject}: no version in the rulebook is in force on {decision_date}.`
- `NUMBER_NOT_IN_FORCE`, reason `value_out_of_date`: `{subject}: {supplied_value} is present but not in force on {decision_date}; it was in force {supplied_effective_from} to {supplied_effective_to}. In force on {decision_date}: {in_force_value} ({in_force_source_id}, PDF page {in_force_pdf_page}). Delta {delta}. To clear, supply {in_force_value} from a source in force on {decision_date}.`
- `NUMBER_NOT_IN_FORCE`, reason `source_not_in_force`: `{subject}: {supplied_value} is the value in force on {decision_date}, but the cited source {supplied_source_id} is not the one in force. To clear, cite {in_force_source_id}.`
- `NUMBER_NOT_IN_FORCE`, reason `value_unknown`: `{subject}: {supplied_value} is not a value of this parameter in any version. In force on {decision_date}: {in_force_value} ({in_force_source_id}, PDF page {in_force_pdf_page}). Delta {delta}.`
- `UNKNOWN_PARAMETER`: `{subject}: not a parameter in this rulebook.`

## Receipt

```
{
  "receipt_schema": "asof-gate/receipt/1",
  "rulebook": <rulebook name>,
  "rulebook_version": <rulebook_version>,
  "inputs": {
    "action": <the action, as given>,
    "case_file_sha256": <sha256 of the case file bytes>,
    "rulebook_sha256": <sha256 of the rulebook file bytes>
  },
  "citations": {
    "authority": {"citation": <authority.citation>, "role": "derivation only; states no dollar figure"},
    "figures": [one entry per parameter with a version in force on the decision date:
      {"parameter", "value", "effective_from", "effective_to", "source_id", "title", "url",
       "pdf_page", "sha256", "role": "states the figure in force"}]
  },
  "decision": {
    "decision_date": <from the action>,
    "verdict": "CLEAR" | "STOP",
    "findings": [<findings>]
  },
  "receipt_sha256": <sha256 of the canonical form of every other field>
}
```

The citations separate the two roles on purpose: the statute is cited as the authority for how
the figure is derived, and the USDA document as the source of the figure itself. The statute
sets no dollar amount, and the receipt never says it does.

**Canonical form:** `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`
encoded as UTF-8. The hash is SHA-256 of those bytes, in lowercase hex.

The hash makes a receipt tamper-evident against accidental change. It is not a signature:
anyone can recompute it. Authenticity comes from recomputation. The verifier rebuilds the
whole receipt from the action, the case file and the rulebook, and accepts it only if every
byte matches.

## Verifier

`python verifier/verify_receipt.py RECEIPT --case CASE_FILE --rulebook RULEBOOK`

Exit 0 only when all of these hold:
1. the case file and rulebook hash to the values recorded in the receipt,
2. every source file the rulebook cites, where present, hashes to its recorded `sha256`,
3. the receipt recomputed from the inputs is byte-identical in canonical form, including the
   verdict, every finding and every message,
4. `receipt_sha256` matches.

Otherwise it exits 1 and says which check failed.
