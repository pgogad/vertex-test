import json

import pytest

from vertex_matcher.matcher import cache_key, parse_selection, payload_for
from vertex_matcher.review import build_review
from vertex_matcher.schemas import Settings


@pytest.mark.parametrize("identity", ["generic", "uncertain"])
@pytest.mark.parametrize("source", ["product_page", "recipe", "category_page", "other"])
def test_identity_gate_overrides_model_match(product, response_text, identity, source):
    body = json.loads(response_text)
    body["identity_status"] = identity
    body["identity_evidence"] = ""
    body["decisions"][0]["source_type"] = source
    selection = parse_selection(json.dumps(body), product)
    assert selection.selected_ids() == []
    assert selection.blocked_matches() == [{"candidate_id": 1, "reasons": ["ambiguous_identity"]}]


@pytest.mark.parametrize("evidence", ["", "Invented Brand", "example.test"])
def test_specific_identity_requires_input_evidence(product, response_text, evidence):
    body = json.loads(response_text)
    body["identity_evidence"] = evidence
    with pytest.raises(ValueError, match="exact quote"):
        parse_selection(json.dumps(body), product)


def test_distinctive_model_does_not_require_brand(product, response_text):
    product.product_title = "ZX-400 portable device"
    body = json.loads(response_text)
    body["identity_evidence"] = "ZX-400"
    assert parse_selection(json.dumps(body), product).selected_ids() == [1]


@pytest.mark.parametrize("variant", ["conflict", "uncertain"])
@pytest.mark.parametrize("source", ["product_page", "recipe", "category_page", "other"])
def test_variant_gate_overrides_model_match(product, response_text, variant, source):
    body = json.loads(response_text)
    body["decisions"][0]["variant_status"] = variant
    body["decisions"][0]["source_type"] = source
    assert parse_selection(json.dumps(body), product).selected_ids() == []


@pytest.mark.parametrize(
    "source",
    ["product_page", "product_document", "product_article", "recipe", "category_page", "other"],
)
def test_source_type_is_metadata_not_a_selection_gate(product, response_text, source):
    body = json.loads(response_text)
    body["decisions"][0]["source_type"] = source
    selection = parse_selection(json.dumps(body), product)
    assert selection.selected_ids() == [1]
    assert selection.decisions[0].source_type == source
    assert selection.blocked_matches() == []


@pytest.mark.parametrize("decision", ["not_match", "insufficient_evidence"])
def test_page_type_does_not_override_nonmatching_decision(product, response_text, decision):
    body = json.loads(response_text)
    body["decisions"][0]["source_type"] = "category_page"
    body["decisions"][0]["decision"] = decision
    assert parse_selection(json.dumps(body), product).selected_ids() == []


@pytest.mark.parametrize("package", ["", " 6 pack", " 24 pack", " 2 litre bottle"])
def test_application_gate_does_not_compare_package_sizes(product, response_text, package):
    product.product_title = "Example orange soda" + package
    assert parse_selection(response_text, product).selected_ids() == [1]


def test_identical_text_requires_consistent_variant_status(product, response_text):
    product.search_results["2"] = product.search_results["1"]
    body = json.loads(response_text)
    body["decisions"][1]["decision"] = "match"
    with pytest.raises(ValueError, match="conflicting"):
        parse_selection(json.dumps(body), product)


def test_review_preserves_labels_and_flags_identical_text_conflicts(product, response_text):
    product.search_results["2"] = product.search_results["1"]
    body = json.loads(response_text)
    body["decisions"][1]["decision"] = "match"
    body["decisions"][1]["variant_status"] = "compatible"
    selection = parse_selection(json.dumps(body), product)
    review = build_review({"product": product}, {"product": [1, 2]}, {"product": selection})
    assert product.trusted_search_results == [1]
    assert review["error_groups"] == {"contradictory_labels": 1}
    example = review["examples"][0]
    assert example["candidate_id"] == 2
    assert example["candidate_text"] == product.search_results["2"]
    assert example["reference_selected"] is False
    assert example["actually_selected"] is True


def test_review_tags_policy_disagreement_without_relabeling(product, response_text):
    body = json.loads(response_text)
    body["decisions"][0]["source_type"] = "recipe"
    body["decisions"][0]["variant_status"] = "uncertain"
    selection = parse_selection(json.dumps(body), product)
    review = build_review({"product": product}, {"product": []}, {"product": selection})
    assert review["error_groups"] == {"insufficient_variant_evidence": 1}
    assert review["label_conflicts"] == []
    assert review["examples"][0]["reference_selected"] is True
    assert review["examples"][0]["error_type"] == "false_negative"
    assert review["examples"][0]["model_decision"]["source_type"] == "recipe"
    assert review["evaluation_basis"] == "supplied_reference_labels"
    assert product.trusted_search_results == [1]


def test_contract_change_invalidates_old_cache(product, monkeypatch):
    settings = Settings(model="test-model")
    current_key = cache_key(payload_for(product), settings)
    monkeypatch.setattr("vertex_matcher.matcher.CONTRACT_VERSION", "3")
    assert cache_key(payload_for(product), settings) != current_key
