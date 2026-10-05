# Specification

This is the published rule, version 2. The gate (`asof_gate/`) and the verifier
(`verifier/verify_receipt.py`) are two separate implementations of it. The verifier does not
import the gate, so a receipt can be checked by anyone who reads this document and writes a
third implementation.

Version 1 checked one figure for one household size against a text case file. Version 2 is a
clean break: version 1 receipts do not verify under it.

## Inputs

1. **An action**: a proposed SNAP determination. It carries the benefit month
   (`benefit_month`, `YYYY-MM`), the date the decision is made (`decision_date`,
   `YYYY-MM-DD`), the facts an intake step proposes (`facts`), and the legal numbers it used
   (`numbers`). It carries no benefit amount. The benefit is computed after the gate.
2. **The case file**: a JSON file with the State, the household members, and the source
   documents (an application, pay stubs, a lease, a utility bill).
3. **The rulebook**: every legal number with the dates it is in force and the document that
   states it, the quality-control tolerance, and the rule that reads each fact from its
   document.
4. **The impact table**: benefit amounts computed outside the gate, keyed by the hash of the
   inputs they were computed from (Rule 3).

Integers only. A floating-point number anywhere in the four inputs is rejected, because two
implementations can serialise the same float differently. A boolean anywhere in the action is
rejected too. Dollar amounts are whole dollars. Yes or no facts are 1 or 0.

### Invalid inputs

These are rejected, not decided. The gate raises an error and the verifier reports that it
could not recompute the receipt:

- a float in any input, or a boolean in the action;
- a case-file document without string `id`, `type`, `person`, `payer` and `date`, or two
  documents with the same `id`;
- a fact entry named in `fact_rules` whose `value` is not an integer, whose `documents` is not
  a list of strings, or which lacks a string `person` when its rule is person-bound;
- more than one entry for a fact whose rule has cardinality `one`;
- a number without a string `parameter` and an integer `value`.

Fact entries whose `name` is not in `fact_rules` are ignored.

## Documents

Every document has an `id`, a `type`, the `person` it names, a `payer` (the employer, landlord
or utility; empty for the application) and a `date`.

A document's **series** is its `(type, person, payer)`.

For a rule that reads `count` documents, the documents **in force** on the decision date are
the `count` most recent documents of the series dated on or before the decision date, ordered
by `(date, id)`. If the series has fewer, all of them are in force and the rule cannot be
applied to them.

## Rule 1: every fact traces to the document in force that supports it

`fact_rules` gives, for each fact: the document `type` it `reads`, how many (`count`), whether
it is `person_bound`, the `field` it reads, and how the value is computed:

| `compute` | value |
|---|---|
| `count` | the number of items in the field of the one document |
| `field` | the field of the one document |
| `biweekly_to_monthly` | the sum of the field over the two documents, times 215, divided by 200, rounded down |

Facts are checked in this order: `household_size`, `monthly_earned_income`, `monthly_rent`,
`heating_or_cooling_cost`; entries of the same fact in the order the action lists them.

- A fact with cardinality `one` and no entry: finding `FACT_MISSING`.

For each entry, the first of these that applies is the finding, and no later one is reported:

1. no document cited: `FACT_NOT_IN_SOURCE`, reason `no_document_cited`;
2. a document id cited twice: reason `duplicate_document`;
3. a cited id is not in the case file: reason `document_not_in_case_file`;
4. a cited document is not of the type the rule reads: reason `wrong_document_type`;
5. the cited documents are not all one series: reason `mixed_series`;
6. the rule is person-bound and the series names a different person than the entry:
   `WRONG_PERSON`, with `document_person`;
7. a cited document is dated after the decision date: `FACT_NOT_IN_SOURCE`, reason
   `document_dated_after_decision`;
8. a cited document is not in force: `SUPERSEDED_DOCUMENT`, with `superseded_documents` (the
   cited documents not in force, by `(date, id)`);
9. the number of cited documents is not `count`: `FACT_NOT_IN_SOURCE`, reason
   `wrong_document_count`;
10. the entry's value is not the rule's value over the cited documents: `FACT_NOT_IN_SOURCE`,
    reason `value_not_supported`.

From step 6 on, the finding also carries `in_force_documents` (id and date of each) and
`supported_value`: the rule's value over the documents in force, or null when fewer than
`count` are in force.

An entry for `household_size` with no finding whose value is not in the rulebook's
`household_sizes`: finding `OUT_OF_SCOPE`.

