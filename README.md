# Stock Checker (`ubstock`)

**Stock Checker** (`v0.4.0`) is a self-contained, real-time inventory and product availability monitor featuring an **automatic Multi-Strategy Stock Detection Engine**, **Price Tracking & Drop Alerts**, **Multi-Channel Notifications (Discord, Telegram, Webhooks)**, **Proxy Rotation & Per-Domain Rate Limiting**, a modern **Web UI Dashboard**, **REST API**, **SQLite persistence**, and **container-first Docker deployment**.

It starts cleanly out of the box with **zero `.env` dependency**—and thanks to Auto-Detect, **you no longer need to hunt for CSS selectors** just to start tracking a product URL.

---

## ✨ Key Features

- **Multi-Strategy Stock Detection Engine (No CSS Selectors Required)**:
  - **Auto-Detect (`auto`, Default)**: Just paste a product URL—Stock Checker automatically evaluates Shopify `.json` APIs, Schema.org / JSON-LD structured data, OpenGraph/product meta tags, and page text/button heuristics, with an optional CSS selector fallback.
  - **Selectable Strategies**: Choose between `auto`, `json_ld`, `shopify`, `text_heuristic`, and `css_selector` per product.
  - **Rich Diagnostic Feedback & Strategy Pills**: Every product card displays its active strategy badge and last detection detail (e.g., which structured signal or button matched).
- **Modern Web UI Dashboard (`http://localhost:8000/`)**:
  - Real-time stock overview with auto-refreshing status cards (**Total Products**, **In Stock**, **Out of Stock**, **Discord Status**, **Check Interval**).
  - Full product management (**Add**, **Edit**, **Delete**, per-product **Check Now**, and **Check All Now**), plus search and status filtering (`All`, `In Stock`, `Out of Stock`, `Error`).
  - **Live Stock Detection Tester**: Test any product URL and detection strategy (plus an optional fallback CSS selector) directly in the browser before saving.
  - **Activity Log**: Persisted stock check and notification history with one-click history clearing.
  - **In-App Settings Panel**: Configure Discord Bot Token (masked on read), Channel ID, check interval, notification toggles, and send a **live test Discord alert** without restarting the application.
- **Zero `.env` Dependency**:
  - Starts immediately with sensible defaults and no required `.env` file or Discord tokens.
  - Optional environment variables (`HOST`, `PORT`, `DATA_DIR`, `DB_PATH`, etc.) remain available for container overrides or initial seeding.
- **SQLite Persistence with WAL Mode**:
  - Thread-safe SQLite database (`data/ubstock.db`) stores settings, monitored products (including `strategy` and `strategy_details`), and check history.
  - Automatically applies backwards-compatible schema migrations for existing databases and migrates legacy `data/products.json` entries on startup.
