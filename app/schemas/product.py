"""
Pydantic schemas for API request/response validation.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, HttpUrl, field_validator


# ── Product Schemas ────────────────────────────────

class ProductCreate(BaseModel):
    """POST /products — body payload."""
    url: HttpUrl
    target_price: float
    telegram_chat_id: str = ""

    @field_validator("target_price")
    @classmethod
    def price_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("target_price must be positive")
        return v

    @field_validator("url")
    @classmethod
    def url_must_be_supported(cls, v: HttpUrl) -> HttpUrl:
        host = str(v).lower()
        if "tiki.vn" not in host and "shopee.vn" not in host:
            raise ValueError("Only tiki.vn and shopee.vn URLs are supported")
        return v


class ProductResponse(BaseModel):
    id: uuid.UUID
    url: str
    platform: str
    product_name: str | None
    target_price: float
    current_price: float | None
    is_active: bool
    is_in_stock: bool
    last_checked_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("platform", mode="before")
    @classmethod
    def extract_platform_value(cls, v):
        """Convert Platform enum to its string value for serialization."""
        return v.value if hasattr(v, "value") else v

    @field_validator("target_price", "current_price", mode="before")
    @classmethod
    def decimal_to_float(cls, v):
        """Convert Decimal from SQLAlchemy Numeric columns to float."""
        if v is not None:
            return float(v)
        return v


class PriceHistoryResponse(BaseModel):
    id: uuid.UUID
    price: float
    is_in_stock: bool
    checked_at: datetime

    model_config = {"from_attributes": True}


class ScrapeResult(BaseModel):
    """Internal DTO returned by scraper functions."""
    product_name: str | None = None
    price: float | None = None
    is_in_stock: bool = True
    error: str | None = None
