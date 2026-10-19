import json

import pytest

from vertex_matcher.schemas import Product


@pytest.fixture(autouse=True)
def isolated_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def product():
    return Product(
        product_title="Example orange soda",
        product_description="Regular orange soda",
        search_results={
            "1": "Title: Example orange soda\nLink: https://example.test/orange\nSnippet: Regular",
            "2": "Title: Example diet orange soda\nLink: https://example.test/diet\nSnippet: Diet",
        },
        trusted_search_results=[1],
    )


@pytest.fixture
def response_text():
    return json.dumps(
        {
            "identity_status": "specific",
            "identity_evidence": "Example",
            "decisions": [
                {
                    "candidate_id": 1,
                    "decision": "match",
                    "variant_status": "compatible",
                    "source_type": "product_page",
                    "reason_code": "identity_match",
                    "evidence": "Same brand and variant",
                    "verification_status": "not_checked",
                    "verification_quote": "",
                },
                {
                    "candidate_id": 2,
                    "decision": "not_match",
                    "variant_status": "conflict",
                    "source_type": "product_page",
                    "reason_code": "variant_conflict",
                    "evidence": "Diet versus regular",
                    "verification_status": "not_checked",
                    "verification_quote": "",
                },
            ],
        }
    )
