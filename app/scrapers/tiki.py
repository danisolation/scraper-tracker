"""
Tiki.vn Scraper — Reverse-Engineered Internal API approach.

─────────────────────────────────────────────────────────────
STRATEGY (as of 2025-2026):
─────────────────────────────────────────────────────────────
Tiki exposes a public REST API that its SPA frontend calls:
    GET https://tiki.vn/api/v2/products/{product_id}
This endpoint returns full product JSON (name, price, stock,
variants, etc.) WITHOUT authentication. The main challenge is:
  1. Anti-bot headers — must send realistic browser headers
  2. Rate limiting — respect throttling, rotate UAs
  3. Occasional Cloudflare challenges on heavy scraping

FALLBACK: If the API returns 403/captcha, we fall back to
Playwright headless browser to render the page and extract
price from the DOM / intercepted network requests.
─────────────────────────────────────────────────────────────
"""
import logging
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.scrapers.base import BaseScraper
from app.scrapers.helpers import (
    extract_tiki_product_id,
    extract_tiki_spid,
    build_base_headers,
)
from app.schemas.product import ScrapeResult
from app.config import get_settings

logger = logging.getLogger(__name__)

# Tiki's internal product API (no auth needed, returns JSON)
TIKI_API_URL = "https://tiki.vn/api/v2/products/{product_id}"


class TikiScraper(BaseScraper):
    """Scrapes product data from Tiki.vn via its internal REST API."""

    def __init__(self):
        self.settings = get_settings()
        self.timeout = httpx.Timeout(
            timeout=self.settings.REQUEST_TIMEOUT_SECONDS,
            connect=10.0,
        )

    # ── Primary: Internal API ──────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
        reraise=True,
    )
    async def _fetch_api(self, product_id: str, spid: str | None) -> dict:
        """
        Call Tiki's internal product API and return the JSON payload.
        Retries up to 3 times on timeout or 5xx errors.
        """
        url = TIKI_API_URL.format(product_id=product_id)
        params = {"platform": "web"}
        if spid:
            params["spid"] = spid

        headers = build_base_headers(referer=f"https://tiki.vn/product-p{product_id}.html")
        # Tiki checks this header — must match their frontend expectations
        headers["x-guest-token"] = "anonymous"

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, verify=False) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            return resp.json()

    def _parse_api_response(self, data: dict) -> ScrapeResult:
        """Extract price, name, and stock status from the API JSON."""
        product_name = data.get("name") or data.get("short_name")

        # Tiki has multiple price fields; priority order:
        # 1. current_seller.price  (actual selling price)
        # 2. price                 (listing price, may include discounts)
        # 3. list_price            (original price before discount)
        price = None
        current_seller = data.get("current_seller")
        if current_seller and current_seller.get("price"):
            price = float(current_seller["price"])
        elif data.get("price"):
            price = float(data["price"])

        # Stock check
        inventory = data.get("inventory_status")
        stock_item = data.get("stock_item", {})
        is_in_stock = True
        if inventory == "out_of_stock":
            is_in_stock = False
        elif stock_item and stock_item.get("qty", 0) <= 0:
            is_in_stock = False

        return ScrapeResult(
            product_name=product_name,
            price=price,
            is_in_stock=is_in_stock,
        )

    # ── Fallback: Playwright ───────────────────────

    async def _fetch_playwright(self, url: str) -> ScrapeResult:
        """
        Headless browser fallback — loads the full page and intercepts
        the product API call that the Tiki SPA makes internally.
        """
        logger.info("Tiki API blocked, falling back to Playwright for %s", url)
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=build_base_headers()["User-Agent"],
                    viewport={"width": 1920, "height": 1080},
                    locale="vi-VN",
                )
                page = await context.new_page()

                # Capture the internal API response that the page itself triggers
                captured_data: dict | None = None

                async def handle_response(response):
                    nonlocal captured_data
                    if "/api/v2/products/" in response.url and response.status == 200:
                        try:
                            captured_data = await response.json()
                        except Exception:
                            pass

                page.on("response", handle_response)

                await page.goto(url, wait_until="networkidle", timeout=30000)

                await browser.close()

                if captured_data:
                    return self._parse_api_response(captured_data)

                return ScrapeResult(error="Playwright could not capture product data from Tiki")

        except Exception as e:
            logger.exception("Playwright fallback failed for Tiki")
            return ScrapeResult(error=f"Playwright error: {str(e)}")

    # ── Public Interface ───────────────────────────

    async def scrape(self, url: str) -> ScrapeResult:
        """
        Main entry point. Tries the internal API first, falls back to
        Playwright if the API is blocked.

        Args:
            url: Full Tiki product URL (e.g. https://tiki.vn/...-p12345678.html)

        Returns:
            ScrapeResult with price, name, stock status, or an error message.
        """
        product_id = extract_tiki_product_id(url)
        if not product_id:
            return ScrapeResult(error=f"Could not extract product ID from Tiki URL: {url}")

        spid = extract_tiki_spid(url)

        # --- Attempt 1: Internal API (fast, lightweight) ---
        try:
            data = await self._fetch_api(product_id, spid)
            result = self._parse_api_response(data)
            if result.price is not None:
                logger.info("Tiki API success: %s → %s VND", product_id, result.price)
                return result
            # Price was None — might be a data issue, try Playwright
            logger.warning("Tiki API returned no price for %s, trying Playwright", product_id)
        except httpx.HTTPStatusError as e:
            logger.warning("Tiki API HTTP %s for product %s", e.response.status_code, product_id)
        except Exception as e:
            logger.warning("Tiki API error for %s: %s", product_id, e)

        # --- Attempt 2: Playwright headless browser ---
        return await self._fetch_playwright(url)
