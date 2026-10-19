# Vertex search-result matcher

Select product-matching search references from uploaded titles, descriptions and
candidate results. Optional URL verification retrieves public pages and supplies
bounded excerpts to the same matcher. This does not assign tax categories or
certify that every claim on a matching page is accurate.

## Setup and run

Requires Python 3.11+ and uv. Supply `OPENAI_API_KEY` from your secret-management
workflow and set `OPENAI_MODEL` to an available structured-output model. The CLI
also supports an existing local `.env`; do not create or commit credential files.
Shell environment values take precedence; `--model` overrides `OPENAI_MODEL`.

```sh
uv sync --locked
uv run vertex-match validate search_results_ground_truth_train.json
uv run vertex-match run search_results_ground_truth_train.json --output-dir runs/new-run --verify-links --no-cache --max-output-tokens 6500
uv run vertex-match evaluate runs/new-run/predictions.json
```

`run` makes paid model calls; `--verify-links` additionally sends GET requests to
candidate URLs. Human selections and outer record keys never enter model requests.
Do not reuse an output directory if you want to preserve its previous reports.

Without `--verify-links`, the standard route uses search-result evidence only.
Verification is an explicit slower option and covers all candidates, including
initially unselected ones, up to the per-product URL cap. There is no separate
expensive agent graph or implicit browser automation.

Useful settings:

| Flag | Default | Purpose |
| --- | --- | --- |
| `--concurrency` | 5 | Concurrent product workers |
| `--requests-per-minute` | 60 | Model request-start rate, including retries |
| `--timeout` | 60 | Seconds per model attempt |
| `--max-attempts` | 3 | Maximum model attempts per product |
| `--max-output-tokens` | 4096 | Model response cap; verification may need 6500 or more |
| `--max-input-chars` | 60000 | Maximum serialized product payload; oversized inputs fail |
| `--page-timeout` | 8 | Seconds per page download/extraction |
| `--page-max-chars` | 3000 | Maximum page excerpt per candidate |
| `--max-pages-per-product` | 10 | Unique URL limit per product |
| `--page-requests-per-minute` | 120 | Global page request-start rate within this process |
| `--customer` | local | Cache namespace; not authentication |
| `--no-cache` | off | Force fresh model inference |

Supply both `--input-price-per-million` and `--output-price-per-million` for cost
estimates, or the corresponding `OPENAI_INPUT_PRICE_PER_MILLION` and
`OPENAI_OUTPUT_PRICE_PER_MILLION` environment variables. No price is inferred.
Token accounting includes returned usage from failed validation attempts and
retries. Unknown usage makes the complete cost estimate null. No dollar budget is
enforced. Estimates exclude hosting and human review.

## Matching and verification

The prompt lives in `src/vertex_matcher/matching_policy.md`. Titles are read as
product-name noun phrases. A distinctive product line or model can identify a
product without a manufacturer; generic categories remain generic. Size and count
are ignored, including composition-preserving product dimensions, but flavors,
meaningful component specifications, formulation and
physical form still matter. A matching recipe, article, collection or manual can
be a valid reference. Recipe ingredients are not the target product's ingredients.

The phrase registry in `alias_registry.py` supplies original source spans and
provenance for BRK, PRTN PWDR, and UNSWET BLK TEA. These are lexical aids, not
independent verification. It also contains a manufacturer-sourced naming mapping
for the explicitly specified V8 V-Fusion 100% Juice line to V8 Veggie Blends 100%
Juice, backed by Campbell's naming document. This excludes Energy, Light and
diluted lower-sugar products, and does not prove unchanged historical ingredients.
BRK is a user-provided Brookshire's mapping specific to
this deployment; do not assume it applies to every customer's catalog.

With URL verification enabled, the fetcher:

- Allows public HTTP(S) URLs on standard ports, without URL credentials.
- Validates DNS addresses and each redirect; rejects private/local destinations.
- Keeps TLS verification enabled, ignores environment proxies, and sends no cookies.
- Deduplicates exact URLs within the run and limits downloads to 2 MB.
- Extracts HTML/plain text and up to five PDF pages. PDF parsing runs in a
  subprocess with a four-second deadline. No browser JavaScript or OCR is run.
- Supplies bounded heading/relevant-text excerpts, body hashes, truncation flags,
  final URLs, timings and explicit failure records. Oversized HTML/plain text is
  explicitly marked partial, with a prefix-only hash; oversized PDFs and unsupported
  encodings fail visibly. Neither transport success nor a partial page proves a match.

The model reports a verification status for every candidate:

| Status | Meaning |
| --- | --- |
| `confirmed` | Fetched text positively identifies the selected compatible product |
| `contradicted` | Fetched text establishes a different product/material variant |
| `inconclusive` | Text was retrieved but does not resolve identity, including bot/login pages |
| `unavailable` | Retrieval failed, URL was absent, or URL cap was exceeded |
| `not_checked` | Verification was disabled |

Confirmed/contradicted judgments require an exact quote from that candidate's
fetched excerpt. Local validation checks the status against retrieval, checks every
quote, and disallows selecting a candidate whose URL verdict is contradicted.
These checks do not independently establish the quote's semantic relevance.
Identical candidate text must receive consistent classifications.

`llm_trusted_search_results` is the delivered union of all supplied human IDs and
model selections. `model_selected_search_results` preserves model-only results;
`human_retained_search_results` identifies IDs retained despite model exclusion.
`selection_review` records each delivered candidate's origin, model decision,
verification status and review flags. Human IDs are retained even on model failure,
which remains an explicit failed run with no model prediction field.

