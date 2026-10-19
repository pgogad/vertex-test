import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vertex_matcher.data import dataset_summary, load_dataset, model_input, write_json
from vertex_matcher.matcher import cache_key, parse_selection, payload_for
from vertex_matcher.schemas import Product, Settings


def test_round_trip_preserves_raw_data(tmp_path, product):
    raw = {" product key ": {**product.model_dump(), "custom_field": "keep"}}
    path = tmp_path / "products.json"
    write_json(path, raw)
    loaded, products = load_dataset(path)
    assert loaded == raw
    assert products[" product key "].product_title == product.product_title


@pytest.mark.parametrize("content", ["[]", "{}", '{"key": 1, "key": 2}', '{"key": NaN}'])
def test_invalid_json_shape(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_dataset(path)


@pytest.mark.parametrize("candidate_id", ["0", "01", "-1", "abc", "١"])
def test_noncanonical_ids_rejected(candidate_id):
    with pytest.raises(ValidationError):
        Product(product_title="", product_description="", search_results={candidate_id: "text"})


@pytest.mark.parametrize("labels", [[99], [1, 1], [True], ["1"]])
def test_bad_labels_rejected(product, labels):
    with pytest.raises(ValidationError):
        Product.model_validate({**product.model_dump(), "trusted_search_results": labels})


def test_input_excludes_labels_and_extra_fields(product):
    enriched = Product.model_validate(
        {
            **product.model_dump(),
            "llm_trusted_search_results": [2],
            "secret_note": "never_send_this",
        }
    )
    payload = model_input(enriched)
    assert set(payload) == {"product_title", "product_description", "search_results"}
    assert "trusted_search_results" not in payload_for(enriched)
    assert "never_send_this" not in payload_for(enriched)


def test_cache_excludes_labels_but_includes_model_and_scope(product):
    settings = Settings(model="test-model")
    initial = cache_key(payload_for(product), settings)
    product.trusted_search_results = [2]
    assert cache_key(payload_for(product), settings) == initial
    assert cache_key(payload_for(product), Settings(model="other-model")) != initial
    assert (
        cache_key(payload_for(product), Settings(model="test-model", customer="other")) != initial
    )
    product.search_results["1"] += " changed"
    assert cache_key(payload_for(product), settings) != initial


def test_selection_validation(product, response_text):
    assert parse_selection(response_text, product).selected_ids() == [1]
    body = json.loads(response_text)
    body["decisions"][1]["candidate_id"] = 1
    with pytest.raises(ValueError):
        parse_selection(json.dumps(body), product)
    body["decisions"].pop()
    with pytest.raises(ValueError):
        parse_selection(json.dumps(body), product)
    body["decisions"][0]["candidate_id"] = 99
    with pytest.raises(ValueError):
        parse_selection(json.dumps(body), product)


def test_duplicate_evidence_requires_consistency(product, response_text):
    product.search_results["2"] = product.search_results["1"]
    with pytest.raises(ValueError, match="conflicting"):
        parse_selection(response_text, product)
    assert len(dataset_summary({"product": product})["identical_text_label_conflicts"]) == 1


def test_training_file_profile():
    root = Path(__file__).resolve().parents[1]
    _, products = load_dataset(root / "search_results_ground_truth_train.json")
    summary = dataset_summary(products)
    assert summary["products"] == 50
    assert summary["candidates"] == 455
    assert summary["approved_candidates"] == 240
    assert summary["empty_reference_products"] == 12
    assert summary["empty_descriptions"] == 23
    assert summary["empty_titles"] == 1
    assert len(summary["identical_text_label_conflicts"]) == 1
