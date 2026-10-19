# US-market unseen-product dataset

Derived from `../unseen_validation_30` on October 19, 2026, following a request to
restrict the unseen set to US-market products and sources. This is a revised
challenge/development dataset: the original set has already been run and reviewed.
It is not a fresh independent holdout or a claim about foundation-model training.
The original mixed-market set and saved runs are preserved.

## Scope

Same 30 product identities; 244 candidates.
Retained 240 original candidates, removed 60,
and added 4 researched US retailer listings for poppi,
Fever-Tree, KIND, and Tide. Each target description explicitly says United States.
Counts vary from 3 to 10 per product. IDs of retained candidates are unchanged;
new listings use ID 11, so IDs can have gaps. The matcher supports this format.

US-market means the intended sales/reference market, not US manufacture. Sources
are US storefronts, manufacturer US locales, or US regulatory/reference records.
Non-US locales and sources without an established US market were removed. Test,
staging, and alternate deployments were also removed in favor of public sources.
Spanish-language pages are not automatically foreign: Neutrogena's Spanish
subdomain explicitly identifies a US audience; Banza's Spanish page is on the
same US storefront. Flavor, formulation, SPF, model, and physical-form mismatches
among US products remain challenge negatives. No filtering used model selections
or reference agreement, and no labels were changed to agree with predictions.

## Files and labels

- `../../search_results_ground_truth_unseen_us.json`: runnable labeled copy in the
  original four-field input format. Contains 118 provisional positive IDs.
- `validation_inputs.json`: identical inputs with no reference labels.
- `provisional_reference_review.json`: inherited/added provisional classifications;
  34 candidates remain unresolved and 92 are excluded variants.
- `review_template.json`: blank independent human-review template. No review is adjudicated.
- `provenance.json`: original candidate lineage, source URLs, market rationale,
  snippets, and new collection queries.
- `market_audit.json`: every retained, removed, and added candidate with reasons.
- `manifest.json`: counts, input hashes, overlap checks, and limitations.

Original title/snippet text is unchanged for retained candidates; inherited
provisional labels remain provisional. New short snippets are source excerpts,
except the Tide snippet is a concise paraphrase. New candidate evidence was
reviewed from US H-E-B, Walmart, and Target pages. Unresolved candidates must not
be interpreted as independently established negatives. US scope alone does not
prove page availability, product match, or formulation equivalence. No paid model
calls were made to construct this revision.

## Run and evaluate

```sh
uv run vertex-match validate search_results_ground_truth_unseen_us.json
uv run vertex-match run search_results_ground_truth_unseen_us.json \
  --output-dir runs/unseen-us-run --verify-links --no-cache --max-output-tokens 6500
uv run vertex-match evaluate runs/unseen-us-run/predictions.json
```

Run from the repository root. Use the new output directory to preserve earlier
results; old reports cannot be replayed directly because input hashes and
candidate coverage differ. Standard metrics score model-only selections. Delivered
human-reference retention is automatic and is not an accuracy score. Scores from
this revised sample are not directly comparable to the original mixed-market run.
For independent human adjudication, use `attach-references` with a completed copy
of the new review template, following the original dataset README workflow.

## New source evidence

- [US poppi listing](https://www.heb.com/product-detail/poppi-prebiotic-soda-4-pk-cans-strawberry-lemon/9414018)
- [US Fever-Tree listing](https://www.walmart.com/ip/639350172)
- [US KIND listing](https://www.walmart.com/ip/139297127)
- [US Tide listing](https://www.target.com/p/-/A-13918061)