`url_verified_search_results` contains only model-selected candidates with confirmed
fetched evidence. HTTP 200 alone never qualifies. Retained human conflicts and
unavailable or inconclusive pages cannot enter this subset. An unavailable page
does not automatically reject a strong snippet match from model selections.

## Evaluation: human selections as the development minimum

The new primary target is recovery of **all** supplied human-selected IDs, allowing
additional supported results. Human labels remain unchanged and excluded from
inference. All standard metrics and acceptance gates score model-only results.
`delivery_reference_minimum` measures baseline retention, which is guaranteed by
construction and is not an accuracy score. `reference_minimum` reports required/recovered candidate counts, recovery
rate, whether the minimum is met, and every missing ID. Failed products count as
unrecovered. `url_verified_reference_minimum` measures that same minimum using only
URL-confirmed selections. It is null when verification was not performed.

Acceptance gates expose `reference_minimum_recovered` and
`reference_minimum_url_verified`, plus completion, latency and known-cost checks.
A reference can remain unresolved or contradicted; the failed gate must stay
visible rather than fabricating a match or silently relaxing the requirement.
`review.json` also contains `minimum_followup`: every human-selected candidate
that the model excludes or that lacks URL confirmation, with its decision, delivery
retention flags and retrieval status. This includes missing verification even where model and human selections agree.

Legacy precision, recall, exact-set agreement and contamination keys remain as
reference-agreement diagnostics. Their conventional false-positive terminology
means additional selections, not demonstrated product errors. They are no longer
the primary acceptance gates. Independently adjudicate additional selections and
sample agreements to measure actual correctness. An empty human reference does
not prove that no matching page exists.

The original 50-product dataset contains 455 candidates and 240 human selections.
It has informed the policy and is development data, not an untouched holdout.
The curated subset also has selection bias and is not independent validation.
Use a new adjudicated dataset to assess generalization; retain disagreements.

## Outputs and operations

| File | Contents |
| --- | --- |
| `predictions.json` | Original fields plus selected IDs and, when enabled, URL-verified IDs |
| `report.json` | Policy hash, contract, settings, decisions, retrieval evidence, metrics, costs and gates |
| `review.json` | Candidate-level human/model disagreements and model reasons |
| `failures.json` | Failed product keys and safe error codes |
| `checkpoints.sqlite` | Successful model decisions and per-attempt audit records |

Input is never overwritten. Failed records retain human IDs with `model_failed`
flags and have no `model_selected_search_results` field. A successful empty model
list is an abstention. Existing prediction fields are removed/replaced, not
used as evidence. Reports are individually atomic writes, not a multi-file transaction.
Exit codes are 0 for completed processing, 1 for incomplete predictions, 2 for
input/configuration/storage errors, and 130 for interruption. A completed run can
still fail quality or verification gates. Unavailable pages are reported separately
from model-processing failures.

Contract 5 adds verification fields and invalidates earlier inference checkpoints.
The cache also includes policy, schema, model, output cap, customer and inference
payload. With verification enabled, URLs are fetched again before looking up model
cache entries; content hashes/excerpts affect cache identity, retrieval timestamps
do not. Cached model results are not proof that a page remains available. Model
aliases can drift; use a pinned model or fresh inference for comparisons.
Never share a checkpoint directory between concurrent CLI processes.

For a controlled policy iteration using the same captured page evidence:

```sh
uv run vertex-match run search_results_ground_truth_train.json --replay-pages runs/new-run/report.json --output-dir runs/policy-iteration --no-cache --max-output-tokens 6500
```

This implies verification mode but makes no URL requests. The dataset hash and
candidate coverage must match, and excerpt budgets are validated. The report marks
retrieval mode `snapshot` and names the source report. It is historical evidence,
not a fresh availability check; fresh end-to-end latency/cost gates are disabled.

This is a single-process baseline. It reads the dataset into memory and keeps
bounded page records; it is not a service for unbounded catalogs. Model and page
limiters are per process, not distributed or token-based. Reports contain product
information; apply your retention and access controls. Browser-only sites, OCR,
large PDFs, product reformulations and ambiguous aliases can require manual review.
The code does not override bot protections, TLS errors or explicit variant conflicts.

## Development

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv export --locked --no-dev --no-emit-project --no-hashes --output-file requirements.txt
```

Tests exercise production routing, validation, retrieval limits and genuine PDF
text extraction without paid model calls. Model semantic accuracy needs live runs
and adjudication; passing these tests does not imply the human minimum is met.

## Score frozen predictions after independent annotation

Use a completed copy of `datasets/unseen_validation_30/review_template.json`:

```sh
uv run vertex-match attach-references runs/unseen-validation-30/predictions.json \
  --reviews datasets/unseen_validation_30/reviews_adjudicated.json \
  --output-dir runs/unseen-validation-30-adjudicated
```

This offline command writes a separate labeled prediction file, evaluation and
annotation audit. It checks exact product/candidate identity and requires complete
adjudication before labeling a product. Pending products remain unlabeled. Model
outputs and URL verdicts are unchanged. Standard scores use model selections;
delivered retention stays separate. See `datasets/unseen_validation_30/README.md`
for the review format and required fields. Unlabeled evaluation explicitly reports
`reference_scores_available: false`.
