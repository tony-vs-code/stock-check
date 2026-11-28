"""Comprehensive tests for UB Stock Monitor backend, DB, stock checker, and API."""

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import discord
import pytest
from fastapi.testclient import TestClient

from api import create_app, mask_token
from config import APP_VERSION
from database import Database, DuplicateProductError
from discord_client import DiscordManager
from product_manager import ProductManager
from stock_checker import StockChecker, StockMonitor


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture()
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Database:
    """Create an isolated SQLite database in a temporary directory."""
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.delenv("CHECK_STOCK_INTERVAL", raising=False)
    db_file = tmp_path / "ubstock.db"
    products_json = tmp_path / "products.json"
    return Database(db_path=str(db_file), products_file=str(products_json))


@pytest.fixture()
def client(temp_db: Database, tmp_path: Path) -> TestClient:
    """Create a FastAPI TestClient wired to the isolated temporary DB."""
    static_dir = tmp_path / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "index.html").write_text(
        "<!DOCTYPE html><html><body><h1>Stock Checker</h1></body></html>",
        encoding="utf-8",
    )
    app = create_app(
        db=temp_db,
        static_dir=str(static_dir),
        start_background_tasks=False,
    )
    with TestClient(app) as test_client:
        yield test_client


# ----------------------------------------------------------------------
# Database & Migration Tests
# ----------------------------------------------------------------------


def test_database_initialization_and_default_settings(temp_db: Database) -> None:
    """Verify default settings are seeded on database creation."""
    settings = temp_db.get_all_settings()
    assert settings["check_stock_interval"] == "15"
    assert settings["notifications_enabled"] == "true"
    assert settings["telegram_bot_token"] == ""
    assert settings["telegram_chat_id"] == ""
    assert settings["telegram_enabled"] == "false"
    assert settings["webhook_url"] == ""
    assert settings["webhook_enabled"] == "false"
    assert settings["price_alerts_enabled"] == "true"
    assert settings["proxy_list"] == ""
    assert settings["proxy_enabled"] == "false"
    assert settings["domain_rate_limit_delay"] == "1.0"
    assert temp_db.list_products() == []
    assert temp_db.get_history() == []


def test_database_seeds_env_vars_if_not_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify DISCORD_TOKEN and DISCORD_CHANNEL_ID env vars seed DB once."""
    monkeypatch.setenv("DISCORD_TOKEN", "env-token-123456")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "9988776655")
    db_file = tmp_path / "seeded.db"

    db = Database(db_path=str(db_file), products_file=str(tmp_path / "none.json"))
    assert db.get_setting("discord_token") == "env-token-123456"
    assert db.get_setting("discord_channel_id") == "9988776655"

    # Updating in DB should not be overwritten on subsequent init
    db.set_setting("discord_token", "db-updated-token")
    db2 = Database(db_path=str(db_file), products_file=str(tmp_path / "none.json"))
    assert db2.get_setting("discord_token") == "db-updated-token"


def test_database_migrates_products_json_when_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify legacy products.json is imported into an empty SQLite table."""
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)

    products_json = tmp_path / "products.json"
    products_json.write_text(
        json.dumps(
            [
                {
                    "name": "G5 PTZ",
                    "url": "https://store.ui.com/us/en/products/uvc-g5-ptz",
                    "selector": "button[label='Add to Cart']",
                },
                {
                    "name": "Dream Machine",
                    "url": "https://store.ui.com/us/en/products/udm-pro",
                    "selector": ".buy-btn",
                },
            ]
        ),
        encoding="utf-8",
    )

    db_file = tmp_path / "migrated.db"
    db = Database(db_path=str(db_file), products_file=str(products_json))
    products = db.list_products()
    assert len(products) == 2
    assert products[0]["name"] == "G5 PTZ"
    assert products[1]["name"] == "Dream Machine"

    # Deleting one product and re-init should NOT re-import (table is non-empty)
    db.delete_product(products[0]["id"])
    db2 = Database(db_path=str(db_file), products_file=str(products_json))
    assert len(db2.list_products()) == 1


def test_database_product_crud_and_duplicate_guard(temp_db: Database) -> None:
    """Verify product CRUD operations and duplicate name protection."""
    created = temp_db.create_product(
        name="Switch Lite",
        url="https://example.com/switch",
        selector=".in-stock",
    )
    assert created["id"] > 0
    assert created["in_stock"] is False

    with pytest.raises(DuplicateProductError):
        temp_db.create_product(
            name="Switch Lite",
            url="https://example.com/other",
            selector=".btn",
        )

    updated = temp_db.update_product(
        created["id"], name="Switch Lite 8", selector="#add"
    )
    assert updated is not None
    assert updated["name"] == "Switch Lite 8"
    assert updated["selector"] == "#add"

    assert temp_db.delete_product(created["id"]) is True
    assert temp_db.delete_product(created["id"]) is False


def test_product_manager_db_compatibility(temp_db: Database) -> None:
    """Verify ProductManager works cleanly against SQLite."""
    pm = ProductManager(db=temp_db)
    assert pm.is_empty() is True
    assert pm.add_product("AP U7", "https://example.com/u7", ".buy") is True
    assert pm.add_product("AP U7", "https://example.com/u7", ".buy") is False
    assert pm.is_empty() is False

    prod = pm.get_product("AP U7")
    assert prod is not None
    assert prod.url == "https://example.com/u7"
    assert "AP U7" in pm.get_product_list_display()

    assert pm.remove_product("AP U7") is True
    assert pm.is_empty() is True


# ----------------------------------------------------------------------
# Stock Checker, Monitor & Unconfigured Discord Fallback Tests
# ----------------------------------------------------------------------


def test_parse_stock_status() -> None:
    """Verify BeautifulSoup selector matching."""
    html_in_stock = "<html><body><button label='Add to Cart'>Buy</button></body></html>"
    html_out = "<html><body><button disabled>Sold Out</button></body></html>"

    assert (
        StockChecker.parse_stock_status(html_in_stock, "button[label='Add to Cart']")
        is True
    )
    assert (
        StockChecker.parse_stock_status(html_out, "button[label='Add to Cart']")
        is False
    )


@pytest.mark.asyncio
async def test_discord_manager_unconfigured_fallback(
    temp_db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    """App must log a friendly notice and never exit when Discord is unconfigured."""
    caplog.set_level(logging.INFO)
    manager = DiscordManager(temp_db)
    assert manager.is_configured() is False
    assert manager.is_connected() is False

    await manager.start()
    assert any(
        "Discord notifications unconfigured" in rec.message for rec in caplog.records
    )
    await manager.stop()


@pytest.mark.asyncio
async def test_discord_manager_invalid_token_does_not_crash(
    temp_db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    """Invalid Discord token must log an error without exiting the application."""
    caplog.set_level(logging.ERROR)
    temp_db.update_settings(
        {"discord_token": "bad-token", "discord_channel_id": "123456789"}
    )
    manager = DiscordManager(temp_db)

    with patch(
        "discord_client.UBStockClient.start",
        new=AsyncMock(side_effect=discord.LoginFailure("Improper token")),
    ):
        await manager.start()
        if manager._task:
            await manager._task
        await manager.stop()

    assert any("invalid bot token" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_stock_monitor_transition_and_notification(temp_db: Database) -> None:
    """Stock changing from out-of-stock to in-stock sends Discord notification."""
    product = temp_db.create_product(
        name="Camera G5",
        url="https://example.com/g5",
        selector="button.add-to-cart",
    )
    temp_db.update_settings(
        {
            "discord_token": "bot-token-xyz",
            "discord_channel_id": "555123",
            "notifications_enabled": "true",
        }
    )

    monitor = StockMonitor(temp_db)
    in_stock_html = "<html><button class='add-to-cart'>Add</button></html>"

    with (
        patch(
            "stock_checker.fetch_product_page",
            new=AsyncMock(return_value=in_stock_html),
        ),
        patch(
            "stock_checker.send_discord_rest_message",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_notify,
    ):
        updated = await monitor.check_product_by_id(product["id"])

    assert updated is not None
    assert updated["in_stock"] is True
    assert updated["last_checked"] is not None
    assert updated["last_status_change"] is not None
    assert updated["last_error"] is None
    mock_notify.assert_awaited_once()

    history = temp_db.get_history()
    assert len(history) == 1
    assert history[0]["status"] == "in_stock"
    assert history[0]["notification_sent"] is True


@pytest.mark.asyncio
async def test_stock_monitor_error_preserves_state(temp_db: Database) -> None:
    """Fetch error records last_error and logs error history without crashing."""
    product = temp_db.create_product(
        name="Gateway Max",
        url="https://example.com/ucg",
        selector=".buy",
    )
    monitor = StockMonitor(temp_db)

    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(side_effect=RuntimeError("Network timeout")),
    ):
        updated = await monitor.check_product_by_id(product["id"])

    assert updated is not None
    assert updated["in_stock"] is False
    assert "Network timeout" in (updated["last_error"] or "")

    history = temp_db.get_history()
    assert len(history) == 1
    assert history[0]["status"] == "error"
    assert history[0]["notification_sent"] is False


# ----------------------------------------------------------------------
# FastAPI Endpoint Tests
# ----------------------------------------------------------------------


def test_health_and_root_static(client: TestClient) -> None:
    """Verify /api/health and static root HTML serving."""
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "version": APP_VERSION}

    root = client.get("/")
    assert root.status_code == 200
    assert "Stock Checker" in root.text


def test_status_and_products_api_lifecycle(client: TestClient) -> None:
    """Verify product CRUD, check endpoints, and aggregate status."""
    status_res = client.get("/api/status")
    assert status_res.status_code == 200
    assert status_res.json() == {
        "total_products": 0,
        "in_stock_count": 0,
        "out_of_stock_count": 0,
        "discord_configured": False,
        "discord_connected": False,
        "notifications_enabled": True,
        "check_interval": 15,
        "last_check": None,
        "is_checking": False,
    }

    # Create product
    create_res = client.post(
        "/api/products",
        json={
            "name": "UDM SE",
            "url": "https://store.ui.com/us/en/products/udm-se",
            "selector": "button[label='Add to Cart']",
        },
    )
    assert create_res.status_code == 201
    created = create_res.json()
    product_id = created["id"]
    assert created["name"] == "UDM SE"
    assert created["in_stock"] is False

    # Duplicate rejected with 409
    dup_res = client.post(
        "/api/products",
        json={
            "name": "UDM SE",
            "url": "https://store.ui.com/us/en/products/udm-se",
            "selector": ".buy",
        },
    )
    assert dup_res.status_code == 409

    # List products
    list_res = client.get("/api/products")
    assert list_res.status_code == 200
    assert len(list_res.json()) == 1

    # Update product
    put_res = client.put(
        f"/api/products/{product_id}",
        json={"name": "UDM SE Updated", "selector": ".add-cart"},
    )
    assert put_res.status_code == 200
    assert put_res.json()["name"] == "UDM SE Updated"
    assert put_res.json()["selector"] == ".add-cart"

    # Immediate check on single product
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value="<button class='add-cart'>Buy</button>"),
    ):
        check_res = client.post(f"/api/products/{product_id}/check")
    assert check_res.status_code == 200
    assert check_res.json()["in_stock"] is True

    # Status reflects in-stock product
    status_after = client.get("/api/status").json()
    assert status_after["total_products"] == 1
    assert status_after["in_stock_count"] == 1
    assert status_after["out_of_stock_count"] == 0
    assert status_after["last_check"] is not None

    # Check all endpoint
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value="<div>Out of Stock</div>"),
    ):
        check_all_res = client.post("/api/check-all")
    assert check_all_res.status_code == 200
    assert check_all_res.json()["checked"] == 1
    assert check_all_res.json()["out_of_stock_count"] == 1

    # History populated & clearable
    hist_res = client.get("/api/history?limit=10")
    assert hist_res.status_code == 200
    assert len(hist_res.json()) == 2

    del_hist = client.delete("/api/history")
    assert del_hist.status_code == 200
    assert del_hist.json()["deleted"] == 2
    assert client.get("/api/history").json() == []

    # Delete product
    del_res = client.delete(f"/api/products/{product_id}")
    assert del_res.status_code == 200
    assert client.get("/api/products").json() == []
    assert client.delete(f"/api/products/{product_id}").status_code == 404


