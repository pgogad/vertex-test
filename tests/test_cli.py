import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from vertex_matcher.cli import build_parser, main
from vertex_matcher.data import write_json
from vertex_matcher.provider import Reply


@pytest.mark.parametrize(
    "options",
    [
        ["--verify-pages"],
    ],
)
def test_removed_page_retrieval_flags_are_rejected(options):
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["run", "input.json", *options])
    assert error.value.code == 2


@pytest.mark.parametrize("launch_mode", ["script", "module"])
def test_python_entrypoints(launch_mode):
    root = Path(__file__).resolve().parents[1]
    entrypoint = (
        [str(root / "src/vertex_matcher/cli.py")]
        if launch_mode == "script"
        else ["-m", "vertex_matcher.cli"]
    )
    result = subprocess.run(
        [
            sys.executable,
            *entrypoint,
            "validate",
            str(root / "search_results_ground_truth_train.json"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["products"] == 50


def test_direct_script_requires_command():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "src/vertex_matcher/cli.py")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "command" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("shell_values", [False, True])
def test_dotenv_loading_and_precedence(tmp_path, product, monkeypatch, shell_values):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=file-test-key\nOPENAI_MODEL=file-test-model\n")
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    if shell_values:
        monkeypatch.setenv("OPENAI_API_KEY", "shell-test-key")
        monkeypatch.setenv("OPENAI_MODEL", "shell-test-model")
    input_path = tmp_path / "input.json"
    write_json(input_path, {"key": product.model_dump()})
    monkeypatch.setattr(sys, "argv", ["vertex-match", "validate", str(input_path)])
    assert main() == 0
    source = "shell" if shell_values else "file"
    assert os.environ["OPENAI_API_KEY"] == f"{source}-test-key"
    parser = build_parser()
    assert parser.parse_args(["run", str(input_path)]).model == f"{source}-test-model"
    assert parser.parse_args(["run", str(input_path), "--model", "cli-model"]).model == "cli-model"


@pytest.mark.parametrize("source", ["product_page", "recipe", "category_page", "other"])
def test_cli_end_to_end(tmp_path, product, response_text, monkeypatch, source):
    input_path = tmp_path / "input.json"
    output_dir = tmp_path / "output"
    raw = {"external key": {**product.model_dump(), "custom": "preserve"}}
    write_json(input_path, raw)
    original = input_path.read_bytes()
    provider = AsyncMock()
    body = json.loads(response_text)
    body["decisions"][0]["source_type"] = source
    provider.complete.return_value = Reply(json.dumps(body), 100, 30)
    monkeypatch.setattr("vertex_matcher.cli.OpenAIProvider", lambda settings: provider)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vertex-match",
            "run",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--model",
            "fake-model",
        ],
    )
    assert main() == 0
    output = json.loads((output_dir / "predictions.json").read_text())
    assert output["external key"]["llm_trusted_search_results"] == [1]
    assert output["external key"]["custom"] == "preserve"
    assert input_path.read_bytes() == original
    report = json.loads((output_dir / "report.json").read_text())
    assert report["complete"] is True
    assert report["contract_version"] == "5"
    assert "retrieval" not in report
    assert report["metrics"]["evaluation_basis"] == "supplied_reference_labels"
    assert report["metrics"]["abstention_rate_on_completed"] == 0
    assert report["products"]["external key"]["decisions"][0]["source_type"] == source
    assert report["metrics"]["precision"] == 1
    assert report["metrics"]["clean_useful_coverage"] == 1
    assert report["products"]["external key"]["identity_status"] == "specific"
    assert report["products"]["external key"]["blocked_matches"] == []
    assert json.loads((output_dir / "review.json").read_text())["examples"] == []
    assert report["estimated_cost_usd"] is None
    monkeypatch.setattr(
        sys, "argv", ["vertex-match", "evaluate", str(output_dir / "predictions.json")]
    )
    assert main() == 0


def test_cli_failure_removes_stale_prediction(tmp_path, product, monkeypatch):
    input_path = tmp_path / "input.json"
    output_dir = tmp_path / "output"
    write_json(input_path, {"key": {**product.model_dump(), "llm_trusted_search_results": [1]}})
    provider = AsyncMock()
    provider.complete.side_effect = TimeoutError()
    monkeypatch.setattr("vertex_matcher.cli.OpenAIProvider", lambda settings: provider)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vertex-match",
            "run",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--model",
            "fake-model",
            "--max-attempts",
            "1",
        ],
    )
    assert main() == 1
    output = json.loads((output_dir / "predictions.json").read_text())
    assert output["key"]["llm_trusted_search_results"] == [1]
    assert "model_selected_search_results" not in output["key"]
    assert "model_failed" in output["key"]["selection_review"][0]["flags"]
    assert json.loads((output_dir / "failures.json").read_text()) == {"key": "TimeoutError"}


