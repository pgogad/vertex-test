import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AuthenticationError, BadRequestError, NotFoundError

from vertex_matcher.provider import OpenAIProvider, Reply
from vertex_matcher.runner import run_products
from vertex_matcher.schemas import Settings
from vertex_matcher.store import Store


class FakeProvider:
    def __init__(self, response_text, failure=None):
        self.response_text = response_text
        self.failure = failure
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.payloads = []

    async def complete(self, instructions, payload):
        self.calls += 1
        self.payloads.append(json.loads(payload))
        self.active += 1
        self.max_active = max(self.active, self.max_active)
        try:
            await asyncio.sleep(0.001)
            if self.failure:
                raise self.failure
            return Reply(self.response_text, 100, 30)
        finally:
            self.active -= 1


@pytest.fixture
def store(tmp_path):
    storage = Store(tmp_path / "checkpoint.sqlite")
    yield storage
    storage.close()


def settings(**overrides):
    return Settings.model_validate(
        {
            "model": "fake-model",
            "requests_per_minute": 1_000_000,
            "max_attempts": 1,
            **overrides,
        }
    )


def test_matching_uses_only_supplied_product_and_search_results(product, response_text, store):
    provider = FakeProvider(response_text)
    report = asyncio.run(run_products({"product": product}, provider, store, settings()))
    assert provider.calls == 1
    assert provider.payloads[0] == {
        "product_title": product.product_title,
        "product_description": product.product_description,
        "search_results": [
            {"candidate_id": int(candidate_id), "text": text}
            for candidate_id, text in product.search_results.items()
        ],
    }
    assert "retrieval" not in report


def test_supplied_input_at_budget_is_preserved(product, response_text, store):
    from vertex_matcher.matcher import payload_for

    provider = FakeProvider(response_text)
    report = asyncio.run(
        run_products(
            {"product": product},
            provider,
            store,
            settings(max_input_chars=len(payload_for(product))),
        )
    )
    assert report["products"]["product"]["status"] == "succeeded"
    assert "page_evidence" not in provider.payloads[0]
    assert provider.payloads[0] == json.loads(payload_for(product))


def test_run_and_resume(product, response_text, store):
    provider = FakeProvider(response_text)
    config = settings(input_price_per_million=1, output_price_per_million=2)
    report = asyncio.run(run_products({"product": product}, provider, store, config))
    assert provider.calls == 1
    assert report["products"]["product"]["status"] == "succeeded"
    assert report["estimated_cost_usd"] == pytest.approx(0.00016)
    assert "trusted_search_results" not in provider.payloads[0]
    resumed = asyncio.run(run_products({"product": product}, provider, store, config))
    assert provider.calls == 1
    assert resumed["cached_products"] == 1
    fresh = asyncio.run(
        run_products({"product": product}, provider, store, config, use_cache=False)
    )
    assert fresh["cached_products"] == 0
    assert provider.calls == 2


def test_failed_call_is_not_abstention(product, response_text, store):
    provider = FakeProvider(response_text, failure=TimeoutError())
    report = asyncio.run(run_products({"product": product}, provider, store, settings()))
    result = report["products"]["product"]
    assert result["status"] == "failed"
    assert "decisions" not in result
    assert report["attempts_with_unknown_usage"] == 1
    assert report["estimated_cost_usd"] is None


def test_invalid_response_usage_is_counted(product, store):
    provider = FakeProvider("not json")
    report = asyncio.run(run_products({"product": product}, provider, store, settings()))
    assert report["products"]["product"]["status"] == "failed"
    assert report["input_tokens"] == 100
    assert report["output_tokens"] == 30
    assert report["attempts_with_unknown_usage"] == 0


def test_worker_concurrency(product, response_text, store):
    provider = FakeProvider(response_text)
    products = {str(index): product for index in range(12)}
    report = asyncio.run(
        run_products(products, provider, store, settings(concurrency=3), use_cache=False)
    )
    assert len(report["products"]) == 12
    assert provider.calls == 12
    assert provider.max_active <= 3


def test_empty_candidates_need_no_api_call(product, response_text, store):
    product.search_results = {}
    product.trusted_search_results = []
    provider = FakeProvider(response_text)
    report = asyncio.run(run_products({"empty": product}, provider, store, settings()))
    assert report["products"]["empty"]["decisions"] == []
    assert provider.calls == 0


