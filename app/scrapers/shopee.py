"""
Shopee.vn Scraper — Reverse-Engineered Internal API approach.

─────────────────────────────────────────────────────────────
STRATEGY (as of 2025-2026):
─────────────────────────────────────────────────────────────
Shopee is significantly harder to scrape than Tiki because:
  1. Heavy anti-bot (Cloudflare + custom WAF + device fingerprinting)
  2. The product API requires an `af_ac_enc_dat` anti-fraud token
     and a rotating `SPC_CDS` / `csrftoken` cookie.
  3. Direct API calls are often blocked with 403 or captcha.

PRIMARY APPROACH:
  We attempt the public item detail API:
      GET https://shopee.vn/api/v4/item/get?itemid={item_id}&shopid={shop_id}
  This sometimes works with correct headers, cookies, and referrer.

FALLBACK:
  When API calls fail (very common), we use Playwright to load
  the product page, intercept the XHR response from Shopee's own
  frontend, and extract the price from the intercepted JSON.

NOTE ON RATE LIMITING:
  Shopee aggressively blocks IPs making >10 req/min. In production
  you MUST use rotating residential proxies for sustained scraping.
─────────────────────────────────────────────────────────────
"""
import logging
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.scrapers.base import BaseScraper
from app.scrapers.helpers import extract_shopee_ids, build_base_headers
from app.schemas.product import ScrapeResult
from app.config import get_settings

logger = logging.getLogger(__name__)

SHOPEE_ITEM_API = "https://shopee.vn/api/v4/item/get"


