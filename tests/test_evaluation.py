import pytest

from vertex_matcher.evaluation import acceptance_gates, evaluate
from vertex_matcher.schemas import Product


def test_metrics(product):
    empty = Product.model_validate({**product.model_dump(), "trusted_search_results": []})
    products = {"matched": product, "empty": empty, "failed": product}
    metrics = evaluate(products, {"matched": [1, 2], "empty": [2]})
    assert metrics["true_positives"] == 1
    assert metrics["evaluation_basis"] == "supplied_reference_labels"
    assert "completeness is unconfirmed" in metrics["interpretation"]
    assert metrics["false_positives"] == 2
    assert metrics["precision"] == pytest.approx(1 / 3)
    assert metrics["recall_on_completed"] == 1
    assert metrics["useful_coverage"] == 0.5
    assert metrics["raw_coverage"] == pytest.approx(2 / 3)
    assert metrics["missing_predictions"] == 1
    assert metrics["contaminated_product_rate_on_completed"] == 1
    assert metrics["empty_reference_false_selection_rate"] == 1
    gates = acceptance_gates(metrics, 1, 0.01, True)
    assert gates["complete"] is False
    assert gates["reference_minimum_recovered"] is False


def test_abstention_is_not_perfect_precision(product):
    metrics = evaluate({"product": product}, {"product": []})
    assert metrics["precision"] is None
    assert metrics["recall_on_completed"] == 0
    assert metrics["useful_coverage"] == 0
    assert metrics["abstention_rate_on_completed"] == 1


def test_abstention_excludes_failures_and_includes_unlabeled_products(product):
    unlabeled = Product.model_validate({**product.model_dump(), "trusted_search_results": None})
    metrics = evaluate(
        {"selected": product, "abstained": unlabeled, "failed": product},
        {"selected": [1], "abstained": []},
    )
    assert metrics["abstention_rate_on_completed"] == 0.5
    assert metrics["missing_predictions"] == 1
    assert evaluate({"failed": product}, {})["abstention_rate_on_completed"] is None


def test_unlabeled_metrics(product):
    product.trusted_search_results = None
    metrics = evaluate({"product": product}, {"product": [1]})
    assert metrics["precision"] is None
    assert metrics["useful_coverage"] is None
    assert metrics["raw_coverage"] == 1
    assert metrics["clean_useful_coverage"] is None


def test_clean_coverage_penalizes_contamination_and_missing_predictions(product):
    products = {"clean": product, "dirty": product, "failed": product}
    metrics = evaluate(products, {"clean": [1], "dirty": [1, 2]})
    assert metrics["correctly_covered_products"] == 2
    assert metrics["cleanly_covered_products"] == 1
    assert metrics["clean_useful_coverage"] == pytest.approx(1 / 3)


@pytest.mark.parametrize("selected", [[99], [1, 1], [True], ["1"]])
def test_bad_predictions(product, selected):
    with pytest.raises(ValueError):
        evaluate({"product": product}, {"product": selected})


def test_gates_require_fresh_run_and_known_cost(product):
    products = {str(index): product for index in range(50)}
    metrics = evaluate(products, {key: [1] for key in products})
    fresh = acceptance_gates(metrics, 119, 0.25, True)
    assert fresh["reference_minimum_recovered"] is True
    assert fresh["fresh_50_products_within_120_seconds"] is True
    assert fresh["fresh_cost_at_most_5_usd_per_1000_products"] is True
    cached = acceptance_gates(metrics, 1, None, False)
    assert cached["fresh_50_products_within_120_seconds"] is None
    assert cached["fresh_cost_at_most_5_usd_per_1000_products"] is None
