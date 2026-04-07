"""
Telegram notification service.
Sends price-drop alerts to users via the Telegram Bot API.
Uses httpx directly (lightweight, no dependency on python-telegram-bot at runtime).
"""
import logging
import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)

TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"


async def send_price_alert(
    chat_id: str,
    product_name: str,
    current_price: float,
    target_price: float,
    product_url: str,
) -> bool:
    """
    Send a formatted price-drop notification to a Telegram chat.

    Returns True if the message was delivered successfully.
    """
    settings = get_settings()
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not configured — cannot send notification")
        return False

    # Format the message with Vietnamese dong currency
    message = (
        "🔔 <b>Price Drop Alert!</b>\n\n"
        f"📦 <b>{_escape_html(product_name)}</b>\n"
        f"💰 Current price: <b>{current_price:,.0f} ₫</b>\n"
        f"🎯 Your target:   <b>{target_price:,.0f} ₫</b>\n"
        f"📉 Savings:        <b>{target_price - current_price:,.0f} ₫</b>\n\n"
        f"🔗 <a href=\"{product_url}\">View Product</a>"
    )

    url = TELEGRAM_SEND_URL.format(token=settings.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            result = resp.json()
            if result.get("ok"):
                logger.info("Telegram alert sent to chat %s for '%s'", chat_id, product_name)
                return True
            else:
                logger.error("Telegram API error: %s", result.get("description"))
                return False
    except Exception as e:
        logger.exception("Failed to send Telegram notification to %s: %s", chat_id, e)
        return False


async def send_error_alert(chat_id: str, product_name: str, error_msg: str) -> bool:
    """Notify the user that scraping failed for one of their products."""
    settings = get_settings()
    if not settings.TELEGRAM_BOT_TOKEN:
        return False

    message = (
        "⚠️ <b>Scraping Error</b>\n\n"
        f"📦 {_escape_html(product_name or 'Unknown product')}\n"
        f"❌ {_escape_html(error_msg)}\n\n"
        "The system will retry on the next scheduled check."
    )

    url = TELEGRAM_SEND_URL.format(token=settings.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json().get("ok", False)
    except Exception:
        logger.exception("Failed to send error alert to %s", chat_id)
        return False


def _escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram's HTML parse mode."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
