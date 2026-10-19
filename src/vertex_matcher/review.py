from collections import Counter

from .data import dataset_summary
from .evaluation import REFERENCE_NOTE
from .schemas import Product, Selection


def build_review(
    products: dict[str, Product],
    predictions: dict[str, list[int]],
    selections: dict[str, Selection],
    verified: dict[str, list[int]] | None = None,
    retrieval: dict | None = None,
) -> dict:
    conflicts = dataset_summary(products)["identical_text_label_conflicts"]
    conflicting_ids = {
        (group["product_key"], candidate_id)
        for group in conflicts
        for candidate_id in group["candidate_ids"]
    }
    groups = Counter()
    examples = []
    for key, selection in selections.items():
        product = products[key]
        if product.trusted_search_results is None:
            continue
        expected = set(product.trusted_search_results)
        selected = set(predictions[key])
        for decision in selection.decisions:
            candidate_id = decision.candidate_id
            if (candidate_id in expected) == (candidate_id in selected):
                continue
            tags = selection.exclusion_reasons(decision)
            if (key, candidate_id) in conflicting_ids:
                tags.append("contradictory_labels")
            if not tags:
                tags.append("other_identity_or_label_disagreement")
            groups.update(tags)
            examples.append(
                {
                    "product_key": key,
                    "product_title": product.product_title,
                    "product_description": product.product_description,
                    "identity_status": selection.identity_status,
                    "identity_evidence": selection.identity_evidence,
                    "candidate_id": candidate_id,
                    "candidate_text": product.search_results[str(candidate_id)],
                    "reference_selected": candidate_id in expected,
                    "delivered_selected": candidate_id in expected | selected,
                    "human_retained": candidate_id in expected - selected,
                    "actually_selected": candidate_id in selected,
                    "error_type": "false_positive"
                    if candidate_id in selected
                    else "false_negative",
                    "model_decision": decision.model_dump(),
                    "review_tags": tags,
                }
            )
    minimum_followup = []
    for key, product in products.items():
        decision_by_id = (
            {d.candidate_id: d.model_dump() for d in selections[key].decisions}
            if key in selections
            else {}
        )
        for candidate_id in product.trusted_search_results or []:
            selected = candidate_id in predictions.get(key, [])
            confirmed = verified is not None and candidate_id in verified.get(key, [])
            if selected and (verified is None or confirmed):
                continue
            page = (retrieval or {}).get("products", {}).get(key, {}).get(str(candidate_id), {})
            minimum_followup.append(
                {
                    "product_key": key,
                    "candidate_id": candidate_id,
                    "selected": selected,
                    "delivered_selected": True,
                    "human_retained": not selected,
                    "url_confirmed": confirmed if verified is not None else None,
                    "candidate_text": product.search_results[str(candidate_id)],
                    "model_decision": decision_by_id.get(candidate_id),
                    "retrieval": {
                        field: page[field]
                        for field in ("status", "final_url", "error", "reason", "truncated")
                        if field in page
                    },
                }
            )
    return {
        "evaluation_basis": "supplied_reference_labels",
        "interpretation": REFERENCE_NOTE,
        "note": (
            "Tags describe model checks, not proven root causes. Review disagreements manually. "
            "Only identical text with opposing reference labels is automatically flagged as a "
            "label conflict. Reference labels and benchmark scores are never changed. "
            "An example can have multiple tags, so group counts can overlap."
        ),
        "error_groups": dict(groups),
        "label_conflicts": conflicts,
        "examples": examples,
        "minimum_followup": minimum_followup,
    }
