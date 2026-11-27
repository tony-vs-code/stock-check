"""FastAPI backend and REST API endpoints for UB Stock Bot."""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException, Query, Request, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from config import APP_VERSION, DEFAULT_SELECTOR, STATIC_DIR
from database import Database, DuplicateProductError
from discord_client import DiscordManager
from log_manager import LogManager
from stock_checker import (
    VALID_STRATEGIES,
    StockChecker,
    StockMonitor,
    send_discord_rest_message,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Pydantic schemas
# ----------------------------------------------------------------------


def _validate_http_url(value: str) -> str:
    cleaned = value.strip()
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        raise ValueError("URL must start with http:// or https://")
    return cleaned


class HealthResponse(BaseModel):
    status: str
    version: str


class StatusResponse(BaseModel):
    total_products: int
    in_stock_count: int
    out_of_stock_count: int
    discord_configured: bool
    discord_connected: bool
    notifications_enabled: bool
    check_interval: int
    last_check: Optional[str] = None
    is_checking: bool


def _validate_strategy_value(value: str) -> str:
    """Normalize and validate a stock detection strategy string."""
    cleaned = value.strip().lower()
    if cleaned not in VALID_STRATEGIES:
        allowed = ", ".join(VALID_STRATEGIES)
        raise ValueError(f"Unsupported strategy '{value}'. Must be one of: {allowed}.")
    return cleaned


class ProductResponse(BaseModel):
    id: int
    name: str
    url: str
    selector: str
    strategy: str = "auto"
    strategy_details: Optional[str] = None
    in_stock: bool
    last_checked: Optional[str] = None
    last_status_change: Optional[str] = None
    last_error: Optional[str] = None
    created_at: Optional[str] = None


class ProductCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    url: str = Field(..., min_length=1, max_length=2048)
    selector: Optional[str] = Field(default=None, max_length=500)
    strategy: str = Field(default="auto", max_length=50)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Product name cannot be empty.")
        return cleaned

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return _validate_http_url(v)

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        return _validate_strategy_value(v)

    @model_validator(mode="after")
    def normalize_selector(self) -> "ProductCreateRequest":
        cleaned_selector = (self.selector or "").strip()
        if self.strategy == "css_selector":
            cleaned_selector = cleaned_selector or DEFAULT_SELECTOR
        if cleaned_selector:
            err = StockChecker.validate_selector(cleaned_selector)
            if err:
                raise ValueError(err)
        self.selector = cleaned_selector
        return self


class ProductUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    url: Optional[str] = Field(default=None, min_length=1, max_length=2048)
    selector: Optional[str] = Field(default=None, max_length=500)
    strategy: Optional[str] = Field(default=None, max_length=50)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Product name cannot be empty.")
        return cleaned

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return _validate_http_url(v)

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return _validate_strategy_value(v)

    @field_validator("selector")
    @classmethod
    def validate_selector(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if cleaned:
            err = StockChecker.validate_selector(cleaned)
            if err:
                raise ValueError(err)
        return cleaned


class TestSelectorRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    selector: Optional[str] = Field(default=None, max_length=500)
    strategy: Optional[str] = Field(default="auto", max_length=50)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return _validate_http_url(v)


class TestSelectorResponse(BaseModel):
    in_stock: bool
    found_selector: bool
    strategy_used: str
    detected_by: str
    error: Optional[str] = None
    details: Optional[str] = None


class CheckAllResponse(BaseModel):
    checked: int
    in_stock_count: int
    out_of_stock_count: int
    products: list[ProductResponse]


class HistoryEntryResponse(BaseModel):
    id: int
    product_id: Optional[int] = None
    product_name: str
    status: str
    notification_sent: bool
    message: Optional[str] = None
    created_at: Optional[str] = None


class SettingsResponse(BaseModel):
    discord_token_set: bool
    discord_token_masked: str
    discord_channel_id: str
    check_stock_interval: int
    notifications_enabled: bool


class SettingsUpdateRequest(BaseModel):
    discord_token: Optional[str] = None
    discord_channel_id: Optional[str] = None
    check_stock_interval: Optional[int] = Field(default=None, ge=1, le=86400)
    notifications_enabled: Optional[bool] = None


class TestDiscordRequest(BaseModel):
    discord_token: Optional[str] = None
    discord_channel_id: Optional[str] = None


class TestDiscordResponse(BaseModel):
    success: bool
    error: Optional[str] = None


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def mask_token(token: str) -> str:
    """Return a masked representation of a Discord bot token."""
    cleaned = token.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= 8:
        return "••••" + cleaned[-2:]
    return f"{cleaned[:4]}...{cleaned[-4:]}"


def _build_settings_response(db: Database) -> SettingsResponse:
    token = (db.get_setting("discord_token", "") or "").strip()
    channel_id = (db.get_setting("discord_channel_id", "") or "").strip()
    raw_interval = db.get_setting("check_stock_interval", "15") or "15"
    try:
        interval = max(1, int(raw_interval))
    except ValueError:
        interval = 15
    raw_enabled = (db.get_setting("notifications_enabled", "true") or "true").lower()
    notifications_enabled = raw_enabled in ("true", "1", "yes", "on")

    return SettingsResponse(
        discord_token_set=bool(token),
        discord_token_masked=mask_token(token),
        discord_channel_id=channel_id,
        check_stock_interval=interval,
        notifications_enabled=notifications_enabled,
    )


# ----------------------------------------------------------------------
# Application factory
# ----------------------------------------------------------------------


def create_app(
    db: Optional[Database] = None,
    monitor: Optional[StockMonitor] = None,
    discord_manager: Optional[DiscordManager] = None,
    static_dir: Optional[str] = None,
    start_background_tasks: bool = True,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        LogManager.setup_logging()
        active_db = db or Database()
        active_discord = discord_manager or DiscordManager(active_db)
        active_monitor = monitor or StockMonitor(
            active_db, discord_manager=active_discord
        )

        application.state.db = active_db
        application.state.discord_manager = active_discord
        application.state.monitor = active_monitor
        application.state.start_background_tasks = start_background_tasks

        if start_background_tasks:
            await active_discord.start()
            active_monitor.start()
        else:
            if not active_discord.is_configured():
                logger.info(
                    "Discord notifications unconfigured. You can configure them "
                    "anytime in the Web UI or via API."
                )

        try:
            yield
        finally:
            if start_background_tasks:
                await active_monitor.stop()
                await active_discord.stop()

    application = FastAPI(
        title="UB Stock Monitor",
        version=APP_VERSION,
        description="Web UI and API for monitoring product stock availability.",
        lifespan=lifespan,
    )

    def _get_db(request: Request) -> Database:
        return request.app.state.db

    def _get_monitor(request: Request) -> StockMonitor:
        return request.app.state.monitor

    def _get_discord(request: Request) -> DiscordManager:
        return request.app.state.discord_manager

    # ------------------------------------------------------------------
    # Health & Status
    # ------------------------------------------------------------------

    @application.get("/api/health", response_model=HealthResponse)
    async def healthcheck() -> HealthResponse:
        """Container/load-balancer healthcheck endpoint."""
        return HealthResponse(status="ok", version=APP_VERSION)

    @application.get("/api/status", response_model=StatusResponse)
    async def get_status(request: Request) -> StatusResponse:
        """Return aggregate monitoring and Discord status."""
        active_db = _get_db(request)
        active_monitor = _get_monitor(request)
        active_discord = _get_discord(request)

        products = active_db.list_products()
        in_stock = sum(1 for p in products if p["in_stock"])
        total = len(products)

        return StatusResponse(
            total_products=total,
            in_stock_count=in_stock,
            out_of_stock_count=total - in_stock,
            discord_configured=active_discord.is_configured(),
            discord_connected=active_discord.is_connected(),
            notifications_enabled=active_monitor.notifications_enabled(),
            check_interval=active_monitor.get_check_interval(),
            last_check=active_monitor.last_check,
            is_checking=active_monitor.is_checking,
        )

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    @application.get("/api/products", response_model=list[ProductResponse])
    async def list_products(request: Request) -> list[dict[str, Any]]:
        """List all monitored products."""
        return _get_db(request).list_products()

    @application.post(
        "/api/products",
        response_model=ProductResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_product(
        payload: ProductCreateRequest, request: Request
    ) -> dict[str, Any]:
        """Create a new monitored product."""
        active_db = _get_db(request)
        try:
            return active_db.create_product(
                name=payload.name,
                url=payload.url,
                selector=payload.selector or "",
                strategy=payload.strategy,
            )
        except DuplicateProductError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc

    @application.post(
        "/api/products/test-selector", response_model=TestSelectorResponse
    )
    async def test_selector(payload: TestSelectorRequest) -> dict[str, Any]:
        """Test a URL and detection strategy without saving to the database."""
        return await StockChecker.test_selector(
            url=payload.url,
            selector=payload.selector,
            strategy=payload.strategy or "auto",
        )

    @application.put("/api/products/{product_id}", response_model=ProductResponse)
    async def update_product(
        product_id: int, payload: ProductUpdateRequest, request: Request
    ) -> dict[str, Any]:
        """Update an existing monitored product."""
        active_db = _get_db(request)
        existing = active_db.get_product(product_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with id {product_id} not found.",
            )

        next_strategy = (
            payload.strategy if payload.strategy is not None else existing["strategy"]
        )
        next_selector = payload.selector
        if next_strategy == "css_selector":
            candidate_selector = (
                next_selector if next_selector is not None else existing["selector"]
            )
            if not (candidate_selector or "").strip():
                next_selector = DEFAULT_SELECTOR

        try:
            updated = active_db.update_product(
                product_id=product_id,
                name=payload.name,
                url=payload.url,
                selector=next_selector,
                strategy=payload.strategy,
            )
        except DuplicateProductError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc

        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with id {product_id} not found.",
            )
        return updated

    @application.delete("/api/products/{product_id}")
    async def delete_product(product_id: int, request: Request) -> dict[str, Any]:
        """Delete a monitored product by ID."""
        deleted = _get_db(request).delete_product(product_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with id {product_id} not found.",
            )
        return {"status": "deleted", "id": product_id}

    @application.post(
        "/api/products/{product_id}/check", response_model=ProductResponse
    )
    async def check_single_product(product_id: int, request: Request) -> dict[str, Any]:
        """Trigger an immediate stock check for a single product."""
        updated = await _get_monitor(request).check_product_by_id(product_id)
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with id {product_id} not found.",
            )
        return updated

    @application.post("/api/check-all", response_model=CheckAllResponse)
    async def check_all_products(request: Request) -> dict[str, Any]:
        """Trigger an immediate stock check for all monitored products."""
        products = await _get_monitor(request).check_all(record_unchanged=True)
        in_stock = sum(1 for p in products if p["in_stock"])
        return {
            "checked": len(products),
            "in_stock_count": in_stock,
            "out_of_stock_count": len(products) - in_stock,
            "products": products,
        }

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    @application.get("/api/history", response_model=list[HistoryEntryResponse])
    async def get_history(
        request: Request,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        """List recent stock check and notification events."""
        return _get_db(request).get_history(limit=limit)

    @application.delete("/api/history")
    async def clear_history(request: Request) -> dict[str, Any]:
        """Clear all history records."""
        deleted = _get_db(request).clear_history()
        return {"status": "cleared", "deleted": deleted}

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    @application.get("/api/settings", response_model=SettingsResponse)
    async def get_settings(request: Request) -> SettingsResponse:
        """Return application settings with Discord token masked."""
        return _build_settings_response(_get_db(request))

    @application.post("/api/settings", response_model=SettingsResponse)
    async def update_settings(
        payload: SettingsUpdateRequest, request: Request
    ) -> SettingsResponse:
        """Update application settings in SQLite."""
        active_db = _get_db(request)
        active_monitor = _get_monitor(request)
        active_discord = _get_discord(request)

        updates: dict[str, str] = {}
        discord_changed = False

        if payload.discord_token is not None:
            existing_token = (active_db.get_setting("discord_token", "") or "").strip()
            cleaned_token = payload.discord_token.strip()
            if cleaned_token != mask_token(existing_token):
                updates["discord_token"] = cleaned_token
                discord_changed = True

        if payload.discord_channel_id is not None:
            updates["discord_channel_id"] = payload.discord_channel_id.strip()
            discord_changed = True

        if payload.check_stock_interval is not None:
            updates["check_stock_interval"] = str(payload.check_stock_interval)

        if payload.notifications_enabled is not None:
            updates["notifications_enabled"] = (
                "true" if payload.notifications_enabled else "false"
            )

        active_db.update_settings(updates)
        active_monitor.notify_settings_changed()

        if discord_changed and request.app.state.start_background_tasks:
            await active_discord.restart()

        return _build_settings_response(active_db)

    @application.post("/api/settings/test-discord", response_model=TestDiscordResponse)
    async def test_discord(
        request: Request,
        payload: Optional[TestDiscordRequest] = Body(default=None),
    ) -> TestDiscordResponse:
        """Send a test Discord notification using provided or saved credentials."""
        active_db = _get_db(request)
        saved_token = (active_db.get_setting("discord_token", "") or "").strip()
        saved_channel = (active_db.get_setting("discord_channel_id", "") or "").strip()

        token = saved_token
        channel_id = saved_channel

        if payload is not None:
            if payload.discord_token is not None:
                provided_token = payload.discord_token.strip()
                if provided_token and provided_token != mask_token(saved_token):
                    token = provided_token
            if payload.discord_channel_id is not None:
                provided_channel = payload.discord_channel_id.strip()
                if provided_channel:
                    channel_id = provided_channel

        if not token or not channel_id:
            return TestDiscordResponse(
                success=False,
                error="Discord token and channel ID are required.",
            )

        ok, err = await send_discord_rest_message(
            token,
            channel_id,
            "✅ **UB Stock Monitor test notification!** Your Discord alerts are "
            "configured properly.",
        )
        return TestDiscordResponse(success=ok, error=err)

    # ------------------------------------------------------------------
    # Static frontend mount (must come after /api/* routes)
    # ------------------------------------------------------------------
    resolved_static_dir = static_dir or STATIC_DIR
    os.makedirs(resolved_static_dir, exist_ok=True)
    application.mount(
        "/",
        StaticFiles(directory=resolved_static_dir, html=True),
        name="static",
    )

    return application


app = create_app()
