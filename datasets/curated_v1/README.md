# Curated product matching development dataset

This is a conservative, provisional subset of `search_results_ground_truth_train.json`.
Unresolved products are quarantined rather than relabeled by an LLM. The original
source file is unchanged. This dataset is not independently adjudicated by Vertex.

## Files

| File | Products | Purpose |
| --- | ---: | --- |
| `cleaned.json` | 30 | All retained records; do not train on this file if using the validation split. |
| `train.json` | 24 | Training examples or prompt-development data. |
| `validation.json` | 6 | Labeled development-validation records. Keep out of training and few-shot prompts. |
| `validation_inputs.json` | 6 | The same validation inputs without reference labels. |
| `quarantine.json` | 20 | Unresolved original records; not training or validation data. |
| `manifest.json` | — | Source/output hashes, split membership, exclusions, reasons, and limitations. |

Training contains 14 products with trusted matches and 10 empty-reference products.
Validation contains 4 products with trusted matches and 2 empty-reference products.
Original product keys, titles, descriptions, candidate IDs/text, and labels are
preserved in every labeled file. Candidate duplicates within a product are retained
because this is a candidate-selection task, not independent candidate-row training.

## What was cleaned

Whole products with documented label contradictions, unresolved variant/formulation
questions, or plausible omitted matches were excluded. Every excluded product and
the evidence candidate IDs are recorded in `manifest.json`. No reference labels were
changed and no model predictions were substituted for labels.

Difficult examples were not removed just because the model got them wrong. Retained
examples include BRK abbreviation resolution, For Tots product-line identification,
product-targeted collections, and product documents. Their policy assumptions are
listed separately in the manifest. Broad page-scope conflicts and specific disputed
formulations remain quarantined. This is risk triage, not proof every retained label
is correct; use Vertex adjudication to resolve the remaining semantic uncertainty.

## Split and leakage protection

The 80/20 split is by whole product, stratified by whether the reference list is empty.
Within each stratum, rank original keys by the SHA-256 of UTF-8 `42` + newline + key;
take the first rounded 20% for validation and keep source order in each output.
Exact membership and the full procedure are in the manifest, so the split can be
reconstructed from the unchanged source and quarantine keys without model outputs.

Checks confirm no train/validation overlap in product keys, normalized product
identity, exact candidate text, or normalized candidate URLs. URL checks remove only
tracking parameters and fragments and preserve other query parameters. These checks
do not establish absence of every possible semantic or brand-family overlap.

## Use

From the repository root:

```sh
uv run vertex-match validate datasets/curated_v1/train.json
uv run vertex-match validate datasets/curated_v1/validation.json
```

After choosing a model, the following performs paid inference and reports metrics:

```sh
uv run vertex-match run datasets/curated_v1/validation.json --output-dir runs/curated-v1-validation --no-cache
```

The existing runner excludes `trusted_search_results` from model requests and uses
those labels only for evaluation. Use `validation_inputs.json` with a separate
inference/training system when its label isolation is not guaranteed. That unlabeled
file intentionally cannot produce reference-based accuracy metrics on its own.
To score saved labeled predictions later:

```sh
uv run vertex-match evaluate runs/curated-v1-validation/predictions.json
```

These are product records in the original schema, not provider-specific fine-tuning
chat-message JSONL. Do not send the reference field to a model as input features.

## Validation limits

This is development validation, not a fresh unbiased holdout: the original source
has already informed the matching policy and this review. Exclusion decisions also
introduce selection bias. Do not present a score on this easier subset as an improved
score on the original 50-product benchmark. Six validation products are too few for
strong generalization claims; collect a separate untouched test set.

Keep quarantined cases and original benchmark scores visible. After Vertex supplies
adjudications, create a new dataset version with a documented label-change ledger;
do not silently overwrite this version. No paid inference was used to create it.
