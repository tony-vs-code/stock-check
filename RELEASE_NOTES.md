# UB Stock Monitor v0.3.0 Release Notes

## Overview

UB Stock Monitor `v0.3.0` introduces the **Multi-Strategy Stock Detection Engine**, eliminating the requirement for users to inspect HTML or provide CSS selectors just to monitor a product URL. With **Auto-Detect (`auto`)** enabled by default, UBStock automatically combines store APIs, structured product metadata, OpenGraph tags, and intelligent page heuristics—while keeping custom CSS selectors available as an explicit strategy or optional fallback.

## Highlights & Enhancements

### Multi-Strategy Stock Detection Engine (`stock_checker.py`)
- **Auto-Detect Cascade (`auto`, Default)**: Evaluates stock availability automatically in order:
  1. **Shopify JSON API (`shopify`)**: Detects Shopify storefronts (or inline JSON payloads) and inspects variant `available` status via `.json` endpoints.
  2. **Schema.org / JSON-LD (`json_ld`)**: Parses `<script type="application/ld+json">` `Product` and `Offer` availability metadata (`InStock`, `PreOrder`, `OutOfStock`, `SoldOut`, etc.).
  3. **OpenGraph & Product Meta Tags**: Inspects `og:availability`, `product:availability`, and `availability` meta tags.
  4. **Text & Button Heuristics (`text_heuristic`)**: Evaluates visible availability text and enabled/disabled purchase action buttons (`Add to Cart`, `Buy Now`, `Pre-Order`, `Sold Out`).
  5. **CSS Selector Fallback (`css_selector`)**: Evaluates an optional user-supplied CSS selector (or `DEFAULT_SELECTOR` when `css_selector` is explicitly selected), including `disabled` / `aria-disabled` state inspection.
- **No CSS Selector Required**: Users can add and monitor products with just a name and URL; CSS selectors are now optional in `auto`, `json_ld`, `shopify`, and `text_heuristic` modes.

### Web UI & Diagnostic Upgrades (`static/index.html`, `static/app.js`, `static/styles.css`)
- **Detection Strategy Dropdown**: Choose between `auto`, `json_ld`, `shopify`, `text_heuristic`, and `css_selector` directly in the Add/Edit Product modal, with a collapsible toggle for adding an optional fallback CSS selector.
- **Rich Live Diagnostic Feedback**: The **Test Stock Detection** flow (`/api/products/test-selector`) reports `in_stock`, `strategy_used`, `detected_by`, and human-readable `details` directly inside the modal before saving.
- **Strategy Pills & Details on Product Cards**: Every product card displays a color-coded strategy badge (`Auto`, `JSON-LD`, `Shopify`, `Heuristic`, `CSS`) alongside persisted diagnostic details (`strategy_details`).

### SQLite Schema Migration & API Parity (`database.py`, `api.py`, `product_manager.py`)
- **Backwards-Compatible SQLite Migration**: Existing `ubstock.db` databases automatically gain `strategy` (default `'auto'`) and `strategy_details` columns on startup without data loss.
- **Validated Strategy Contracts**: Product create/update and test endpoints validate strategy values (`auto`, `json_ld`, `shopify`, `text_heuristic`, `css_selector`) and return `strategy` and `strategy_details` in `ProductResponse`.

## Reliability & Fixes

- **Expanded Automated Test Suite (`test_main.py`)**: **29/29 tests passing**, covering Shopify JSON parsing, JSON-LD availability, OpenGraph meta tags, text/button heuristics, auto-detect cascade fallbacks, CSS selector disabled checks, backwards-compatible SQLite column migration, and FastAPI strategy API contracts.
- **Cross-Tier Contract Parity**: Verified 100% alignment across **8 API routes** (`api.py` ↔ `static/app.js`) and **71 DOM IDs** (`static/app.js` ↔ `static/index.html`), plus clean JavaScript syntax verification (`node --check static/app.js`).
- **Linting & Formatting**: Verified 100% clean `uv run ruff check .` and `uv run ruff format --check .`.

## Deployment Instructions

1. **Pull or clone the repository** and navigate to the project root.
2. **Start or rebuild the stack with Docker Compose**:
   ```bash
   docker compose up -d --build
   ```
   Existing SQLite databases in `./data/ubstock.db` will migrate automatically on startup.
3. **Access the Web UI**: Open `http://localhost:8000` in your browser to add products using **Auto-Detect** (no CSS selector required).
4. **Verify container health**:
   ```bash
   curl -f http://localhost:8000/api/health
   ```
   Expected response: `{"status":"ok","version":"0.3.0"}`.
