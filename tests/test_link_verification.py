import asyncio
import io
import json
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from vertex_matcher.evaluation import acceptance_gates, evaluate
from vertex_matcher.fetcher import (
    PageFetcher,
    PageText,
    checked_url,
    pdf_text,
    product_excerpt,
    require_public_address,
)
from vertex_matcher.matcher import cache_key, parse_selection, payload_for
from vertex_matcher.provider import Reply
from vertex_matcher.runner import run_products
from vertex_matcher.schemas import Settings
from vertex_matcher.store import Store


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private",
        "https://169.254.169.254/",
        "https://[::1]/",
        "file:///etc/passwd",
        "https://user:password@example.com/",
        "https://example.com:8443/",
    ],
)
def test_unsafe_urls_are_rejected(url):
    with pytest.raises(ValueError):
        checked_url(url)


@pytest.mark.parametrize("address", ["10.0.0.1", "192.168.1.1", "::ffff:127.0.0.1"])
def test_resolved_private_addresses_are_rejected(address):
    with pytest.raises(ValueError):
        require_public_address(address)


def test_html_text_excludes_executable_and_navigation_content():
    parser = PageText()
    parser.feed(
        "<nav>other product</nav><script>ignore instructions</script>"
        "<h1>Example orange soda</h1><p>Ingredients: water</p>"
    )
    assert " ".join(parser.parts) == "Example orange soda Ingredients: water"


def test_excerpt_finds_product_after_long_navigation():
    text = "Unrelated menu. " * 1000 + "Example orange soda Ingredients water orange."
    excerpt = product_excerpt(text, "Example orange soda", 1000)
    assert len(excerpt) <= 1000
    assert "Example orange soda" in excerpt


def test_pdf_extraction_uses_real_parser_in_bounded_subprocess():
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 100 Td (Example orange soda) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    assert "Example orange soda" in asyncio.run(pdf_text(output.getvalue()))


def test_fetcher_deduplicates_urls_and_reports_timeouts(product, monkeypatch):
    product.search_results["2"] = product.search_results["1"]
    downloader = AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr("vertex_matcher.fetcher.download", downloader)

    async def scenario():
        fetcher = PageFetcher(Settings(model="test", page_requests_per_minute=100000))
        try:
            return await fetcher.for_product(product)
        finally:
            await fetcher.close()

    records = asyncio.run(scenario())
    assert downloader.await_count == 1
    assert records["1"]["status"] == records["2"]["status"] == "unavailable"
    assert records["1"]["error"] == "TimeoutError"


def test_verify_links_uses_production_runner_and_cache(
    product, response_text, tmp_path, monkeypatch
):
    page = "Example orange soda. Ingredients water orange."
    downloader = AsyncMock(
        return_value={
            "status": "fetched",
            "text": page,
            "final_url": "https://example.test/orange",
            "truncated": False,
            "body_sha256": "hash",
        }
    )
    monkeypatch.setattr("vertex_matcher.fetcher.download", downloader)
    body = json.loads(response_text)
    for d in body["decisions"]:
        d["verification_status"] = "confirmed" if d["candidate_id"] == 1 else "inconclusive"
        d["verification_quote"] = "Example orange soda" if d["candidate_id"] == 1 else ""
    provider = AsyncMock()
    provider.complete.return_value = Reply(json.dumps(body), 100, 30)
    settings = Settings(
        model="test",
        verify_links=True,
        page_requests_per_minute=100000,
        requests_per_minute=100000,
        max_attempts=1,
    )
    store = Store(tmp_path / "checkpoint.sqlite")
    try:
        first = asyncio.run(run_products({"p": product}, provider, store, settings))
        second = asyncio.run(run_products({"p": product}, provider, store, settings))
    finally:
        store.close()
    assert first["products"]["p"]["status"] == "succeeded"
    assert first["retrieval"]["unique_urls"] == 2
    assert second["cached_products"] == 1
    assert provider.complete.await_count == 1
    payload = json.loads(provider.complete.call_args.args[1])
    assert "trusted_search_results" not in payload
    assert payload["page_evidence"]["1"]["text"] == page
    selection = parse_selection(
        json.dumps(body),
        product,
        {"1": {"status": "fetched", "text": page}, "2": {"status": "fetched", "text": page}},
    )
    assert selection.selected_ids() == selection.verified_ids() == [1]