def test_test_selector_endpoint(client: TestClient) -> None:
    """Verify /api/products/test-selector checks without saving."""
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value="<div id='stock'>In Stock</div>"),
    ):
        res = client.post(
            "/api/products/test-selector",
            json={"url": "https://example.com/item", "selector": "#stock"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["in_stock"] is True
    assert body["found_selector"] is True
    assert body["strategy_used"] == "text_heuristic"
    assert body["detected_by"] == "in_stock_text"
    assert body["error"] is None
    assert body["details"] is not None
    assert client.get("/api/products").json() == []

    # Invalid CSS selector returns graceful error
    bad_res = client.post(
        "/api/products/test-selector",
        json={"url": "https://example.com/item", "selector": "div[[["},
    )
    assert bad_res.status_code == 200
    assert bad_res.json()["in_stock"] is False
    assert bad_res.json()["found_selector"] is False
    assert bad_res.json()["error"] is not None


def test_settings_and_test_discord_endpoints(client: TestClient) -> None:
    """Verify /api/settings GET/POST masking and /api/settings/test-discord."""
    initial = client.get("/api/settings")
    assert initial.status_code == 200
    assert initial.json() == {
        "discord_token_set": False,
        "discord_token_masked": "",
        "discord_channel_id": "",
        "check_stock_interval": 15,
        "notifications_enabled": True,
        "telegram_bot_token_set": False,
        "telegram_bot_token_masked": "",
        "telegram_chat_id": "",
        "telegram_enabled": False,
        "webhook_url": "",
        "webhook_enabled": False,
        "price_alerts_enabled": True,
        "proxy_list": "",
        "proxy_enabled": False,
        "domain_rate_limit_delay": 1.0,
    }

    # Test discord without credentials returns success=False cleanly
    no_creds = client.post("/api/settings/test-discord", json={})
    assert no_creds.status_code == 200
    assert no_creds.json()["success"] is False
    assert no_creds.json()["error"] is not None

    # Save settings
    saved = client.post(
        "/api/settings",
        json={
            "discord_token": "supersecrettoken1234",
            "discord_channel_id": "1234567890",
            "check_stock_interval": 30,
            "notifications_enabled": False,
        },
    )
    assert saved.status_code == 200
    data = saved.json()
    assert data["discord_token_set"] is True
    assert data["discord_token_masked"] == mask_token("supersecrettoken1234")
    assert "supersecrettoken1234" not in data["discord_token_masked"]
    assert data["discord_channel_id"] == "1234567890"
    assert data["check_stock_interval"] == 30
    assert data["notifications_enabled"] is False

    # Partial update preserves existing token
    partial = client.post("/api/settings", json={"check_stock_interval": 45})
    assert partial.status_code == 200
    assert partial.json()["discord_token_set"] is True
    assert partial.json()["check_stock_interval"] == 45

    # Test discord with saved credentials (mocked HTTP)
    with patch(
        "api.send_discord_rest_message",
        new=AsyncMock(return_value=(True, None)),
    ) as mock_send:
        ok_res = client.post("/api/settings/test-discord")
    assert ok_res.status_code == 200
    assert ok_res.json() == {"success": True, "error": None}
    mock_send.assert_awaited_once()
    assert mock_send.await_args.args[0] == "supersecrettoken1234"
    assert mock_send.await_args.args[1] == "1234567890"


# ----------------------------------------------------------------------
# Static Assets, Zero-.env Lifecycle & Edge-Case Integration Tests
# ----------------------------------------------------------------------


def test_real_static_assets_served(temp_db: Database) -> None:
    """Verify FastAPI serves static assets at /, /favicon.svg, /styles.css, /app.js."""
    app = create_app(
        db=temp_db,
        static_dir="static",
        start_background_tasks=False,
    )
    with TestClient(app) as real_client:
        index_res = real_client.get("/")
        assert index_res.status_code == 200
        assert "text/html" in index_res.headers["content-type"]
        assert "Stock Checker" in index_res.text
        assert "/favicon.svg" in index_res.text
        assert "/styles.css" in index_res.text
        assert "/app.js" in index_res.text
        assert 'id="product-strategy-select"' in index_res.text
        assert 'id="btn-toggle-optional-selector"' in index_res.text
        assert 'id="optional-selector-toggle-row"' in index_res.text
        assert 'id="product-selector-group"' in index_res.text

        favicon_res = real_client.get("/favicon.svg")
        assert favicon_res.status_code == 200
        assert "svg" in favicon_res.headers["content-type"]
        assert "<svg" in favicon_res.text

        css_res = real_client.get("/styles.css")
        assert css_res.status_code == 200
        assert "text/css" in css_res.headers["content-type"]
        assert "--bg-base" in css_res.text
        assert ".strategy-badge" in css_res.text
        assert ".strategy-pill" in css_res.text

        js_res = real_client.get("/app.js")
        assert js_res.status_code == 200
        assert "javascript" in js_res.headers["content-type"]
        assert "/api/products" in js_res.text
        assert "/api/products/test-selector" in js_res.text
        assert "/api/settings" in js_res.text
        assert "product-strategy-select" in js_res.text
        assert "btn-toggle-optional-selector" in js_res.text
        assert "strategy-pill--auto" in js_res.text


def test_zero_env_startup_with_background_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """App starts cleanly with background tasks when .env and env vars are absent."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.delenv("CHECK_STOCK_INTERVAL", raising=False)

    db = Database(
        db_path=str(tmp_path / "data" / "ubstock.db"),
        products_file=str(tmp_path / "data" / "products.json"),
    )
    static_dir = tmp_path / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")

    app = create_app(
        db=db,
        static_dir=str(static_dir),
        start_background_tasks=True,
    )
    with TestClient(app) as live_client:
        health = live_client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        status_res = live_client.get("/api/status")
        assert status_res.status_code == 200
        data = status_res.json()
        assert data["discord_configured"] is False
        assert data["discord_connected"] is False
        assert data["check_interval"] == 15


def test_empty_states_and_invalid_product_inputs(client: TestClient) -> None:
    """Verify empty state responses, 404s, 409 rename conflicts, and 422 validations."""
    # Empty check-all & empty history clear
    empty_check = client.post("/api/check-all")
    assert empty_check.status_code == 200
    assert empty_check.json() == {
        "checked": 0,
        "in_stock_count": 0,
        "out_of_stock_count": 0,
        "products": [],
    }
    empty_clear = client.delete("/api/history")
    assert empty_clear.status_code == 200
    assert empty_clear.json() == {"status": "cleared", "deleted": 0}

    # Non-existent product endpoints return 404
    assert client.put("/api/products/9999", json={"name": "Missing"}).status_code == 404
    assert client.post("/api/products/9999/check").status_code == 404

    # Invalid URL, whitespace name, invalid selector rejected with 422
    assert (
        client.post(
            "/api/products",
            json={
                "name": "Bad URL",
                "url": "ftp://example.com/item",
                "selector": ".buy",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/products",
            json={
                "name": "   ",
                "url": "https://example.com/item",
                "selector": ".buy",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/products",
            json={
                "name": "Bad Selector",
                "url": "https://example.com/item",
                "selector": "div[[[",
            },
        ).status_code
        == 422
    )

    # Blank selector defaults to '' for auto, and DEFAULT_SELECTOR for css_selector
    default_sel = client.post(
        "/api/products",
        json={
            "name": "Product One",
            "url": "https://example.com/one",
            "selector": "   ",
        },
    )
    assert default_sel.status_code == 201
    assert default_sel.json()["strategy"] == "auto"
    assert default_sel.json()["selector"] == ""

    css_default_sel = client.post(
        "/api/products",
        json={
            "name": "Product CSS Default",
            "url": "https://example.com/css",
            "strategy": "css_selector",
        },
    )
    assert css_default_sel.status_code == 201
    assert css_default_sel.json()["strategy"] == "css_selector"
    assert css_default_sel.json()["selector"] == "button[label='Add to Cart']"

    # Unsupported strategy rejected with 422
    assert (
        client.post(
            "/api/products",
            json={
                "name": "Bad Strategy",
                "url": "https://example.com/bad-strategy",
                "strategy": "magic_ai",
            },
        ).status_code
        == 422
    )

    # Duplicate name on PUT returns 409 Conflict
    second = client.post(
        "/api/products",
        json={
            "name": "Product Two",
            "url": "https://example.com/two",
            "selector": ".buy",
        },
    )
    assert second.status_code == 201
    second_id = second.json()["id"]

    rename_conflict = client.put(
        f"/api/products/{second_id}",
        json={"name": "Product One"},
    )
    assert rename_conflict.status_code == 409


def test_test_selector_edge_cases(client: TestClient) -> None:
    """Verify /api/products/test-selector handles misses, failures, and bad URLs."""
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value="<div>Sold Out</div>"),
    ):
        miss = client.post(
            "/api/products/test-selector",
            json={"url": "https://example.com/item", "selector": "#buy-now"},
        )
    assert miss.status_code == 200
    miss_json = miss.json()
    assert miss_json["in_stock"] is False
    assert miss_json["found_selector"] is False
    assert miss_json["strategy_used"] == "text_heuristic"
    assert miss_json["detected_by"] == "sold_out_text"
    assert miss_json["error"] is None

    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(side_effect=RuntimeError("Connection refused")),
    ):
        err = client.post(
            "/api/products/test-selector",
            json={"url": "https://example.com/item", "selector": "#buy-now"},
        )
    assert err.status_code == 200
    assert err.json()["in_stock"] is False
    assert err.json()["found_selector"] is False
    assert "Connection refused" in (err.json()["error"] or "")

    bad_url = client.post(
        "/api/products/test-selector",
        json={"url": "not-a-url", "selector": "#buy-now"},
    )
    assert bad_url.status_code == 422


def test_dynamic_settings_persistence_and_notification_toggle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify settings persist across DB reloads, mask tokens, and gate alerts."""
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.delenv("CHECK_STOCK_INTERVAL", raising=False)

    db_path = tmp_path / "persist.db"
    db = Database(db_path=str(db_path), products_file=str(tmp_path / "none.json"))
    static_dir = tmp_path / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    app = create_app(db=db, static_dir=str(static_dir), start_background_tasks=False)
    with TestClient(app) as api_client:
        # Invalid check interval rejected
        bad_interval = api_client.post(
            "/api/settings", json={"check_stock_interval": 0}
        )
        assert bad_interval.status_code == 422
        assert (
            api_client.post(
                "/api/settings", json={"check_stock_interval": 90000}
            ).status_code
            == 422
        )

        # Save token and disable notifications
        res = api_client.post(
            "/api/settings",
            json={
                "discord_token": "real-secret-token-9876",
                "discord_channel_id": "111222333",
                "check_stock_interval": 42,
                "notifications_enabled": False,
            },
        )
        assert res.status_code == 200
        masked = res.json()["discord_token_masked"]

        # Sending back masked token does NOT overwrite saved token
        res2 = api_client.post(
            "/api/settings",
            json={"discord_token": masked, "check_stock_interval": 60},
        )
        assert res2.status_code == 200
        assert db.get_setting("discord_token") == "real-secret-token-9876"

        # Status reflects dynamic DB interval & disabled notifications
        st = api_client.get("/api/status").json()
        assert st["check_interval"] == 60
        assert st["notifications_enabled"] is False
        assert st["discord_configured"] is True

        # Create product and trigger transition while notifications are disabled
        prod = api_client.post(
            "/api/products",
            json={
                "name": "G5 Bullet",
                "url": "https://example.com/g5-bullet",
                "selector": ".buy",
            },
        ).json()

        with (
            patch(
                "stock_checker.fetch_product_page",
                new=AsyncMock(return_value="<button class='buy'>Add</button>"),
            ),
            patch(
                "stock_checker.send_discord_rest_message",
                new=AsyncMock(return_value=(True, None)),
            ) as mock_notify,
        ):
            chk = api_client.post(f"/api/products/{prod['id']}/check")
        assert chk.status_code == 200
        assert chk.json()["in_stock"] is True
        mock_notify.assert_not_awaited()

        history = api_client.get("/api/history?limit=1").json()
        assert len(history) == 1
        assert history[0]["notification_sent"] is False

        # Test-discord with override credentials and failure response
        with patch(
            "api.send_discord_rest_message",
            new=AsyncMock(return_value=(False, "Discord API error (HTTP 401)")),
        ) as mock_test_send:
            fail_res = api_client.post(
                "/api/settings/test-discord",
                json={
                    "discord_token": "override-token-123",
                    "discord_channel_id": "999888777",
                },
            )
        assert fail_res.status_code == 200
        assert fail_res.json() == {
            "success": False,
            "error": "Discord API error (HTTP 401)",
        }
        mock_test_send.assert_awaited_once()
        assert mock_test_send.await_args.args[0] == "override-token-123"
        assert mock_test_send.await_args.args[1] == "999888777"

        # History query boundary validation
        assert api_client.get("/api/history?limit=0").status_code == 422
        assert api_client.get("/api/history?limit=501").status_code == 422

    # Verify persistence across fresh Database instance
    reloaded_db = Database(
        db_path=str(db_path), products_file=str(tmp_path / "none.json")
    )
    assert reloaded_db.get_setting("check_stock_interval") == "60"
    assert reloaded_db.get_setting("notifications_enabled") == "false"
    assert reloaded_db.get_setting("discord_token") == "real-secret-token-9876"
    assert len(reloaded_db.list_products()) == 1


# ----------------------------------------------------------------------
# Multi-Strategy Stock Detection & DB Migration Tests
# ----------------------------------------------------------------------


def test_sqlite_migration_adds_strategy_columns(tmp_path: Path) -> None:
    """Verify existing DBs without strategy columns are migrated cleanly."""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            url TEXT NOT NULL,
            selector TEXT NOT NULL,
            in_stock INTEGER DEFAULT 0,
            last_checked TIMESTAMP,
            last_status_change TIMESTAMP,
            last_error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        INSERT INTO products (name, url, selector, in_stock)
        VALUES ('Legacy Item', 'https://example.com/legacy', '.buy', 1)
        """
    )
    conn.commit()
    conn.close()

    migrated = Database(db_path=str(db_path), products_file=str(tmp_path / "none.json"))
    with sqlite3.connect(str(db_path)) as verify_conn:
        cols = {
            row[1]
            for row in verify_conn.execute("PRAGMA table_info(products)").fetchall()
        }
    assert "strategy" in cols
    assert "strategy_details" in cols

    items = migrated.list_products()
    assert len(items) == 1
    assert items[0]["name"] == "Legacy Item"
    assert items[0]["strategy"] == "auto"
    assert items[0]["strategy_details"] is None


def test_product_manager_strategy_fields(temp_db: Database) -> None:
    """Verify ProductManager add/get/update/list handle strategy fields."""
    pm = ProductManager(db=temp_db)
    assert pm.add_product(
        name="Widget",
        url="https://shop.example.com/products/widget",
        selector="",
        strategy="shopify",
        strategy_details="1 of 1 variant(s) available",
    )
    prod = pm.get_product("Widget")
    assert prod is not None
    assert prod.strategy == "shopify"
    assert prod.strategy_details == "1 of 1 variant(s) available"

    all_prods = pm.get_products()
    assert len(all_prods) == 1
    assert all_prods[0].id == prod.id

    assert prod.id is not None
    updated = pm.update_product(
        prod.id,
        strategy="json_ld",
        strategy_details="Offer availability: InStock",
    )
    assert updated is not None
    assert updated.strategy == "json_ld"
    assert updated.strategy_details == "Offer availability: InStock"


@pytest.mark.asyncio
async def test_strategy_shopify_explicit_and_auto() -> None:
    """Verify Shopify JSON detection in explicit and auto modes."""
    in_stock_payload = {
        "product": {
            "variants": [
                {"id": 1, "available": False},
                {"id": 2, "available": True},
            ]
        }
    }
    out_stock_payload = {
        "product": {
            "variants": [
                {"id": 1, "available": False},
            ]
        }
    }

    with patch(
        "stock_checker.fetch_shopify_json",
        new=AsyncMock(return_value=(in_stock_payload, None)),
    ):
        res_in = await StockChecker.detect_stock(
            url="https://coolshop.com/products/widget",
            strategy="shopify",
        )
    assert res_in["in_stock"] is True
    assert res_in["strategy_used"] == "shopify"
    assert res_in["detected_by"] == "shopify_json"
    assert "1 of 2" in (res_in["details"] or "")
    assert res_in["error"] is None

    with patch(
        "stock_checker.fetch_shopify_json",
        new=AsyncMock(return_value=(out_stock_payload, None)),
    ):
        res_out = await StockChecker.detect_stock(
            url="https://coolshop.com/products/widget",
            strategy="shopify",
        )
    assert res_out["in_stock"] is False
    assert res_out["strategy_used"] == "shopify"
    assert "0 of 1" in (res_out["details"] or "")

    # Auto mode triggers Shopify JSON when HTML contains Shopify storefront markers
    shopify_html = (
        '<html><head><script src="https://cdn.shopify.com/s/app.js"></script>'
        "</head><body><div>Sold Out</div></body></html>"
    )
    with (
        patch(
            "stock_checker.fetch_product_page",
            new=AsyncMock(return_value=shopify_html),
        ),
        patch(
            "stock_checker.fetch_shopify_json",
            new=AsyncMock(return_value=(in_stock_payload, None)),
        ),
    ):
        res_auto = await StockChecker.detect_stock(
            url="https://coolshop.com/products/widget",
            strategy="auto",
        )
    assert res_auto["in_stock"] is True
    assert res_auto["strategy_used"] == "shopify"
    assert res_auto["detected_by"] == "shopify_json"


@pytest.mark.asyncio
async def test_strategy_json_ld_variants_and_auto() -> None:
    """Verify Schema.org JSON-LD detection for InStock, PreOrder, and OutOfStock."""
    html_preorder = """
    <html><head>
      <script type="application/ld+json">{bad json</script>
      <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@graph": [
            {"@type": "WebPage", "name": "Store"},
            {
              "@type": "Product",
              "name": "Router",
              "offers": [
                {"@type": "Offer", "availability": "https://schema.org/OutOfStock"},
                {"@type": "Offer", "availability": "https://schema.org/PreOrder"}
              ]
            }
          ]
        }
      </script>
    </head><body><p>Sold Out</p></body></html>
    """
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_preorder),
    ):
        res_auto = await StockChecker.detect_stock(
            url="https://example.com/router", strategy="auto"
        )
    assert res_auto["in_stock"] is True
    assert res_auto["strategy_used"] == "json_ld"
    assert res_auto["detected_by"] == "json_ld_offer"

    html_sold_out = """
    <html><head>
      <script type="application/ld+json">
        {
          "@type": "Product",
          "offers": {"@type": "Offer", "availability": "https://schema.org/SoldOut"}
        }
      </script>
    </head></html>
    """
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_sold_out),
    ):
        res_explicit = await StockChecker.detect_stock(
            url="https://example.com/router", strategy="json_ld"
        )
    assert res_explicit["in_stock"] is False
    assert res_explicit["strategy_used"] == "json_ld"
    assert "SoldOut" in (res_explicit["details"] or "")


@pytest.mark.asyncio
async def test_strategy_meta_tags_opengraph() -> None:
    """Verify OpenGraph and product meta availability detection in auto mode."""
    html_og_in = (
        '<html><head><meta property="og:availability" content="instock" /></head>'
        "<body></body></html>"
    )
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_og_in),
    ):
        res_in = await StockChecker.detect_stock(
            url="https://example.com/item", strategy="auto"
        )
    assert res_in["in_stock"] is True
    assert res_in["strategy_used"] == "meta_tags"
    assert res_in["detected_by"] == "og:availability"

    html_prod_out = (
        '<html><head><meta property="product:availability" content="out of stock" />'
        "</head><body></body></html>"
    )
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_prod_out),
    ):
        res_out = await StockChecker.detect_stock(
            url="https://example.com/item", strategy="auto"
        )
    assert res_out["in_stock"] is False
    assert res_out["strategy_used"] == "meta_tags"
    assert res_out["detected_by"] == "product:availability"


@pytest.mark.asyncio
async def test_strategy_text_heuristic_and_disabled_buttons() -> None:
    """Verify text heuristics, button disabled/aria-disabled, and script ignoring."""
    html_enabled = (
        "<html><body><button type='button'>Add to Cart</button></body></html>"
    )
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_enabled),
    ):
        res_ok = await StockChecker.detect_stock(
            url="https://example.com/item", strategy="text_heuristic"
        )
    assert res_ok["in_stock"] is True
    assert res_ok["strategy_used"] == "text_heuristic"
    assert res_ok["detected_by"] == "button_enabled"

    html_disabled = "<html><body><button disabled>Add to Cart</button></body></html>"
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_disabled),
    ):
        res_dis = await StockChecker.detect_stock(
            url="https://example.com/item", strategy="text_heuristic"
        )
    assert res_dis["in_stock"] is False
    assert res_dis["detected_by"] == "button_disabled"

    html_aria_disabled = (
        '<html><body><a role="button" aria-disabled="true">Buy Now</a></body></html>'
    )
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_aria_disabled),
    ):
        res_aria = await StockChecker.detect_stock(
            url="https://example.com/item", strategy="text_heuristic"
        )
    assert res_aria["in_stock"] is False
    assert res_aria["detected_by"] == "button_disabled"

    # Script tag text like "sold out" must be ignored
    html_script_noise = (
        "<html><body><script>var msg = 'sold out';</script>"
        "<p>Currently Unavailable</p></body></html>"
    )
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_script_noise),
    ):
        res_text = await StockChecker.detect_stock(
            url="https://example.com/item", strategy="text_heuristic"
        )
    assert res_text["in_stock"] is False
    assert res_text["detected_by"] == "sold_out_text"


@pytest.mark.asyncio
async def test_strategy_css_selector_with_disabled_inspection() -> None:
    """Verify css_selector strategy detects enabled vs disabled matched elements."""
    html_enabled = "<html><body><button label='Add to Cart'>Buy</button></body></html>"
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_enabled),
    ):
        res_ok = await StockChecker.detect_stock(
            url="https://example.com/item",
            selector="button[label='Add to Cart']",
            strategy="css_selector",
        )
    assert res_ok["in_stock"] is True
    assert res_ok["found_selector"] is True
    assert res_ok["strategy_used"] == "css_selector"
    assert res_ok["detected_by"] == "css_selector"

    html_disabled = (
        "<html><body><button label='Add to Cart' disabled>Buy</button></body></html>"
    )
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_disabled),
    ):
        res_dis = await StockChecker.detect_stock(
            url="https://example.com/item",
            selector="button[label='Add to Cart']",
            strategy="css_selector",
        )
    assert res_dis["in_stock"] is False
    assert res_dis["found_selector"] is True
    assert res_dis["strategy_used"] == "css_selector"
    assert res_dis["detected_by"] == "css_selector_disabled"


@pytest.mark.asyncio
async def test_auto_cascade_falls_back_to_css_selector() -> None:
    """Verify auto strategy falls through Steps 1-4 to CSS selector fallback."""
    neutral_html = "<html><body><span class='custom-ready'>Go</span></body></html>"
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=neutral_html),
    ):
        res = await StockChecker.detect_stock(
            url="https://example.com/item",
            selector=".custom-ready",
            strategy="auto",
        )
    assert res["in_stock"] is True
    assert res["found_selector"] is True
    assert res["strategy_used"] == "css_selector"
    assert res["detected_by"] == "css_selector"


def test_api_multi_strategy_create_update_test_and_check(client: TestClient) -> None:
    """Verify API endpoints persist strategy, run test-selector, and check-all."""
    # Create JSON-LD product without a CSS selector
    p1 = client.post(
        "/api/products",
        json={
            "name": "LD Product",
            "url": "https://example.com/ld-product",
            "strategy": "json_ld",
        },
    )
    assert p1.status_code == 201
    p1_data = p1.json()
    assert p1_data["strategy"] == "json_ld"
    assert p1_data["selector"] == ""
    assert p1_data["strategy_details"] is None

    # Create Shopify product
    p2 = client.post(
        "/api/products",
        json={
            "name": "Shopify Product",
            "url": "https://mystore.myshopify.com/products/gadget",
            "strategy": "shopify",
        },
    )
    assert p2.status_code == 201
    assert p2.json()["strategy"] == "shopify"

    # Update LD Product to css_selector without selector -> auto-populates default
    upd = client.put(
        f"/api/products/{p1_data['id']}",
        json={"strategy": "css_selector"},
    )
    assert upd.status_code == 200
    assert upd.json()["strategy"] == "css_selector"
    assert upd.json()["selector"] == "button[label='Add to Cart']"

    # Switch back to json_ld and test check + check-all
    client.put(
        f"/api/products/{p1_data['id']}",
        json={"strategy": "json_ld", "selector": ""},
    )

    ld_html = (
        '<script type="application/ld+json">'
        '{"@type":"Product","offers":{"availability":"https://schema.org/InStock"}}'
        "</script>"
    )
    shopify_payload = {"product": {"variants": [{"id": 1, "available": False}]}}

    with (
        patch(
            "stock_checker.fetch_product_page",
            new=AsyncMock(return_value=ld_html),
        ),
        patch(
            "stock_checker.fetch_shopify_json",
            new=AsyncMock(return_value=(shopify_payload, None)),
        ),
    ):
        chk_all = client.post("/api/check-all")

    assert chk_all.status_code == 200
    summary = chk_all.json()
    assert summary["checked"] == 2
    assert summary["in_stock_count"] == 1
    assert summary["out_of_stock_count"] == 1

    by_name = {p["name"]: p for p in summary["products"]}
    assert by_name["LD Product"]["in_stock"] is True
    assert "json_ld" in (by_name["LD Product"]["strategy_details"] or "")
    assert by_name["Shopify Product"]["in_stock"] is False
    assert "shopify" in (by_name["Shopify Product"]["strategy_details"] or "")

    # Test /api/products/test-selector with explicit strategy
    with patch(
        "stock_checker.fetch_shopify_json",
        new=AsyncMock(
            return_value=({"product": {"variants": [{"available": True}]}}, None)
        ),
    ):
        test_res = client.post(
            "/api/products/test-selector",
            json={
                "url": "https://mystore.myshopify.com/products/gadget",
                "strategy": "shopify",
            },
        )
    assert test_res.status_code == 200
    assert test_res.json()["in_stock"] is True
    assert test_res.json()["strategy_used"] == "shopify"
    assert test_res.json()["detected_by"] == "shopify_json"


def test_api_omitted_selector_non_css_strategies_and_strategy_updates(
    client: TestClient,
) -> None:
    """Verify omitted selectors for non-CSS strategies and PUT strategy transitions."""
    # 1. All non-CSS strategies succeed on POST /api/products when selector is omitted
    for strat in ("auto", "json_ld", "shopify", "text_heuristic"):
        res = client.post(
            "/api/products",
            json={
                "name": f"No Selector {strat}",
                "url": f"https://example.com/{strat}",
                "strategy": strat,
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert body["strategy"] == strat
        assert body["selector"] == ""

    # Explicit null selector is also normalized to "" for non-CSS strategies
    null_sel = client.post(
        "/api/products",
        json={
            "name": "Null Selector Heuristic",
            "url": "https://example.com/null-sel",
            "strategy": "text_heuristic",
            "selector": None,
        },
    )
    assert null_sel.status_code == 201
    assert null_sel.json()["selector"] == ""
    prod_id = null_sel.json()["id"]

    # 2. /api/products/test-selector works with omitted selector across non-CSS modes
    html_button = "<button>Add to Cart</button>"
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_button),
    ):
        for strat in ("auto", "text_heuristic"):
            test_res = client.post(
                "/api/products/test-selector",
                json={"url": "https://example.com/item", "strategy": strat},
            )
            assert test_res.status_code == 200
            assert test_res.json()["in_stock"] is True
            assert test_res.json()["strategy_used"] == "text_heuristic"
            assert test_res.json()["error"] is None

        # css_selector with omitted selector falls back to DEFAULT_SELECTOR
        css_fallback = client.post(
            "/api/products/test-selector",
            json={"url": "https://example.com/item", "strategy": "css_selector"},
        )
        assert css_fallback.status_code == 200
        assert css_fallback.json()["strategy_used"] == "css_selector"
        assert css_fallback.json()["in_stock"] is False
        assert css_fallback.json()["found_selector"] is False

    # Unsupported strategy in test-selector returns structured error
    bad_strat_test = client.post(
        "/api/products/test-selector",
        json={"url": "https://example.com/item", "strategy": "unknown_mode"},
    )
    assert bad_strat_test.status_code == 200
    assert bad_strat_test.json()["in_stock"] is False
    assert "Unsupported strategy" in (bad_strat_test.json()["error"] or "")

    # 3. Updating strategy on an existing product preserves/populates selector properly
    # Non-CSS -> non-CSS with omitted selector keeps selector=""
    upd1 = client.put(f"/api/products/{prod_id}", json={"strategy": "json_ld"})
    assert upd1.status_code == 200
    assert upd1.json()["strategy"] == "json_ld"
    assert upd1.json()["selector"] == ""

    # Non-CSS -> css_selector with omitted selector auto-populates DEFAULT_SELECTOR
    upd2 = client.put(f"/api/products/{prod_id}", json={"strategy": "css_selector"})
    assert upd2.status_code == 200
    assert upd2.json()["strategy"] == "css_selector"
    assert upd2.json()["selector"] == "button[label='Add to Cart']"

    # Custom css_selector -> auto preserves custom selector as fallback
    upd3 = client.put(
        f"/api/products/{prod_id}",
        json={"selector": ".custom-buy", "strategy": "css_selector"},
    )
    assert upd3.status_code == 200
    assert upd3.json()["selector"] == ".custom-buy"

    upd4 = client.put(f"/api/products/{prod_id}", json={"strategy": "auto"})
    assert upd4.status_code == 200
    assert upd4.json()["strategy"] == "auto"
    assert upd4.json()["selector"] == ".custom-buy"

    # Invalid strategy update returns 422
    bad_upd = client.put(f"/api/products/{prod_id}", json={"strategy": "invalid_strat"})
    assert bad_upd.status_code == 422

    # Updated strategy is persisted and used during /api/products/{id}/check
    upd5 = client.put(
        f"/api/products/{prod_id}",
        json={"strategy": "text_heuristic", "selector": ""},
    )
    assert upd5.status_code == 200
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value="<button>Buy Now</button>"),
    ):
        chk = client.post(f"/api/products/{prod_id}/check")
    assert chk.status_code == 200
    assert chk.json()["in_stock"] is True
    assert chk.json()["strategy"] == "text_heuristic"
    assert "text_heuristic" in (chk.json()["strategy_details"] or "")


@pytest.mark.asyncio
async def test_bot_commands_add_product_strategy_and_selector_display(
    temp_db: Database,
) -> None:
    """Verify Discord /add_product defaults to auto strategy and formats selector."""
    from unittest.mock import MagicMock

    from bot_commands import BotCommands, create_commands

    pm = ProductManager(db=temp_db)
    commands = BotCommands(pm)

    interaction = MagicMock()
    interaction.user = "tester#0001"
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    await commands.add_product(
        interaction,
        name="Auto Item",
        url="https://example.com/auto",
    )
    msg = interaction.response.send_message.await_args.args[0]
    assert "**Product:** Auto Item" in msg
    assert "**Strategy:** auto" in msg
    assert "**Selector:** (Auto-detect - no selector required)" in msg

    saved = pm.get_product("Auto Item")
    assert saved is not None
    assert saved.strategy == "auto"
    assert saved.selector == ""

    # Explicit css_selector without selector defaults to DEFAULT_SELECTOR
    interaction2 = MagicMock()
    interaction2.user = "tester#0001"
    interaction2.response = MagicMock()
    interaction2.response.send_message = AsyncMock()

    await commands.add_product(
        interaction2,
        name="CSS Item",
        url="https://example.com/css",
        strategy="css_selector",
    )
    msg2 = interaction2.response.send_message.await_args.args[0]
    assert "**Strategy:** css_selector" in msg2
    assert "**Selector:** button[label='Add to Cart']" in msg2

    # Ensure slash command wrapper builds cleanly
    ping, add_cmd, remove_cmd, list_cmd = create_commands(pm)
    assert add_cmd.name == "add_product"
    assert [p.name for p in add_cmd.parameters] == [
        "name",
        "url",
        "strategy",
        "selector",
    ]


# ----------------------------------------------------------------------
# Price Tracking, Multi-Channel Notifications, Proxy & Rate Limit Tests
# ----------------------------------------------------------------------


def test_sqlite_migration_adds_price_columns_and_table(tmp_path: Path) -> None:
    """Verify legacy DBs migrate price columns, price_history table, and settings."""
    import sqlite3

    db_path = tmp_path / "legacy_v2.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            url TEXT NOT NULL,
            selector TEXT NOT NULL DEFAULT '',
            strategy TEXT DEFAULT 'auto',
            strategy_details TEXT,
            in_stock INTEGER DEFAULT 0,
            last_checked TIMESTAMP,
            last_status_change TIMESTAMP,
            last_error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        INSERT INTO products (name, url, selector, in_stock)
        VALUES ('Old Router', 'https://example.com/router', '.buy', 0)
        """
    )
    conn.commit()
    conn.close()

    migrated = Database(db_path=str(db_path), products_file=str(tmp_path / "none.json"))
    with sqlite3.connect(str(db_path)) as verify_conn:
        cols = {
            row[1]
            for row in verify_conn.execute("PRAGMA table_info(products)").fetchall()
        }
        tables = {
            row[0]
            for row in verify_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert {"current_price", "currency", "previous_price"}.issubset(cols)
    assert "price_history" in tables

    items = migrated.list_products()
    assert len(items) == 1
    assert items[0]["current_price"] is None
    assert items[0]["currency"] == "USD"
    assert items[0]["previous_price"] is None
    assert migrated.get_setting("price_alerts_enabled") == "true"
    assert migrated.get_setting("domain_rate_limit_delay") == "1.0"


def test_database_price_history_crud_and_price_transitions(temp_db: Database) -> None:
    """Verify record_price, get_price_history, and previous_price tracking."""
    prod = temp_db.create_product(
        name="Dream Router",
        url="https://example.com/udr",
        selector=".buy",
    )
    pid = prod["id"]

    # First price observation
    temp_db.record_price(pid, "Dream Router", 199.0, "USD")
    p1 = temp_db.update_product_check_result(
        pid, in_stock=True, error=None, current_price=199.0, currency="USD"
    )
    assert p1 is not None
    assert p1["current_price"] == 199.0
    assert p1["previous_price"] is None
    assert p1["currency"] == "USD"

    # Unchanged price preserves previous_price
    temp_db.record_price(pid, "Dream Router", 199.0, "USD")
    p2 = temp_db.update_product_check_result(
        pid, in_stock=True, error=None, current_price=199.0, currency="USD"
    )
    assert p2 is not None
    assert p2["current_price"] == 199.0
    assert p2["previous_price"] is None

    # Price drop sets previous_price to prior current_price
    temp_db.record_price(pid, "Dream Router", 149.0, "USD")
    p3 = temp_db.update_product_check_result(
        pid, in_stock=True, error=None, current_price=149.0, currency="USD"
    )
    assert p3 is not None
    assert p3["current_price"] == 149.0
    assert p3["previous_price"] == 199.0

    # Subsequent unchanged check keeps previous_price=199.0
    p4 = temp_db.update_product_check_result(
        pid, in_stock=True, error=None, current_price=149.0, currency="USD"
    )
    assert p4 is not None
    assert p4["previous_price"] == 199.0

    history = temp_db.get_price_history("Dream Router", limit=2)
    assert len(history) == 2
    assert history[0]["price"] == 149.0
    assert history[1]["price"] == 199.0

    all_history = temp_db.get_price_history("Dream Router")
    assert len(all_history) == 3

    # Rename updates price_history product_name
    temp_db.update_product(pid, name="Dream Router 7")
    renamed_hist = temp_db.get_price_history("Dream Router 7")
    assert len(renamed_hist) == 3


@pytest.mark.asyncio
async def test_price_extraction_across_all_strategies() -> None:
    """Verify price & currency extraction across Shopify, JSON-LD, Meta, and Text."""
    # 1. Shopify variant price
    shopify_payload = {
        "product": {
            "variants": [
                {"id": 1, "available": True, "price": "249.50"},
                {"id": 2, "available": False, "price": "299.00"},
            ]
        }
    }
    with patch(
        "stock_checker.fetch_shopify_json",
        new=AsyncMock(return_value=(shopify_payload, None)),
    ):
        res_shopify = await StockChecker.detect_stock(
            url="https://shop.example.com/products/cam",
            strategy="shopify",
        )
    assert res_shopify["in_stock"] is True
    assert res_shopify["price"] == 249.50
    assert res_shopify["currency"] == "USD"

    # 2. JSON-LD price & currency
    html_json_ld = """
    <html><head>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Product",
      "name": "EU Switch",
      "offers": {
        "@type": "Offer",
        "availability": "https://schema.org/InStock",
        "price": "389.99",
        "priceCurrency": "EUR"
      }
    }
    </script>
    </head><body></body></html>
    """
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_json_ld),
    ):
        res_ld = await StockChecker.detect_stock(
            url="https://example.com/eu-switch", strategy="json_ld"
        )
    assert res_ld["in_stock"] is True
    assert res_ld["price"] == 389.99
    assert res_ld["currency"] == "EUR"

    # 3. OpenGraph / Meta tags price
    html_meta = """
    <html><head>
      <meta property="og:availability" content="instock" />
      <meta property="product:price:amount" content="129.00" />
      <meta property="product:price:currency" content="GBP" />
    </head><body></body></html>
    """
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_meta),
    ):
        res_meta = await StockChecker.detect_stock(
            url="https://example.com/meta-item", strategy="auto"
        )
    assert res_meta["strategy_used"] == "meta_tags"
    assert res_meta["price"] == 129.0
    assert res_meta["currency"] == "GBP"

    # 4. Text heuristic price regex ($1,299.99) + ignores script noise
    html_text = """
    <html>
      <head><script>var dummy = "$9999.99";</script></head>
      <body>
        <span class="price">$1,299.99</span>
        <button>Add to Cart</button>
      </body>
    </html>
    """
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_text),
    ):
        res_text = await StockChecker.detect_stock(
            url="https://example.com/text-item", strategy="text_heuristic"
        )
    assert res_text["in_stock"] is True
    assert res_text["price"] == 1299.99
    assert res_text["currency"] == "USD"

    # 5. CSS selector + fallback meta price
    html_css = """
    <html>
      <head><meta property="og:price:amount" content="79.95" /></head>
      <body><div id="buy-box">Ready</div></body>
    </html>
    """
    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(return_value=html_css),
    ):
        res_css = await StockChecker.detect_stock(
            url="https://example.com/css-item",
            selector="#buy-box",
            strategy="css_selector",
        )
    assert res_css["in_stock"] is True
    assert res_css["price"] == 79.95