**What the documents support.** For each entry, the *supported entry* is:

- with no finding: the entry as proposed;
- with a finding from step 6 on and a non-null `supported_value`: the series' person (for a
  person-bound rule) and the `supported_value`;
- otherwise (steps 1 to 5, a null `supported_value`, `FACT_MISSING`, `OUT_OF_SCOPE`): none.

The gate checks what the action claims. It does not look for documents the action ignored,
except through supersession.

## Rule 2: every legal number is the one in force on the decision date

Rule 2 is applied only when the action has a `household_size` entry whose supplied value is in
the rulebook's `household_sizes`. That value picks the cell of each parameter: the household
size for a parameter `by` `household_size`, and `all` otherwise.

A version is **in force** on date `d` when `effective_from <= d <= effective_to`.

For each parameter in the rulebook:

- If the action does not supply it: finding `NUMBER_MISSING`.
- If no version is in force on the decision date: finding `NUMBER_NO_VERSION_IN_FORCE`.
- Otherwise, with `v` the version in force:
  - supplied value equals `v`'s cell and supplied `source_id` equals `v.source_id`: passes.
  - supplied value equals `v`'s cell but the source differs: `NUMBER_NOT_IN_FORCE`, reason
    `source_not_in_force`.
  - supplied value equals the same cell of another version: `NUMBER_NOT_IN_FORCE`, reason
    `value_out_of_date`, recording that version's dates (the most recent such version, by
    `effective_from`, if more than one has the value).
  - otherwise: `NUMBER_NOT_IN_FORCE`, reason `value_unknown`.

`delta` is the cell in force minus the supplied value. Each of these findings carries `key`,
the cell used.

A number the action supplies for a parameter the rulebook does not contain: finding
`UNKNOWN_PARAMETER`.

## Verdict

`STOP` if there is at least one finding, otherwise `CLEAR`. Findings are sorted by
`(kind, subject, entry)`. `subject` is the fact or parameter name. `entry` is the position of
the fact entry in the action's `facts` list, counted from 0, and -1 for every other finding.

The label in Rule 3 never changes the verdict. A stop that would have cost nothing is still a
stop.

## Rule 3: what the stop would have cost, and whether it counts

**Determination inputs.** From a list of fact entries and a set of figures:

```
{
  "benefit_month": <from the action>,
  "state": <from the case file>,
  "members": [{"id", "age", "k12_student", "weekly_hours"} for each household member, by id],
  "household_size": <value>,
  "earned_income": [{"person", "monthly"} for each monthly_earned_income entry, by (person, monthly)],
  "monthly_rent": <value>,
  "heating_or_cooling_cost": <value>,
  "figures": {<parameter>: <value> for every parameter in the rulebook}
}
```

- **As proposed**: the action's fact entries and the numbers it supplied. Available only when
  Rule 2 was applied, no fact is missing, and every parameter was supplied.
- **As supported**: the supported entries of Rule 1 and, for every parameter, the cell in
  force on the decision date for the supported household size. Available only when every
  entry has a supported entry, the supported household size is in scope, and every parameter
  has a version in force.

**Lookup.** The key of a set of inputs is the SHA-256 of its canonical form. The impact table
maps keys to `{"eligible": 1 or 0, "monthly_benefit": whole dollars}`. A key that is absent
gives null for both.

**Tolerance.** The quality-control tolerance used is the `qc_tolerance` version in force on
the decision date (`basis` `in_force`). If none is, it is the version with the latest
`effective_to` before the decision date (`basis` `carried_forward`). If there is none of
those either, there is no tolerance.

**The `impact` block:**

- No findings: `{"status": "clear", "proposed": <as proposed, looked up>, "supported": null}`.
- Otherwise `status` is `not_computed` with the first `reason` that applies, or `computed`:
  - `proposed_inputs_incomplete`: the inputs as proposed are not available;
  - `supported_inputs_unavailable`: the inputs as supported are not available;
  - `not_in_impact_table`: either lookup gave null;
  - `no_tolerance_published`: there is no tolerance.

When computed, `error` is the benefit as proposed minus the benefit as supported, and `class`
and `counts_toward_payment_error_rate` are the first row that applies:

| condition | `class` | counts |
|---|---|---|
| `error` is 0 | `no_benefit_impact` | false |
| eligible as proposed, ineligible as supported | `payment_to_ineligible_household` | true |
| ineligible as proposed, eligible as supported | `wrongful_denial` | false |
| `error` above 0 | `overpayment` | true when `error` is greater than the tolerance |
| `error` below 0 | `underpayment` | true when `-error` is greater than the tolerance |

