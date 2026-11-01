"""Polite HTTP and headless browser access.

Every request goes through here so that rate limits, robots.txt, retries and
the raw page archive are handled in one place. Raw responses are saved under
data/raw/<date>/<source>/ so a parser can be fixed and re-run against the
exact pages that broke it, without hitting the site again.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger("fetch")


_CHALLENGE = ("cf-chl-", "challenge-platform", "px-captcha", "_incapsula_resource", "verify you are a human",
              "are you a robot", "access denied</title>", "attention required!")


def _is_challenge(body: str) -> bool:
    """Bot challenge interstitials, not ordinary pages that merely load a reCAPTCHA script."""
    head = body[:20000].lower()
    return len(body) < 60000 and any(m in head for m in _CHALLENGE)


class Blocked(Exception):
    """robots.txt disallows the URL, or the site answered with a bot challenge."""


class Fetcher:
    def __init__(self, cfg: dict, raw_dir: Path, source: str):
        self.cfg = cfg
        self.source = source
        self.raw_dir = raw_dir / source
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = float(cfg.get("min_interval_seconds", 3.0))
        self.respect_robots = bool(cfg.get("respect_robots_txt", True))
        self.max_retries = int(cfg.get("max_retries", 3))
        self.user_agent = cfg.get("user_agent", "RossoMarketBot/1.0")
        self._last: dict[str, float] = {}
        self._robots: dict[str, Optional[RobotFileParser]] = {}
        self.requests = 0
        self.client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Language": "en-US,en;q=0.9"},
            timeout=float(cfg.get("timeout_seconds", 30)),
            follow_redirects=True,
            http2=False,
        )

    # -------------------------------------------------------------- etiquette
    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        host = urlparse(url).scheme + "://" + urlparse(url).netloc
        if host not in self._robots:
            rp = RobotFileParser()
            try:
                r = self.client.get(host + "/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
                self._robots[host] = rp
            except httpx.HTTPError:
                self._robots[host] = None
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    def _wait(self, url: str):
        host = urlparse(url).netloc
        gap = time.monotonic() - self._last.get(host, 0)
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap + random.uniform(0, 0.5))
        self._last[host] = time.monotonic()

    def _archive(self, url: str, body: str, ext: str):
        name = hashlib.sha1(url.encode()).hexdigest()[:16] + "." + ext
        (self.raw_dir / name).write_text(body, encoding="utf-8")
        with open(self.raw_dir / "index.tsv", "a", encoding="utf-8") as f:
            f.write(f"{name}\t{url}\n")

    # -------------------------------------------------------------- requests
    def request(self, method: str, url: str, **kw) -> httpx.Response:
        if not self._allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            self._wait(url)
            try:
                r = self.client.request(method, url, **kw)
                self.requests += 1
            except httpx.HTTPError as e:
                if attempt == self.max_retries:
                    raise
                log.warning("%s %s failed (%s), retrying", method, url, e)
                time.sleep(delay); delay *= 2
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                wait = float(r.headers.get("retry-after", delay)) if str(r.headers.get("retry-after", "")).isdigit() else delay
                log.warning("%s %s -> %s, waiting %.0fs", method, url, r.status_code, wait)
                time.sleep(wait); delay *= 2
                continue
            if r.status_code in (401, 403) or _is_challenge(r.text):
                raise Blocked(f"{url} answered {r.status_code}" + (" with a bot challenge page" if r.status_code < 400 else ""))
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            self._archive(url if method == "GET" else f"{method} {url} {kw.get('json') or kw.get('data') or ''}",
                          r.text, "json" if "json" in ctype else "html")
            return r
        raise RuntimeError("unreachable")

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def get_json(self, url: str, **kw):
        return self.get(url, **kw).json()

    def post_json(self, url: str, payload: dict, **kw):
        return self.request("POST", url, json=payload, **kw).json()

    def close(self):
        self.client.close()


class Browser:
    """Headless Chromium for sites that build their pages in JavaScript.

    The useful trick: rather than scraping the rendered HTML, `sniff` records
    every JSON response the page itself downloads. Those are the site's own
    data feeds, which change far less often than its markup.
    """

    def __init__(self, cfg: dict, fetcher: Fetcher):
        self.cfg = cfg
        self.fetcher = fetcher
        self._pw = None
        self._browser = None

    # One Playwright instance per process: starting a second one while the first is running
    # fails ("Sync API inside the asyncio loop"), which is what broke Mecum on the first live run.
    _shared_pw = None
    _shared_browser = None

    def _ensure(self):
        if Browser._shared_browser is None:
            from playwright.sync_api import sync_playwright
            Browser._shared_pw = sync_playwright().start()
            Browser._shared_browser = Browser._shared_pw.chromium.launch(headless=True)
        self._browser = Browser._shared_browser
        return self._browser

    def sniff(self, url: str, url_filter: Callable[[str], bool] = lambda u: True,
              scrolls: int = 4, wait_ms: int = 2500, click_text: Optional[str] = None,
              max_pages: int = 1, next_selector: Optional[str] = None) -> tuple[list, str]:
        """Open url, scroll to trigger lazy loading, return (json_payloads, final_html)."""
        if not self.fetcher._allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        browser = self._ensure()
        ctx = browser.new_context(user_agent=self.fetcher.user_agent, locale="en-US",
                                  viewport={"width": 1400, "height": 1000})
        page = ctx.new_page()
        payloads: list = []

        def on_response(resp):
            try:
                if "json" in (resp.headers.get("content-type") or "") and url_filter(resp.url):
                    body = resp.json()
                    payloads.append({"url": resp.url, "body": body})
                    self.fetcher._archive(resp.url, json.dumps(body), "json")
            except Exception:
                pass

        page.on("response", on_response)
        self.fetcher._wait(url)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(wait_ms)
        if click_text:
            try:
                page.get_by_text(click_text, exact=False).first.click(timeout=3000)
            except Exception:
                pass
        for p in range(max_pages):
            for _ in range(scrolls):
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(900)
            if p + 1 < max_pages and next_selector:
                nxt = page.locator(next_selector).first
                if not nxt.count():
                    break
                nxt.click()
                page.wait_for_timeout(wait_ms)
        html = page.content()
        self.fetcher._archive(url, html, "html")
        ctx.close()
        return payloads, html

    def html(self, url: str, wait_ms: int = 1500) -> str:
        """Load a page in headless Chromium and return its HTML. Reuses one context so
        cookies set by the site's bot protection on the first page carry over."""
        if not self.fetcher._allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        if getattr(self, "_page", None) is None:
            self._ctx = self._ensure().new_context(
                user_agent=self.cfg.get("browser_user_agent") or
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36",
                locale="en-US", viewport={"width": 1400, "height": 1000})
            self._page = self._ctx.new_page()
        self.fetcher._wait(url)
        resp = self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        self._page.wait_for_timeout(wait_ms)
        body = self._page.content()
        self.fetcher.requests += 1
        if (resp and resp.status in (401, 403)) or _is_challenge(body):
            raise Blocked(f"{url} answered {resp.status if resp else '?'} in the browser too")
        self.fetcher._archive(url, body, "html")
        return body

    def json_in_page(self, origin_page: str, api_url: str):
        """Call a site's JSON API from inside one of its own pages, so the request carries the
        cookies and browser fingerprint its bot protection expects."""
        if getattr(self, "_page", None) is None or not self._page.url.startswith(origin_page.split("/", 3)[0] + "//" + origin_page.split("/")[2]):
            self.html(origin_page, wait_ms=3000)
        self.fetcher._wait(api_url)
        result = self._page.evaluate(
            """async (u) => { const r = await fetch(u, {credentials: 'include', headers: {'Accept': 'application/json'}});
                              return {status: r.status, body: await r.text()}; }""", api_url)
        self.fetcher.requests += 1
        if result["status"] in (401, 403) or _is_challenge(result["body"]):
            raise Blocked(f"{api_url} answered {result['status']} inside the browser")
        self.fetcher._archive(api_url, result["body"], "json")
        return json.loads(result["body"])

    def capture_requests(self, url: str, contains: str, wait_ms: int = 5000) -> list[str]:
        """Open url and return the URLs of requests the page makes that contain `contains`.
        Used to read public, runtime issued search keys (Mecum's Algolia key)."""
        if not self.fetcher._allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        ctx = self._ensure().new_context(user_agent=self.fetcher.user_agent, locale="en-US")
        page = ctx.new_page()
        seen: list[str] = []
        page.on("request", lambda req: seen.append(req.url) if contains in req.url else None)
        self.fetcher._wait(url)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(wait_ms)
        ctx.close()
        return seen

    def close(self):
        if getattr(self, "_ctx", None) is not None:
            self._ctx.close()
            self._ctx = self._page = None

    @staticmethod
    def shutdown():
        if Browser._shared_browser is not None:
            Browser._shared_browser.close()
            Browser._shared_pw.stop()
            Browser._shared_browser = Browser._shared_pw = None
