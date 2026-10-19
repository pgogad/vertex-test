import asyncio
import json
import random
import time
from uuid import uuid4

from openai import APIConnectionError, APIStatusError

from .fetcher import PageFetcher
from .matcher import (
    POLICY,
    MatchingContractError,
    cache_key,
    parse_selection,
    payload_for,
    validate_verification,
)
from .provider import Provider
from .schemas import Product, Selection, Settings
from .store import Store


class RateLimiter:
    def __init__(self, requests_per_minute: float):
        self.interval = 60 / requests_per_minute
        self.next_start = 0.0
        self.lock = asyncio.Lock()

    async def wait(self, stop: asyncio.Event) -> bool:
        async with self.lock:
            if stop.is_set():
                return False
            delay = self.next_start - time.monotonic()
            if delay > 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                    return False
                except TimeoutError:
                    pass
            if stop.is_set():
                return False
            self.next_start = time.monotonic() + self.interval
            return True


def error_details(error: Exception) -> dict:
    details = {"error": type(error).__name__}
    if isinstance(error, MatchingContractError):
        details["message"] = str(error)
    elif isinstance(error, APIStatusError):
        messages = {
            400: "The API rejected the request. Check model compatibility and request settings.",
            401: "Authentication failed. Check OPENAI_API_KEY and the active project.",
            403: "Access denied. Check model and project permissions.",
            404: "Model or API resource not found. Check OPENAI_MODEL, access, and API base URL.",
            429: "Rate limit or quota exceeded. Check account limits and available credits.",
        }
        details.update(
            http_status=error.status_code,
            provider_code=error.code if isinstance(error.code, str) else None,
            request_id=error.request_id,
            message=messages.get(error.status_code, "The API returned an unsuccessful response."),
        )
    elif isinstance(error, (APIConnectionError, TimeoutError)):
        details["message"] = "Request timed out or could not connect to the API."
    else:
        details["message"] = "Model response was incomplete, refused, or failed output validation."
    return details


def retryable(error: Exception | None) -> bool:
    if isinstance(error, APIStatusError):
        return error.status_code in {408, 409, 429} or error.status_code >= 500
    return isinstance(error, (APIConnectionError, TimeoutError, ValueError))