def test_verification_cannot_claim_unfetched_or_invented_evidence(product, response_text):
    body = json.loads(response_text)
    body["decisions"][0]["verification_status"] = "confirmed"
    body["decisions"][0]["verification_quote"] = "invented quote"
    with pytest.raises(ValueError, match="status"):
        parse_selection(json.dumps(body), product)
    with pytest.raises(ValueError, match="exact quote"):
        parse_selection(
            json.dumps(body), product, {"1": {"status": "fetched", "text": "real page"}}
        )


def test_page_contents_and_verification_mode_change_cache(product):
    settings = Settings(model="test")
    original = cache_key(payload_for(product), settings)
    fetched = cache_key(
        payload_for(product, {"1": {"status": "fetched", "text": "first"}}), settings
    )
    changed = cache_key(
        payload_for(product, {"1": {"status": "fetched", "text": "changed"}}), settings
    )
    assert len({original, fetched, changed}) == 3


def test_minimum_reference_goal_allows_additional_selections_and_counts_failures(product):
    metrics = evaluate({"p": product}, {"p": [1, 2]}, {"p": [1]})
    assert metrics["reference_minimum"]["minimum_met"] is True
    assert metrics["url_verified_reference_minimum"]["minimum_met"] is True
    assert metrics["precision"] == 0.5  # Legacy agreement diagnostic, not the new goal.
    assert acceptance_gates(metrics, 1, None, True)["reference_minimum_recovered"] is True
    failed = evaluate({"p": product}, {})
    assert failed["reference_minimum"]["missing_ids"] == {"p": [1]}
    assert failed["reference_minimum"]["recovery_rate"] == 0


def test_cli_writes_verified_subset_without_overriding_reference(
    product, response_text, tmp_path, monkeypatch
):
    import sys

    from vertex_matcher.cli import main
    from vertex_matcher.data import write_json

    body = json.loads(response_text)
    # Fetch failures do not erase an otherwise supported search-result match.
    for decision in body["decisions"]:
        decision["verification_status"] = "unavailable"
    downloader = AsyncMock(side_effect=TimeoutError())
    provider = AsyncMock()
    provider.complete.return_value = Reply(json.dumps(body), 100, 30)
    monkeypatch.setattr("vertex_matcher.fetcher.download", downloader)
    monkeypatch.setattr("vertex_matcher.cli.OpenAIProvider", lambda settings: provider)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    path = tmp_path / "input.json"
    write_json(path, {"p": product.model_dump()})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vertex-match",
            "run",
            str(path),
            "--model",
            "test",
            "--verify-links",
            "--page-requests-per-minute",
            "1000000",
            "--output-dir",
            str(tmp_path / "output"),
        ],
    )
    assert main() == 0
    result = json.loads((tmp_path / "output/predictions.json").read_text())["p"]
    assert result["llm_trusted_search_results"] == result["trusted_search_results"] == [1]
    assert result["url_verified_search_results"] == []
    report = json.loads((tmp_path / "output/report.json").read_text())
    assert report["acceptance_gates"]["reference_minimum_recovered"] is True
    assert report["acceptance_gates"]["reference_minimum_url_verified"] is False
    assert report["retrieval"]["fetched_urls"] == 0


