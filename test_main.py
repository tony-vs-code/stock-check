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
        "<!DOCTYPE html><html><body><h1>UB Stock Monitor</h1></body></html>",
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
    assert "UB Stock Monitor" in root.text


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
    assert res.json() == {"in_stock": True, "found_selector": True, "error": None}
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
    """Verify FastAPI serves real UI assets at /, /styles.css, and /app.js."""
    app = create_app(
        db=temp_db,
        static_dir="static",
        start_background_tasks=False,
    )
    with TestClient(app) as real_client:
        index_res = real_client.get("/")
        assert index_res.status_code == 200
        assert "text/html" in index_res.headers["content-type"]
        assert "UBStock" in index_res.text
        assert "/styles.css" in index_res.text
        assert "/app.js" in index_res.text

        css_res = real_client.get("/styles.css")
        assert css_res.status_code == 200
        assert "text/css" in css_res.headers["content-type"]
        assert "--bg-base" in css_res.text

        js_res = real_client.get("/app.js")
        assert js_res.status_code == 200
        assert "javascript" in js_res.headers["content-type"]
        assert "/api/products" in js_res.text
        assert "/api/settings" in js_res.text


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

    # Blank selector defaults to DEFAULT_SELECTOR
    default_sel = client.post(
        "/api/products",
        json={
            "name": "Product One",
            "url": "https://example.com/one",
            "selector": "   ",
        },
    )
    assert default_sel.status_code == 201
    assert default_sel.json()["selector"] == "button[label='Add to Cart']"

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
    assert miss.json() == {"in_stock": False, "found_selector": False, "error": None}

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
