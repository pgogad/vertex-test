from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Product(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")

    product_title: str
    product_description: str
    search_results: dict[str, str]
    trusted_search_results: list[int] | None = None

    @model_validator(mode="after")
    def validate_ids(self):
        for candidate_id in self.search_results:
            if not candidate_id.isascii() or not candidate_id.isdecimal():
                raise ValueError("Search-result IDs must be positive integer strings")
            if int(candidate_id) < 1 or str(int(candidate_id)) != candidate_id:
                raise ValueError("Search-result IDs must be canonical positive integer strings")
        if self.trusted_search_results is not None:
            validate_selection(self.trusted_search_results, self)
        return self


def validate_selection(selected: list[int], product: Product) -> None:
    if not isinstance(selected, list):
        raise ValueError("Selections must be lists")
    if any(type(candidate_id) is not int for candidate_id in selected):
        raise ValueError("Selections must contain integers, not strings or booleans")
    if len(selected) != len(set(selected)):
        raise ValueError("Selections must not contain duplicate IDs")
    if any(str(candidate_id) not in product.search_results for candidate_id in selected):
        raise ValueError("Selection contains an unknown candidate ID")


class Decision(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    candidate_id: int
    decision: Literal["match", "not_match", "insufficient_evidence"]
    variant_status: Literal["compatible", "conflict", "uncertain"]
    source_type: Literal[
        "product_page", "product_document", "product_article", "recipe", "category_page", "other"
    ]
    reason_code: str
    evidence: str
    verification_status: Literal[
        "confirmed", "contradicted", "inconclusive", "unavailable", "not_checked"
    ]
    verification_quote: str


class Selection(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    identity_status: Literal["specific", "generic", "uncertain"]
    identity_evidence: str
    decisions: list[Decision]

    def validate_for(self, product: Product) -> None:
        if self.identity_status == "specific":
            evidence = self.identity_evidence.strip()
            if not evidence or not (
                evidence in product.product_title or evidence in product.product_description
            ):
                raise ValueError(
                    "Specific identity requires an exact quote from the uploaded product"
                )
        candidate_ids = [decision.candidate_id for decision in self.decisions]
        validate_selection(candidate_ids, product)
        if set(candidate_ids) != {int(candidate_id) for candidate_id in product.search_results}:
            raise ValueError("Every candidate must have exactly one decision")
        seen = {}
        for decision in self.decisions:
            text = product.search_results[str(decision.candidate_id)]
            outcome = (
                decision.decision,
                decision.variant_status,
                decision.source_type,
                decision.verification_status,
            )
            if text in seen and seen[text] != outcome:
                raise ValueError("Identical candidate text received conflicting decisions")
            seen[text] = outcome

    def exclusion_reasons(self, decision: Decision) -> list[str]:
        reasons = []
        if self.identity_status != "specific":
            reasons.append("ambiguous_identity")
        if decision.variant_status == "conflict":
            reasons.append("variant_mismatch")
        elif decision.variant_status == "uncertain":
            reasons.append("insufficient_variant_evidence")
        if decision.verification_status == "contradicted":
            reasons.append("url_contradiction")
        return reasons

    def selected_ids(self) -> list[int]:
        return sorted(
            item.candidate_id
            for item in self.decisions
            if item.decision == "match" and not self.exclusion_reasons(item)
        )

    def blocked_matches(self) -> list[dict]:
        return [
            {"candidate_id": item.candidate_id, "reasons": self.exclusion_reasons(item)}
            for item in self.decisions
            if item.decision == "match" and self.exclusion_reasons(item)
        ]

    def verified_ids(self) -> list[int]:
        selected = set(self.selected_ids())
        return sorted(
            item.candidate_id
            for item in self.decisions
            if item.candidate_id in selected and item.verification_status == "confirmed"
        )


class Settings(BaseModel):
    model: str = Field(min_length=1)
    concurrency: int = Field(default=5, ge=1, le=100)
    requests_per_minute: float = Field(default=60, gt=0, allow_inf_nan=False)
    timeout: float = Field(default=60, gt=0, allow_inf_nan=False)
    max_attempts: int = Field(default=3, ge=1, le=5)
    max_output_tokens: int = Field(default=4096, ge=256)
    max_input_chars: int = Field(default=60000, ge=1)
    customer: str = Field(default="local", min_length=1)
    verify_links: bool = False
    page_timeout: float = Field(default=8, gt=0, le=60, allow_inf_nan=False)
    page_max_chars: int = Field(default=3000, ge=500, le=10000)
    max_pages_per_product: int = Field(default=10, ge=1, le=30)
    page_requests_per_minute: float = Field(default=120, gt=0, allow_inf_nan=False)
    input_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_prices(self):
        if (self.input_price_per_million is None) != (self.output_price_per_million is None):
            raise ValueError("Provide both input and output token prices, or neither")
        return self
