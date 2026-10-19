import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from vertex_matcher.annotations import attach_references
from vertex_matcher.data import (
    dataset_summary,
    load_dataset,
    reject_constant,
    unique_object,
    write_json,
)
from vertex_matcher.delivery import build_delivery
from vertex_matcher.evaluation import (
    acceptance_gates,
    evaluate,
    evaluate_saved_predictions,
    reference_minimum,
)
from vertex_matcher.matcher import CONTRACT_VERSION, POLICY
from vertex_matcher.provider import OpenAIProvider
from vertex_matcher.review import build_review
from vertex_matcher.runner import run_products
from vertex_matcher.schemas import Selection, Settings
from vertex_matcher.store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Match supplied search results to products")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Inspect input and labels without API calls")
    validate.add_argument("input", type=Path)
    score = commands.add_parser("evaluate", help="Score a JSON file containing predictions")
    score.add_argument("input", type=Path)
    attach = commands.add_parser(
        "attach-references", help="Attach adjudicated reviews and score frozen predictions offline"
    )
    attach.add_argument("input", type=Path, help="Saved predictions.json")
    attach.add_argument("--reviews", type=Path, required=True)
    attach.add_argument("--output-dir", type=Path, required=True)
    run = commands.add_parser("run", help="Select search results with an LLM")
    run.add_argument("input", type=Path)
    run.add_argument("--output-dir", type=Path, default=Path("runs/latest"))
    run.add_argument("--model", default=os.getenv("OPENAI_MODEL"))
    run.add_argument("--customer", default="local")
    run.add_argument("--concurrency", type=int, default=5)
    run.add_argument("--requests-per-minute", type=float, default=60)
    run.add_argument("--timeout", type=float, default=60)
    run.add_argument("--max-attempts", type=int, default=3)
    run.add_argument("--max-output-tokens", type=int, default=4096)
    run.add_argument("--max-input-chars", type=int, default=60000)
    run.add_argument(
        "--input-price-per-million",
        type=float,
        default=os.getenv("OPENAI_INPUT_PRICE_PER_MILLION") or None,
    )
    run.add_argument(
        "--output-price-per-million",
        type=float,
        default=os.getenv("OPENAI_OUTPUT_PRICE_PER_MILLION") or None,
    )
    run.add_argument("--no-cache", action="store_true", help="Force fresh inference")
    run.add_argument("--verify-links", action="store_true", help="Fetch candidate URLs as evidence")
    run.add_argument(
        "--replay-pages",
        type=Path,
        help="Reuse a prior report's page evidence for a controlled policy experiment",
    )
    run.add_argument("--page-timeout", type=float, default=8)
    run.add_argument("--page-max-chars", type=int, default=3000)
    run.add_argument("--max-pages-per-product", type=int, default=10)
    run.add_argument("--page-requests-per-minute", type=float, default=120)
    return parser


