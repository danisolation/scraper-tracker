"""
Background price-checking scheduler using APScheduler.

─────────────────────────────────────────────────────────────
WHY APScheduler OVER Celery:
─────────────────────────────────────────────────────────────
For a medium-scale app tracking <10k products:
  - APScheduler runs in-process (no Redis/RabbitMQ dependency)
  - Simpler deployment and operational overhead
  - AsyncIOScheduler integrates natively with FastAPI's event loop
  - Celery is overkill until you need distributed workers

When to migrate to Celery:
  - >10k products needing parallel scraping across workers
  - Need for task retries with complex failure policies
  - Multi-server deployment required
─────────────────────────────────────────────────────────────
"""
import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.product import Product, Platform
from app.models.price_history import PriceHistory
from app.scrapers.tiki import TikiScraper
from app.scrapers.shopee import ShopeeScraper
from app.services.notifier import send_price_alert

logger = logging.getLogger(__name__)

# Singleton scheduler instance
scheduler = AsyncIOScheduler()

# Scraper instances (stateless, safe to reuse)
_scrapers = {
    Platform.TIKI: TikiScraper(),
    Platform.SHOPEE: ShopeeScraper(),
}


async def check_prices_job():
    """
    Core scheduled job: iterates all active products, scrapes current prices,
    saves to history, and sends Telegram alerts when price <= target.

    Runs inside the FastAPI event loop via APScheduler's AsyncIOScheduler.
    """
    settings = get_settings()
    logger.info("═══ Starting scheduled price check ═══")

    async with AsyncSessionLocal() as session:
        # Fetch all active products with their user (for chat_id)
        stmt = (
            select(Product)
            .where(Product.is_active == True)   # noqa: E712 (SQLAlchemy requires == for filter)
            .options(selectinload(Product.user))
        )
        result = await session.execute(stmt)
        products = result.scalars().all()

        if not products:
            logger.info("No active products to check")
            return

        logger.info("Checking %d active products", len(products))

        # Use a semaphore to limit concurrent scrapes (avoid IP bans)
        semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_SCRAPES)

        async def _process_product(product: Product):
            async with semaphore:
                await _scrape_and_update(session, product)

        # Run all scrapes concurrently (bounded by semaphore)
        tasks = [_process_product(p) for p in products]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Commit all changes in one transaction
        await session.commit()

    logger.info("═══ Price check complete ═══")


async def _scrape_and_update(session, product: Product):
    """
    Scrape a single product, update its DB record, save price history,
    and trigger notification if price dropped below target.
    """
    scraper = _scrapers.get(product.platform)
    if not scraper:
        logger.error("No scraper for platform: %s", product.platform)
        return

    try:
        result = await scraper.scrape(product.url)

        if result.error:
            logger.warning(
                "Scrape error for product %s (%s): %s",
                product.id, product.url, result.error
            )
            return

        now = datetime.now(timezone.utc)

        # Update product name if we got one and didn't have it before
        if result.product_name and not product.product_name:
            product.product_name = result.product_name

        # Update current price and stock status
        previous_price = product.current_price
        product.current_price = result.price
        product.is_in_stock = result.is_in_stock
        product.last_checked_at = now

        # Record in price history
        history_entry = PriceHistory(
            product_id=product.id,
            price=result.price,
            is_in_stock=result.is_in_stock,
            checked_at=now,
        )
        session.add(history_entry)

        logger.info(
            "Product %s: %s → %s VND (stock: %s)",
            product.id,
            previous_price,
            result.price,
            result.is_in_stock,
        )

        # ── Notification Logic ─────────────────────
        # Send alert if:
        #   1. Price is at or below the user's target
        #   2. Product is in stock
        #   3. Price actually dropped (avoid repeat alerts if price stays the same)
        should_notify = (
            result.price is not None
            and result.price <= float(product.target_price)
            and result.is_in_stock
            and (previous_price is None or result.price < float(previous_price))
        )

        if should_notify and product.user:
            await send_price_alert(
                chat_id=product.user.telegram_chat_id,
                product_name=product.product_name or "Unknown product",
                current_price=result.price,
                target_price=float(product.target_price),
                product_url=product.url,
            )

    except Exception:
        logger.exception("Unexpected error processing product %s", product.id)


def start_scheduler():
    """
    Start the APScheduler background job. Call this from FastAPI's lifespan.
    """
    settings = get_settings()
    scheduler.add_job(
        check_prices_job,
        trigger=IntervalTrigger(minutes=settings.PRICE_CHECK_INTERVAL_MINUTES),
        id="price_checker",
        name="Check product prices",
        replace_existing=True,  # Avoid duplicate jobs on hot reload
        max_instances=1,        # Never overlap runs
    )
    scheduler.start()
    logger.info(
        "Scheduler started — checking prices every %d minutes",
        settings.PRICE_CHECK_INTERVAL_MINUTES,
    )


def stop_scheduler():
    """Gracefully shut down the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
