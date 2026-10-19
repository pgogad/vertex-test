from .schemas import Product, validate_selection

REFERENCE_NOTE = (
    "Precision, recall, false positives, false negatives, and label-based coverage measure "
    "agreement with supplied reference labels, not independently verified product correctness. "
    "Reviewers may use different heuristics; reference-label completeness is unconfirmed. "
    "Unselected reference candidates count as negatives for scoring only. "
    "Original labels are not changed."
)


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def reference_minimum(products: dict[str, Product], predictions: dict[str, list[int]]) -> dict:
    labeled = {key: p for key, p in products.items() if p.trusted_search_results is not None}
    missing = {
        key: sorted(set(p.trusted_search_results) - set(predictions.get(key, [])))
        for key, p in labeled.items()
    }
    total = sum(len(p.trusted_search_results) for p in labeled.values())
    missed = sum(len(ids) for ids in missing.values())
    return {
        "required_candidates": total,
        "recovered_candidates": total - missed,
        "recovery_rate": ratio(total - missed, total),
        "minimum_met": not any(missing.values()) if labeled else None,
        "missing_ids": {key: ids for key, ids in missing.items() if ids},
        "note": "Human selections are a development minimum, not proof of matching correctness. "
        "Failures count as unrecovered; additional selections are not penalized here.",
    }


def evaluate(
    products: dict[str, Product],
    predictions: dict[str, list[int]],
    verified: dict[str, list[int]] | None = None,
) -> dict:
    if set(predictions) - set(products):
        raise ValueError("Predictions contain unknown products")
    for key, selected in predictions.items():
        validate_selection(selected, products[key])
    if verified is not None:
        for key, ids in verified.items():
            if key not in predictions:
                raise ValueError("URL-verified IDs require a completed prediction")
            validate_selection(ids, products[key])
            if set(ids) - set(predictions[key]):
                raise ValueError("URL-verified IDs must be a subset of selected IDs")
    labeled = {
        key: item for key, item in products.items() if item.trusted_search_results is not None
    }
    completed = {key: item for key, item in labeled.items() if key in predictions}
    true_positives = false_positives = false_negatives = 0
    useful = clean_useful = contaminated = exact = empty_false_selections = 0
    errors = []
    for key, product in completed.items():
        expected = set(product.trusted_search_results)
        selected = set(predictions[key])
        correct = expected & selected
        extra = selected - expected
        missed = expected - selected
        true_positives += len(correct)
        false_positives += len(extra)
        false_negatives += len(missed)
        useful += bool(correct)
        clean_useful += bool(correct) and not extra
        contaminated += bool(extra)
        exact += selected == expected
        empty_false_selections += not expected and bool(selected)
        if extra or missed:
            errors.append(
                {"product_key": key, "false_positives": sorted(extra), "missed": sorted(missed)}
            )
    matchable = sum(bool(item.trusted_search_results) for item in labeled.values())
    empty_completed = sum(item.trusted_search_results == [] for item in completed.values())
    return {
        "evaluation_basis": "supplied_reference_labels",
        "reference_minimum": reference_minimum(products, predictions),
        "url_verified_reference_minimum": (
            reference_minimum(products, verified) if verified is not None else None
        ),
        "interpretation": REFERENCE_NOTE,
        "total_products": len(products),
        "completed_products": len(predictions),
        "missing_predictions": len(products) - len(predictions),
        "labeled_products": len(labeled),
        "evaluated_products": len(completed),
        "raw_coverage": ratio(sum(bool(ids) for ids in predictions.values()), len(products)),
        "abstention_rate_on_completed": ratio(
            sum(not ids for ids in predictions.values()), len(predictions)
        ),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives_on_completed": false_negatives,
        "precision": ratio(true_positives, true_positives + false_positives),
        "recall_on_completed": ratio(true_positives, true_positives + false_negatives),
        "useful_coverage": ratio(useful, matchable),
        "matchable_products": matchable,
        "correctly_covered_products": useful,
        "cleanly_covered_products": clean_useful,
        "clean_useful_coverage": ratio(clean_useful, matchable),
        "contaminated_product_rate_on_completed": ratio(contaminated, len(completed)),
        "exact_set_agreement_on_completed": ratio(exact, len(completed)),
        "empty_reference_products_evaluated": empty_completed,
        "empty_reference_false_selections": empty_false_selections,
        "empty_reference_false_selection_rate": ratio(empty_false_selections, empty_completed),
        "errors": errors,
    }


def acceptance_gates(metrics: dict, elapsed: float, cost: float | None, fresh: bool) -> dict:
    complete = metrics["missing_predictions"] == 0
    fully_labeled = metrics["labeled_products"] == metrics["total_products"]
    return {
        "complete": complete,
        "reference_minimum_recovered": (
            metrics["reference_minimum"]["minimum_met"] if fully_labeled else None
        ),
        "reference_minimum_url_verified": (
            metrics["url_verified_reference_minimum"]["minimum_met"]
            if fully_labeled and metrics["url_verified_reference_minimum"] is not None
            else None
        ),
        "fresh_50_products_within_120_seconds": (
            elapsed <= 120 if complete and fresh and metrics["total_products"] == 50 else None
        ),
        "fresh_cost_at_most_5_usd_per_1000_products": (
            cost / metrics["total_products"] * 1000 <= 5
            if complete and fresh and cost is not None and metrics["total_products"]
            else None
        ),
    }


def evaluate_saved_predictions(raw: dict, products: dict[str, Product]) -> dict:
    """Score saved model outputs, preserving the distinction from delivered unions."""
    predictions = {}
    verified = {}
    for key, value in raw.items():
        field = (
            "model_selected_search_results"
            if "model_selected_search_results" in value or "selection_review" in value
            else "llm_trusted_search_results"
        )
        if field in value:
            predictions[key] = value[field]
        if "url_verified_search_results" in value:
            verified[key] = value["url_verified_search_results"]
    metrics = evaluate(products, predictions, verified or None)
    metrics["reference_scores_available"] = bool(metrics["labeled_products"])
    if any("selection_review" in value for value in raw.values()):
        delivered = {key: value.get("llm_trusted_search_results", []) for key, value in raw.items()}
        for key, ids in delivered.items():
            validate_selection(ids, products[key])
            if not set(predictions.get(key, [])).issubset(ids):
                raise ValueError("Model selections must be included in delivered selections")
        metrics["delivery_reference_minimum"] = reference_minimum(products, delivered)
    return metrics
