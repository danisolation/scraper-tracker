"""
Shared utilities for all scrapers:
- Realistic User-Agent rotation
- Common header construction
- URL parsing helpers
"""
import re
import random
from fake_useragent import UserAgent

# Initialize once — falls back to a hardcoded list if the online DB is down
_ua = UserAgent(fallback="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# ── Hardcoded fallback pool for extra resilience ──
_FALLBACK_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
]


def get_random_ua() -> str:
    """Return a random realistic browser User-Agent string."""
    try:
        return _ua.random
    except Exception:
        return random.choice(_FALLBACK_UAS)


def build_base_headers(referer: str = "") -> dict[str, str]:
    """Construct browser-like headers that pass basic bot detection."""
    headers = {
        "User-Agent": get_random_ua(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if referer:
        headers["Referer"] = referer
    return headers


# ── URL Parsers ────────────────────────────────────

def extract_tiki_product_id(url: str) -> str | None:
    """
    Extract the product ID (spid / product_id) from a Tiki URL.
    Examples:
        https://tiki.vn/some-product-p12345678.html  →  '12345678'
        https://tiki.vn/some-product-p12345678.html?spid=99999  →  '12345678' (with spid=99999 as seller variant)
    """
    # Pattern 1: "-p{id}.html"
    match = re.search(r"-p(\d+)\.html", url)
    if match:
        return match.group(1)
    # Pattern 2: query param "product_id" or just the trailing numeric slug
    match = re.search(r"[?&]product_id=(\d+)", url)
    if match:
        return match.group(1)
    return None


def extract_tiki_spid(url: str) -> str | None:
    """Extract the seller-product variant id (spid) from query params."""
    match = re.search(r"[?&]spid=(\d+)", url)
    return match.group(1) if match else None


def extract_shopee_ids(url: str) -> tuple[str | None, str | None]:
    """
    Extract (shop_id, item_id) from a Shopee URL.
    Format: https://shopee.vn/product-name-i.{shop_id}.{item_id}
    Alt:    https://shopee.vn/product-name-i.{shop_id}.{item_id}?...
    """
    # Primary pattern: "i.{shop_id}.{item_id}"
    match = re.search(r"i\.(\d+)\.(\d+)", url)
    if match:
        return match.group(1), match.group(2)
    return None, None


def detect_platform(url: str) -> str | None:
    """Return 'tiki' or 'shopee' based on the URL, or None."""
    lower = url.lower()
    if "tiki.vn" in lower:
        return "tiki"
    if "shopee.vn" in lower:
        return "shopee"
    return None