class ShopeeScraper(BaseScraper):
    """Scrapes product data from Shopee.vn via internal API + Playwright fallback."""

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
    async def _fetch_api(self, shop_id: str, item_id: str) -> dict:
        """
        Attempt Shopee's internal item API. This requires browser-like
        headers and may fail with 403 if anti-bot kicks in.
        """
        headers = build_base_headers(referer=f"https://shopee.vn/product-i.{shop_id}.{item_id}")
        # Shopee checks these specific headers
        headers.update({
            "x-shopee-language": "vi",
            "x-requested-with": "XMLHttpRequest",
            "x-api-source": "pc",
        })

        params = {
            "itemid": item_id,
            "shopid": shop_id,
        }

        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            verify=False,  # Bypass SSL issues (corporate proxies, ISP interception)
        ) as client:
            resp = await client.get(SHOPEE_ITEM_API, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

            # Shopee wraps the response: {"error": 0, "data": {...}}
            if data.get("error") not in (0, None):
                raise httpx.HTTPStatusError(
                    f"Shopee API error code: {data.get('error')}",
                    request=resp.request,
                    response=resp,
                )
            return data

    def _parse_api_response(self, data: dict) -> ScrapeResult:
        """
        Parse the nested Shopee API response.
        Price is returned in units of 100000 (i.e., actual price * 100000).
        """
        item_data = data.get("data") or {}

        product_name = item_data.get("name")

        # Shopee returns prices multiplied by 100000
        raw_price = item_data.get("price")
        price_min = item_data.get("price_min")
        price_max = item_data.get("price_max")

        price = None
        if raw_price and raw_price > 0:
            price = raw_price / 100000
        elif price_min and price_min > 0:
            price = price_min / 100000

        # Stock check
        stock = item_data.get("stock", 0)
        sold_out = item_data.get("is_sold_out", False)
        is_in_stock = stock > 0 and not sold_out

        return ScrapeResult(
            product_name=product_name,
            price=price,
            is_in_stock=is_in_stock,
        )

    # ── Fallback: Playwright ───────────────────────

    def _run_playwright_sync(self, url: str, shop_id: str, item_id: str) -> ScrapeResult:
        """
        Run Playwright using the SYNC API in a worker thread.
        On Windows, we must set ProactorEventLoopPolicy so that the
        internal event loop Playwright creates can spawn subprocesses.
        """
        import asyncio, sys, re
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=build_base_headers()["User-Agent"],
                viewport={"width": 1920, "height": 1080},
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
            )
            page = context.new_page()

            # Block unnecessary resources to speed up page load
            page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2}", lambda route: route.abort())
            page.route("**/ads/**", lambda route: route.abort())

            captured_data: dict | None = None

            def handle_response(response):
                nonlocal captured_data
                if (
                    "/api/v4/item/get" in response.url
                    and response.status == 200
                ):
                    try:
                        captured_data = response.json()
                    except Exception:
                        pass

            page.on("response", handle_response)

            page.goto(url, wait_until="networkidle", timeout=45000)

            if not captured_data:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                page.wait_for_timeout(3000)

            # Try XHR-intercepted data first
            if captured_data:
                browser.close()
                return self._parse_api_response(captured_data)

            # Fallback: extract price directly from the rendered DOM
            logger.info("Shopee XHR intercept failed, trying DOM extraction")

            # Debug: dump page HTML for analysis
            try:
                debug_html = page.content()
                debug_title = page.title()
                debug_url = page.url
                logger.info("Page title: %s", debug_title)
                logger.info("Page URL after navigation: %s", debug_url)
                logger.info("Page HTML length: %d chars", len(debug_html))
                with open("shopee_debug.html", "w", encoding="utf-8") as f:
                    f.write(debug_html)
                logger.info("Saved debug HTML to shopee_debug.html")
            except Exception as e:
                logger.debug("Could not save debug HTML: %s", e)

            result = self._extract_from_dom(page)
            browser.close()
            return result

    def _extract_from_dom(self, page) -> ScrapeResult:
        """Extract product name & price from the rendered Shopee page DOM."""
        import re, json

        product_name = None
        price = None

        # ── 1. Try JSON-LD structured data (most reliable) ──
        try:
            ld_scripts = page.query_selector_all('script[type="application/ld+json"]')
            for script in ld_scripts:
                raw = script.inner_text()
                data = json.loads(raw)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") == "Product":
                        product_name = product_name or item.get("name")
                        offers = item.get("offers") or {}
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        p = offers.get("price") or offers.get("lowPrice")
                        if p:
                            price = float(p)
                            logger.info("JSON-LD extraction got price: %s", price)
                            return ScrapeResult(product_name=product_name, price=price, is_in_stock=True)
        except Exception as e:
            logger.debug("JSON-LD extraction failed: %s", e)

        # ── 2. Try meta tags (og:title, product:price) ──
        try:
            meta_title = page.query_selector('meta[property="og:title"]')
            if meta_title:
                product_name = product_name or meta_title.get_attribute("content")

            for attr in ["product:price:amount", "og:price:amount"]:
                meta_price = page.query_selector(f'meta[property="{attr}"]')
                if meta_price:
                    val = meta_price.get_attribute("content")
                    if val:
                        cleaned = re.sub(r"[^\d.]", "", val)
                        if cleaned:
                            price = float(cleaned)
                            logger.info("Meta tag extraction got price: %s", price)
                            return ScrapeResult(product_name=product_name, price=price, is_in_stock=True)
        except Exception as e:
            logger.debug("Meta tag extraction failed: %s", e)

        # ── 3. Try CSS selectors on rendered DOM ──
        if not product_name:
            try:
                for selector in ["h1", "div[class*='product-briefing'] span"]:
                    el = page.query_selector(selector)
                    if el:
                        text = el.inner_text().strip()
                        if len(text) > 5:
                            product_name = text
                            break
            except Exception:
                pass

        if not product_name:
            try:
                product_name = page.title().split(" | ")[0].strip() or None
            except Exception:
                pass

        try:
            # Walk all elements looking for price-like text with ₫
            price_elements = page.query_selector_all("[class*='price'], [class*='Price']")
            for el in price_elements:
                text = el.inner_text().strip()
                cleaned = re.sub(r"[^\d.,]", "", text.replace("₫", ""))
                cleaned = cleaned.replace(".", "").replace(",", "")
                if cleaned.isdigit() and int(cleaned) > 1000:
                    price = float(cleaned)
                    break
        except Exception:
            pass

        # ── 4. Regex scan page source for price patterns ──
        if price is None:
            try:
                html = page.content()
                # Look for "price":12345 or "price_min":12345 in inline JS/JSON
                for pattern in [
                    r'"price"\s*:\s*(\d{5,})',
                    r'"price_min"\s*:\s*(\d{5,})',
                    r'"priceMin"\s*:\s*(\d{5,})',
                ]:
                    m = re.search(pattern, html)
                    if m:
                        raw_val = int(m.group(1))
                        # Shopee stores prices * 100000
                        if raw_val > 1_000_000:
                            price = raw_val / 100_000
                        else:
                            price = float(raw_val)
                        logger.info("HTML regex extraction got price: %s", price)
                        break
            except Exception as e:
                logger.debug("HTML regex extraction failed: %s", e)

        # ── 5. Regex on visible text ──
        if price is None:
            try:
                body_text = page.inner_text("body")
                matches = re.findall(r"₫\s*([\d.]+)", body_text)
                for m in matches:
                    cleaned = m.replace(".", "")
                    if cleaned.isdigit() and int(cleaned) > 1000:
                        price = float(cleaned)
                        break
            except Exception:
                pass

        if price is not None:
            logger.info("DOM extraction got price: %s", price)
            return ScrapeResult(product_name=product_name, price=price, is_in_stock=True)

        logger.warning("DOM extraction could not find price")
        return ScrapeResult(
            product_name=product_name,
            error="Playwright could not capture product price from Shopee",
        )

    # ── Fallback 2: Direct HTML fetch ──────────────

    async def _fetch_page_html(self, url: str, shop_id: str, item_id: str) -> ScrapeResult:
        """
        Fetch the product page HTML directly with httpx.
        Shopee embeds product data in <script> tags and meta tags for
        SEO/social crawlers — no login required for this data.
        """
        import re, json

        logger.info("Trying direct HTML fetch for Shopee page")
        headers = build_base_headers(referer="https://shopee.vn/")
        # Use a Googlebot-like UA — Shopee serves richer HTML to search crawlers
        headers["User-Agent"] = (
            "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
        )

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                verify=False,
            ) as client:
                resp = await client.get(url, headers=headers)
                html = resp.text
                logger.info("HTML fetch status %s, length %d", resp.status_code, len(html))
        except Exception as e:
            logger.warning("HTML fetch failed: %s", e)
            return ScrapeResult(error=f"HTML fetch failed: {e}")

        product_name = None
        price = None

        # ── 1. JSON-LD ──
        try:
            for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
                data = json.loads(m.group(1))
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") == "Product":
                        product_name = item.get("name")
                        offers = item.get("offers") or {}
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        p = offers.get("price") or offers.get("lowPrice")
                        if p:
                            price = float(p)
                            logger.info("HTML JSON-LD got price: %s", price)
                            return ScrapeResult(product_name=product_name, price=price, is_in_stock=True)
        except Exception as e:
            logger.debug("HTML JSON-LD parse failed: %s", e)

        # ── 2. Meta tags ──
        try:
            m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', html)
            if m:
                product_name = product_name or m.group(1)

            for prop in ["product:price:amount", "og:price:amount"]:
                m = re.search(rf'<meta[^>]+property=["\']' + re.escape(prop) + r'["\'][^>]+content=["\']([^"\']+)', html)
                if m:
                    cleaned = re.sub(r"[^\d.]", "", m.group(1))
                    if cleaned:
                        price = float(cleaned)
                        logger.info("HTML meta tag got price: %s", price)
                        return ScrapeResult(product_name=product_name, price=price, is_in_stock=True)
        except Exception as e:
            logger.debug("HTML meta parse failed: %s", e)

        # ── 3. Regex on inline JSON in <script> tags ──
        try:
            for pattern in [
                r'"price"\s*:\s*(\d{5,})',
                r'"price_min"\s*:\s*(\d{5,})',
            ]:
                m = re.search(pattern, html)
                if m:
                    raw_val = int(m.group(1))
                    if raw_val > 1_000_000:
                        price = raw_val / 100_000
                    else:
                        price = float(raw_val)
                    logger.info("HTML regex got price: %s", price)

            # Try to grab name from inline data too
            if not product_name:
                m = re.search(r'"name"\s*:\s*"([^"]{5,})"', html)
                if m:
                    product_name = m.group(1)
        except Exception as e:
            logger.debug("HTML regex parse failed: %s", e)

        if price is not None:
            return ScrapeResult(product_name=product_name, price=price, is_in_stock=True)

        logger.warning("Direct HTML fetch could not extract price")
        return ScrapeResult(product_name=product_name, error="HTML fetch: no price found")

    async def _fetch_playwright(self, url: str, shop_id: str, item_id: str) -> ScrapeResult:
        """
        Headless browser fallback. Runs sync Playwright in a thread
        to avoid Windows event loop subprocess limitations.
        """
        import asyncio
        logger.info("Shopee API blocked, falling back to Playwright for %s", url)
        try:
            return await asyncio.to_thread(self._run_playwright_sync, url, shop_id, item_id)
        except Exception as e:
            logger.exception("Playwright fallback failed for Shopee")
            return ScrapeResult(error=f"Playwright error: {str(e)}")

    # ── Public Interface ───────────────────────────

    async def scrape(self, url: str) -> ScrapeResult:
        """
        Main entry point. Tries internal API first, falls back to Playwright.

        Args:
            url: Full Shopee product URL (e.g. https://shopee.vn/...-i.123.456)

        Returns:
            ScrapeResult with price, name, stock status, or an error message.
        """
        shop_id, item_id = extract_shopee_ids(url)
        if not shop_id or not item_id:
            return ScrapeResult(error=f"Could not extract shop_id/item_id from Shopee URL: {url}")

        # --- Attempt 1: Internal API ---
        try:
            data = await self._fetch_api(shop_id, item_id)
            result = self._parse_api_response(data)
            if result.price is not None:
                logger.info("Shopee API success: %s.%s → %s VND", shop_id, item_id, result.price)
                return result
            logger.warning("Shopee API returned no price for %s.%s, trying Playwright", shop_id, item_id)
        except httpx.HTTPStatusError as e:
            logger.warning("Shopee API HTTP %s for item %s.%s", e.response.status_code, shop_id, item_id)
        except Exception as e:
            logger.warning("Shopee API error for %s.%s: %s", shop_id, item_id, e)

        # --- Attempt 2: Fetch product page HTML directly ---
        result = await self._fetch_page_html(url, shop_id, item_id)
        if result.price is not None:
            return result

        # --- Attempt 3: Playwright headless browser ---
        return await self._fetch_playwright(url, shop_id, item_id)
