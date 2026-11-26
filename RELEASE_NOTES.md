# UB Stock Monitor v0.2.0 Release Notes

## Overview

UB Stock Monitor `v0.2.0` transforms the application from a CLI/Discord-only bot into a self-contained, web-managed stock monitoring platform. This release introduces a modern FastAPI-powered Web UI dashboard, thread-safe SQLite persistence, zero `.env` configuration requirements, dynamic Discord notification management, and container-first Docker Compose deployment.

## Highlights & Enhancements

### Modern Web UI Dashboard
- **Real-Time Monitoring Dashboard**: Built a responsive single-page Web UI (`static/index.html`, `static/app.js`, `static/styles.css`) served directly by FastAPI with auto-refreshing status counters, live status indicators, and instant manual checks (`Check All Now` and per-product checks).
- **Product Management & CSS Selector Testing**: Add, edit, search, and delete monitored products directly from the browser via modal forms, including an interactive CSS selector test endpoint (`/api/products/test-selector`) before saving.
- **Activity & Stock Change Log**: Dedicated **Activity Log** tab backed by persisted check and notification history, with support for clearing historical entries.

### SQLite Persistence
- **Thread-Safe SQLite Storage**: Replaced flat JSON-only storage with a dedicated SQLite persistence layer (`database.py`) using WAL journal mode for reliable concurrent reads and writes across settings, products, and check history.
- **Automatic Legacy Migration**: Existing `products.json` entries are automatically migrated into SQLite on initialization without manual intervention.

### Zero `.env` Dependency
- **Out-of-the-Box Startup**: Removed mandatory `.env` file requirements—the application starts cleanly with safe built-in defaults (`config.py`) and optional environment variable seeding on first run.
- **Clean Repository Hygiene**: Updated `.gitignore` to exclude runtime databases (`data/*.db`, `data/*.sqlite3`, `data/*.json`), logs (`logs/`, `*.log`), local `.env` files, and local agent tooling.

### Dynamic Discord Settings & Notifications
- **Runtime Configuration via Web UI**: Configure Discord Bot Token (masked in API responses), Discord Channel ID, polling interval, and notification toggles dynamically through `/api/settings` without restarting containers or editing files.
- **Live Discord Lifecycle & Test Alerts**: Dynamically start, reconnect, or disable the Discord client (`discord_client.py`) when settings change, and verify delivery anytime using the built-in test notification endpoint (`/api/settings/test-discord`).

## Stack Deployment Readiness

- **Containerized Runtime (`Dockerfile`)**: Multi-purpose Python 3.11 slim image using `uv sync --frozen` for reproducible dependency installation, exposing port `8000` with persistent `/usr/src/app/data` and `/usr/src/app/logs` directories.
- **Docker Compose Support (`docker-compose.yml`)**: Ready-to-run service definition mapping port `8000:8000`, mounting `./data` and `./logs` volumes, and setting `restart: unless-stopped`.
- **Container Healthcheck on `/api/health`**: Built-in Docker and Compose `HEALTHCHECK` polling `http://localhost:8000/api/health` every 30s to verify service availability and application version (`0.2.0`).

## Deployment Instructions

1. **Clone the repository** and navigate to the project root.
2. **Start the stack with Docker Compose**:
   ```bash
   docker compose up -d --build
   ```
3. **Access the Web UI**: Open `http://localhost:8000` in your browser to add products, adjust check intervals, and optionally configure Discord notifications.
4. **Verify container health**:
   ```bash
   curl -f http://localhost:8000/api/health
   ```
   Expected response: `{"status":"ok","version":"0.2.0"}`.

## Reliability & Fixes

- **Comprehensive Test Suite (`test_main.py`)**: 19 automated unit and integration tests covering SQLite persistence, legacy JSON migration, stock checking and notification throttling, FastAPI REST endpoints, settings masking, and Discord lifecycle handling.
- **Cross-Tier Contract Validation**: Verified 100% alignment between FastAPI endpoints (`api.py`), frontend API calls (`static/app.js`), and DOM element bindings (`static/index.html`), alongside clean JavaScript syntax verification.
- **Code Quality & Linting**: Enforced clean Python formatting and linting across the codebase with `ruff check` and `ruff format --check`.
