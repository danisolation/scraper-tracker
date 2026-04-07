"""
Product model — a tracked product URL with its target price.
Supports multiple platforms via the `platform` enum column.
"""
import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import String, Numeric, Boolean, DateTime, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Platform(str, enum.Enum):
    TIKI = "tiki"
    SHOPEE = "shopee"


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, name="platform_enum", create_type=False,
             values_callable=lambda x: [e.value for e in x]),
        nullable=False, index=True
    )
    product_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_price: Mapped[float] = mapped_column(
        Numeric(14, 2), nullable=False,
        comment="Price threshold in VND — notify when current price <= this value"
    )
    current_price: Mapped[float | None] = mapped_column(
        Numeric(14, 2), nullable=True,
        comment="Last scraped price in VND"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # ── Relationships ──────────────────────────────
    user: Mapped["User"] = relationship("User", back_populates="products")
    price_history: Mapped[list["PriceHistory"]] = relationship(
        "PriceHistory", back_populates="product", cascade="all, delete-orphan",
        order_by="desc(PriceHistory.checked_at)"
    )

    def __repr__(self) -> str:
        return f"<Product {self.platform.value}: {self.product_name} target={self.target_price}>"
