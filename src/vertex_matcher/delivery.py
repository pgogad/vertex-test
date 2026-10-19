"""Retain the supplied human baseline without changing model measurements."""

from .schemas import Product, Selection


def build_delivery(product: Product, selection: Selection | None) -> dict:
    model_ids = set(selection.selected_ids() if selection else [])
    human_ids = set(product.trusted_search_results or [])
    decisions = {d.candidate_id: d for d in selection.decisions} if selection else {}
    flags = []
    for candidate_id in sorted(model_ids | human_ids):
        decision = decisions.get(candidate_id)
        reasons = selection.exclusion_reasons(decision) if decision else ["model_failed"]
        if candidate_id in human_ids - model_ids:
            reasons = ["retained_human_selection", *reasons]
        verification = decision.verification_status if decision else "not_checked"
        if verification != "confirmed":
            reasons = [*reasons, "url_not_confirmed"]
        flags.append(
            {
                "candidate_id": candidate_id,
                "human_selected": candidate_id in human_ids,
                "model_selected": candidate_id in model_ids,
                "model_decision": decision.model_dump() if decision else None,
                "verification_status": verification,
                "needs_review": bool(reasons),
                "flags": reasons,
            }
        )
    return {
        "llm_trusted_search_results": sorted(model_ids | human_ids),
        "human_retained_search_results": sorted(human_ids - model_ids),
        "selection_review": flags,
    }