An error equal to the tolerance does not count. The tolerance does not apply to a payment to
an ineligible household. A wrongful denial costs the household its benefit and is outside the
payment error rate, which is measured on cases that were paid; denials are reviewed
separately as negative cases (7 CFR 275.13).

Each side of the block is `{"inputs", "inputs_sha256", "eligible", "monthly_benefit"}`. A
computed block also carries `tolerance`: `{"value", "basis", "source_id", "pdf_page",
"effective_from", "effective_to"}`.

## Messages

Each finding carries a `message`, and the impact block carries one. They are built with
exactly these templates. `{ids}` is the cited document ids joined with `, `. `{dated X}` is
each document as `ID (date)` joined with `, `. `{delta}` is written with a sign, `+16` or
`-16`. `{who}` is `{subject} for {person} = {supplied_value}` when the finding has a `person`,
and `{subject} = {supplied_value}` otherwise. `{reads}` and `{count}` come from the fact's
rule. `{name}` is `{subject}[{key}]`, or `{subject}` for `UNKNOWN_PARAMETER`. Findings of kind
`FACT_NOT_IN_SOURCE` and `SUPERSEDED_DOCUMENT` carry `decision_date`.

- `FACT_MISSING`: `{subject}: not supplied. It is a material fact for this decision.`
- `OUT_OF_SCOPE`: `{who}: outside the household sizes this rulebook covers.`
- `FACT_NOT_IN_SOURCE`, by reason:
  - `no_document_cited`: `{who}: no document is cited. To clear, cite the {reads} it comes from.`
  - `duplicate_document`: `{who}: the same document is cited twice.`
  - `document_not_in_case_file`: `{who}: a cited document ({ids}) is not in the case file.`
  - `wrong_document_type`: `{who}: this fact is read from a {reads}, and a cited document is not one.`
  - `mixed_series`: `{who}: the cited documents are not all for the same person and payer.`
  - `document_dated_after_decision`: `{who}: a cited document is dated after the decision date {decision_date}.`
  - `wrong_document_count`: `{who}: this fact is read from {count} {reads} document(s), and {number cited} cited. In force: {dated in_force_documents}.`
  - `value_not_supported`: `{who}: not supported by {ids}, which give {supported_value}. To clear, supply {supported_value}.`
- `WRONG_PERSON`: `{who}: {ids} name {document_person}, not {person}.` and, when
  `supported_value` is not null, a space and
  `The documents in force support {subject} for {document_person} of {supported_value}.`
- `SUPERSEDED_DOCUMENT`: `{who}: present but superseded on {decision_date}: {dated superseded_documents}. In force: {dated in_force_documents}.`
  and, when `supported_value` is not null, a space and
  `Those give {supported_value}. To clear, supply {supported_value} from them.`
- `NUMBER_MISSING`: `{name}: not supplied. The decision needs the value in force on {decision_date}.`
- `NUMBER_NO_VERSION_IN_FORCE`: `{name}: no version in the rulebook is in force on {decision_date}.`
- `NUMBER_NOT_IN_FORCE`, reason `value_out_of_date`: `{name}: {supplied_value} is present but not in force on {decision_date}; it was in force {supplied_effective_from} to {supplied_effective_to}. In force on {decision_date}: {in_force_value} ({in_force_source_id}, PDF page {in_force_pdf_page}). Delta {delta}. To clear, supply {in_force_value} from a source in force on {decision_date}.`
- `NUMBER_NOT_IN_FORCE`, reason `source_not_in_force`: `{name}: {supplied_value} is the value in force on {decision_date}, but the cited source {supplied_source_id} is not the one in force. To clear, cite {in_force_source_id}.`
- `NUMBER_NOT_IN_FORCE`, reason `value_unknown`: `{name}: {supplied_value} is not a value of this parameter in any version. In force on {decision_date}: {in_force_value} ({in_force_source_id}, PDF page {in_force_pdf_page}). Delta {delta}.`
- `UNKNOWN_PARAMETER`: `{name}: not a parameter in this rulebook.`

Impact messages. `{p}` and `{s}` are the monthly benefit as proposed and as supported, `{e}`
is the size of the error without its sign, and `{tol}` is
`the ${value} quality-control tolerance in force on {decision_date}` for basis `in_force` and
`the ${value} quality-control tolerance, the last USDA has published (in force {effective_from} to {effective_to}; none is published for {decision_date})`
for basis `carried_forward`.

