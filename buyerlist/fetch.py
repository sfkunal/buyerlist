"""Fetch and reduce a website to the few pages that actually carry signal.

Two design points matter here:

1. We score and select pages rather than crawling breadth-first. Feeding whole
   sites to the model is what makes extraction expensive; feeding the ~12 pages
   that carry the signal is what makes it cheap.

2. The same crawler serves both directions of the pipeline. `PROFILES` holds the
   URL-scoring keywords for an SMB target vs. a PE firm, and nothing else differs.

Everything is cached to disk, so development re-runs cost nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from selectolax.parser import HTMLParser

CACHE_DIR = Path("data/cache")
CACHE_VERSION = 2  # bump when the cached payload shape changes
# Identifying the crawler is basic courtesy to the small sites this hits, so the
# contact is part of the UA. Set a real address before running at any volume.
USER_AGENT = (
    "buyerlist-research/0.1 (SMB M&A buyer-list prototype; "
    "contact: enter_email_here@gmail.com)"
)
MAX_CONCURRENCY = 8
REQUEST_TIMEOUT = 20.0
HARD_TIMEOUT = 35.0      # wall-clock ceiling per request
PAGE_HARD_TIMEOUT = 50.0   # per-page ceiling: the request ceiling plus queueing slack
SITE_HARD_TIMEOUT = 150.0  # wall-clock ceiling for one whole site
MAX_PAGES_DEFAULT = 12
MIN_USEFUL_TEXT = 400  # chars across whole site, below which we call it degraded


# --------------------------------------------------------------------------
# Page scoring profiles
# --------------------------------------------------------------------------

# Positive keywords are matched against the URL path and link text. Higher
# weight = pull this page in sooner. Negative keywords are hard skips.
PROFILES: dict[str, dict] = {
    "company": {
        "keywords": {
            "about": 10, "who-we-are": 10, "our-story": 9, "company": 8,
            "service": 9, "services": 9, "what-we-do": 9, "solutions": 7,
            "products": 8, "capabilities": 8, "industries": 7, "markets": 7,
            "location": 8, "locations": 8, "areas-we-serve": 9, "service-area": 9,
            "team": 7, "leadership": 7, "staff": 6, "our-people": 6,
            "careers": 5, "jobs": 5, "employment": 5,
            "contact": 4, "history": 6, "fleet": 6, "equipment": 6,
            "certifications": 5, "licenses": 5, "clients": 5, "customers": 5,
        },
        "skip": [
            "privacy", "terms", "cookie", "sitemap.xml", "login", "cart",
            "wp-content", "wp-admin", "/tag/", "/author/", "feed", ".pdf",
            "facebook.com", "twitter.com", "linkedin.com", "instagram.com",
        ],
    },
    "fund": {
        "keywords": {
            # The criteria page is the single highest-value page for a PE firm.
            "criteria": 15, "investment-criteria": 15, "what-we-look-for": 14,
            "investment-approach": 12, "our-approach": 10, "strategy": 11,
            "portfolio": 14, "investments": 13, "companies": 11, "our-portfolio": 14,
            "about": 8, "team": 6, "firm": 8, "who-we-are": 8,
            "news": 7, "press": 7, "insights": 4, "announcements": 7,
            "sectors": 10, "industries": 10, "focus": 10,
        },
        "skip": [
            "privacy", "terms", "cookie", "login", "disclosure", "legal",
            "wp-content", "wp-admin", "/tag/", "/author/", "feed",
            "facebook.com", "twitter.com", "linkedin.com", "instagram.com",
        ],
    },
}


@dataclass
class FetchedPage:
    url: str
    title: str
    text: str
    score: int
    status: int

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass
class SiteSnapshot:
    root_url: str
    profile: str
    pages: list[FetchedPage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    degraded: bool = False
    fetched_at: float = field(default_factory=time.time)

    @property
    def total_chars(self) -> int:
        return sum(p.chars for p in self.pages)

    def to_prompt_text(self, max_chars: int = 45_000) -> str:
        """Render the snapshot as the model-facing document.

        Pages are already score-ordered, so truncation drops the least useful
        page rather than the tail of every page.
        """
        parts: list[str] = []
        budget = max_chars
        for p in self.pages:
            if budget <= 0:
                break
            # Per-page cap scales with score. Criteria and portfolio pages carry
            # the signal; news/listing pages are mostly repeated boilerplate and
            # would otherwise crowd them out of the budget.
            cap = 12_000 if p.score >= 12 else (6_000 if p.score >= 8 else 2_500)
            body = p.text[: min(cap, max(0, budget))]
            parts.append(f'<page url="{p.url}" title="{p.title}">\n{body}\n</page>')
            budget -= len(body)
        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_chars"] = self.total_chars
        return d


# --------------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------------


def _cache_path(url: str) -> Path:
    return CACHE_DIR / f"{hashlib.sha1(url.encode()).hexdigest()}.json"


def _cache_get(url: str) -> dict | None:
    p = _cache_path(url)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text())
    except json.JSONDecodeError:
        return None
    # Ignore entries written by an older payload shape. Cheaper than remembering
    # to clear the cache by hand every time the schema moves.
    if payload.get("_v") != CACHE_VERSION:
        return None
    return payload


def _cache_put(url: str, payload: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "_v": CACHE_VERSION}
    _cache_path(url).write_text(json.dumps(payload))


# --------------------------------------------------------------------------
# Text extraction
# --------------------------------------------------------------------------


def extract_text(html: str) -> tuple[str, str]:
    """Return (title, text). Falls back to raw DOM text when trafilatura bails.

    trafilatura is tuned for articles and sometimes returns nothing on the
    brochure-style sites that dominate this dataset, so the fallback matters.
    """
    title = ""
    try:
        tree = HTMLParser(html)
        if tree.head is not None:
            node = tree.css_first("title")
            if node:
                title = node.text(strip=True)
    except Exception:
        pass

    text = trafilatura.extract(
        html, include_comments=False, include_tables=True, favor_precision=True
    )
    if not text or len(text) < 200:
        try:
            tree = HTMLParser(html)
            for sel in ("script", "style", "nav", "footer", "noscript", "svg"):
                for node in tree.css(sel):
                    node.decompose()
            body = tree.body
            raw = body.text(separator=" ", strip=True) if body else ""
            raw = re.sub(r"\s+", " ", raw)
            if len(raw) > len(text or ""):
                text = raw
        except Exception:
            pass

    return title, _squeeze(text or "")


_ERROR_PAGE_MARKERS = (
    "nothing found",
    "page not found",
    "404 not found",
    "page you are looking for",
    "post you are looking for",
    "page cannot be found",
    "page doesn't exist",
    "page does not exist",
    "this page isn't available",
)


def _looks_like_error_page(title: str, text: str) -> bool:
    """Catch themed error pages served with a 2xx status.

    Some CMS themes return a decorated "not found" page under HTTP 200. Those
    carry enough boilerplate to clear the minimum-length guard, so without this
    they reach the model as though they were the page we asked for. Only the
    opening of the document is examined, and only for short pages, so a real
    page that merely mentions a 404 somewhere is not discarded.
    """
    head = f"{title} {text[:400]}".lower()
    if len(text) > 1500:
        return False
    return any(marker in head for marker in _ERROR_PAGE_MARKERS)


def _squeeze(text: str, limit: int = 12_000) -> str:
    """Collapse whitespace and cap per-page length.

    Most of these sites repeat a nav block and a footer on every page; the cap
    keeps that repetition from dominating the token budget.
    """
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()[:limit]


# --------------------------------------------------------------------------
# Link discovery and scoring
# --------------------------------------------------------------------------


def score_url(url: str, link_text: str, profile: str) -> int:
    cfg = PROFILES[profile]
    haystack = f"{urlparse(url).path.lower()} {link_text.lower()}"

    for bad in cfg["skip"]:
        if bad in haystack or bad in url.lower():
            return -1

    score = 0
    for kw, weight in cfg["keywords"].items():
        if kw in haystack:
            score = max(score, weight)

    # Shallow paths tend to be the real section pages rather than deep leaf content.
    depth = len([s for s in urlparse(url).path.split("/") if s])
    if depth <= 1:
        score += 2
    elif depth >= 4:
        score -= 3
    return score


def discover_links(html: str, base_url: str, profile: str) -> list[tuple[str, int]]:
    base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    out: dict[str, int] = {}
    try:
        tree = HTMLParser(html)
    except Exception:
        return []

    for node in tree.css("a"):
        href = node.attributes.get("href")
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href).split("#")[0].rstrip("/")
        host = urlparse(absolute).netloc.lower().removeprefix("www.")
        if host != base_host:
            continue
        s = score_url(absolute, node.text(strip=True) or "", profile)
        if s > 0:
            out[absolute] = max(out.get(absolute, 0), s)

    return sorted(out.items(), key=lambda kv: -kv[1])


def _cap_by_path_prefix(
    ranked: list[tuple[str, int]], limit: int, per_prefix: int = 2
) -> list[tuple[str, int]]:
    """Take the top `limit` pages, allowing at most `per_prefix` per parent path.

    SMB sites are full of templated SEO doorway pages — /service-area/affton,
    /service-area/arnold, /service-area/ballwin — that all score identically and
    carry near-identical text. Without this cap a dozen suburb pages crowd out
    the about, services, and team pages that actually carry signal.
    """
    seen: dict[str, int] = {}
    out: list[tuple[str, int]] = []
    for url, score in ranked:
        segs = [s for s in urlparse(url).path.split("/") if s]
        prefix = "/".join(segs[:-1]) if len(segs) > 1 else (segs[0] if segs else "")
        if seen.get(prefix, 0) >= per_prefix:
            continue
        seen[prefix] = seen.get(prefix, 0) + 1
        out.append((url, score))
        if len(out) >= limit:
            break
    return out


async def _sitemap_urls(client: httpx.AsyncClient, root: str, profile: str) -> list[tuple[str, int]]:
    """Best-effort sitemap read. Many SMB sites have one; plenty don't."""
    candidates = [urljoin(root, "/sitemap.xml"), urljoin(root, "/sitemap_index.xml")]
    found: dict[str, int] = {}
    for sm in candidates:
        try:
            r = await client.get(sm, timeout=10.0)
            if r.status_code != 200 or "<url" not in r.text[:5000]:
                continue
            for m in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", r.text):
                u = m.group(1).split("#")[0].rstrip("/")
                s = score_url(u, "", profile)
                if s > 0:
                    found[u] = max(found.get(u, 0), s)
            if found:
                break
        except Exception:
            continue
    return sorted(found.items(), key=lambda kv: -kv[1])


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------


