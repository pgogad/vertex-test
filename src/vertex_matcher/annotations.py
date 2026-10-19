"""Join independent adjudications to frozen predictions without changing model output."""

import copy
from datetime import date

from .schemas import Product


def attach_references(raw: dict, products: dict[str, Product], reviews: dict) -> tuple[dict, dict]:
    if not isinstance(reviews, dict) or not reviews:
        raise ValueError("Reviews must be a nonempty object keyed by product identifier")
    if set(reviews) - set(products):
        raise ValueError("Reviews contain unknown product identifiers")
    output = copy.deepcopy(raw)
    attached = []
    pending = []
    for key, product in products.items():
        # A delivered union cannot stand in for a missing frozen model output.
        if "model_selected_search_results" not in raw[key] and "selection_review" not in raw[key]:
            raise ValueError(f"{key}: frozen model output or explicit failure marker required")
        output[key].pop("trusted_search_results", None)
        review = reviews.get(key)
        if review is None:
            pending.append(key)
            continue
        if not isinstance(review, dict):
            raise ValueError(f"{key}: review must be an object")
        for field in ("product_title", "product_description"):
            if review.get(field) != getattr(product, field):
                raise ValueError(f"{key}: annotation {field} differs from frozen input")
        candidates = review.get("candidate_reviews")
        if not isinstance(candidates, dict) or set(candidates) != set(product.search_results):
            raise ValueError(f"{key}: reviews must cover the exact frozen candidate IDs")
        for candidate_id, text in product.search_results.items():
            candidate = candidates[candidate_id]
            if not isinstance(candidate, dict) or candidate.get("candidate_text") != text:
                raise ValueError(
                    f"{key}/{candidate_id}: annotation candidate differs from frozen input"
                )
            if candidate.get("assessment") not in (None, "match", "not_match", "uncertain"):
                raise ValueError(f"{key}/{candidate_id}: invalid assessment")
            if candidate.get("url_verification") not in (
                None,
                "confirmed",
                "contradicted",
                "inconclusive",
                "unavailable",
            ):
                raise ValueError(f"{key}/{candidate_id}: invalid URL verification")
        if type(review.get("adjudicated")) is not bool:
            raise ValueError(f"{key}: adjudicated must be a boolean")
        if not review["adjudicated"]:
            pending.append(key)
            continue
        reviewer = review.get("reviewer")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError(f"{key}: adjudication requires a reviewer")
        try:
            reviewed_on = review.get("reviewed_on")
            if (
                not isinstance(reviewed_on, str)
                or date.fromisoformat(reviewed_on).isoformat() != reviewed_on
            ):
                raise ValueError()
        except ValueError:
            raise ValueError(f"{key}: reviewed_on must be an ISO date (YYYY-MM-DD)") from None
        if any(c.get("assessment") not in {"match", "not_match"} for c in candidates.values()):
            raise ValueError(f"{key}: adjudication contains incomplete or uncertain assessments")
        output[key]["trusted_search_results"] = sorted(
            int(candidate_id)
            for candidate_id, c in candidates.items()
            if c["assessment"] == "match"
        )
        attached.append(key)
    return output, {
        "attached_products": attached,
        "pending_products": pending,
        "note": "Only adjudicated products are labeled. Frozen model, delivery and URL verdicts "
        "are unchanged. Human URL assessments remain in the source review file. "
        "Previously attached references are replaced, not merged.",
    }