async def run_products(
    products: dict[str, Product],
    provider: Provider,
    store: Store,
    settings: Settings,
    use_cache: bool = True,
    page_snapshot: dict | None = None,
) -> dict:
    started = time.monotonic()
    run_id = str(uuid4())
    limiter = RateLimiter(settings.requests_per_minute)
    items = iter(products.items())
    results = {}
    attempts = []
    fatal_error = asyncio.Event()
    if page_snapshot is not None and not settings.verify_links:
        raise ValueError("Page snapshot requires verification mode")
    fetcher = PageFetcher(settings) if settings.verify_links and page_snapshot is None else None
    pages = {}

    async def process(key: str, product: Product) -> dict:
        payload = payload_for(product)
        if len(payload) > settings.max_input_chars:
            return {"status": "failed", "error": "input_too_large"}
        if fatal_error.is_set():
            return {"status": "failed", "error": "provider_configuration_failure"}
        page_evidence = (
            page_snapshot["products"][key]
            if page_snapshot is not None
            else await fetcher.for_product(product)
            if fetcher
            else None
        )
        if page_evidence is not None:
            pages[key] = page_evidence
            payload = payload_for(product, page_evidence)
            if len(payload) > settings.max_input_chars:
                return {"status": "failed", "error": "input_with_pages_too_large"}
        fingerprint = cache_key(payload, settings)
        if use_cache:
            cached = store.get(fingerprint)
            if cached is not None:
                cached.validate_for(product)
                validate_verification(cached, page_evidence)
                return {"status": "succeeded", "cached": True, **cached.model_dump()}
        if not product.search_results:
            selection = Selection(identity_status="uncertain", identity_evidence="", decisions=[])
            store.save(fingerprint, selection)
            return {"status": "succeeded", "cached": False, **selection.model_dump()}
        request_payload = payload
        for attempt_number in range(1, settings.max_attempts + 1):
            if not await limiter.wait(fatal_error):
                return {"status": "failed", "error": "provider_configuration_failure"}
            attempt = {
                "attempt": attempt_number,
                "input_tokens": None,
                "output_tokens": None,
                "status": "failed",
            }
            attempt_start = time.monotonic()
            selection = None
            error = None
            try:
                reply = await asyncio.wait_for(
                    provider.complete(POLICY, request_payload), timeout=settings.timeout
                )
                attempt.update(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens)
                if reply.status != "completed" or not reply.text:
                    raise ValueError("Incomplete response or refusal")
                selection = parse_selection(reply.text, product, page_evidence)
                attempt["status"] = "succeeded"
            except (APIConnectionError, APIStatusError, TimeoutError, ValueError) as caught:
                error = caught
                attempt["error"] = type(caught).__name__
                attempt["error_details"] = error_details(caught)
                if isinstance(caught, APIStatusError):
                    attempt["http_status"] = caught.status_code
                    if caught.status_code in {401, 403, 404}:
                        fatal_error.set()
            attempt["elapsed_seconds"] = round(time.monotonic() - attempt_start, 3)
            store.save_attempt(run_id, key, attempt)
            attempts.append(attempt)
            if selection is not None:
                store.save(fingerprint, selection)
                return {"status": "succeeded", "cached": False, **selection.model_dump()}
            if not retryable(error) or attempt_number == settings.max_attempts:
                return {
                    "status": "failed",
                    "error": attempt["error"],
                    "error_details": attempt["error_details"],
                }
            if isinstance(error, MatchingContractError):
                repaired = json.dumps(
                    {**json.loads(payload), "response_repair": str(error)}, ensure_ascii=False
                )
                if len(repaired) <= settings.max_input_chars:
                    request_payload = repaired
            delay = min(2 ** (attempt_number - 1), 8) + random.random()
            try:
                await asyncio.wait_for(fatal_error.wait(), timeout=delay)
                return {"status": "failed", "error": "provider_configuration_failure"}
            except TimeoutError:
                pass
        raise RuntimeError("Attempt loop ended unexpectedly")

    async def worker() -> None:
        for key, product in items:
            results[key] = await process(key, product)

    workers = [
        asyncio.create_task(worker()) for _ in range(min(settings.concurrency, len(products)))
    ]
    try:
        await asyncio.gather(*workers)
    finally:
        for worker_task in workers:
            worker_task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        if fetcher:
            await fetcher.close()
    known_input = sum(attempt["input_tokens"] or 0 for attempt in attempts)
    known_output = sum(attempt["output_tokens"] or 0 for attempt in attempts)
    unknown_usage = sum(
        attempt["input_tokens"] is None or attempt["output_tokens"] is None for attempt in attempts
    )
    known_cost = None
    if settings.input_price_per_million is not None:
        known_cost = (
            known_input * settings.input_price_per_million
            + known_output * settings.output_price_per_million
        ) / 1_000_000
    report = {
        "run_id": run_id,
        "first_error": next(
            (attempt["error_details"] for attempt in attempts if "error_details" in attempt), None
        ),
        "settings": settings.model_dump(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "cached_products": sum(result.get("cached", False) for result in results.values()),
        "api_attempts": len(attempts),
        "input_tokens": known_input,
        "output_tokens": known_output,
        "attempts_with_unknown_usage": unknown_usage,
        "known_usage_estimated_cost_usd": known_cost,
        "estimated_cost_usd": known_cost if unknown_usage == 0 else None,
        "products": {key: results[key] for key in products},
    }
    if fetcher:
        report["retrieval"] = {
            "enabled": True,
            "mode": "live",
            "unique_urls": len(fetcher.tasks),
            "products": pages,
            "fetched_urls": sum(
                task.done() and not task.cancelled() and task.result()["status"] == "fetched"
                for task in fetcher.tasks.values()
            ),
        }
    elif page_snapshot is not None:
        report["retrieval"] = {**page_snapshot, "mode": "snapshot"}
    return report