@pytest.mark.parametrize("concurrency", [1, 3])
@pytest.mark.parametrize("provider_code", ["context_length_exceeded", None])
def test_bad_request_only_fails_affected_product(
    product, response_text, store, concurrency, provider_code
):
    response = httpx.Response(400, request=httpx.Request("POST", "https://example.test"))
    error = BadRequestError(
        "Invalid product input", response=response, body={"code": provider_code}
    )
    provider = FakeProvider(response_text)
    provider.complete = AsyncMock(
        side_effect=[error, Reply(response_text, 100, 30), Reply(response_text, 100, 30)]
    )
    report = asyncio.run(
        run_products(
            {"bad": product, "good": product, "also_good": product},
            provider,
            store,
            settings(concurrency=concurrency, max_attempts=3),
            use_cache=False,
        )
    )
    assert provider.complete.await_count == 3
    assert report["api_attempts"] == 3
    assert report["products"]["bad"]["status"] == "failed"
    assert report["products"]["bad"]["error"] == "BadRequestError"
    assert report["products"]["bad"]["error_details"]["http_status"] == 400
    assert report["products"]["bad"]["error_details"]["provider_code"] == provider_code
    assert report["products"]["good"]["status"] == "succeeded"
    assert report["products"]["also_good"]["status"] == "succeeded"


def test_fatal_authentication_stops_new_requests(product, response_text, store):
    response = httpx.Response(401, request=httpx.Request("POST", "https://example.test"))
    error = AuthenticationError("invalid key", response=response, body=None)
    provider = FakeProvider(response_text, failure=error)
    report = asyncio.run(
        run_products(
            {"first": product, "second": product}, provider, store, settings(concurrency=1)
        )
    )
    assert provider.calls == 1
    assert all(item["status"] == "failed" for item in report["products"].values())


def test_model_not_found_stops_rate_limited_workers(product, response_text, store):
    response = httpx.Response(
        404,
        request=httpx.Request("POST", "https://example.test/responses"),
        headers={"x-request-id": "req_test"},
    )
    error = NotFoundError(
        "Do not log this raw message: sk-secret-test-value",
        response=response,
        body={"code": "model_not_found", "type": "invalid_request_error"},
    )
    provider = FakeProvider(response_text, failure=error)

    async def scenario():
        return await asyncio.wait_for(
            run_products(
                {str(index): product for index in range(50)},
                provider,
                store,
                settings(requests_per_minute=1, concurrency=5),
            ),
            timeout=2,
        )

    report = asyncio.run(scenario())
    assert provider.calls == 1
    assert len(report["products"]) == 50
    assert all(item["status"] == "failed" for item in report["products"].values())
    assert report["first_error"]["http_status"] == 404
    assert report["first_error"]["provider_code"] == "model_not_found"
    assert report["first_error"]["request_id"] == "req_test"
    assert "OPENAI_MODEL" in report["first_error"]["message"]
    assert "sk-secret-test-value" not in json.dumps(report)
    audit = store.connection.execute("SELECT details FROM attempts").fetchone()[0]
    assert "sk-secret-test-value" not in audit


def test_retry_after_invalid_response(product, response_text, store, monkeypatch):
    provider = FakeProvider(response_text)
    provider.complete = AsyncMock(
        side_effect=[Reply("not json", 10, 5), Reply(response_text, 20, 10)]
    )
    monkeypatch.setattr("vertex_matcher.runner.random.random", lambda: 0)
    report = asyncio.run(
        run_products({"product": product}, provider, store, settings(max_attempts=2))
    )
    assert report["api_attempts"] == 2
    assert report["input_tokens"] == 30
    assert report["products"]["product"]["status"] == "succeeded"


def test_provider_request_contract(response_text, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")

    async def scenario():
        provider = OpenAIProvider(settings())
        create = AsyncMock(
            return_value=SimpleNamespace(
                output_text=response_text,
                status="completed",
                usage=SimpleNamespace(input_tokens=100, output_tokens=30),
            )
        )
        monkeypatch.setattr(provider.client.responses, "create", create)
        try:
            reply = await provider.complete("policy", "payload")
            arguments = create.call_args.kwargs
            assert arguments["store"] is False
            assert arguments["text"]["format"]["strict"] is True
            assert arguments["input"] == "payload"
            assert reply.input_tokens == 100
            assert reply.text == response_text
        finally:
            await provider.close()

    asyncio.run(scenario())
