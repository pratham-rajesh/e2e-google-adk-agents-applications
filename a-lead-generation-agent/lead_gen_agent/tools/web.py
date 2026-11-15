"""Safe web fetching + lightweight page enrichment.

The URLs fetched here come from LLM output, so treat them as untrusted input:
only http(s), no private / loopback / link-local addresses, bounded size and time.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx

MAX_BYTES = 400_000
TIMEOUT_S = 8.0
USER_AGENT = "lead-gen-agent/0.1 (research bot)"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_DESC_RE = re.compile(
    r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"'](.*?)[\"']", re.I | re.S
)
_LINK_RE = re.compile(r"href=[\"'](https?://[^\"']+)[\"']", re.I)
_JUNK_EMAIL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
_TECH_HINTS = {
    "shopify": "Shopify",
    "salesforce": "Salesforce",
    "hubspot": "HubSpot",
    "kubernetes": "Kubernetes",
    "snowflake": "Snowflake",
    "stripe": "Stripe",
    "zendesk": "Zendesk",
    "intercom": "Intercom",
    "segment.com": "Segment",
}


class UnsafeUrlError(ValueError):
    pass


def validate_public_url(url: str) -> str:
    """Raise UnsafeUrlError unless `url` is http(s) and resolves only to public IPs."""
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeUrlError(f"unsupported URL: {url!r}")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"cannot resolve {parsed.hostname}: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeUrlError(f"{parsed.hostname} resolves to non-public address {ip}")
    return url


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def extract_page_facts(html: str) -> dict:
    """Pure function: pull title, description, emails, social links and tech hints from HTML."""
    title = _clean(m.group(1)) if (m := _TITLE_RE.search(html)) else ""
    desc = _clean(m.group(1)) if (m := _META_DESC_RE.search(html)) else ""
    emails = sorted(
        {e.lower() for e in _EMAIL_RE.findall(html) if not e.lower().endswith(_JUNK_EMAIL_SUFFIXES)}
    )[:5]
    links = _LINK_RE.findall(html)
    social = sorted(
        {l for l in links if any(d in l for d in ("linkedin.com/company", "twitter.com/", "x.com/"))}
    )[:4]
    lowered = html.lower()
    tech = sorted({label for needle, label in _TECH_HINTS.items() if needle in lowered})
    return {"title": title, "description": desc, "emails": emails, "social": social, "tech": tech}


async def fetch_company_page(
    url: str, client: httpx.AsyncClient | None = None, check_dns: bool = True
) -> dict:
    """Fetch a homepage and return facts. Never raises: failures are reported in the result."""
    result: dict = {"url": url, "ok": False}
    try:
        if check_dns:
            url = await asyncio.to_thread(validate_public_url, url)
        result["url"] = url
        owns_client = client is None
        client = client or httpx.AsyncClient(
            timeout=TIMEOUT_S, follow_redirects=False, headers={"User-Agent": USER_AGENT}
        )
        try:
            resp = await client.get(url)
            hops = 0
            # Follow redirects manually so every hop is re-validated.
            while resp.is_redirect and hops < 3:
                nxt = str(resp.next_request.url) if resp.next_request else resp.headers["location"]
                if check_dns:
                    nxt = await asyncio.to_thread(validate_public_url, nxt)
                resp = await client.get(nxt)
                hops += 1
            resp.raise_for_status()
            html = resp.text[:MAX_BYTES]
        finally:
            if owns_client:
                await client.aclose()
        result.update(extract_page_facts(html))
        result["ok"] = True
    except (UnsafeUrlError, httpx.HTTPError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result
