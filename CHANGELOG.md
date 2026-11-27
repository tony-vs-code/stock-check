# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-11-27

### Added
- **Multi-Strategy Stock Detection Engine (`stock_checker.py`)**: Implemented five detection modes—`auto` (default), `json_ld`, `shopify`, `text_heuristic`, and `css_selector`—allowing product monitoring without manually writing CSS selectors.
- **Auto-Detect Cascade (`auto`)**: Evaluates Shopify `.json` endpoints, Schema.org / JSON-LD `Offer` availability, OpenGraph/product meta tags, visible text and purchase button heuristics, and optional CSS selector fallback.
- **Strategy UI Controls & Card Badges (`static/index.html`, `static/app.js`, `static/styles.css`)**: Added a Detection Strategy dropdown, collapsible optional fallback selector toggle, live **Test Stock Detection** diagnostic feedback (`strategy_used`, `detected_by`, `details`), and strategy pills on product cards.
- **SQLite Schema Migration (`database.py`)**: Added backwards-compatible `ALTER TABLE` migration for `strategy` and `strategy_details` columns on existing `products` tables.
- **Strategy API & Test Coverage (`api.py`, `product_manager.py`, `test_main.py`)**: Added strategy validation and response fields across product and test endpoints, expanding the test suite to 29 passing tests.

### Changed
- **Optional CSS Selectors**: Updated product creation, editing, and UI forms so CSS selectors are no longer required when using `auto`, `json_ld`, `shopify`, or `text_heuristic`.
- **Application Version (`config.py`, `pyproject.toml`)**: Bumped version to `0.3.0`.

## [0.2.0] - 2026-11-26

### Added
- **Modern Web UI Dashboard**: FastAPI-served static dashboard (`static/index.html`, `static/app.js`, `static/styles.css`) with live monitoring stats, product CRUD modals, CSS selector tester, activity log, and settings view.
- **REST API (`api.py`)**: Full FastAPI endpoints for health (`/api/health`), monitoring status (`/api/status`), product CRUD and checks (`/api/products`), selector testing (`/api/products/test-selector`), history (`/api/history`), and runtime settings (`/api/settings`, `/api/settings/test-discord`).
- **SQLite Persistence (`database.py`)**: Thread-safe SQLite database with WAL mode for settings, monitored products, and stock check history, including automatic migration from legacy `products.json`.
- **Dynamic Discord Settings & Notifications**: Configure Discord bot token (masked on read), channel ID, check interval, and notification toggle dynamically at runtime without restarts.
- **Stack Deployment Support**: Added `docker-compose.yml` and updated `Dockerfile` with persistent `./data` and `./logs` volume mounts and `/api/health` container healthchecks.
- **Comprehensive Test Suite (`test_main.py`)**: Added 19 automated tests covering database persistence, stock checking, API endpoints, and Discord manager integration.

### Changed
- **Zero `.env` Dependency (`config.py`)**: Replaced mandatory `.env` startup requirements with safe defaults and optional environment seeding on first run.
- **Application Entrypoint (`main.py`)**: Unified startup around Uvicorn/FastAPI lifecycle managing background stock monitoring and optional Discord bot integration.
- **Repository Exclusions (`.gitignore`)**: Excluded runtime SQLite databases, logs, `.env`, and local agent files from version control.

## [0.1.0] - 2025-08-02

### Added
- Initial Discord bot implementation with slash commands (`/ping`, `/add_product`, `/remove_product`, `/list_products`), JSON-based product storage, periodic stock checking, and Dockerfile support.