- **Smart Discord Integration (Optional)**:
  - Notifies only when stock transitions from out-of-stock to in-stock (with REST fallback if the Gateway client isn't connected).
  - Supports interactive slash commands (`/ping`, `/add_product`, `/remove_product`, `/list_products`).
- **Stack Deployment Ready**:
  - Includes `docker-compose.yml` and `Dockerfile` with persistent volume mounts (`./data`, `./logs`), port `8000:8000`, and an integrated `/api/health` container healthcheck.
- **Automatic Log Rotation**:
  - Rotating log files in `logs/ui_stock_bot.log` (5 MB max, 5 backups, automatic cleanup after 7 days).

---

## 🏗️ Architecture

```text
ubstock/
├── main.py                 # Application entrypoint (Uvicorn + FastAPI)
├── api.py                  # FastAPI REST API, Pydantic schemas & static serving
├── config.py               # Defaults and optional environment variable overrides
├── database.py             # Thread-safe SQLite persistence (WAL mode + JSON migration)
├── product_manager.py      # Product CRUD wrapper backed by SQLite
├── stock_checker.py        # Multi-strategy stock detection, polling & alert dispatch
├── discord_client.py       # Optional Discord client & dynamic lifecycle manager
├── bot_commands.py         # Discord slash commands (/ping, /add_product, etc.)
├── log_manager.py          # Rotating file & console logging configuration
├── utils.py                # Shared HTTP request helpers
├── test_main.py            # Automated unit and integration test suite
├── Dockerfile              # Python 3.11 slim container with uv and healthcheck
├── docker-compose.yml      # Ready-to-run Compose stack definition
├── pyproject.toml          # Project metadata, dependencies, and Ruff config
├── static/                 # Web UI single-page frontend
│   ├── favicon.svg         # Custom isometric inventory & radar SVG icon
│   ├── index.html          # Dashboard layout, modals, and views
│   ├── styles.css          # Responsive dark/modern UI styling
│   └── app.js              # Interactive frontend logic & API client
├── data/
│   └── ubstock.db          # SQLite database (auto-created at runtime)
└── logs/
    └── ui_stock_bot.log    # Rotating application logs (auto-created at runtime)
```

---

## 🚀 Quick Start

### Option 1: Docker Compose (Recommended)

No `.env` file or pre-configuration is needed.

1. **Clone the repository**:
   ```bash
   git clone https://github.com/tony-vs-code/stock-check.git
   cd stock-check
   ```

2. **Start the stack**:
   ```bash
   docker compose up -d --build
   ```

3. **Open the Web UI**:
   Visit **[http://localhost:8000](http://localhost:8000)** in your browser to add products (no CSS selector needed with **Auto-Detect**), test stock detection, and optionally configure Discord alerts.

4. **Verify container health**:
   ```bash
   curl -f http://localhost:8000/api/health
   # {"status":"ok","version":"0.4.0"}
   ```

#### Included `docker-compose.yml`

```yaml
services:
  ubstock:
    build: .
    container_name: ubstock
    ports:
      - "8000:8000"
    volumes:
      - ./data:/usr/src/app/data
      - ./logs:/usr/src/app/logs
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/health"]
      interval: 30s
      timeout: 5s
      start_period: 5s
      retries: 3
```

---

### Option 2: Docker CLI

```bash
# Build from source
docker build -t ubstock .

# Run without any .env file
docker run -d \
  --name ubstock \
  -p 8000:8000 \
  -v $(pwd)/data:/usr/src/app/data \
  -v $(pwd)/logs:/usr/src/app/logs \
  --restart unless-stopped \
  ubstock
```

---

### Option 3: Local Run with `uv`

**Prerequisites**: Python 3.11+ and [uv](https://github.com/astral-sh/uv).

```bash
# Install dependencies
uv sync

# Start the Web UI, REST API, and background stock monitor
uv run python main.py
```

Then open **[http://localhost:8000](http://localhost:8000)** in your browser.

---

## 🖥️ Web UI Dashboard

Hosted directly at `http://localhost:8000/`:

1. **Summary Status Cards**:
   - **Total Products**, **In Stock**, **Out of Stock**, **Discord Status** (`Unconfigured`, `Configured`, or `Connected`), and **Check Interval** (with last check timestamp).
   - Live status pill (`Idle` / `Checking`) and automatic 10-second dashboard refresh.
2. **Products Tab**:
   - Search by product name, URL, or CSS selector, and filter by **All**, **In Stock**, **Out of Stock**, or **Error**.
   - **Strategy & Method Pills**: Each product card displays its detection strategy badge and a dynamic **Method** pill (`⚡ Auto-Detect`, `📄 Schema.org JSON-LD`, `🛍️ Shopify JSON API`, `🔍 Text & Button Heuristics`, or **Selector** for `css_selector`), plus optional fallback selector pills and live diagnostic details.
   - **Add / Edit Product Modal**: Pick a **Detection Strategy** (`Auto-Detect` is selected by default so **no CSS selector is required**), optionally expand **Add optional fallback selector** if desired, and click **Test Stock Detection** to preview live detection results (`strategy_used`, `detected_by`, `details`) before saving.
   - **Check Now** button per product and global **Check All Now** action.
3. **Activity Log Tab**:
   - Chronological feed of stock status changes, manual checks, errors, and Discord notifications, with a **Clear History** button.
4. **Settings Tab**:
   - Configure **Discord Bot Token** (always masked when returned from the API), **Discord Channel ID**, **Check Interval** (`1–86400` seconds), and **Enable Notifications** toggle.
   - **Send Test Alert** button to validate Discord credentials immediately without leaving the dashboard.

---

## 🎯 Multi-Strategy Stock Detection

You no longer need to inspect HTML or write CSS selectors to monitor products. By default, every new product uses **`auto`** mode, and you can also pin a specific strategy per product:

| Strategy | Value | How It Detects Stock |
| --- | --- | --- |
| **Auto-Detect (Default)** | `auto` | Runs a multi-tier cascade: **Shopify `.json` API** (for Shopify stores or inline JSON payloads) → **Schema.org / JSON-LD** → **OpenGraph / product meta tags** → **Text & button heuristics** → **Optional CSS selector fallback**. |
| **Schema.org / JSON-LD** | `json_ld` | Parses `<script type="application/ld+json">` `Product`/`Offer` `availability` fields (`InStock`, `OutOfStock`, `PreOrder`, `SoldOut`, etc.). |
| **Shopify JSON API** | `shopify` | Queries the product's `.json` endpoint and inspects variant `available` flags without needing HTML selectors. |
| **Text & Button Heuristics** | `text_heuristic` | Evaluates visible availability phrases (e.g., `Sold Out`, `Out of Stock`, `In Stock`) and enabled/disabled purchase buttons (`Add to Cart`, `Buy Now`, `Pre-Order`). |
| **Custom CSS Selector** | `css_selector` | Evaluates a specific CSS selector (defaulting to `button[label='Add to Cart']` if omitted) and checks whether matched elements are enabled or disabled. |

---

## ⚙️ Configuration & Environment Variables

### Zero `.env` Required

A `.env` file is **not required**. All runtime settings and products are persisted in `data/ubstock.db` (SQLite with WAL mode) and managed via the Web UI or REST API.

### Optional Environment Overrides

You can still supply environment variables for container customization or first-run database seeding:

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | Bind address for Uvicorn |
| `PORT` | `8000` | HTTP port for Web UI & REST API |
| `DATA_DIR` | `data` | Directory for SQLite database and legacy migration |
| `DB_PATH` | `data/ubstock.db` | Explicit path to the SQLite database file |
| `LOG_DIR` | `logs` | Directory for rotating log files |
| `STATIC_DIR` | `static` | Directory containing Web UI static assets |
| `DISCORD_TOKEN` | `""` | Optional Discord bot token (seeds DB on first run if unset) |
| `DISCORD_CHANNEL_ID` | `0` | Optional Discord channel ID (seeds DB on first run if unset) |
| `CHECK_STOCK_INTERVAL` | `15` | Optional default check interval in seconds (seeds DB on first run) |
| `CLEAN_LOGS_INTERVAL` | `168` | Log cleanup interval in hours (default: 7 days) |

---

## 📡 REST API Reference

Interactive OpenAPI / Swagger documentation is also available at **`http://localhost:8000/docs`**.

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Container healthcheck (`{"status": "ok", "version": "0.4.0"}`) |
| `GET` | `/api/status` | Aggregate product counts, Discord connection status, interval & check state |
| `GET` | `/api/products` | List all monitored products (including `strategy` and `strategy_details`) |
| `POST` | `/api/products` | Add a new product (`name`, `url`, optional `strategy` defaulting to `"auto"`, optional `selector`) |
| `PUT` | `/api/products/{product_id}` | Update an existing product's `name`, `url`, `strategy`, or `selector` |
| `DELETE` | `/api/products/{product_id}` | Remove a monitored product |
| `POST` | `/api/products/{product_id}/check` | Trigger an immediate stock check for a single product |
| `POST` | `/api/products/test-selector` | Test a `url`, `strategy`, and optional CSS `selector` live without saving to SQLite (returns `in_stock`, `strategy_used`, `detected_by`, `details`) |
| `POST` | `/api/check-all` | Trigger an immediate stock check across all monitored products |
| `GET` | `/api/history` | List recent stock check & notification events (`?limit=50`, `1–500`) |
| `DELETE` | `/api/history` | Clear all persisted activity history records |
| `GET` | `/api/settings` | Retrieve current settings (Discord token is masked) |
| `POST` | `/api/settings` | Update settings dynamically at runtime & hot-reload Discord client |
| `POST` | `/api/settings/test-discord` | Send a test Discord notification using provided or saved credentials |

---

## 💬 Discord Integration (Optional)

When a Discord Bot Token and Channel ID are configured in the **Settings** tab (or via `/api/settings`):

- **Automatic Stock Alerts**: When a product transitions from out-of-stock to in-stock, an alert is sent to the configured channel (using the connected Discord Gateway client, or falling back to the Discord REST API):
  ```text
  🚨 UVC G5 PTZ is back in stock! 🚨
  Link: https://store.ui.com/us/en/products/uvc-g5-ptz
  Detected: 2026-11-26 14:30:25
  ```
- **Slash Commands**:
  - `/ping` — Check bot responsiveness.
  - `/add_product name:<name> url:<url> [strategy:<strategy>] [selector:<css>]` — Add a product to monitoring (defaults to `auto`, with no CSS selector required; if `strategy` is `css_selector` and `selector` is omitted, defaults to `button[label='Add to Cart']`).
  - `/remove_product name:<name>` — Remove a product from monitoring.
  - `/list_products` — Display all monitored products.

### Getting Discord Credentials

1. Visit the [Discord Developer Portal](https://discord.com/developers/applications) and create an application.
2. Under **Bot**, create a bot and copy its **Token**.
3. Under **OAuth2 → URL Generator**, select `bot` and `applications.commands` scopes with **Send Messages** and **Use Slash Commands** permissions, then invite the bot to your server.
4. Enable **Developer Mode** in Discord (**User Settings → Advanced**), right-click your target channel, and choose **Copy Channel ID**.
5. Paste both values into the **Settings** tab in the Stock Checker Web UI, click **Save Settings**, and verify with **Send Test Alert**.

---

## 📊 Monitoring & Logs

- **Application Logs**: Stored in `logs/ui_stock_bot.log` (rotates at 5 MB, retains 5 backups, cleans up `.log` backups older than 7 days).
- **Local Viewing**:
  ```bash
  tail -f logs/ui_stock_bot.log
  ```
- **Docker Viewing**:
  ```bash
  docker compose logs -f ubstock
  ```

---

## 🔧 Development & Testing

```bash
# Install project and dev dependencies
uv sync --dev

# Run the automated test suite
uv run pytest -v

# Run linting and formatting checks
uv run ruff check .
uv run ruff format --check .
```

---

## 🤝 Contributing

Contributions are welcome! Feel free to open an issue or submit a pull request with tests and documentation updates.
