import json
from dataclasses import replace

import pytest

from vertex_matcher import alias_registry
from vertex_matcher.alias_registry import normalization_evidence
from vertex_matcher.matcher import cache_key, payload_for
from vertex_matcher.schemas import Product, Settings


@pytest.mark.parametrize(
    ("title", "canonical"),
    [
        ("ALOHA PRTN PWDR VANILLA 1 EA", "protein powder"),
        ("Lipton Unswet Blk Tea 14oz", "unsweetened black tea"),
        ("ALOHA prtn\t pwdr VANILLA", "protein powder"),
        ("(PRTN PWDR), vanilla", "protein powder"),
        ("BRK STRAWBERRY DRINKING WATER", "Brookshire's"),
        ("brk strawberry drinking water", "Brookshire's"),
    ],
)
def test_phrase_expansion_preserves_original_input(title, canonical):
    product = Product(product_title=title, product_description="", search_results={})
    payload = json.loads(payload_for(product))
    assert payload["product_title"] == title
    assert product.product_title == title
    evidence = payload["normalization_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["canonical"] == canonical
    assert evidence[0]["field"] == "product_title"
    assert title[evidence[0]["start"] : evidence[0]["end"]] == evidence[0]["original"]
    assert evidence[0]["registry_id"]
    assert evidence[0]["provenance"]


@pytest.mark.parametrize(
    "title",
    ["Apple", "XBRK", "BRKX", "CB", "PRTN", "PWDR", "XPRTN PWDR", "PRTN PWDRX"],
)
def test_unknown_generic_and_partial_tokens_are_not_expanded(title):
    product = Product(product_title=title, product_description="", search_results={})
    assert normalization_evidence(product) == []
    assert "normalization_evidence" not in json.loads(payload_for(product))


def test_description_occurrences_have_exact_source_spans(product):
    product.product_description = "PRTN PWDR, not PRTN PWDR VANILLA"
    payload = json.loads(payload_for(product))
    assert payload["product_description"] == product.product_description
    evidence = payload["normalization_evidence"]
    assert len(evidence) == 2
    for occurrence in evidence:
        assert occurrence["field"] == "product_description"
        assert (
            product.product_description[occurrence["start"] : occurrence["end"]]
            == occurrence["original"]
        )


def test_candidates_pages_and_extra_fields_cannot_supply_normalization(product):
    product.search_results["1"] = "BRK PRTN PWDR"
    enriched = Product.model_validate(
        {
            **product.model_dump(),
            "normalization_evidence": [{"canonical": "invented brand"}],
            "page_evidence": {"1": "BRK UNSWET BLK TEA"},
        }
    )
    payload = json.loads(payload_for(enriched))
    assert "normalization_evidence" not in payload
    assert "page_evidence" not in payload


def test_relevant_registry_changes_invalidate_cache(product, monkeypatch):
    product.product_title = "BRK STRAWBERRY DRINKING WATER"
    settings = Settings(model="test-model")
    before = cache_key(payload_for(product), settings)
    updated = replace(alias_registry.ALIASES[0], provenance="Reviewed project mapping")
    monkeypatch.setattr(alias_registry, "ALIASES", (updated, *alias_registry.ALIASES[1:]))
    assert cache_key(payload_for(product), settings) != before


def test_brand_alias_in_description_has_user_provenance(product):
    product.product_description = "BRK strawberry drinking water"
    evidence = normalization_evidence(product)
    assert len(evidence) == 1
    assert evidence[0]["field"] == "product_description"
    assert evidence[0]["attribute"] == "brand"
    assert evidence[0]["canonical"] == "Brookshire's"
    assert evidence[0]["original"] == "BRK"
    assert evidence[0]["provenance"].startswith("User-provided mapping")


def test_starter_registry_has_unique_ids_and_phrases():
    assert len({alias.registry_id for alias in alias_registry.ALIASES}) == len(
        alias_registry.ALIASES
    )
    assert len({alias.phrase.casefold() for alias in alias_registry.ALIASES}) == len(
        alias_registry.ALIASES
    )


def test_verified_line_alias_has_manufacturer_provenance_and_preserves_modifiers():
    product = Product(
        product_title="V8 V-Fusion 100% Juice Pomegranate Blueberry",
        product_description="",
        search_results={},
    )
    evidence = normalization_evidence(product)
    assert evidence[0]["attribute"] == "verified_product_line_name"
    assert "campbellsfoodservice.com" in evidence[0]["source_url"]
    assert "previously" in evidence[0]["source_quote"]
    assert "Pomegranate Blueberry" in product.product_title
    energy = Product(
        product_title="V8 V-Fusion + Energy Pomegranate Blueberry",
        product_description="",
        search_results={},
    )
    light = Product(
        product_title="V8 V-Fusion Light Pomegranate Blueberry",
        product_description="",
        search_results={},
    )
    assert normalization_evidence(energy) == normalization_evidence(light) == []