@pytest.mark.asyncio
async def test_telegram_and_webhook_dispatchers() -> None:
    """Verify send_telegram_notification and send_webhook_notification HTTP calls."""
    from unittest.mock import MagicMock

    from notifications import (
        build_webhook_payload,
        send_telegram_notification,
        send_webhook_notification,
    )

    # Validation failures without HTTP calls
    ok_empty, err_empty = await send_telegram_notification("", "", "hi")
    assert ok_empty is False
    assert err_empty is not None

    ok_bad_url, err_bad_url = await send_webhook_notification("not-a-url", {})
    assert ok_bad_url is False
    assert err_bad_url is not None

    captured_calls: list[dict] = []

    class FakeResponse:
        def __init__(self, status_code: int, text_body: str = "ok") -> None:
            self.status = status_code
            self._text = text_body

        async def text(self) -> str:
            return self._text

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def post(self, url, **kwargs):
            captured_calls.append({"url": url, "kwargs": kwargs})
            return FakeResponse(200)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch("notifications.aiohttp.ClientSession", new=FakeSession):
        tg_ok, tg_err = await send_telegram_notification(
            "12345:ABC-TOKEN", "-100999", "Alert message"
        )
        assert tg_ok is True
        assert tg_err is None
        assert (
            captured_calls[0]["url"]
            == "https://api.telegram.org/bot12345:ABC-TOKEN/sendMessage"
        )
        assert captured_calls[0]["kwargs"]["json"]["chat_id"] == "-100999"
        assert captured_calls[0]["kwargs"]["json"]["text"] == "Alert message"

        payload = build_webhook_payload(
            event="stock_alert",
            product="AP U7 Pro",
            url="https://example.com/u7-pro",
            status="in_stock",
            price=189.0,
            message="Back in stock!",
        )
        wh_ok, wh_err = await send_webhook_notification(
            "https://hooks.example.com/notify", payload
        )
        assert wh_ok is True
        assert wh_err is None
        assert captured_calls[1]["url"] == "https://hooks.example.com/notify"
        assert captured_calls[1]["kwargs"]["json"]["event"] == "stock_alert"
        assert captured_calls[1]["kwargs"]["json"]["price"] == 189.0

    # Non-2xx returns clean error
    mock_session_fail = MagicMock()
    mock_session_fail.return_value.__aenter__ = AsyncMock(
        return_value=MagicMock(
            post=MagicMock(return_value=FakeResponse(500, "Server Error"))
        )
    )
    mock_session_fail.return_value.__aexit__ = AsyncMock(return_value=False)
    with patch("notifications.aiohttp.ClientSession", new=mock_session_fail):
        fail_ok, fail_err = await send_webhook_notification(
            "https://hooks.example.com/fail", {"event": "test"}
        )
    assert fail_ok is False
    assert "HTTP 500" in (fail_err or "")


