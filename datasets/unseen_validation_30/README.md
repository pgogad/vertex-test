# Unseen product validation set

30 new product identities and 300 web-search candidates, collected on October 19,
2026. This is a frozen challenge set for this project's matching policy. It has
zero normalized product-title or candidate-URL overlap with the original 50-product
dataset, which also supplies the existing `curated_v1` split. No matching inference
was run on these inputs during construction.

There are eight beverages, eight foods, eight personal-care products, four
household products, and two equipment products. Cases include different flavors,
regular versus sugar-free, 9g versus 6g fiber, lotion versus cream, SPF, liquid versus
pods, replacement-filter types, model numbers, pack sizes, recipes, PDFs, broad
catalog pages, and international listings.

| File | Use |
| --- | --- |
| `validation_inputs.json` | Production-compatible inputs: 30 products, 10 candidates each, no reference labels. |
| `review_template.json` | Separate blank candidate judgments, verification evidence and reviewer fields. |
| `provenance.json` | Search queries, returned titles, source URLs and short excerpts. |
| `product_queries.json`, `supplement_queries.json` | Exact search-query inventory. |
| `search_provenance/` | Saved search-tool responses for auditing the extraction. |
| `manifest.json` | Collection method, limitations, category membership and file hashes. |

## Training-format copy with provisional references

`../../search_results_ground_truth_unseen.json` has the same four fields per
product as `search_results_ground_truth_train.json`: `product_title`,
`product_description`, `search_results`, and `trusted_search_results`.
It contains 30 products, 300 unchanged candidates, and 143 provisional trusted IDs.
One product has an empty reference list because all retrieved candidates describe
reduced-sodium variants of the requested standard soup.

These positive references were curated by the assistant from the retrieved source
evidence, with targeted live checks for ambiguous URLs. They are not human labels
or an independent golden benchmark. `provisional_reference_review.json` records
all selections, product-level rationale, and 42 unresolved candidate IDs. Omitted
IDs are not all verified negatives. Product-line and default-formulation assumptions
are documented there. Original unlabeled inputs and the blank human-review template
are preserved; no review has been marked as human adjudicated.

```sh
uv run vertex-match validate search_results_ground_truth_unseen.json
uv run vertex-match run search_results_ground_truth_unseen.json \
  --verify-links --max-output-tokens 6500 \
  --output-dir runs/unseen-validation-30-with-references
uv run vertex-match evaluate runs/unseen-validation-30-with-references/predictions.json
```

Supplied trusted IDs are retained in the delivered union by application logic.
Evaluate model quality using `model_selected_search_results`; 100% delivered
retention does not establish matching accuracy. References are excluded from model
messages. Replace these provisional references with Ryan's/human-reviewed IDs when
available. Use the unlabeled-input workflow below for independent human adjudication.

## Independent review

Freeze the policy before running this holdout. Have reviewers inspect the inputs
and source URLs without viewing model predictions. In a separate copy of
`review_template.json`, assess every candidate as `match`, `not_match`, or `uncertain`.
Record URL verification separately as `confirmed`, `contradicted`, `inconclusive`,
or `unavailable`, with a short literal evidence quote where appropriate. Different
pack sizes alone need not mean a different product. Regional formulations and
incomplete product identity require explicit judgment.

Use a second reviewer to resolve disagreements and document the adjudication.
Do not turn unresolved cases or inaccessible pages into negative labels. Once a
product's judgments are complete, create a separate labeled copy of the inputs and
set `trusted_search_results` to the reviewed match IDs. Use `[]` only when all
candidates have been reviewed and none qualify. Leave incomplete products unlabeled.
The review template is an annotation artifact; the existing evaluator does not
consume it directly.

## Run the frozen policy

From the repository root:

```sh
uv run vertex-match validate datasets/unseen_validation_30/validation_inputs.json
uv run vertex-match run datasets/unseen_validation_30/validation_inputs.json \
  --verify-links --max-output-tokens 6500 --output-dir runs/unseen-validation-30
uv run vertex-match evaluate runs/unseen-validation-30/predictions.json
```

Until human references are added, evaluation reports processing completeness with
`reference_scores_available: false`; agreement and recovery rates are unavailable.

After independent review, save a completed copy as `reviews_adjudicated.json` and
attach it to the frozen predictions without rerunning inference:

```sh
uv run vertex-match attach-references runs/unseen-validation-30/predictions.json \
  --reviews datasets/unseen_validation_30/reviews_adjudicated.json \
  --output-dir runs/unseen-validation-30-adjudicated
uv run vertex-match evaluate runs/unseen-validation-30-adjudicated/predictions.json
```

The command writes `predictions.json`, `evaluation.json`, and `annotation_report.json`
to a fresh directory. It makes no API calls and protects source files and existing
output artifacts. It checks exact product identities, candidate IDs and text.
A product requires `adjudicated: true`, a nonempty `reviewer`, a `reviewed_on` date
in `YYYY-MM-DD` format, and `match`/`not_match` assessments for every candidate.
Uncertain or incomplete adjudications are rejected; pending products remain
unlabeled. Any previous reference labels in the copy are replaced by this review
batch, so stale labels cannot silently influence the scores.

Only `trusted_search_results` is attached. Saved model selections, delivered IDs,
verification verdicts and delivery review flags remain frozen as originally emitted.
Consequently, original delivery flags may describe an earlier human baseline; use
the separate adjudicated reviews to interpret the new references. The audit records
source paths, SHA-256 hashes and attached/pending product keys.

Scores use `model_selected_search_results`. A missing model result is a processing
failure even if its delivered list retains human IDs. Delivered-list retention is
reported separately and is not a model-quality score. Human URL judgments remain
in the review file; they do not overwrite the model's verification claims.

## Sampling and evidence limits

Products are assistant-curated identities, not a random production sample. Most
input titles are clearer than the abbreviated training titles. Several search
queries deliberately include a competing variant. For ten products, the first
three supplementary result blocks precede the main query's results; extraction
then takes the first ten distinct eligible URLs. No prediction-driven filtering
or label-based selection was used. Distinct international and retailer URLs may
repeat the same product content. Excerpts are at most 40 words from returned
search metadata (or the beginning of returned text if that metadata is empty).

Search retrieval does not guarantee every live URL is accessible or substantively
verifies a match. The production verification route performs that separate check.
"Unseen" means absent from this project's existing product dataset; it makes no
claim about foundation-model training data. If this set is used to tune policy,
it becomes development data and a new holdout is needed for independent validation.