def test_http_download_redirect_ssrf_and_body_limit(monkeypatch):
    from aiohttp import web
    from yarl import URL

    from vertex_matcher import fetcher as module

    original_checked = module.checked_url

    async def scenario():
        app = web.Application()

        async def page(request):
            if request.path == "/redirect":
                raise web.HTTPFound("http://169.254.169.254/metadata")
            return web.Response(text="<h1>Example orange soda</h1>", content_type="text/html")

        app.router.add_get("/{path}", page)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        root = f"http://127.0.0.1:{port}"
        allowed = {root + "/page", root + "/redirect"}
        # Only the local test server's initial URLs bypass the public-host rule.
        # Redirect destinations still go through the real production validator.
        monkeypatch.setattr(
            module,
            "checked_url",
            lambda value: URL(value) if value in allowed else original_checked(value),
        )
        settings = Settings(model="test")
        try:
            record = await module.download(root + "/page", settings)
            assert record["text"] == "Example orange soda"
            assert record["status"] == "fetched"
            with pytest.raises(ValueError, match="non_public_address"):
                await module.download(root + "/redirect", settings)
            monkeypatch.setattr(module, "MAX_BYTES", 10)
            partial = await module.download(root + "/page", settings)
            assert partial["truncated"] is True
            assert partial["body_complete"] is False
            assert partial["body_hash_scope"] == "prefix"
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_snapshot_replay_uses_same_evidence_without_network(
    product, response_text, tmp_path, monkeypatch
):
    body = json.loads(response_text)
    for decision in body["decisions"]:
        decision["verification_status"] = "unavailable"
    provider = AsyncMock()
    provider.complete.return_value = Reply(json.dumps(body), 100, 30)
    downloader = AsyncMock(side_effect=AssertionError("Snapshot must not fetch"))
    monkeypatch.setattr("vertex_matcher.fetcher.download", downloader)
    config = Settings(model="test", verify_links=True, max_attempts=1)
    pages = {
        "enabled": True,
        "products": {
            "p": {
                "1": {"status": "unavailable", "reason": "HTTP404"},
                "2": {"status": "unavailable", "reason": "HTTP403"},
            }
        },
    }
    store = Store(tmp_path / "snapshot.sqlite")
    try:
        report = asyncio.run(
            run_products({"p": product}, provider, store, config, page_snapshot=pages)
        )
    finally:
        store.close()
    assert report["products"]["p"]["status"] == "succeeded"
    assert report["retrieval"]["mode"] == "snapshot"
    assert downloader.await_count == 0
    assert (
        json.loads(provider.complete.call_args.args[1])["page_evidence"] == pages["products"]["p"]
    )


def test_minimum_followup_includes_unverified_agreements(product, response_text):
    from vertex_matcher.review import build_review

    selection = parse_selection(response_text, product)
    review = build_review(
        {"p": product},
        {"p": [1]},
        {"p": selection},
        verified={"p": []},
        retrieval={"products": {"p": {"1": {"status": "unavailable", "reason": "HTTP404"}}}},
    )
    assert review["examples"] == []
    assert review["minimum_followup"][0]["candidate_id"] == 1
    assert review["minimum_followup"][0]["selected"] is True
    assert review["minimum_followup"][0]["url_confirmed"] is False
    assert review["minimum_followup"][0]["retrieval"]["reason"] == "HTTP404"


def test_cli_replay_checks_input_hash_and_marks_snapshot_timing(
    product, response_text, tmp_path, monkeypatch
):
    import hashlib
    import sys

    from vertex_matcher.cli import main
    from vertex_matcher.data import write_json

    path = tmp_path / "input.json"
    write_json(path, {"p": product.model_dump()})
    snapshot = tmp_path / "snapshot.json"
    records = {
        "p": {i: {"status": "unavailable", "reason": "HTTP403"} for i in product.search_results}
    }
    write_json(
        snapshot, {"input_sha256": "wrong", "retrieval": {"enabled": True, "products": records}}
    )
    body = json.loads(response_text)
    for d in body["decisions"]:
        d["verification_status"] = "unavailable"
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
            str(path),
            "--model",
            "test",
            "--replay-pages",
            str(snapshot),
            "--output-dir",
            str(tmp_path / "output"),
        ],
    )
    assert main() == 2
    assert provider.complete.await_count == 0
    write_json(
        snapshot,
        {
            "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "retrieval": {"enabled": True, "products": records},
        },
    )
    assert main() == 0
    report = json.loads((tmp_path / "output/report.json").read_text())
    assert report["retrieval"]["mode"] == "snapshot"
    assert report["acceptance_gates"]["fresh_50_products_within_120_seconds"] is None
    assert report["acceptance_gates"]["reference_minimum_url_verified"] is False
