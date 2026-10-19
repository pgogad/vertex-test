import asyncio
import hashlib
import ipaddress
import re
import socket
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser

import aiohttp
from yarl import URL

from .schemas import Product, Settings

MAX_BYTES = 2_000_000


def require_public_address(address: str) -> None:
    parsed = ipaddress.ip_address(address)
    if not parsed.is_global or parsed.is_multicast or parsed.is_reserved:
        raise ValueError("non_public_address")
    if isinstance(parsed, ipaddress.IPv6Address) and (
        parsed.ipv4_mapped or parsed.sixtofour or parsed.teredo
    ):
        raise ValueError("transition_address")


def checked_url(value: str) -> URL:
    url = URL(value)
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.user is not None
        or url.password is not None
        or url.port != (443 if url.scheme == "https" else 80)
        or "%" in url.host
    ):
        raise ValueError("unsupported_url")
    try:
        ipaddress.ip_address(url.host)
    except ValueError:
        pass
    else:
        require_public_address(url.host)
    return url.with_fragment(None)


class PublicResolver(aiohttp.ThreadedResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        addresses = await super().resolve(host, port, family)
        if not addresses:
            raise ValueError("no_addresses")
        for address in addresses:
            require_public_address(address["host"])
        return addresses


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "template", "nav", "footer"}:
            self.hidden.append(tag)

    def handle_endtag(self, tag):
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


async def download(url: str, settings: Settings) -> dict:
    current = checked_url(url)
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), limit=1)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=settings.page_timeout),
        trust_env=False,
        cookie_jar=aiohttp.DummyCookieJar(),
        auto_decompress=False,
        headers={"User-Agent": "VertexMatcher/0.1", "Accept-Encoding": "identity"},
    ) as session:
        for redirect in range(4):
            async with session.get(current, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    if redirect == 3 or not response.headers.get("Location"):
                        raise ValueError("redirect_limit_or_missing_location")
                    current = checked_url(str(current.join(URL(response.headers["Location"]))))
                    continue
                response.raise_for_status()
                if response.status != 200:
                    raise ValueError("unsupported_status")
                if response.content_type not in {"text/html", "text/plain"}:
                    if response.content_type != "application/pdf":
                        raise ValueError("unsupported_content_type")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise ValueError("unsupported_encoding")
                body = bytearray()
                body_complete = True
                async for chunk in response.content.iter_chunked(16384):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        if response.content_type == "application/pdf":
                            raise ValueError("page_too_large")
                        del body[MAX_BYTES:]
                        body_complete = False
                        break
                if response.content_type == "application/pdf":
                    text = await pdf_text(bytes(body))
                else:
                    text = body.decode(response.charset or "utf-8", errors="replace")
                if response.content_type == "text/html":
                    parser = PageText()
                    parser.feed(text)
                    parser.close()
                    text = " ".join(parser.parts)
                text = " ".join(text.split())
                if not text:
                    raise ValueError("empty_page")
                return {
                    "status": "fetched",
                    "final_url": str(current),
                    "text": text[:60000],
                    "truncated": not body_complete or len(text) > 60000,
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                    "body_complete": body_complete,
                    "body_hash_scope": "complete" if body_complete else "prefix",
                    "content_type": response.content_type,
                }
    raise ValueError("no_response")


async def pdf_text(body: bytes) -> str:
    # Isolate untrusted PDF parsing; an asyncio timeout alone cannot stop CPU parsing.
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "vertex_matcher.page_pdf",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(body), timeout=4)
        if process.returncode:
            raise ValueError("pdf_extraction_failed")
        return stdout.decode("utf-8")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


class PageFetcher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.semaphore = asyncio.Semaphore(min(settings.concurrency, 3))
        self.tasks = {}
        self.rate_lock = asyncio.Lock()
        self.next_start = 0.0

    async def fetch(self, url: str) -> dict:
        async with self.semaphore:
            async with self.rate_lock:
                delay = self.next_start - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                self.next_start = time.monotonic() + 60 / self.settings.page_requests_per_minute
            started = time.monotonic()
            record = {"url": url, "fetched_at": datetime.now(timezone.utc).isoformat()}
            try:
                async with asyncio.timeout(self.settings.page_timeout):
                    record.update(await download(url, self.settings))
            except (aiohttp.ClientError, OSError, ValueError, LookupError, TimeoutError) as error:
                record.update(status="unavailable", error=type(error).__name__)
                if type(error) is ValueError:
                    record["reason"] = str(error)
                if isinstance(error, aiohttp.ClientResponseError):
                    record["http_status"] = error.status
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            return record

    async def for_product(self, product: Product) -> dict:
        selected = {}
        urls = []
        for candidate_id, candidate in product.search_results.items():
            match = re.search(r"^Link:[ \t]*(\S+)", candidate, flags=re.MULTILINE)
            if not match:
                selected[candidate_id] = None
                continue
            url = match.group(1)
            if url not in urls:
                if len(urls) >= self.settings.max_pages_per_product:
                    selected[candidate_id] = None
                    continue
                urls.append(url)
            if url not in self.tasks:
                self.tasks[url] = asyncio.create_task(self.fetch(url))
            selected[candidate_id] = self.tasks[url]
        records = {}
        for candidate_id, task in selected.items():
            record = (
                dict(await task)
                if task is not None
                else {"status": "unavailable", "reason": "missing_link_or_page_limit"}
            )
            if record["status"] == "fetched":
                original = record["text"]
                record["text"] = product_excerpt(
                    original, product.product_title, self.settings.page_max_chars
                )
                record["truncated"] = record.get("truncated", False) or (
                    len(original) > self.settings.page_max_chars
                )
            records[candidate_id] = record
        return records

    async def close(self) -> None:
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


def product_excerpt(text: str, title: str, budget: int) -> str:
    """Keep the heading plus relevant literal text windows; never synthesize product facts."""
    if len(text) <= budget:
        return text
    tokens = {t for t in re.findall(r"\w+", title.casefold()) if len(t) > 2}
    heading_size = min(600, budget // 3)
    remaining = budget - heading_size - 7
    windows = [text[start : start + remaining] for start in range(heading_size, len(text), 500)]
    best = max(windows, key=lambda chunk: sum(token in chunk.casefold() for token in tokens))
    return text[:heading_size] + "\n[...]\n" + best
