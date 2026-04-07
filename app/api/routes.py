"""
API routes for the price tracker.
"""
import uuid
import logging
import traceback
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.user import User
from app.models.product import Product, Platform
from app.models.price_history import PriceHistory
from app.schemas.product import ProductCreate, ProductResponse, PriceHistoryResponse, ScrapeResult
from app.scrapers.helpers import detect_platform, extract_tiki_product_id, extract_shopee_ids
from app.scrapers import TikiScraper, ShopeeScraper
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["products"])


# ── POST /products — Add a new product to track ───

@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(payload: ProductCreate, db: AsyncSession = Depends(get_db)):
    """
    Register a product URL for price tracking.
    - Detects the platform (Tiki / Shopee) from the URL.
    - Creates or finds the user by telegram_chat_id.
    - Immediately scrapes the current price as a baseline.
    """
    url_str = str(payload.url)
    platform_name = detect_platform(url_str)
    if not platform_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported platform. Only tiki.vn and shopee.vn are supported.",
        )

    platform = Platform(platform_name)

    # Validate that we can extract IDs from the URL
    if platform == Platform.TIKI and not extract_tiki_product_id(url_str):
        raise HTTPException(status_code=400, detail="Invalid Tiki product URL format")
    if platform == Platform.SHOPEE:
        sid, iid = extract_shopee_ids(url_str)
        if not sid or not iid:
            raise HTTPException(status_code=400, detail="Invalid Shopee product URL format")

    # Find or create user by telegram_chat_id
    # Use the default from settings if none was provided
    chat_id = payload.telegram_chat_id or get_settings().TELEGRAM_DEFAULT_CHAT_ID
    if not chat_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="telegram_chat_id is required (or set TELEGRAM_DEFAULT_CHAT_ID in .env)",
        )

    stmt = select(User).where(User.telegram_chat_id == chat_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        user = User(telegram_chat_id=chat_id)
        db.add(user)
        await db.flush()  # Get the user.id assigned

    # Check if this user is already tracking this exact URL
    existing_stmt = select(Product).where(
        Product.url == url_str,
        Product.user_id == user.id,
        Product.is_active == True,  # noqa: E712
    )
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You are already tracking this product (id: {existing.id})",
        )

    # Do an initial scrape to get product name and current price
    scraper = TikiScraper() if platform == Platform.TIKI else ShopeeScraper()
    try:
        scrape_result = await scraper.scrape(url_str)
    except Exception as e:
        logger.error("Scrape failed for %s:\n%s", url_str, traceback.format_exc())
        scrape_result = ScrapeResult(error=str(e))

    product = Product(
        user_id=user.id,
        url=url_str,
        platform=platform,
        product_name=scrape_result.product_name,
        target_price=payload.target_price,
        current_price=scrape_result.price,
        is_in_stock=scrape_result.is_in_stock,
        last_checked_at=datetime.now(timezone.utc) if scrape_result.price else None,
    )
    db.add(product)
    await db.flush()

    # Save initial price to history if scrape succeeded
    if scrape_result.price is not None:
        history = PriceHistory(
            product_id=product.id,
            price=scrape_result.price,
            is_in_stock=scrape_result.is_in_stock,
        )
        db.add(history)

    return product


# ── GET /products — List all tracked products ─────

@router.get("/products", response_model=list[ProductResponse])
async def list_products(
    is_active: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """Return all products, optionally filtered by active status."""
    stmt = select(Product).where(Product.is_active == is_active).order_by(Product.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


# ── GET /products/{id} — Single product detail ────

@router.get("/products/{product_id}", response_model=ProductResponse)
async def get_product(product_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    stmt = select(Product).where(Product.id == product_id)
    result = await db.execute(stmt)
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


# ── GET /products/{id}/history — Price history ────

@router.get("/products/{product_id}/history", response_model=list[PriceHistoryResponse])
async def get_price_history(
    product_id: uuid.UUID,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """Return the price history for a product, newest first."""
    stmt = (
        select(PriceHistory)
        .where(PriceHistory.product_id == product_id)
        .order_by(PriceHistory.checked_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


# ── DELETE /products/{id} — Stop tracking ─────────

@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_product(product_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Soft-delete: sets is_active=False instead of removing the row."""
    stmt = select(Product).where(Product.id == product_id)
    result = await db.execute(stmt)
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    product.is_active = False


# ── POST /products/{id}/check — Manual price check ─

@router.post("/products/{product_id}/check", response_model=ProductResponse)
async def manual_check(product_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Trigger an immediate price check for a single product."""
    stmt = select(Product).where(Product.id == product_id)
    result = await db.execute(stmt)
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    scraper = TikiScraper() if product.platform == Platform.TIKI else ShopeeScraper()
    scrape_result = await scraper.scrape(product.url)

    if scrape_result.error:
        raise HTTPException(status_code=502, detail=f"Scrape failed: {scrape_result.error}")

    now = datetime.now(timezone.utc)
    product.current_price = scrape_result.price
    product.is_in_stock = scrape_result.is_in_stock
    product.last_checked_at = now
    if scrape_result.product_name and not product.product_name:
        product.product_name = scrape_result.product_name

    if scrape_result.price is not None:
        history = PriceHistory(
            product_id=product.id,
            price=scrape_result.price,
            is_in_stock=scrape_result.is_in_stock,
            checked_at=now,
        )
        db.add(history)

    return product
