# Stock Checker v0.4.0 Release Notes

## Overview

Stock Checker `v0.4.0` introduces **Multi-Channel Notifications** (Telegram and Custom Generic Webhooks alongside Discord), **Proxy Rotation & Per-Domain Rate Limiting** for resilient scraping, and **End-to-End Price Tracking & History** with automatic price-drop alerts across all detection strategies.

## Highlights & Enhancements

### Multi-Channel Notifications (`notifications.py`, `api.py`, `stock_checker.py`, `static/`)
- **Telegram Bot Integration**: Added `send_telegram_notification` via the Telegram Bot API with configurable Bot Token (masked on read in `/api/settings`) and Chat ID, plus live test delivery via `POST /api/settings/test-telegram`.
- **Custom Generic Webhook Integration**: Added `send_webhook_notification` and `build_webhook_payload` delivering structured JSON events (`event`, `product`, `url`, `status`, `price`, `message`, `timestamp`) to any HTTP/HTTPS webhook endpoint, with live testing via `POST /api/settings/test-webhook`.
- **Unified Alert Dispatch**: Both restock (`in_stock`) and price-drop (`price_drop`) events dispatch concurrently across enabled Discord (with embed support), Telegram, and Webhook channels.
- **Dashboard Notification Settings**: Added **Telegram Integration** and **Webhook Integration** cards in Settings with enable toggles, masked token visibility toggle, status badges, and one-click test buttons.

### Proxy Rotation & Per-Domain Rate Limiting (`stock_checker.py`, `utils.py`, `static/`)
- **Round-Robin Proxy Pool (`ProxyRotator`)**: Supports configuring newline- or comma-separated HTTP/HTTPS proxy URLs, rotating cleanly across enabled proxies on product fetches (`fetch_product_page(url, proxy=...)`).
- **Per-Domain Request Throttling (`DomainRateLimiter`)**: Enforces a configurable minimum delay (`domain_rate_limit_delay`) between consecutive requests to the same hostname to reduce rate-limiting and anti-bot blocks.
- **Anti-Bot Controls UI**: Added Proxy Rotation toggle, Proxy Pool list input, and Per-Domain Rate Limit Delay controls in the **Polling, Anti-Bot & Price Alerts** settings card.

### Price Tracking & History (`database.py`, `stock_checker.py`, `api.py`, `static/`)
- **Multi-Strategy Price Extraction**: Extracts product prices and currencies across Shopify JSON API, Schema.org JSON-LD `Offer` data, OpenGraph/product meta tags, and page text heuristics.
- **SQLite Price History Persistence**: Added `price_history` table and backwards-compatible `ALTER TABLE` migrations adding `current_price`, `previous_price`, and `currency` columns to `products`.
- **Price Drop Detection & Alerts**: Records historical prices on change and dispatches price-drop alerts (with previous vs. new price comparison and savings percentage) when a product's price decreases and price alerts are enabled.
- **Price History API & UI Modal**: Added `GET /api/products/{name}/price-history`, product card price & delta pills, and an interactive **Price History** modal displaying current price, overall change, tracked entries, and chronological price table.

## Reliability & Fixes

- **Automated Test Suite (`test_main.py`)**: **42/42 tests passing**, including dedicated coverage for Telegram/Webhook dispatchers, masked token validation, proxy rotation, domain rate limiting, multi-strategy price extraction, SQLite price history migrations, price drop alerts, and frontend contract checks.
- **Cross-Tier Contract Audit & JS Syntax**: Verified full alignment across backend API routes and frontend DOM IDs, plus clean JavaScript syntax verification (`node --check static/app.js`).
- **Linting & Formatting**: Verified clean `uv run ruff check .` and `uv run ruff format --check .`.

## Deployment Instructions

1. **Pull or clone the repository** and navigate to the project root.
2. **Start or rebuild the stack with Docker Compose**:
   ```bash
   docker compose up -d --build
   ```
3. **Access the Web UI**: Open `http://localhost:8000` in your browser.
4. **Verify container health**:
   ```bash
   curl -f http://localhost:8000/api/health
   ```
   Expected response: `{"status":"ok","version":"0.4.0"}`.