UA_TOKEN = "buyerlist-research"  # robots.txt matches on the agent token, not the full string


def _robots_ok(root: str) -> tuple[bool, str]:
    """Check robots.txt, fetching it ourselves.

    RobotFileParser.read() fetches with a stdlib urllib User-Agent, which WAFs in
    front of these sites routinely 403 — and a 403 on robots.txt makes the parser
    report disallow_all. That reads a permissive robots.txt as a blanket block.
    We fetch with our real UA and hand the text to parse() instead, and treat an
    unreachable robots.txt as "no restrictions stated".
    """
    robots_url = urljoin(root, "/robots.txt")
    try:
        r = httpx.get(
            robots_url,
            timeout=10.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
    except Exception:
        return (True, "")

    if r.status_code != 200 or not r.text.strip():
        return (True, "")

    try:
        rp = RobotFileParser()
        rp.parse(r.text.splitlines())
        if rp.can_fetch(UA_TOKEN, root):
            return (True, "")
        return (False, "robots.txt disallows this agent")
    except Exception:
        return (True, "")


async def _get(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> dict | None:
    cached = _cache_get(url)
    if cached is not None:
        return cached
    async with sem:
        try:
            # asyncio.wait_for, not just the httpx timeout. httpx timeouts are
            # per-operation: a server that trickles bytes resets the read timeout
            # on every byte and the request never returns. One such site stalled
            # a whole index build. This is the only hard wall-clock bound.
            r = await asyncio.wait_for(
                client.get(url, timeout=REQUEST_TIMEOUT, follow_redirects=True),
                timeout=HARD_TIMEOUT,
            )
            ctype = r.headers.get("content-type", "")
            # Track the post-redirect URL: sites frequently serve from a canonical
            # domain that differs from the one we were handed, and all their
            # internal links point at that canonical host.
            final_url = str(r.url).split("#")[0].rstrip("/")
            if "html" not in ctype and "text" not in ctype:
                payload = {
                    "url": url, "final_url": final_url,
                    "status": r.status_code, "html": "", "skipped": ctype,
                }
            else:
                payload = {
                    "url": url, "final_url": final_url,
                    "status": r.status_code, "html": r.text, "skipped": "",
                }
        except Exception as e:
            payload = {
                "url": url, "final_url": url, "status": 0,
                "html": "", "skipped": f"error: {type(e).__name__}",
            }
        # Only successes are persisted. The disk cache has no expiry, so caching a
        # DNS blip or a 503 would drop this firm from every later resumed build —
        # the opposite of what a resumable pipeline needs. Non-HTML and 4xx/5xx
        # responses are real answers from the server and still cache as before.
        if payload["status"] != 0:
            _cache_put(url, payload)
        await asyncio.sleep(0.25)  # be polite to small sites
        return payload


async def _get_bounded(
    client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore, deadline: float
) -> dict | None:
    """Fetch one page under its own wall-clock bound, returning None if it blows it.

    A single site-wide timeout around the gather cancelled every sibling request,
    so one trickling server cost us the entire site and left only the homepage.
    Bounding each page separately degrades that to one missing page. The clock
    starts when the task is created, not when the semaphore lets it through, so
    the whole batch is also bounded — and `deadline` caps the batch explicitly.
    """
    budget = min(PAGE_HARD_TIMEOUT, deadline - asyncio.get_running_loop().time())
    if budget <= 0:
        return None
    try:
        return await asyncio.wait_for(_get(client, url, sem), timeout=budget)
    except Exception:
        return None


async def fetch_site(
    root_url: str, profile: str = "company", max_pages: int = MAX_PAGES_DEFAULT
) -> SiteSnapshot:
    if not root_url.startswith("http"):
        root_url = "https://" + root_url
    root_url = root_url.rstrip("/")

    snap = SiteSnapshot(root_url=root_url, profile=profile)

    allowed, why = _robots_ok(root_url)
    if not allowed:
        snap.notes.append(why)
        snap.degraded = True
        return snap

    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        home = await _get(client, root_url, sem)
        if not home or not home.get("html"):
            snap.notes.append(f"homepage unreachable ({home.get('skipped') if home else 'no data'})")
            snap.degraded = True
            return snap

        # Re-root on the canonical domain if we were redirected. Without this,
        # same-host link filtering rejects the entire nav.
        effective_root = home.get("final_url") or root_url
        if urlparse(effective_root).netloc.lower() != urlparse(root_url).netloc.lower():
            snap.notes.append(f"redirected to canonical domain {effective_root}")
            snap.root_url = effective_root

        title, text = extract_text(home["html"])
        snap.pages.append(
            FetchedPage(
                url=effective_root, title=title, text=text, score=100, status=home["status"]
            )
        )

        candidates = dict(await _sitemap_urls(client, effective_root, profile))
        for u, s in discover_links(home["html"], effective_root, profile):
            candidates[u] = max(candidates.get(u, 0), s)
        candidates.pop(effective_root, None)
        candidates.pop(root_url, None)

        ranked = _cap_by_path_prefix(
            sorted(candidates.items(), key=lambda kv: -kv[1]), max_pages - 1
        )
        if not ranked:
            snap.notes.append("no internal links scored above threshold (likely a one-pager)")

        deadline = asyncio.get_running_loop().time() + SITE_HARD_TIMEOUT
        results = await asyncio.gather(
            *[_get_bounded(client, u, sem, deadline) for u, _ in ranked],
            return_exceptions=True,
        )

        dropped = sum(1 for res in results if not isinstance(res, dict))
        if dropped:
            snap.notes.append(
                f"{dropped} of {len(ranked)} sub-page(s) dropped (timed out or errored); "
                "the rest were kept"
            )

        for (u, s), res in zip(ranked, results):
            # `res` may be None (page-level timeout) or an Exception instance,
            # since gather runs with return_exceptions=True.
            if not isinstance(res, dict) or not res.get("html"):
                continue
            # Reject error responses. Themed 404 pages carry enough boilerplate
            # ("Nothing Found — the post you are looking for is not available")
            # to clear the length guard below, so without this they reach the
            # model as if they were the About page.
            if res.get("status", 0) >= 400:
                continue
            t, txt = extract_text(res["html"])
            if len(txt) < 120:  # nav-only shell, not worth the tokens
                continue
            if _looks_like_error_page(t, txt):
                continue
            snap.pages.append(
                FetchedPage(url=u, title=t, text=txt, score=s, status=res["status"])
            )

    snap.pages.sort(key=lambda p: -p.score)

    if snap.total_chars < MIN_USEFUL_TEXT:
        snap.degraded = True
        snap.notes.append(
            f"only {snap.total_chars} chars extracted across {len(snap.pages)} page(s) — "
            "likely JavaScript-rendered or image-only"
        )

    return snap


def fetch_site_sync(root_url: str, profile: str = "company", max_pages: int = MAX_PAGES_DEFAULT):
    return asyncio.run(fetch_site(root_url, profile, max_pages))
