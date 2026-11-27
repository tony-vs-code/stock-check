# Stock Checker v0.3.1 Release Notes

## Overview

Stock Checker `v0.3.1` introduces the official rebranding to **Stock Checker**, a custom SVG site icon and header emblem (`static/favicon.svg`), dynamic **Method** pills on product cards replacing legacy static selector rows, and full strategy selection support in the Discord `/add_product` slash command.

## Highlights & Enhancements

### Official Rebranding to Stock Checker (`static/index.html`, `api.py`, `discord_client.py`, `bot_commands.py`)
- **Unified Brand Identity**: Rebranded the Web UI header, HTML page title (`Stock Checker - Real-Time Inventory & Stock Monitor`), FastAPI application title, Discord test alert messages, and Discord bot startup messages to **Stock Checker**.
- **Updated Search & Onboarding Copy**: Updated dashboard search placeholder and empty-state guidance to highlight multi-strategy auto-detection without requiring CSS selectors.

### Custom SVG Favicon & Header Icon (`static/favicon.svg`, `static/styles.css`)
- **New Custom Brand Icon**: Added `static/favicon.svg` featuring an isometric inventory box, sky-blue radar pulse arcs, and a glowing emerald stock-in checkmark badge, wired into both `<link rel="icon">` and the dashboard header.

### Dynamic Product Card Method Pills (`static/app.js`, `static/styles.css`)
- **Strategy-Aware Card Metadata**: Replaced the legacy static `Selector` row on product cards with dynamic **Method** pills (`⚡ Auto-Detect`, `📄 Schema.org JSON-LD`, `🛍️ Shopify JSON API`, `🔍 Text & Button Heuristics`, or **Selector** for `css_selector`), including optional fallback selector pills when configured under `auto`.

### Discord `/add_product` Strategy Support (`bot_commands.py`)
- **Strategy Selection & Auto-Detect Default**: Updated `/add_product` with an optional `strategy` choice parameter (`auto`, `json_ld`, `shopify`, `text_heuristic`, `css_selector`) defaulting to `auto`, with clean selector formatting (`(Auto-detect - no selector required)` when no selector is needed).

## Reliability & Fixes

- **Automated Test Suite (`test_main.py`)**: **30/30 tests passing**, including dedicated coverage for `/add_product` strategy choices, `(Auto-detect - no selector required)` formatting, and `Stock Checker` HTML/API branding.
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
   Expected response: `{"status":"ok","version":"0.3.1"}`.
