import json
import os
import tempfile
from pathlib import Path

from .schemas import Product


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON contains a duplicate object key")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def load_dataset(path: Path) -> tuple[dict, dict[str, Product]]:
    raw = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Input must be a nonempty object keyed by product identifier")
    products = {key: Product.model_validate(value) for key, value in raw.items()}
    return raw, products


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def model_input(product: Product) -> dict:
    return {
        "product_title": product.product_title,
        "product_description": product.product_description,
        "search_results": [
            {"candidate_id": int(candidate_id), "text": text}
            for candidate_id, text in product.search_results.items()
        ],
    }


def dataset_summary(products: dict[str, Product]) -> dict:
    conflicts = []
    for key, product in products.items():
        if product.trusted_search_results is None:
            continue
        groups = {}
        for candidate_id, text in product.search_results.items():
            groups.setdefault(text, []).append(int(candidate_id))
        trusted = set(product.trusted_search_results)
        for candidate_ids in groups.values():
            if len({candidate_id in trusted for candidate_id in candidate_ids}) > 1:
                conflicts.append({"product_key": key, "candidate_ids": candidate_ids})
    return {
        "products": len(products),
        "candidates": sum(len(product.search_results) for product in products.values()),
        "labeled_products": sum(
            product.trusted_search_results is not None for product in products.values()
        ),
        "approved_candidates": sum(
            len(product.trusted_search_results or []) for product in products.values()
        ),
        "empty_reference_products": sum(
            product.trusted_search_results == [] for product in products.values()
        ),
        "empty_titles": sum(not product.product_title.strip() for product in products.values()),
        "empty_descriptions": sum(
            not product.product_description.strip() for product in products.values()
        ),
        "identical_text_label_conflicts": conflicts,
    }
