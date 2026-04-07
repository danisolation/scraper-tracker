"""
PriceHistory model — stores a timestamped snapshot of a product's price.
One row per scrape check, enabling historical charting on the frontend.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Numeric, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    price: Mapped[float] = mapped_column(
        Numeric(14, 2), nullable=False, comment="Scraped price in VND"
    )
    is_in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # ── Relationships ──────────────────────────────
    product: Mapped["Product"] = relationship("Product", back_populates="price_history")

    def __repr__(self) -> str:
        return f"<PriceHistory product={self.product_id} price={self.price} at={self.checked_at}>"
