# Scraper Tracker

Monitor product prices on **Tiki.vn** and **Shopee.vn** with automatic **Telegram alerts** when prices drop below your target.

## Quick Start

```bash
# 1. Create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

# 2. Install dependencies
pip install -r requirements.txt
playwright install chromium   # For Shopee fallback browser scraping

# 3. Configure environment
copy .env.example .env
# Edit .env with your PostgreSQL credentials and Telegram bot token

# 4. Run the server
python run.py
```

API docs available at: `http://localhost:8000/docs`

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/products` | Add a product to track |
| `GET` | `/api/v1/products` | List all tracked products |
| `GET` | `/api/v1/products/{id}` | Get product details |
| `GET` | `/api/v1/products/{id}/history` | Get price history |
| `POST` | `/api/v1/products/{id}/check` | Trigger manual price check |
| `DELETE` | `/api/v1/products/{id}` | Stop tracking a product |

## Architecture

- **Scraping**: Internal API reverse-engineering (primary) + Playwright headless browser (fallback)
- **Scheduling**: APScheduler runs price checks at configurable intervals
- **Notifications**: Telegram Bot API alerts when price ≤ target
# scraper-tracker
