import hashlib
import json
from importlib.resources import files

from .alias_registry import normalization_evidence
from .data import model_input
from .schemas import Product, Selection, Settings

POLICY = files("vertex_matcher").joinpath("matching_policy.md").read_text(encoding="utf-8")
CONTRACT_VERSION = "5"


class MatchingContractError(ValueError):
    """A controlled, non-sensitive explanation of invalid URL verification output."""


def payload_for(product: Product, page_evidence: dict | None = None) -> str:
    data = model_input(product)
    aliases = normalization_evidence(product)
    if aliases:
        data["normalization_evidence"] = aliases
    if page_evidence is not None:
        data["page_evidence"] = {
            key: {
                name: value
                for name, value in page.items()
                if name
                in {
                    "status",
                    "final_url",
                    "text",
                    "truncated",
                    "body_sha256",
                    "content_type",
                    "body_complete",
                    "body_hash_scope",
                    "http_status",
                    "error",
                    "reason",
                }
            }
            for key, page in page_evidence.items()
        }
    return json.dumps(data, ensure_ascii=False)


def cache_key(payload: str, settings: Settings) -> str:
    configuration = {
        "contract_version": CONTRACT_VERSION,
        "policy": POLICY,
        "schema": Selection.model_json_schema(),
        "customer": settings.customer,
        "model": settings.model,
        "max_output_tokens": settings.max_output_tokens,
        "payload": payload,
    }
    encoded = json.dumps(configuration, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def parse_selection(text: str, product: Product, page_evidence: dict | None = None) -> Selection:
    selection = Selection.model_validate_json(text)
    try:
        selection.validate_for(product)
    except ValueError as error:
        raise MatchingContractError(str(error)) from error
    validate_verification(selection, page_evidence)
    return selection


def validate_verification(selection: Selection, page_evidence: dict | None) -> None:
    for item in selection.decisions:
        page = (page_evidence or {}).get(str(item.candidate_id))
        if page is None:
            expected = {"not_checked"}
        elif page["status"] != "fetched":
            expected = {"unavailable"}
        else:
            expected = {"confirmed", "contradicted", "inconclusive"}
        if item.verification_status not in expected:
            raise MatchingContractError(
                "Verification status does not correspond to fetched evidence"
            )
        if item.verification_status in {"confirmed", "contradicted"}:
            if not item.verification_quote.strip() or item.verification_quote not in page["text"]:
                raise MatchingContractError(
                    f"Candidate {item.candidate_id}: URL verification requires an exact quote "
                    "from this candidate's fetched text. Copy a short literal substring, "
                    "without changing punctuation or adding ellipses. If the fetched text "
                    "cannot support the verdict, use inconclusive with an empty quote."
                )
            if item.verification_status == "confirmed" and (
                selection.identity_status != "specific"
                or item.decision != "match"
                or item.variant_status != "compatible"
            ):
                raise MatchingContractError(
                    "Confirmed verification must be a compatible selected match"
                )
            if item.verification_status == "contradicted" and item.decision == "match":
                raise MatchingContractError("Contradicted URL evidence cannot be selected")
        elif item.verification_quote:
            raise MatchingContractError(
                "No verification quote is allowed without a decisive page verdict"
            )