- `clear`: `No stop, so no error to measure.` and then, when the lookup as proposed found a
  row, a space and `On these facts the household is ineligible.` (eligible 0) or
  `On these facts the benefit is ${p} a month.` (eligible 1)
- `not_computed`: `Benefit impact not computed ({reason}). Not labelled.`
- `no_benefit_impact`: `Does not count toward the payment error rate: no benefit impact (${p} a month as proposed and as supported).`
- `payment_to_ineligible_household`: `Counts toward the payment error rate: ${p} a month would be paid to a household that is ineligible on the supported facts. The tolerance does not apply to an ineligible household.`
- `wrongful_denial`: `Does not count toward the payment error rate: a wrongful denial. As proposed the household is ineligible; on the supported facts it is eligible for ${s} a month, and loses it. Denials are reviewed as negative cases, outside the payment error rate.`
- `overpayment` or `underpayment` that counts: `Counts toward the payment error rate: an overpayment of ${e} a month (${p} as proposed, ${s} as supported), above {tol}.` (or `an underpayment`)
- `overpayment` or `underpayment` that does not: `Does not count toward the payment error rate: an overpayment of ${e} a month (${p} as proposed, ${s} as supported), at or below {tol}.` (or `an underpayment`)

## Receipt

```
{
  "receipt_schema": "asof-gate/receipt/2",
  "rulebook": <rulebook name>,
  "rulebook_version": <rulebook_version>,
  "inputs": {
    "action": <the action, as given>,
    "case_file_sha256": <sha256 of the case file bytes>,
    "rulebook_sha256": <sha256 of the rulebook file bytes>,
    "impact_table_sha256": <sha256 of the impact table file bytes>
  },
  "citations": {
    "authority": {"citation": <authority.citation>, "role": "derivation only; states no dollar figure"},
    "figures": [when Rule 2 was applied, one entry per parameter with a version in force on the
      decision date, in rulebook order:
      {"parameter", "key", "value", <source fields>, "role": "states the figure in force"}],
    "tolerance": null, or when the impact is computed
      {"value", "basis", <source fields>, "role": "states the tolerance"},
    "impact_engine": {"name", "version" (from the impact table's "engine"),
      "role": "computed the benefit amounts; not used to reach the verdict"}
  },
  "decision": {
    "decision_date": <from the action>,
    "verdict": "CLEAR" | "STOP",
    "findings": [<findings>],
    "impact": <the impact block, with its message>
  },
  "receipt_sha256": <sha256 of the canonical form of every other field>
}
```

`<source fields>` are `source_id`, `pdf_page`, `effective_from` and `effective_to` from the
version, and `title`, `url` and `sha256` from the rulebook's `sources` entry for it.

The citations keep three roles apart on purpose. The statute is cited as the authority for how
the figures are derived; it sets no dollar amount, and the receipt never says it does. The
USDA documents are cited as the source of each figure and of the tolerance. The engine that
computed the benefit amounts is named as exactly that.

**Canonical form:** `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`
encoded as UTF-8. The hash is SHA-256 of those bytes, in lowercase hex.

The hash makes a receipt tamper-evident against accidental change. It is not a signature:
anyone can recompute it. Authenticity comes from recomputation. The verifier rebuilds the
whole receipt from the action, the case file, the rulebook and the impact table, and accepts
it only if every byte matches.

## What the verifier does and does not establish

The verdict and every finding are recomputed from the case file and the rulebook alone. The
label is recomputed from those plus the impact table: the verifier derives the two sets of
determination inputs itself, hashes them itself, and applies the classification itself. What
it takes from the table is two numbers per side, the eligibility and the monthly benefit.

It does not recompute those benefit amounts. They are PolicyEngine's, and
`tools/policyengine_tool.py impacts` reruns them. A table with a wrong benefit in it, used
consistently, would verify. A table that differs from the one the receipt was built with
would not.

## Verifier

`python verifier/verify_receipt.py RECEIPT --case CASE_FILE --rulebook RULEBOOK --impacts TABLE`

Exit 0 only when all of these hold:
1. the case file, rulebook and impact table hash to the values recorded in the receipt,
2. every source file the rulebook lists, where present, hashes to its recorded `sha256`,
3. the receipt recomputed from the inputs is byte-identical in canonical form, including the
   verdict, every finding, every message, and the impact block with its label,
4. `receipt_sha256` matches.

Otherwise it exits 1 and says which check failed.