@pytest.mark.asyncio
async def test_stock_monitor_multi_channel_and_price_drop_alerts(
    temp_db: Database,
) -> None:
    """Verify multi-channel stock alerts, price history recording, and price drops."""
    product = temp_db.create_product(
        name="Switch Pro Max",
        url="https://example.com/switch-pro-max",
        selector="",
        strategy="auto",
    )
    temp_db.update_settings(
        {
            "notifications_enabled": "true",
            "discord_token": "disc-tok",
            "discord_channel_id": "111222",
            "telegram_bot_token": "tg-tok",
            "telegram_chat_id": "998877",
            "telegram_enabled": "true",
            "webhook_url": "https://hooks.example.com/events",
            "webhook_enabled": "true",
            "price_alerts_enabled": "true",
            "domain_rate_limit_delay": "0.0",
        }
    )
    monitor = StockMonitor(temp_db)

    # Check 1: Out of stock at $499.00 (no alerts, records price)
    html_oos_499 = (
        "<html><body><span>$499.00</span>"
        "<button disabled>Add to Cart</button></body></html>"
    )
    with (
        patch(
            "stock_checker.fetch_product_page",
            new=AsyncMock(return_value=html_oos_499),
        ),
        patch(
            "stock_checker.send_discord_rest_message",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_disc,
        patch(
            "stock_checker.send_telegram_notification",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_tg,
        patch(
            "stock_checker.send_webhook_notification",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_wh,
    ):
        res1 = await monitor._check_product(temp_db.get_product(product["id"]))

    assert res1["current_price"] == 499.0
    assert res1["previous_price"] is None
    mock_disc.assert_not_awaited()
    mock_tg.assert_not_awaited()
    mock_wh.assert_not_awaited()

    # Check 2: Back in stock AND price dropped to $399.00 -> sends both alerts!
    html_in_399 = (
        "<html><body><span>$399.00</span><button>Add to Cart</button></body></html>"
    )
    with (
        patch(
            "stock_checker.fetch_product_page",
            new=AsyncMock(return_value=html_in_399),
        ),
        patch(
            "stock_checker.send_discord_rest_message",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_disc2,
        patch(
            "stock_checker.send_telegram_notification",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_tg2,
        patch(
            "stock_checker.send_webhook_notification",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_wh2,
    ):
        res2 = await monitor._check_product(temp_db.get_product(product["id"]))

    assert res2["in_stock"] is True
    assert res2["current_price"] == 399.0
    assert res2["previous_price"] == 499.0
    assert mock_disc2.await_count == 2  # stock alert + price drop embed
    assert "embed" in mock_disc2.await_args_list[1].kwargs
    assert mock_tg2.await_count == 2
    assert mock_wh2.await_count == 2
    events = [c.args[1]["event"] for c in mock_wh2.await_args_list]
    assert events == ["stock_alert", "price_drop"]

    price_hist = temp_db.get_price_history("Switch Pro Max")
    assert [entry["price"] for entry in price_hist] == [399.0, 499.0]

    # Check 3: Price drops further to $349.00 with price_alerts_enabled=false
    temp_db.set_setting("price_alerts_enabled", "false")
    html_in_349 = (
        "<html><body><span>$349.00</span><button>Add to Cart</button></body></html>"
    )
    with (
        patch(
            "stock_checker.fetch_product_page",
            new=AsyncMock(return_value=html_in_349),
        ),
        patch(
            "stock_checker.send_discord_rest_message",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_disc3,
        patch(
            "stock_checker.send_telegram_notification",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_tg3,
        patch(
            "stock_checker.send_webhook_notification",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_wh3,
    ):
        res3 = await monitor._check_product(temp_db.get_product(product["id"]))

    assert res3["current_price"] == 349.0
    assert res3["previous_price"] == 399.0
    mock_disc3.assert_not_awaited()
    mock_tg3.assert_not_awaited()
    mock_wh3.assert_not_awaited()
    assert len(temp_db.get_price_history("Switch Pro Max")) == 3


@pytest.mark.asyncio
async def test_proxy_rotation_and_domain_rate_limiting(temp_db: Database) -> None:
    """Verify ProxyRotator round-robin and DomainRateLimiter per-domain throttling."""
    from stock_checker import DomainRateLimiter, ProxyRotator

    rotator = ProxyRotator()
    raw = "http://proxy1:8080, http://proxy2:8080\nhttp://proxy3:8080\n"
    assert rotator.parse_proxies(raw) == [
        "http://proxy1:8080",
        "http://proxy2:8080",
        "http://proxy3:8080",
    ]
    assert rotator.next_proxy(False, raw) is None
    assert rotator.next_proxy(True, "") is None
    seq = [rotator.next_proxy(True, raw) for _ in range(4)]
    assert seq == [
        "http://proxy1:8080",
        "http://proxy2:8080",
        "http://proxy3:8080",
        "http://proxy1:8080",
    ]

    # DomainRateLimiter sleeps when hitting same domain within delay
    limiter = DomainRateLimiter()
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    with patch("stock_checker.asyncio.sleep", side_effect=fake_sleep):
        slept1 = await limiter.wait_if_needed("https://store.ui.com/item1", delay=2.0)
        slept2 = await limiter.wait_if_needed("https://store.ui.com/item2", delay=2.0)
        slept3 = await limiter.wait_if_needed("https://other.com/item1", delay=2.0)

    assert slept1 == 0.0
    assert slept2 > 1.5
    assert slept3 == 0.0
    assert len(sleep_calls) == 1
    assert "store.ui.com" in limiter.last_domain_request
    assert "other.com" in limiter.last_domain_request

    # StockMonitor passes rotated proxies to StockChecker.check_product
    temp_db.create_product("Item A", "https://alpha.example.com/a")
    temp_db.create_product("Item B", "https://beta.example.com/b")
    temp_db.update_settings(
        {
            "proxy_enabled": "true",
            "proxy_list": "http://p1:3128\nhttp://p2:3128",
            "domain_rate_limit_delay": "0.0",
        }
    )
    monitor = StockMonitor(temp_db)
    seen_proxies: list[str] = []

    async def fake_check(product, proxy=None):
        seen_proxies.append(proxy)
        return {
            "in_stock": False,
            "found_selector": False,
            "strategy_used": "auto",
            "detected_by": "test",
            "error": None,
            "details": "ok",
            "price": 10.0,
            "currency": "USD",
        }

    with patch("stock_checker.StockChecker.check_product", side_effect=fake_check):
        await monitor.check_all()

    assert seen_proxies == ["http://p1:3128", "http://p2:3128"]


def test_api_new_settings_test_endpoints_and_price_history(
    client: TestClient,
) -> None:
    """Verify new settings persistence, test-telegram, test-webhook, price-history."""
    # Update new settings
    res = client.post(
        "/api/settings",
        json={
            "telegram_bot_token": "999888:SecretTelegramBotToken",
            "telegram_chat_id": "-100123456",
            "telegram_enabled": True,
            "webhook_url": "https://hooks.example.com/ubstock",
            "webhook_enabled": True,
            "price_alerts_enabled": False,
            "proxy_list": "http://p1:8080\nhttp://p2:8080",
            "proxy_enabled": True,
            "domain_rate_limit_delay": 2.5,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["telegram_bot_token_set"] is True
    assert body["telegram_bot_token_masked"] == mask_token(
        "999888:SecretTelegramBotToken"
    )
    assert "SecretTelegramBotToken" not in body["telegram_bot_token_masked"]
    assert body["telegram_chat_id"] == "-100123456"
    assert body["telegram_enabled"] is True
    assert body["webhook_url"] == "https://hooks.example.com/ubstock"
    assert body["webhook_enabled"] is True
    assert body["price_alerts_enabled"] is False
    assert body["proxy_list"] == "http://p1:8080\nhttp://p2:8080"
    assert body["proxy_enabled"] is True
    assert body["domain_rate_limit_delay"] == 2.5

    # Invalid webhook URL rejected
    bad_wh = client.post(
        "/api/settings", json={"webhook_url": "ftp://invalid.example.com"}
    )
    assert bad_wh.status_code == 422

    # Masked telegram token preserved on partial update
    partial = client.post(
        "/api/settings",
        json={
            "telegram_bot_token": body["telegram_bot_token_masked"],
            "domain_rate_limit_delay": 1.5,
        },
    )
    assert partial.status_code == 200
    assert partial.json()["domain_rate_limit_delay"] == 1.5

    # Test Telegram with saved credentials
    with patch(
        "api.send_telegram_notification",
        new=AsyncMock(return_value=(True, None)),
    ) as mock_tg:
        tg_res = client.post("/api/settings/test-telegram")
    assert tg_res.status_code == 200
    assert tg_res.json() == {"success": True, "error": None}
    mock_tg.assert_awaited_once()
    assert mock_tg.await_args.args[0] == "999888:SecretTelegramBotToken"
    assert mock_tg.await_args.args[1] == "-100123456"

    # Test Webhook with saved URL and override URL
    with patch(
        "api.send_webhook_notification",
        new=AsyncMock(return_value=(True, None)),
    ) as mock_wh:
        wh_res = client.post("/api/settings/test-webhook")
    assert wh_res.status_code == 200
    assert wh_res.json() == {"success": True, "error": None}
    mock_wh.assert_awaited_once()
    assert mock_wh.await_args.args[0] == "https://hooks.example.com/ubstock"
    assert mock_wh.await_args.args[1]["event"] == "test"

    # Price history endpoint: 404 for unknown product
    missing_hist = client.get("/api/products/DoesNotExist/price-history")
    assert missing_hist.status_code == 404

    # Create product, run two checks with different prices, verify response & history
    created = client.post(
        "/api/products",
        json={
            "name": "G5 Dome",
            "url": "https://example.com/g5-dome",
            "strategy": "auto",
        },
    ).json()
    assert created["current_price"] is None
    assert created["currency"] == "USD"
    assert created["previous_price"] is None

    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(
            return_value="<html><span>$179.00</span><button>Add to Cart</button></html>"
        ),
    ):
        chk1 = client.post(f"/api/products/{created['id']}/check")
    assert chk1.status_code == 200
    assert chk1.json()["current_price"] == 179.0
    assert chk1.json()["previous_price"] is None

    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(
            return_value="<html><span>$149.00</span><button>Add to Cart</button></html>"
        ),
    ):
        chk2 = client.post(f"/api/products/{created['id']}/check")
    assert chk2.status_code == 200
    assert chk2.json()["current_price"] == 149.0
    assert chk2.json()["previous_price"] == 179.0

    hist_res = client.get("/api/products/G5%20Dome/price-history?limit=10")
    assert hist_res.status_code == 200
    hist_items = hist_res.json()
    assert len(hist_items) == 2
    assert hist_items[0]["price"] == 149.0
    assert hist_items[1]["price"] == 179.0
    assert hist_items[0]["currency"] == "USD"


# ----------------------------------------------------------------------
# QA Edge-Case, Boundary & Cross-Tier Contract Tests
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_channel_notification_edge_cases_and_failures(
    temp_db: Database,
) -> None:
    """Verify partial channel failures, global toggle, price increases, formatting."""
    from unittest.mock import MagicMock

    from notifications import (
        format_price,
        send_discord_rest_message,
        send_telegram_notification,
        send_webhook_notification,
    )

    assert format_price(None) == "N/A"
    assert format_price(1234.5, "usd") == "$1,234.50"
    assert format_price(99.9, "EUR") == "€99.90"
    assert format_price(50, "GBP") == "£50.00"
    assert format_price(500, "CHF") == "CHF 500.00"

    # Network exceptions in notification dispatchers return clean (False, err) tuples
    broken_session = MagicMock()
    broken_session.return_value.__aenter__ = AsyncMock(
        side_effect=RuntimeError("DNS failure")
    )
    broken_session.return_value.__aexit__ = AsyncMock(return_value=False)
    with patch("notifications.aiohttp.ClientSession", new=broken_session):
        d_ok, d_err = await send_discord_rest_message("tok", "123", "hi")
        t_ok, t_err = await send_telegram_notification("tok", "123", "hi")
        w_ok, w_err = await send_webhook_notification(
            "https://hooks.example.com/test", {"event": "test"}
        )
    assert d_ok is False and "DNS failure" in (d_err or "")
    assert t_ok is False and "DNS failure" in (t_err or "")
    assert w_ok is False and "DNS failure" in (w_err or "")

    # Setup product and all 3 channels; Discord fails while Telegram & Webhook succeed
    prod = temp_db.create_product("U7 Pro Wall", "https://example.com/u7-wall")
    temp_db.update_settings(
        {
            "notifications_enabled": "true",
            "discord_token": "disc-tok",
            "discord_channel_id": "111222",
            "telegram_bot_token": "tg-tok",
            "telegram_chat_id": "998877",
            "telegram_enabled": "true",
            "webhook_url": "https://hooks.example.com/events",
            "webhook_enabled": "true",
            "price_alerts_enabled": "true",
            "domain_rate_limit_delay": "0.0",
        }
    )
    monitor = StockMonitor(temp_db)

    # First check: In stock at $199.00 -> Discord fails, Telegram & Webhook succeed
    html_199 = (
        "<html><body><span>$199.00</span><button>Add to Cart</button></body></html>"
    )
    with (
        patch(
            "stock_checker.fetch_product_page",
            new=AsyncMock(return_value=html_199),
        ),
        patch(
            "stock_checker.send_discord_rest_message",
            new=AsyncMock(return_value=(False, "Discord 401")),
        ),
        patch(
            "stock_checker.send_telegram_notification",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_tg,
        patch(
            "stock_checker.send_webhook_notification",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_wh,
    ):
        await monitor._check_product(temp_db.get_product(prod["id"]))

    mock_tg.assert_awaited_once()
    mock_wh.assert_awaited_once()
    hist = temp_db.get_history()
    assert hist[0]["status"] == "in_stock"
    assert hist[0]["notification_sent"] is True
    assert "Discord: Discord 401" in hist[0]["message"]

    # Price increase ($199 -> $249) must NOT trigger a price drop alert
    html_249 = (
        "<html><body><span>$249.00</span><button>Add to Cart</button></body></html>"
    )
    with (
        patch(
            "stock_checker.fetch_product_page",
            new=AsyncMock(return_value=html_249),
        ),
        patch(
            "stock_checker.send_discord_rest_message",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_disc_up,
        patch(
            "stock_checker.send_telegram_notification",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_tg_up,
        patch(
            "stock_checker.send_webhook_notification",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_wh_up,
    ):
        up_res = await monitor._check_product(temp_db.get_product(prod["id"]))

    assert up_res["current_price"] == 249.0
    assert up_res["previous_price"] == 199.0
    mock_disc_up.assert_not_awaited()
    mock_tg_up.assert_not_awaited()
    mock_wh_up.assert_not_awaited()

    # Global notifications_enabled=false suppresses price drop alerts even if
    # price_alerts_enabled=true
    temp_db.set_setting("notifications_enabled", "false")
    html_179 = (
        "<html><body><span>$179.00</span><button>Add to Cart</button></body></html>"
    )
    with (
        patch(
            "stock_checker.fetch_product_page",
            new=AsyncMock(return_value=html_179),
        ),
        patch(
            "stock_checker.send_telegram_notification",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_tg_muted,
        patch(
            "stock_checker.send_webhook_notification",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_wh_muted,
    ):
        muted_res = await monitor._check_product(temp_db.get_product(prod["id"]))

    assert muted_res["current_price"] == 179.0
    assert muted_res["previous_price"] == 249.0
    mock_tg_muted.assert_not_awaited()
    mock_wh_muted.assert_not_awaited()


def test_api_test_endpoints_masked_tokens_and_validation_edges(
    client: TestClient,
) -> None:
    """Verify test-telegram/webhook edge cases, token masking, and delay bounds."""
    # Missing credentials fail cleanly with success=False
    empty_tg = client.post("/api/settings/test-telegram", json={})
    assert empty_tg.status_code == 200
    assert empty_tg.json()["success"] is False
    assert "required" in (empty_tg.json()["error"] or "").lower()

    empty_wh = client.post("/api/settings/test-webhook", json={})
    assert empty_wh.status_code == 200
    assert empty_wh.json()["success"] is False

    bad_wh_test = client.post(
        "/api/settings/test-webhook", json={"webhook_url": "ftp://bad.example.com"}
    )
    assert bad_wh_test.status_code == 200
    assert bad_wh_test.json()["success"] is False
    assert "http" in (bad_wh_test.json()["error"] or "").lower()

    # Invalid URLs and domain rate limit extremes in POST /api/settings
    assert (
        client.post(
            "/api/settings", json={"webhook_url": "javascript:alert(1)"}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/settings", json={"domain_rate_limit_delay": -0.1}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/settings", json={"domain_rate_limit_delay": 300.1}
        ).status_code
        == 422
    )

    # Boundary min (0.0) and max (300.0) succeed
    min_res = client.post("/api/settings", json={"domain_rate_limit_delay": 0.0})
    assert min_res.status_code == 200
    assert min_res.json()["domain_rate_limit_delay"] == 0.0

    max_res = client.post("/api/settings", json={"domain_rate_limit_delay": 300.0})
    assert max_res.status_code == 200
    assert max_res.json()["domain_rate_limit_delay"] == 300.0

    # Save real tokens, verify masked tokens are NOT overwritten by masked values
    save_res = client.post(
        "/api/settings",
        json={
            "discord_token": "DiscordRealSecretToken123",
            "discord_channel_id": "555666",
            "telegram_bot_token": "TelegramRealSecretToken456",
            "telegram_chat_id": "-100777",
            "webhook_url": "https://hooks.example.com/saved",
        },
    )
    assert save_res.status_code == 200
    saved = save_res.json()

    # Re-posting masked tokens keeps real secrets intact
    repost = client.post(
        "/api/settings",
        json={
            "discord_token": saved["discord_token_masked"],
            "telegram_bot_token": saved["telegram_bot_token_masked"],
            "proxy_enabled": True,
            "proxy_list": "",
        },
    )
    assert repost.status_code == 200
    assert repost.json()["discord_token_set"] is True
    assert repost.json()["telegram_bot_token_set"] is True

    # Sending masked token to test-telegram still uses the saved unmasked secret
    with patch(
        "api.send_telegram_notification",
        new=AsyncMock(return_value=(True, None)),
    ) as mock_tg:
        tg_masked = client.post(
            "/api/settings/test-telegram",
            json={
                "telegram_bot_token": saved["telegram_bot_token_masked"],
                "telegram_chat_id": "-100777",
            },
        )
    assert tg_masked.status_code == 200
    assert tg_masked.json()["success"] is True
    assert mock_tg.await_args.args[0] == "TelegramRealSecretToken456"

    # Override credentials in test-telegram and test-webhook take precedence
    with patch(
        "api.send_telegram_notification",
        new=AsyncMock(return_value=(False, "Unauthorized")),
    ) as mock_tg_ov:
        tg_ov = client.post(
            "/api/settings/test-telegram",
            json={
                "telegram_bot_token": "OverrideToken",
                "telegram_chat_id": "12345",
            },
        )
    assert tg_ov.json() == {"success": False, "error": "Unauthorized"}
    assert mock_tg_ov.await_args.args == (
        "OverrideToken",
        "12345",
        "✅ Stock Checker test notification! Your Telegram alerts are "
        "configured properly.",
    )

    with patch(
        "api.send_webhook_notification",
        new=AsyncMock(return_value=(True, None)),
    ) as mock_wh_ov:
        wh_ov = client.post(
            "/api/settings/test-webhook",
            json={"webhook_url": "https://override.example.com/hook"},
        )
    assert wh_ov.json() == {"success": True, "error": None}
    assert mock_wh_ov.await_args.args[0] == "https://override.example.com/hook"

    # Clearing webhook_url and telegram_bot_token with empty strings works
    cleared = client.post(
        "/api/settings",
        json={"webhook_url": "", "telegram_bot_token": ""},
    )
    assert cleared.status_code == 200
    assert cleared.json()["webhook_url"] == ""
    assert cleared.json()["telegram_bot_token_set"] is False


@pytest.mark.asyncio
async def test_proxy_and_domain_rate_limit_edge_cases(temp_db: Database) -> None:
    """Verify empty proxy lists, proxy passthrough, and domain rate limit edges."""
    from unittest.mock import MagicMock

    from stock_checker import DomainRateLimiter, ProxyRotator, fetch_shopify_json
    from utils import fetch_product_page

    rotator = ProxyRotator()
    assert rotator.next_proxy(True, "   \n , \r\n  ") is None
    assert rotator.next_proxy(True, "http://p1:8080") == "http://p1:8080"
    # Updating list resets round-robin index cleanly
    assert rotator.next_proxy(True, "http://p2:8080\nhttp://p3:8080") == (
        "http://p2:8080"
    )
    assert rotator.next_proxy(True, "http://p2:8080\nhttp://p3:8080") == (
        "http://p3:8080"
    )

    # Empty proxy list with proxy_enabled=True passes proxy=None
    temp_db.update_settings(
        {
            "proxy_enabled": "true",
            "proxy_list": " , \n ",
            "domain_rate_limit_delay": "invalid-float",
        }
    )
    monitor = StockMonitor(temp_db)
    assert monitor.get_next_proxy() is None
    assert monitor.get_domain_rate_limit_delay() == 1.0

    temp_db.set_setting("domain_rate_limit_delay", "-5.0")
    assert monitor.get_domain_rate_limit_delay() == 0.0

    # DomainRateLimiter edge cases: empty URL, delay=0, case-insensitive netloc
    limiter = DomainRateLimiter()
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    with patch("stock_checker.asyncio.sleep", side_effect=fake_sleep):
        assert await limiter.wait_if_needed("not-a-valid-url", delay=5.0) == 0.0
        assert await limiter.wait_if_needed("https://Example.COM/a", delay=0.0) == 0.0
        assert await limiter.wait_if_needed("https://Example.COM/a", delay=-2.0) == 0.0
        # First valid delayed call records timestamp without sleeping
        assert await limiter.wait_if_needed("https://Store.UI.com/p1", delay=2.0) == 0.0
        # Case-insensitive match on same domain triggers sleep
        slept = await limiter.wait_if_needed("http://store.ui.com/p2", delay=2.0)
    assert slept > 1.5
    assert len(sleep_calls) == 1

    # Verify fetch_product_page & fetch_shopify_json forward proxy argument to aiohttp
    get_kwargs: list[dict] = []

    class FakeResp:
        status = 200

        async def text(self) -> str:
            return "<html>ok</html>"

        async def json(self, content_type=None):
            return {"product": {"variants": []}}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeClientSession:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get(self, url, **kwargs):
            get_kwargs.append({"url": url, "kwargs": kwargs})
            return FakeResp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with (
        patch("utils.aiohttp.ClientSession", new=FakeClientSession),
        patch("stock_checker.aiohttp.ClientSession", new=FakeClientSession),
    ):
        html = await fetch_product_page(
            "https://example.com/item", proxy="http://proxy1:8080"
        )
        payload, err = await fetch_shopify_json(
            "https://shop.example.com/products/widget", proxy="http://proxy2:8080"
        )
    assert html == "<html>ok</html>"
    assert err is None and isinstance(payload, dict)
    assert get_kwargs[0]["kwargs"].get("proxy") == "http://proxy1:8080"
    assert get_kwargs[1]["kwargs"].get("proxy") == "http://proxy2:8080"
    # Confirm MagicMock import unused lint doesn't trigger
    assert MagicMock is not None


@pytest.mark.asyncio
async def test_price_extraction_and_history_edge_cases(
    temp_db: Database, client: TestClient
) -> None:
    """Verify price parsing boundaries, error preservation, and history limits."""
    from stock_checker import _extract_price_from_text, _parse_price_value

    assert _parse_price_value(None) is None
    assert _parse_price_value(True) is None
    assert _parse_price_value(-15.0) is None
    assert _parse_price_value(float("nan")) is None
    assert _parse_price_value(float("inf")) is None
    assert _parse_price_value("") is None
    assert _parse_price_value("389,99") == 389.99
    assert _parse_price_value("$1,299.50") == 1299.50
    assert _parse_price_value("0.00") == 0.0
    assert _extract_price_from_text("No price here") == (None, "USD")
    assert _extract_price_from_text("Only £49 today") == (49.0, "GBP")

    # JSON-LD AggregateOffer lowPrice + @graph
    graph_html = """
    <html><head>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@graph": [
          {
            "@type": "Product",
            "offers": {
              "@type": "AggregateOffer",
              "availability": "https://schema.org/InStock",
              "lowPrice": "219.00",
              "priceCurrency": "CAD"
            }
          }
        ]
      }
      </script>
    </head><body></body></html>
    """
    with patch(
        "stock_checker.fetch_product_page", new=AsyncMock(return_value=graph_html)
    ):
        res_graph = await StockChecker.detect_stock(
            "https://example.com/graph", strategy="json_ld"
        )
    assert res_graph["in_stock"] is True
    assert res_graph["price"] == 219.0
    assert res_graph["currency"] == "CAD"

    # Error during check must NOT overwrite price or record price_history
    created = client.post(
        "/api/products",
        json={"name": "Cloud Gateway", "url": "https://example.com/ucg"},
    ).json()
    pid = created["id"]

    # Initially empty price history returns 200 []
    empty_hist = client.get("/api/products/Cloud%20Gateway/price-history")
    assert empty_hist.status_code == 200
    assert empty_hist.json() == []

    # Invalid limit bounds rejected with 422
    assert (
        client.get("/api/products/Cloud%20Gateway/price-history?limit=0").status_code
        == 422
    )
    assert (
        client.get("/api/products/Cloud%20Gateway/price-history?limit=501").status_code
        == 422
    )

    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(
            return_value="<html><span>$129.00</span><button>Add to Cart</button></html>"
        ),
    ):
        ok_chk = client.post(f"/api/products/{pid}/check")
    assert ok_chk.status_code == 200
    assert ok_chk.json()["current_price"] == 129.0

    with patch(
        "stock_checker.fetch_product_page",
        new=AsyncMock(side_effect=Exception("Connection reset")),
    ):
        err_chk = client.post(f"/api/products/{pid}/check")
    assert err_chk.status_code == 200
    assert err_chk.json()["last_error"] == "Connection reset"
    assert err_chk.json()["current_price"] == 129.0
    assert len(client.get("/api/products/Cloud%20Gateway/price-history").json()) == 1

    # Renaming product preserves price history under new name
    upd = client.put(f"/api/products/{pid}", json={"name": "Cloud Gateway Max"})
    assert upd.status_code == 200
    assert client.get("/api/products/Cloud%20Gateway/price-history").status_code == 404
    renamed_hist = client.get("/api/products/Cloud%20Gateway%20Max/price-history")
    assert renamed_hist.status_code == 200
    assert len(renamed_hist.json()) == 1


def test_cross_tier_frontend_contracts_for_new_features(client: TestClient) -> None:
    """Verify static frontend contains all new UI controls, modals, and API routes."""
    root = Path(__file__).resolve().parent
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    js = (root / "static" / "app.js").read_text(encoding="utf-8")

    # Settings UI elements
    for elem_id in (
        "setting-telegram-enabled",
        "setting-telegram-token",
        "setting-telegram-chat-id",
        "btn-test-telegram",
        "test-telegram-feedback",
        "setting-webhook-enabled",
        "setting-webhook-url",
        "btn-test-webhook",
        "test-webhook-feedback",
        "setting-price-alerts-enabled",
        "setting-proxy-enabled",
        "setting-proxy-list",
        "setting-domain-rate-limit-delay",
        "price-history-modal",
        "price-history-tbody",
        "price-history-empty",
        "price-history-current-price",
        "price-history-change-stat",
        "price-history-count-stat",
    ):
        assert f'id="{elem_id}"' in html

    # JS API calls & payload fields
    for fragment in (
        "/api/settings/test-telegram",
        "/api/settings/test-webhook",
        "/price-history",
        "telegram_bot_token",
        "telegram_chat_id",
        "telegram_enabled",
        "webhook_url",
        "webhook_enabled",
        "price_alerts_enabled",
        "proxy_list",
        "proxy_enabled",
        "domain_rate_limit_delay",
        "current_price",
        "previous_price",
    ):
        assert fragment in js
