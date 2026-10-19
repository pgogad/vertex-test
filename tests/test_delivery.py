import json

from vertex_matcher.cli import main
from vertex_matcher.data import write_json
from vertex_matcher.delivery import build_delivery
from vertex_matcher.schemas import Selection


def test_conflicting_human_selection_is_retained_without_confirmation(product, response_text):
    body = json.loads(response_text)
    body["decisions"][0].update(
        decision="not_match", variant_status="conflict", verification_status="contradicted"
    )
    selection = Selection.model_validate(body)
    delivery = build_delivery(product, selection)
    assert delivery["llm_trusted_search_results"] == [1]
    assert delivery["human_retained_search_results"] == [1]
    assert selection.selected_ids() == selection.verified_ids() == []
    flag = delivery["selection_review"][0]
    assert flag["needs_review"]
    assert flag["verification_status"] == "contradicted"
    assert "url_contradiction" in flag["flags"]


def test_unlabeled_delivery_uses_model_only(product, response_text):
    product.trusted_search_results = None
    selection = Selection.model_validate_json(response_text)
    assert (
        build_delivery(product, selection)["llm_trusted_search_results"] == selection.selected_ids()
    )


def test_evaluate_keeps_retention_separate(tmp_path, product, monkeypatch, capsys):
    path = tmp_path / "predictions.json"
    write_json(
        path,
        {
            "p": {
                **product.model_dump(),
                "llm_trusted_search_results": [1],
                "model_selected_search_results": [],
                "selection_review": [],
            }
        },
    )
    monkeypatch.setattr("sys.argv", ["vertex-match", "evaluate", str(path)])
    assert main() == 0
    metrics = json.loads(capsys.readouterr().out)
    assert metrics["reference_minimum"]["recovered_candidates"] == 0
    assert metrics["delivery_reference_minimum"]["recovered_candidates"] == 1
