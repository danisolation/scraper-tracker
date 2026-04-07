"""
Abstract base class for platform scrapers.
Every scraper must implement `scrape(url)` returning a ScrapeResult.
"""
from abc import ABC, abstractmethod
from app.schemas.product import ScrapeResult


class BaseScraper(ABC):
    """Interface contract for all e-commerce scrapers."""

    @abstractmethod
    async def scrape(self, url: str) -> ScrapeResult:
        """
        Given a product URL, return its current price and metadata.
        Must handle:
          - Out of stock products
          - Timeouts / HTTP errors
          - Anti-bot blocks (retry, then fallback to Playwright)
        """
        ...