async def execute_run(args, raw: dict, products: dict) -> int:
    if not args.model or not args.model.strip():
        raise ValueError("Set --model or OPENAI_MODEL to a model supporting structured outputs")
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("Set OPENAI_API_KEY before running inference")
    page_snapshot = None
    if args.replay_pages is not None:
        args.verify_links = True
        previous = json.loads(args.replay_pages.read_text(encoding="utf-8"))
        if previous.get("input_sha256") != hashlib.sha256(args.input.read_bytes()).hexdigest():
            raise ValueError("Snapshot input hash does not match the current dataset")
        page_snapshot = previous.get("retrieval")
        if not isinstance(page_snapshot, dict) or not page_snapshot.get("enabled"):
            raise ValueError("Snapshot report must contain enabled URL retrieval")
        for key, product in products.items():
            pages = page_snapshot.get("products", {}).get(key)
            if not isinstance(pages, dict) or set(pages) != set(product.search_results):
                raise ValueError("Snapshot must cover every product and candidate")
            for page in pages.values():
                if not isinstance(page, dict) or page.get("status") not in {
                    "fetched",
                    "unavailable",
                }:
                    raise ValueError("Invalid page snapshot status")
                if page["status"] == "fetched" and (
                    not isinstance(page.get("text"), str)
                    or not page["text"].strip()
                    or len(page["text"]) > args.page_max_chars
                ):
                    raise ValueError("Snapshot text is empty or exceeds the excerpt budget")
        page_snapshot = {**page_snapshot, "source_report": str(args.replay_pages)}
    settings = Settings.model_validate(
        {name: getattr(args, name) for name in Settings.model_fields}
    )
    names = [
        "predictions.json",
        "report.json",
        "review.json",
        "failures.json",
        "checkpoints.sqlite",
    ]
    if args.input.resolve() in {(args.output_dir / name).resolve() for name in names}:
        raise ValueError("Input must not overlap output or checkpoint files")
    store = Store(args.output_dir / "checkpoints.sqlite")
    provider = OpenAIProvider(settings)
    try:
        report = await run_products(
            products,
            provider,
            store,
            settings,
            use_cache=not args.no_cache,
            page_snapshot=page_snapshot,
        )
    finally:
        await provider.close()
        store.close()
    predictions = {}
    selections = {}
    verified = {}
    failures = {}
    output = {}
    delivered = {}
    for key, original in raw.items():
        output[key] = dict(original)
        output[key].pop("llm_trusted_search_results", None)
        output[key].pop("url_verified_search_results", None)
        output[key].pop("model_selected_search_results", None)
        selection = None
        result = report["products"][key]
        if result["status"] == "succeeded":
            selection = Selection.model_validate(
                {name: result[name] for name in Selection.model_fields}
            )
            selections[key] = selection
            predictions[key] = selection.selected_ids()
            result["selected_ids"] = predictions[key]
            result["blocked_matches"] = selection.blocked_matches()
            output[key]["model_selected_search_results"] = predictions[key]
            if settings.verify_links:
                verified[key] = selection.verified_ids()
                result["url_verified_ids"] = verified[key]
                output[key]["url_verified_search_results"] = verified[key]
        else:
            failures[key] = result["error"]
        delivery = build_delivery(products[key], selection)
        output[key].update(delivery)
        delivered[key] = delivery["llm_trusted_search_results"]
        result["delivery"] = delivery
    report["delivery_reference_minimum"] = reference_minimum(products, delivered)
    report["delivery_note"] = (
        "Human IDs are retained by construction; metrics measure model-only selections."
    )
    report["complete"] = not failures
    report["input_sha256"] = hashlib.sha256(args.input.read_bytes()).hexdigest()
    report["policy_sha256"] = hashlib.sha256(POLICY.encode()).hexdigest()
    report["contract_version"] = CONTRACT_VERSION
    report["dataset"] = dataset_summary(products)
    report["metrics"] = evaluate(products, predictions, verified if settings.verify_links else None)
    review = build_review(
        products,
        predictions,
        selections,
        verified if settings.verify_links else None,
        report.get("retrieval"),
    )
    report["error_groups"] = review["error_groups"]
    report["estimated_cost_per_1000_products_usd"] = (
        report["estimated_cost_usd"] / len(products) * 1000
        if not failures
        and report["cached_products"] == 0
        and report["estimated_cost_usd"] is not None
        else None
    )
    report["acceptance_gates"] = acceptance_gates(
        report["metrics"],
        report["elapsed_seconds"],
        report["estimated_cost_usd"],
        fresh=report["cached_products"] == 0 and page_snapshot is None,
    )
    write_json(args.output_dir / "predictions.json", output)
    write_json(args.output_dir / "failures.json", failures)
    write_json(args.output_dir / "review.json", review)
    write_json(args.output_dir / "report.json", report)
    summary = {key: value for key, value in report.items() if key not in {"products", "dataset"}}
    if "retrieval" in summary:
        summary["retrieval"] = {
            key: value for key, value in summary["retrieval"].items() if key != "products"
        }
    summary["metrics"] = {key: value for key, value in report["metrics"].items() if key != "errors"}
    for name in ("reference_minimum", "url_verified_reference_minimum"):
        if summary["metrics"][name] is not None:
            summary["metrics"][name] = {
                key: value
                for key, value in summary["metrics"][name].items()
                if key != "missing_ids"
            }
    print(json.dumps(summary, indent=2))
    return 0 if not failures else 1


def main() -> int:
    load_dotenv(Path.cwd() / ".env", override=False)
    parser = build_parser()
    args = parser.parse_args()
    try:
        raw, products = load_dataset(args.input)
        if args.command == "validate":
            print(json.dumps(dataset_summary(products), indent=2))
            return 0
        if args.command == "evaluate":
            metrics = evaluate_saved_predictions(raw, products)
            print(json.dumps(metrics, indent=2))
            return 0 if metrics["missing_predictions"] == 0 else 1
        if args.command == "attach-references":
            destinations = [
                args.output_dir / name
                for name in ("predictions.json", "evaluation.json", "annotation_report.json")
            ]
            if any(
                p.exists() or p.resolve() in {args.input.resolve(), args.reviews.resolve()}
                for p in destinations
            ):
                raise ValueError(
                    "Choose fresh output paths; source and existing artifacts are protected"
                )
            reviews = json.loads(
                args.reviews.read_text(encoding="utf-8"),
                object_pairs_hook=unique_object,
                parse_constant=reject_constant,
            )
            output, audit = attach_references(raw, products, reviews)
            labeled_products = {
                key: type(products[key]).model_validate(value) for key, value in output.items()
            }
            metrics = evaluate_saved_predictions(output, labeled_products)
            audit.update(
                {
                    "predictions_source": str(args.input),
                    "predictions_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
                    "reviews_source": str(args.reviews),
                    "reviews_sha256": hashlib.sha256(args.reviews.read_bytes()).hexdigest(),
                }
            )
            for path, value in zip(destinations, (output, metrics, audit), strict=True):
                write_json(path, value)
            print(json.dumps({"annotations": audit, "metrics": metrics}, indent=2))
            return 0 if metrics["missing_predictions"] == 0 else 1
        return asyncio.run(execute_run(args, raw, products))
    except ValidationError as error:
        issues = error.errors(include_input=False, include_url=False, include_context=False)
        print(json.dumps({"validation_errors": issues}, indent=2), file=sys.stderr)
        return 2
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. Completed checkpoints can be reused on the next run.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
