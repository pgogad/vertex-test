import copy
import json
import sys

import pytest

from vertex_matcher.annotations import attach_references
from vertex_matcher.cli import main
from vertex_matcher.data import write_json
from vertex_matcher.evaluation import evaluate_saved_predictions


def frozen(product):
    return {
        "p": {
            **product.model_dump(),
            "model_selected_search_results": [],
            "llm_trusted_search_results": [1],
            "selection_review": [],
            "url_verified_search_results": [],
        }
    }


def review(product):
    return {
        "p": {
            "product_title": product.product_title,
            "product_description": product.product_description,
            "reviewer": "Independent reviewer",
            "reviewed_on": "2026-10-19",
            "adjudicated": True,
            "candidate_reviews": {
                key: {
                    "candidate_text": text,
                    "assessment": "match" if key == "1" else "not_match",
                    "url_verification": "unavailable",
                }
                for key, text in product.search_results.items()
            },
        }
    }


def test_attach_preserves_frozen_fields_and_scores_model_only(product):
    raw = frozen(product)
    before = copy.deepcopy(raw)
    output, audit = attach_references(raw, {"p": product}, review(product))
    assert raw == before
    assert output == before
    assert audit["attached_products"] == ["p"]
    metrics = evaluate_saved_predictions(output, {"p": product})
    assert metrics["reference_scores_available"]
    assert metrics["reference_minimum"]["recovered_candidates"] == 0
    assert metrics["delivery_reference_minimum"]["recovered_candidates"] == 1


@pytest.mark.parametrize("assessment", [None, "uncertain"])
def test_adjudication_rejects_unresolved_candidates(product, assessment):
    annotations = review(product)
    annotations["p"]["candidate_reviews"]["1"]["assessment"] = assessment
    with pytest.raises(ValueError, match="incomplete or uncertain"):
        attach_references(frozen(product), {"p": product}, annotations)


def test_pending_reviews_do_not_become_negative_or_stale_labels(product):
    annotations = review(product)
    annotations["p"]["adjudicated"] = False
    output, audit = attach_references(frozen(product), {"p": product}, annotations)
    assert "trusted_search_results" not in output["p"]
    assert audit["pending_products"] == ["p"]
    unlabeled = product.model_copy(update={"trusted_search_results": None})
    metrics = evaluate_saved_predictions(output, {"p": unlabeled})
    assert metrics["completed_products"] == 1
    assert not metrics["reference_scores_available"]
    assert metrics["precision"] is None
    assert metrics["recall_on_completed"] is None
    assert metrics["reference_minimum"]["recovery_rate"] is None


@pytest.mark.parametrize("change", ["text", "title", "ids", "reviewer", "date", "boolean"])
def test_mismatched_or_invalid_annotations_rejected(product, change):
    annotations = review(product)
    r = annotations["p"]
    if change == "text":
        r["candidate_reviews"]["1"]["candidate_text"] += " changed"
    elif change == "title":
        r["product_title"] += " changed"
    elif change == "ids":
        del r["candidate_reviews"]["2"]
    elif change == "reviewer":
        r["reviewer"] = " "
    elif change == "date":
        r["reviewed_on"] = "yesterday"
    else:
        r["adjudicated"] = "true"
    with pytest.raises(ValueError):
        attach_references(frozen(product), {"p": product}, annotations)


def test_completed_all_negative_review_is_empty_reference(product):
    annotations = review(product)
    for candidate in annotations["p"]["candidate_reviews"].values():
        candidate["assessment"] = "not_match"
    output, _ = attach_references(frozen(product), {"p": product}, annotations)
    assert output["p"]["trusted_search_results"] == []


def test_delivered_union_cannot_replace_missing_model_field(product):
    raw = frozen(product)
    del raw["p"]["model_selected_search_results"]
    del raw["p"]["selection_review"]
    with pytest.raises(ValueError, match="frozen model output"):
        attach_references(raw, {"p": product}, review(product))


def test_cli_attach_is_offline_protects_sources_and_evaluates_copy(
    tmp_path, product, monkeypatch, capsys
):
    predictions = tmp_path / "frozen.json"
    reviews = tmp_path / "reviews.json"
    write_json(predictions, frozen(product))
    write_json(reviews, review(product))
    before = predictions.read_bytes()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("vertex_matcher.cli.OpenAIProvider", lambda _: pytest.fail("API invoked"))
    out = tmp_path / "annotated"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vertex-match",
            "attach-references",
            str(predictions),
            "--reviews",
            str(reviews),
            "--output-dir",
            str(out),
        ],
    )
    assert main() == 0
    assert predictions.read_bytes() == before
    metrics = json.loads((out / "evaluation.json").read_text())
    assert metrics["reference_minimum"]["recovered_candidates"] == 0
    monkeypatch.setattr(sys, "argv", ["vertex-match", "evaluate", str(out / "predictions.json")])
    capsys.readouterr()
    assert main() == 0
    assert json.loads(capsys.readouterr().out) == metrics
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vertex-match",
            "attach-references",
            str(predictions),
            "--reviews",
            str(reviews),
            "--output-dir",
            str(out),
        ],
    )
    assert main() == 2


@pytest.mark.parametrize("field", ["assessment", "url_verification"])
def test_malformed_review_enums_are_validation_errors(product, field):
    annotations = review(product)
    annotations["p"]["candidate_reviews"]["1"][field] = {}
    with pytest.raises(ValueError, match="invalid"):
        attach_references(frozen(product), {"p": product}, annotations)