def test_validate_requires_no_credentials(tmp_path, product, monkeypatch):
    input_path = tmp_path / "input.json"
    write_json(input_path, {"key": product.model_dump()})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["vertex-match", "validate", str(input_path)])
    assert main() == 0


def test_missing_model_fails_before_api_call(tmp_path, product, monkeypatch):
    input_path = tmp_path / "input.json"
    write_json(input_path, {"key": product.model_dump()})
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setattr(sys, "argv", ["vertex-match", "run", str(input_path)])
    assert main() == 2


def test_full_dataset_with_mocked_provider(tmp_path, monkeypatch):
    async def select_all(instructions, payload):
        product = json.loads(payload)
        decisions = [
            {
                "candidate_id": candidate["candidate_id"],
                "decision": "match",
                "variant_status": "compatible",
                "source_type": "product_page",
                "reason_code": "test_fixture",
                "evidence": "Synthetic selection, not a real product judgment",
                "verification_status": "not_checked",
                "verification_quote": "",
            }
            for candidate in product["search_results"]
        ]
        body = {
            "identity_status": "specific",
            "identity_evidence": product["product_title"] or product["product_description"],
            "decisions": decisions,
        }
        return Reply(json.dumps(body), 100, 30)

    provider = AsyncMock()
    provider.complete.side_effect = select_all
    monkeypatch.setattr("vertex_matcher.cli.OpenAIProvider", lambda settings: provider)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vertex-match",
            "run",
            str(root / "search_results_ground_truth_train.json"),
            "--output-dir",
            str(tmp_path),
            "--model",
            "fake-model",
            "--requests-per-minute",
            "1000000",
        ],
    )
    assert main() == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["complete"] is True
    assert report["metrics"]["true_positives"] == 240
    assert report["metrics"]["false_positives"] == 215
    assert report["metrics"]["correctly_covered_products"] == 38
    assert report["acceptance_gates"]["reference_minimum_recovered"] is True
    assert provider.complete.call_count == 50


def test_price_environment_defaults_and_flag_override(monkeypatch):
    monkeypatch.setenv("OPENAI_INPUT_PRICE_PER_MILLION", "1.25")
    monkeypatch.setenv("OPENAI_OUTPUT_PRICE_PER_MILLION", "2.5")
    parser = build_parser()
    args = parser.parse_args(["run", "input.json"])
    assert args.input_price_per_million == 1.25
    assert args.output_price_per_million == 2.5
    args = parser.parse_args(["run", "input.json", "--input-price-per-million", "3"])
    assert args.input_price_per_million == 3


def test_cli_applies_gates_and_estimates_cost(tmp_path, product, response_text, monkeypatch):
    body = json.loads(response_text)
    body["identity_status"] = "generic"
    body["identity_evidence"] = ""
    input_path = tmp_path / "input.json"
    output_dir = tmp_path / "run"
    write_json(input_path, {"product": product.model_dump()})
    provider = AsyncMock()
    provider.complete.return_value = Reply(json.dumps(body), 100, 30)
    monkeypatch.setattr("vertex_matcher.cli.OpenAIProvider", lambda settings: provider)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vertex-match",
            "run",
            str(input_path),
            "--model",
            "fake-model",
            "--output-dir",
            str(output_dir),
            "--input-price-per-million",
            "1",
            "--output-price-per-million",
            "2",
        ],
    )
    assert main() == 0
    predictions = json.loads((output_dir / "predictions.json").read_text())
    assert predictions["product"]["llm_trusted_search_results"] == [1]
    assert predictions["product"]["model_selected_search_results"] == []
    assert predictions["product"]["human_retained_search_results"] == [1]
    assert predictions["product"]["trusted_search_results"] == [1]
    report = json.loads((output_dir / "report.json").read_text())
    assert report["metrics"]["reference_minimum"]["recovered_candidates"] == 0
    assert report["delivery_reference_minimum"]["recovered_candidates"] == 1
    assert report["products"]["product"]["blocked_matches"] == [
        {"candidate_id": 1, "reasons": ["ambiguous_identity"]}
    ]
    assert report["estimated_cost_per_1000_products_usd"] == pytest.approx(0.16)
    review = json.loads((output_dir / "review.json").read_text())
    assert review["error_groups"] == {"ambiguous_identity": 1}
    assert review["examples"][0]["model_decision"]["decision"] == "match"
    assert review["examples"][0]["actually_selected"] is False
