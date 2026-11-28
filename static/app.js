(() => {
  "use strict";

  const POLL_INTERVAL_MS = 10000;
  const DEFAULT_SELECTOR = "button[label='Add to Cart']";

  const STRATEGY_META = {
    auto: {
      badge: "Auto",
      label: "Auto-Detect",
      hint: "Automatically evaluates Schema.org JSON-LD, Shopify APIs, meta tags, and page heuristics.",
    },
    json_ld: {
      badge: "JSON-LD",
      label: "Schema.org JSON-LD",
      hint: "Parses Schema.org Product & Offer availability metadata embedded in the page.",
    },
    shopify: {
      badge: "Shopify",
      label: "Shopify JSON API",
      hint: "Queries the store's /products/<handle>.json endpoint (with HTML fallback).",
    },
    text_heuristic: {
      badge: "Text",
      label: "Text & Button Heuristics",
      hint: "Analyzes Add to Cart buttons, disabled states, and visible availability text.",
    },
    css_selector: {
      badge: "CSS",
      label: "Custom CSS Selector",
      hint: "Checks whether the specified CSS selector matches an element on the page.",
    },
    meta_tags: {
      badge: "Meta",
      label: "Meta Tags",
      hint: "Inspects OpenGraph and product availability meta tags.",
    },
  };

  const state = {
    activeTab: "products",
    statusFilter: "all",
    searchQuery: "",
    status: null,
    products: [],
    history: [],
    settings: null,
    editingProductId: null,
    optionalSelectorExpanded: false,
    checkingProductIds: new Set(),
    isCheckingAll: false,
    isRefreshing: false,
    settingsDirty: false,
    confirmCallback: null,
    pollTimerId: null,
  };

  // ---------------------------------------------------------------------------
  // API Helper
  // ---------------------------------------------------------------------------

  async function apiFetch(endpoint, options = {}) {
    const config = {
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    };

    const response = await fetch(endpoint, config);
    const contentType = response.headers.get("content-type") || "";
    let payload = null;

    if (contentType.includes("application/json")) {
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
    } else {
      const text = await response.text();
      payload = text ? { detail: text } : null;
    }

    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      if (payload && payload.detail) {
        if (Array.isArray(payload.detail)) {
          message = payload.detail
            .map((err) => err.msg || JSON.stringify(err))
            .join("; ");
        } else {
          message = String(payload.detail);
        }
      }
      const error = new Error(message);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }

    return payload;
  }

  // ---------------------------------------------------------------------------
  // Formatting & Safety Helpers
  // ---------------------------------------------------------------------------

  function escapeHtml(value) {
    if (value === null || value === undefined) {
      return "";
    }
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function sanitizeUrl(url) {
    if (!url) {
      return "#";
    }
    const trimmed = String(url).trim();
    if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
      return trimmed;
    }
    return "#";
  }

  function parseTimestamp(ts) {
    if (!ts) {
      return null;
    }
    const raw = String(ts).trim();
    const normalized =
      /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(raw)
        ? raw.replace(" ", "T") + "Z"
        : raw;
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatRelativeTime(ts) {
    const date = parseTimestamp(ts);
    if (!date) {
      return "Never";
    }
    const diffSeconds = Math.floor((Date.now() - date.getTime()) / 1000);
    if (diffSeconds < 5) {
      return "Just now";
    }
    if (diffSeconds < 60) {
      return `${diffSeconds}s ago`;
    }
    const diffMinutes = Math.floor(diffSeconds / 60);
    if (diffMinutes < 60) {
      return `${diffMinutes}m ago`;
    }
    const diffHours = Math.floor(diffMinutes / 60);
    if (diffHours < 24) {
      return `${diffHours}h ago`;
    }
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 30) {
      return `${diffDays}d ago`;
    }
    return date.toLocaleDateString();
  }

  function formatExactTime(ts) {
    const date = parseTimestamp(ts);
    return date ? date.toLocaleString() : "Never";
  }

  function formatInterval(seconds) {
    const n = Number(seconds);
    if (!Number.isFinite(n) || n <= 0) {
      return "15s";
    }
    if (n < 60) {
      return `${n}s`;
    }
    if (n % 3600 === 0) {
      return `${n / 3600}h`;
    }
    if (n % 60 === 0) {
      return `${n / 60}m`;
    }
    return `${n}s`;
  }

  function formatPrice(price, currency = "USD") {
    if (price === null || price === undefined || price === "") {
      return "";
    }
    const num = Number(price);
    if (!Number.isFinite(num)) {
      return "";
    }
    const code = String(currency || "USD").trim().toUpperCase();
    const symbols = {
      USD: "$",
      EUR: "€",
      GBP: "£",
      JPY: "¥",
      CAD: "CA$",
      AUD: "A$",
    };
    const formatted = num.toFixed(2);
    if (symbols[code]) {
      return `${symbols[code]}${formatted}`;
    }
    return `${code} ${formatted}`;
  }

  function setButtonLoading(buttonEl, labelEl, isLoading, loadingText, idleText) {
    if (!buttonEl) {
      return;
    }
    buttonEl.disabled = isLoading;
    if (labelEl) {
      labelEl.textContent = isLoading ? loadingText : idleText;
    }
    const existingSpinner = buttonEl.querySelector(".spinner");
    const svgIcon = buttonEl.querySelector("svg.btn-icon");
    if (isLoading) {
      if (svgIcon) {
        svgIcon.classList.add("is-hidden");
      }
      if (!existingSpinner) {
        const spinner = document.createElement("span");
        spinner.className = "spinner";
        spinner.setAttribute("aria-hidden", "true");
        buttonEl.prepend(spinner);
      }
    } else {
      if (existingSpinner) {
        existingSpinner.remove();
      }
      if (svgIcon) {
        svgIcon.classList.remove("is-hidden");
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Toast Notifications
  // ---------------------------------------------------------------------------

  function showToast(message, type = "info", durationMs = 4200) {
    const container = document.getElementById("toast-container");
    if (!container) {
      return;
    }

    const toast = document.createElement("div");
    toast.className = `toast toast--${type}`;
    toast.setAttribute("role", "status");

    const body = document.createElement("div");
    body.className = "toast-body";

    const dot = document.createElement("span");
    dot.className = "toast-dot";
    dot.setAttribute("aria-hidden", "true");

    const text = document.createElement("span");
    text.textContent = message;

    body.appendChild(dot);
    body.appendChild(text);

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "toast-close";
    closeBtn.setAttribute("aria-label", "Dismiss notification");
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", () => toast.remove());

    toast.appendChild(body);
    toast.appendChild(closeBtn);
    container.appendChild(toast);

    if (durationMs > 0) {
      window.setTimeout(() => {
        if (toast.parentNode) {
          toast.remove();
        }
      }, durationMs);
    }
  }

  // ---------------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------------

  function renderStatus() {
    const st = state.status;
    if (!st) {
      return;
    }

    const pill = document.getElementById("live-status-pill");
    const pillText = document.getElementById("live-status-text");
    const totalEl = document.getElementById("stat-total-products");
    const inStockEl = document.getElementById("stat-in-stock");
    const outStockEl = document.getElementById("stat-out-of-stock");
    const discordEl = document.getElementById("stat-discord-status");
    const intervalEl = document.getElementById("stat-check-interval");
    const lastCheckEl = document.getElementById("stat-last-check");
    const settingsDot = document.getElementById("settings-unconfigured-dot");

    if (totalEl) totalEl.textContent = String(st.total_products ?? 0);
    if (inStockEl) inStockEl.textContent = String(st.in_stock_count ?? 0);
    if (outStockEl) outStockEl.textContent = String(st.out_of_stock_count ?? 0);
    if (intervalEl) intervalEl.textContent = formatInterval(st.check_interval);

    if (lastCheckEl) {
      lastCheckEl.textContent = st.last_check
        ? `Last check: ${formatRelativeTime(st.last_check)}`
        : "Never checked";
      lastCheckEl.title = formatExactTime(st.last_check);
    }

    if (discordEl) {
      if (st.discord_connected) {
        discordEl.textContent = "Connected";
        discordEl.className = "stat-value stat-value--sm stat-value--emerald";
      } else if (st.discord_configured) {
        discordEl.textContent = st.notifications_enabled ? "Configured" : "Paused";
        discordEl.className = "stat-value stat-value--sm";
      } else {
        discordEl.textContent = "Unconfigured";
        discordEl.className = "stat-value stat-value--sm stat-value--rose";
      }
    }

    if (settingsDot) {
      settingsDot.classList.toggle("is-hidden", Boolean(st.discord_configured));
    }

    if (pill && pillText) {
      pill.classList.remove(
        "status-pill--active",
        "status-pill--checking",
        "status-pill--idle",
        "status-pill--unconfigured"
      );

      if (st.is_checking || state.isCheckingAll) {
        pill.classList.add("status-pill--checking");
        pillText.textContent = "Checking...";
      } else if (!st.discord_configured && (st.total_products ?? 0) === 0) {
        pill.classList.add("status-pill--unconfigured");
        pillText.textContent = "Unconfigured";
      } else if ((st.total_products ?? 0) > 0) {
        pill.classList.add("status-pill--active");
        pillText.textContent = "Active";
      } else {
        pill.classList.add("status-pill--idle");
        pillText.textContent = "Idle";
      }
    }

    const checkAllBtn = document.getElementById("btn-check-all");
    if (checkAllBtn && !state.isCheckingAll) {
      checkAllBtn.disabled = (st.total_products ?? 0) === 0;
    }
  }

  function getStrategyMeta(strategy) {
    const key = String(strategy || "auto").toLowerCase();
    return STRATEGY_META[key] || STRATEGY_META.auto;
  }

  function formatDetectedBy(strategyUsed, detectedBy) {
    const strategyKey = String(strategyUsed || "").toLowerCase();
    if (strategyKey === "json_ld") {
      return "Detected via Schema.org JSON-LD";
    }
    if (strategyKey === "shopify") {
      return "Detected via Shopify API";
    }
    if (strategyKey === "meta_tags") {
      return detectedBy
        ? `Detected via Meta Tags (${detectedBy})`
        : "Detected via Meta Tags";
    }
    if (strategyKey === "text_heuristic") {
      return "Detected via Text Heuristics";
    }
    if (strategyKey === "css_selector") {
      return "Detected via Custom CSS Selector";
    }
    if (strategyKey === "auto") {
      return detectedBy && detectedBy !== "none"
        ? `Auto-Detect (${detectedBy})`
        : "Evaluated via Auto-Detect";
    }
    return detectedBy ? `Detected via ${detectedBy}` : "Detection completed";
  }

  function getFilteredProducts() {
    const query = state.searchQuery.trim().toLowerCase();
    return state.products.filter((product) => {
      if (state.statusFilter === "in_stock" && !product.in_stock) {
        return false;
      }
      if (state.statusFilter === "out_of_stock" && product.in_stock) {
        return false;
      }
      if (state.statusFilter === "error" && !product.last_error) {
        return false;
      }
      if (query) {
        const haystack = `${product.name || ""} ${product.url || ""} ${product.selector || ""} ${product.strategy || ""} ${product.strategy_details || ""}`.toLowerCase();
        if (!haystack.includes(query)) {
          return false;
        }
      }
      return true;
    });
  }

  function renderProducts() {
    const grid = document.getElementById("products-grid");
    const emptyState = document.getElementById("products-empty-state");
    const filterEmpty = document.getElementById("products-filter-empty");
    const tabBadge = document.getElementById("tab-products-badge");

    const allCount = state.products.length;
    const inStockCount = state.products.filter((p) => p.in_stock).length;
    const outOfStockCount = allCount - inStockCount;
    const errorCount = state.products.filter((p) => Boolean(p.last_error)).length;

    if (tabBadge) tabBadge.textContent = String(allCount);
    const countAllEl = document.getElementById("filter-count-all");
    const countInEl = document.getElementById("filter-count-in-stock");
    const countOutEl = document.getElementById("filter-count-out-of-stock");
    const countErrEl = document.getElementById("filter-count-error");
    if (countAllEl) countAllEl.textContent = String(allCount);
    if (countInEl) countInEl.textContent = String(inStockCount);
    if (countOutEl) countOutEl.textContent = String(outOfStockCount);
    if (countErrEl) countErrEl.textContent = String(errorCount);

    if (!grid || !emptyState || !filterEmpty) {
      return;
    }

    if (allCount === 0) {
      grid.innerHTML = "";
      grid.classList.add("is-hidden");
      filterEmpty.classList.add("is-hidden");
      emptyState.classList.remove("is-hidden");
      return;
    }

    emptyState.classList.add("is-hidden");
    const filtered = getFilteredProducts();

    if (filtered.length === 0) {
      grid.innerHTML = "";
      grid.classList.add("is-hidden");
      filterEmpty.classList.remove("is-hidden");
      return;
    }

    filterEmpty.classList.add("is-hidden");
    grid.classList.remove("is-hidden");

    grid.innerHTML = filtered
      .map((product) => {
        const isChecking = state.checkingProductIds.has(product.id);
        const safeUrl = escapeHtml(sanitizeUrl(product.url));
        const safeDisplayUrl = escapeHtml(product.url);
        const safeName = escapeHtml(product.name);
        const rawSelector = (product.selector || "").trim();
        const safeSelector = escapeHtml(rawSelector);
        const strategyKey = String(product.strategy || "auto").toLowerCase();
        const strategyMeta = getStrategyMeta(strategyKey);
        const safeStrategyKey = escapeHtml(
          STRATEGY_META[strategyKey] ? strategyKey : "auto"
        );
        const safeStrategyDetails = product.strategy_details
          ? escapeHtml(product.strategy_details)
          : "";
        const strategyTooltip = product.strategy_details
          ? `${strategyMeta.label}: ${product.strategy_details}`
          : strategyMeta.label;
        const cardStateClass = product.in_stock
          ? "product-card--in-stock"
          : "product-card--out-of-stock";

        const statusBadge = product.last_error
          ? `<span class="badge badge--error" title="${escapeHtml(product.last_error)}"><span class="badge-dot"></span>Error</span>`
          : product.in_stock
            ? `<span class="badge badge--in-stock" title="${escapeHtml(strategyTooltip)}"><span class="badge-dot"></span>In Stock</span>`
            : `<span class="badge badge--out-of-stock" title="${escapeHtml(strategyTooltip)}"><span class="badge-dot"></span>Out of Stock</span>`;

        const strategyBadge = `<span class="strategy-badge strategy-badge--${safeStrategyKey}" title="${escapeHtml(strategyTooltip)}">${escapeHtml(strategyMeta.badge)}</span>`;

        let methodMarkup = "";
        if (strategyKey === "css_selector") {
          const displaySelector = safeSelector || escapeHtml(DEFAULT_SELECTOR);
          methodMarkup = `
            <div class="selector-row" title="CSS Selector: ${displaySelector}">
              <span class="meta-label">Selector</span>
              <code class="selector-pill">${displaySelector}</code>
            </div>`;
        } else if (strategyKey === "json_ld") {
          methodMarkup = `
            <div class="selector-row">
              <span class="meta-label">Method</span>
              <span class="strategy-pill strategy-pill--json_ld">📄 Schema.org JSON-LD</span>
            </div>`;
        } else if (strategyKey === "shopify") {
          methodMarkup = `
            <div class="selector-row">
              <span class="meta-label">Method</span>
              <span class="strategy-pill strategy-pill--shopify">🛍️ Shopify JSON API</span>
            </div>`;
        } else if (strategyKey === "text_heuristic") {
          methodMarkup = `
            <div class="selector-row">
              <span class="meta-label">Method</span>
              <span class="strategy-pill strategy-pill--text_heuristic">🔍 Text &amp; Button Heuristics</span>
            </div>`;
        } else {
          const fallbackMarkup = rawSelector
            ? `<span class="meta-fallback" title="Optional fallback selector: ${safeSelector}">Fallback: <code>${safeSelector}</code></span>`
            : "";
          methodMarkup = `
            <div class="selector-row">
              <span class="meta-label">Method</span>
              <span class="strategy-pill strategy-pill--auto">⚡ Auto-Detect</span>
              ${fallbackMarkup}
            </div>`;
        }

        const strategyDetailsMarkup = safeStrategyDetails
          ? `<div class="strategy-details-text" title="Live detection status: ${safeStrategyDetails}">${safeStrategyDetails}</div>`
          : "";

        let priceRowMarkup = "";
        if (product.current_price !== null && product.current_price !== undefined) {
          const currPrice = Number(product.current_price);
          const currency = product.currency || "USD";
          const formattedCurr = escapeHtml(formatPrice(currPrice, currency));
          let previousPriceMarkup = "";
          let trendBadgeMarkup = "";

          if (
            product.previous_price !== null &&
            product.previous_price !== undefined &&
            Number(product.previous_price) !== currPrice
          ) {
            const prevPrice = Number(product.previous_price);
            const formattedPrev = escapeHtml(formatPrice(prevPrice, currency));
            previousPriceMarkup = `<span class="price-previous" title="Previous price">${formattedPrev}</span>`;

            if (prevPrice > 0) {
              const pctChange = ((currPrice - prevPrice) / prevPrice) * 100;
              if (currPrice < prevPrice) {
                trendBadgeMarkup = `<span class="price-trend price-trend--drop">▼ ${Math.abs(pctChange).toFixed(1)}% off</span>`;
              } else {
                trendBadgeMarkup = `<span class="price-trend price-trend--up">▲ +${Math.abs(pctChange).toFixed(1)}%</span>`;
              }
            }
          }

          priceRowMarkup = `
            <div class="product-price-row">
              <button
                type="button"
                class="price-badge js-view-price-history"
                data-product-name="${safeName}"
                data-product-id="${product.id}"
                title="Click to view price history (${escapeHtml(currency)})"
              >
                <span class="price-badge-value">${formattedCurr}</span>
              </button>
              ${previousPriceMarkup}
              ${trendBadgeMarkup}
            </div>`;
        }

        const lastCheckedRel = formatRelativeTime(product.last_checked);
        const lastCheckedExact = formatExactTime(product.last_checked);
        const statusChangeRel = product.last_status_change
          ? formatRelativeTime(product.last_status_change)
          : "No change yet";
        const statusChangeExact = formatExactTime(product.last_status_change);

        const errorMarkup = product.last_error
          ? `<div class="product-error-box">Last error: ${escapeHtml(product.last_error)}</div>`
          : "";

        const checkBtnContent = isChecking
          ? `<span class="spinner" aria-hidden="true"></span><span>Checking...</span>`
          : `<svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg><span>Check Now</span>`;

        return `
          <article class="product-card ${cardStateClass}" data-product-id="${product.id}">
            <div class="product-card-top">
              <div class="product-title-wrap">
                <h3 class="product-name">${safeName}</h3>
                <a class="product-url-link" href="${safeUrl}" target="_blank" rel="noopener noreferrer" title="${safeDisplayUrl}">
                  <span class="product-url-text">${safeDisplayUrl}</span>
                  <svg class="external-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                    <polyline points="15 3 21 3 21 9" />
                    <line x1="10" y1="14" x2="21" y2="3" />
                  </svg>
                </a>
              </div>
              <div class="product-card-badges">
                ${statusBadge}
                ${strategyBadge}
              </div>
            </div>

            <div class="product-meta">
              ${priceRowMarkup}
              ${methodMarkup}
              ${strategyDetailsMarkup}

              <div class="product-timestamps">
                <span title="${escapeHtml(lastCheckedExact)}">Checked: ${escapeHtml(lastCheckedRel)}</span>
                <span title="${escapeHtml(statusChangeExact)}">Changed: ${escapeHtml(statusChangeRel)}</span>
              </div>

              ${errorMarkup}
            </div>

            <div class="product-card-actions">
              <button
                type="button"
                class="btn btn-sm btn-secondary js-check-product"
                data-id="${product.id}"
                ${isChecking ? "disabled" : ""}
              >
                ${checkBtnContent}
              </button>
              <div class="product-actions-secondary">
                <button
                  type="button"
                  class="btn btn-sm btn-ghost js-view-price-history"
                  data-product-name="${safeName}"
                  data-product-id="${product.id}"
                  title="View price history"
                >
                  Price History
                </button>
                <button type="button" class="btn btn-sm btn-ghost js-edit-product" data-id="${product.id}">
                  Edit
                </button>
                <button type="button" class="btn btn-sm btn-danger-ghost js-delete-product" data-id="${product.id}">
                  Delete
                </button>
              </div>
            </div>
          </article>
        `;
      })
      .join("");
  }

  function renderHistory() {
    const tbody = document.getElementById("history-table-body");
    const tableCard = document.getElementById("history-table-card");
    const emptyState = document.getElementById("history-empty-state");
    const tabBadge = document.getElementById("tab-history-badge");
    const clearBtn = document.getElementById("btn-clear-history");

    if (tabBadge) {
      tabBadge.textContent = String(state.history.length);
    }
    if (clearBtn) {
      clearBtn.disabled = state.history.length === 0;
    }

    if (!tbody || !tableCard || !emptyState) {
      return;
    }

    if (state.history.length === 0) {
      tbody.innerHTML = "";
      tableCard.classList.add("is-hidden");
      emptyState.classList.remove("is-hidden");
      return;
    }

    emptyState.classList.add("is-hidden");
    tableCard.classList.remove("is-hidden");

    tbody.innerHTML = state.history
      .map((entry) => {
        let statusMarkup = `<span class="badge badge--neutral">${escapeHtml(entry.status || "unknown")}</span>`;
        if (entry.status === "in_stock") {
          statusMarkup = `<span class="badge badge--in-stock"><span class="badge-dot"></span>In Stock</span>`;
        } else if (entry.status === "out_of_stock") {
          statusMarkup = `<span class="badge badge--out-of-stock"><span class="badge-dot"></span>Out of Stock</span>`;
        } else if (entry.status === "price_drop") {
          statusMarkup = `<span class="badge badge--price-drop"><span class="badge-dot"></span>Price Drop</span>`;
        } else if (entry.status === "error") {
          statusMarkup = `<span class="badge badge--error"><span class="badge-dot"></span>Error</span>`;
        }

        const alertMarkup = entry.notification_sent
          ? `<span class="badge badge--cyan">Alert Sent</span>`
          : `<span class="badge badge--neutral">No Alert</span>`;

        const relTime = formatRelativeTime(entry.created_at);
        const exactTime = formatExactTime(entry.created_at);

        return `
          <tr>
            <td class="history-time" title="${escapeHtml(exactTime)}">${escapeHtml(relTime)}</td>
            <td class="history-product-name">${escapeHtml(entry.product_name || "Unknown Product")}</td>
            <td>${statusMarkup}</td>
            <td>${alertMarkup}</td>
            <td class="history-message">${escapeHtml(entry.message || "—")}</td>
          </tr>
        `;
      })
      .join("");
  }

  function renderSettingsForm(force = false) {
    const st = state.settings;
    if (!st) {
      return;
    }
    if (state.settingsDirty && !force) {
      return;
    }

    const tokenInput = document.getElementById("setting-discord-token");
    const channelInput = document.getElementById("setting-discord-channel-id");
    const intervalInput = document.getElementById("setting-check-interval");
    const notifCheckbox = document.getElementById("setting-notifications-enabled");
    const tokenBadge = document.getElementById("settings-token-badge");
    const tokenHint = document.getElementById("setting-token-hint");

    const tgEnabledCheckbox = document.getElementById("setting-telegram-enabled");
    const tgTokenInput = document.getElementById("setting-telegram-token");
    const tgTokenHint = document.getElementById("setting-telegram-token-hint");
    const tgChatIdInput = document.getElementById("setting-telegram-chat-id");
    const tgBadge = document.getElementById("settings-telegram-badge");

    const webhookEnabledCheckbox = document.getElementById("setting-webhook-enabled");
    const webhookUrlInput = document.getElementById("setting-webhook-url");
    const webhookBadge = document.getElementById("settings-webhook-badge");

    const priceAlertsCheckbox = document.getElementById("setting-price-alerts-enabled");
    const proxyEnabledCheckbox = document.getElementById("setting-proxy-enabled");
    const proxyListTextarea = document.getElementById("setting-proxy-list");
    const domainDelayInput = document.getElementById("setting-domain-rate-limit-delay");

    if (tokenInput) {
      tokenInput.value = "";
      tokenInput.placeholder = st.discord_token_set
        ? `Saved (${st.discord_token_masked}) — enter new token to replace`
        : "Paste your Discord bot token";
    }
    if (channelInput) {
      channelInput.value = st.discord_channel_id || "";
    }
    if (intervalInput) {
      intervalInput.value = String(st.check_stock_interval ?? 15);
    }
    if (notifCheckbox) {
      notifCheckbox.checked = Boolean(st.notifications_enabled);
    }
    if (tokenBadge) {
      if (st.discord_token_set && st.discord_channel_id) {
        tokenBadge.textContent = "Configured";
        tokenBadge.className = "badge badge--in-stock";
      } else if (st.discord_token_set || st.discord_channel_id) {
        tokenBadge.textContent = "Incomplete";
        tokenBadge.className = "badge badge--error";
      } else {
        tokenBadge.textContent = "Not Configured";
        tokenBadge.className = "badge badge--neutral";
      }
    }
    if (tokenHint) {
      tokenHint.textContent = st.discord_token_set
        ? `Current saved token: ${st.discord_token_masked}. Leave blank to keep unchanged.`
        : "Enter your Discord bot token to enable stock alerts.";
    }

    // Telegram Integration
    if (tgEnabledCheckbox) {
      tgEnabledCheckbox.checked = Boolean(st.telegram_enabled);
    }
    if (tgTokenInput) {
      tgTokenInput.value = "";
      tgTokenInput.placeholder = st.telegram_bot_token_set
        ? `Saved (${st.telegram_bot_token_masked}) — enter new token to replace`
        : "Paste your Telegram bot token";
    }
    if (tgTokenHint) {
      tgTokenHint.textContent = st.telegram_bot_token_set
        ? `Current saved token: ${st.telegram_bot_token_masked}. Leave blank to keep unchanged.`
        : "Enter your Telegram bot token from @BotFather.";
    }
    if (tgChatIdInput) {
      tgChatIdInput.value = st.telegram_chat_id || "";
    }
    if (tgBadge) {
      if (st.telegram_bot_token_set && st.telegram_chat_id) {
        tgBadge.textContent = "Configured";
        tgBadge.className = "badge badge--in-stock";
      } else if (st.telegram_bot_token_set || st.telegram_chat_id) {
        tgBadge.textContent = "Incomplete";
        tgBadge.className = "badge badge--error";
      } else {
        tgBadge.textContent = "Not Configured";
        tgBadge.className = "badge badge--neutral";
      }
    }

    // Webhook Integration
    if (webhookEnabledCheckbox) {
      webhookEnabledCheckbox.checked = Boolean(st.webhook_enabled);
    }
    if (webhookUrlInput) {
      webhookUrlInput.value = st.webhook_url || "";
    }
    if (webhookBadge) {
      if ((st.webhook_url || "").trim()) {
        webhookBadge.textContent = "Configured";
        webhookBadge.className = "badge badge--in-stock";
      } else {
        webhookBadge.textContent = "Not Configured";
        webhookBadge.className = "badge badge--neutral";
      }
    }

    // Price Alerts, Proxy Rotation & Domain Rate Limit
    if (priceAlertsCheckbox) {
      priceAlertsCheckbox.checked =
        st.price_alerts_enabled !== undefined
          ? Boolean(st.price_alerts_enabled)
          : true;
    }
    if (proxyEnabledCheckbox) {
      proxyEnabledCheckbox.checked = Boolean(st.proxy_enabled);
    }
    if (proxyListTextarea) {
      proxyListTextarea.value = st.proxy_list || "";
    }
    if (domainDelayInput) {
      domainDelayInput.value = String(st.domain_rate_limit_delay ?? 1.0);
    }

    state.settingsDirty = false;
  }

  // ---------------------------------------------------------------------------
  // Data Fetching
  // ---------------------------------------------------------------------------

  async function loadSettings(force = false) {
    const data = await apiFetch("/api/settings");
    state.settings = data;
    renderSettingsForm(force);
  }

  async function refreshDashboard({ silent = false, includeSettings = false } = {}) {
    if (state.isRefreshing) {
      return;
    }
    state.isRefreshing = true;

    const refreshBtn = document.getElementById("btn-refresh");
    if (refreshBtn && !silent) {
      refreshBtn.disabled = true;
    }

    try {
      const requests = [
        apiFetch("/api/status"),
        apiFetch("/api/products"),
        apiFetch("/api/history?limit=100"),
      ];
      if (includeSettings) {
        requests.push(apiFetch("/api/settings"));
      }

      const [statusData, productsData, historyData, settingsData] =
        await Promise.all(requests);

      state.status = statusData;
      state.products = Array.isArray(productsData) ? productsData : [];
      state.history = Array.isArray(historyData) ? historyData : [];
      if (settingsData) {
        state.settings = settingsData;
        renderSettingsForm(false);
      }

      renderStatus();
      renderProducts();
      renderHistory();

      const syncLabel = document.getElementById("last-sync-label");
      if (syncLabel) {
        syncLabel.textContent = `Updated ${new Date().toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })}`;
      }
    } catch (error) {
      if (!silent) {
        showToast(error.message || "Failed to refresh dashboard", "error");
      }
    } finally {
      state.isRefreshing = false;
      if (refreshBtn && !silent) {
        refreshBtn.disabled = false;
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Tab Navigation
  // ---------------------------------------------------------------------------

  function switchTab(tabName) {
    state.activeTab = tabName;

    document.querySelectorAll(".nav-tab").forEach((tabEl) => {
      tabEl.classList.toggle("is-active", tabEl.dataset.tab === tabName);
    });

    document.querySelectorAll(".tab-view").forEach((viewEl) => {
      viewEl.classList.toggle("is-active", viewEl.id === `view-${tabName}`);
    });

    if (tabName === "settings" && !state.settings) {
      loadSettings(true).catch((err) => showToast(err.message, "error"));
    }
  }

  // ---------------------------------------------------------------------------
  // Product Modal (Add / Edit) & Test Stock Detection
  // ---------------------------------------------------------------------------

  function syncStrategyUI() {
    const strategySelect = document.getElementById("product-strategy-select");
    const strategyHint = document.getElementById("product-strategy-hint");
    const toggleRow = document.getElementById("optional-selector-toggle-row");
    const toggleBtn = document.getElementById("btn-toggle-optional-selector");
    const selectorGroup = document.getElementById("product-selector-group");
    const selectorInput = document.getElementById("product-selector-input");
    const optionalTag = document.getElementById("product-selector-optional-tag");
    const selectorHint = document.getElementById("product-selector-hint");

    const strategy = strategySelect ? strategySelect.value : "auto";
    const meta = getStrategyMeta(strategy);

    if (strategyHint) {
      strategyHint.textContent = meta.hint;
    }

    const isCss = strategy === "css_selector";

    if (toggleRow) {
      toggleRow.classList.toggle("is-hidden", isCss);
    }

    const showSelectorGroup = isCss || state.optionalSelectorExpanded;
    if (selectorGroup) {
      selectorGroup.classList.toggle("is-hidden", !showSelectorGroup);
    }

    if (toggleBtn) {
      toggleBtn.setAttribute("aria-expanded", String(showSelectorGroup));
      toggleBtn.textContent = state.optionalSelectorExpanded
        ? "Hide optional selector"
        : "Add optional fallback selector";
    }

    if (optionalTag) {
      optionalTag.classList.toggle("is-hidden", isCss);
    }

    if (selectorInput) {
      selectorInput.required = isCss;
      if (isCss && !selectorInput.value.trim()) {
        selectorInput.value = DEFAULT_SELECTOR;
      }
    }

    if (selectorHint) {
      selectorHint.textContent = isCss
        ? "Required: matching element indicates the product is in stock."
        : "Optional fallback CSS selector if automatic detection is inconclusive.";
    }
  }

  function openProductModal(product = null) {
    const modal = document.getElementById("product-modal");
    const titleEl = document.getElementById("product-modal-title");
    const nameInput = document.getElementById("product-name-input");
    const urlInput = document.getElementById("product-url-input");
    const strategySelect = document.getElementById("product-strategy-select");
    const selectorInput = document.getElementById("product-selector-input");
    const saveLabel = document.getElementById("btn-save-product-label");
    const testResult = document.getElementById("selector-test-result");
    const formError = document.getElementById("product-form-error");

    state.editingProductId = product ? product.id : null;
    const savedStrategy = product && product.strategy ? product.strategy : "auto";
    const savedSelector = product ? (product.selector || "").trim() : "";

    state.optionalSelectorExpanded = Boolean(
      product && savedStrategy !== "css_selector" && savedSelector
    );

    if (titleEl) {
      titleEl.textContent = product ? "Edit Product" : "Add Product";
    }
    if (saveLabel) {
      saveLabel.textContent = product ? "Update Product" : "Save Product";
    }
    if (nameInput) nameInput.value = product ? product.name : "";
    if (urlInput) urlInput.value = product ? product.url : "";
    if (strategySelect) strategySelect.value = savedStrategy;
    if (selectorInput) {
      selectorInput.value =
        savedStrategy === "css_selector"
          ? savedSelector || DEFAULT_SELECTOR
          : savedSelector;
    }

    syncStrategyUI();

    if (testResult) {
      testResult.className = "inline-feedback is-hidden";
      testResult.textContent = "";
    }
    if (formError) {
      formError.classList.add("is-hidden");
      formError.textContent = "";
    }

    if (modal) {
      modal.classList.remove("is-hidden");
      if (nameInput) {
        nameInput.focus();
      }
    }
  }

  function closeProductModal() {
    const modal = document.getElementById("product-modal");
    if (modal) {
      modal.classList.add("is-hidden");
    }
    state.editingProductId = null;
    state.optionalSelectorExpanded = false;
  }

  async function handleTestSelector() {
    const urlInput = document.getElementById("product-url-input");
    const strategySelect = document.getElementById("product-strategy-select");
    const selectorInput = document.getElementById("product-selector-input");
    const btn = document.getElementById("btn-test-selector");
    const btnLabel = document.getElementById("btn-test-selector-label");
    const resultEl = document.getElementById("selector-test-result");

    const url = urlInput ? urlInput.value.trim() : "";
    const strategy = strategySelect ? strategySelect.value : "auto";
    const rawSelector = selectorInput ? selectorInput.value.trim() : "";
    const selector =
      strategy === "css_selector"
        ? rawSelector || DEFAULT_SELECTOR
        : rawSelector || null;

    if (!url) {
      if (resultEl) {
        resultEl.className = "inline-feedback inline-feedback--error";
        resultEl.textContent = "Please enter a valid product URL before testing.";
      }
      return;
    }

    setButtonLoading(btn, btnLabel, true, "Testing...", "Test Stock Detection");
    if (resultEl) {
      resultEl.className = "inline-feedback";
      resultEl.textContent = "Fetching product page and running stock detection...";
    }

    try {
      const res = await apiFetch("/api/products/test-selector", {
        method: "POST",
        body: JSON.stringify({ url, selector, strategy }),
      });

      if (!resultEl) {
        return;
      }

      const detectedLabel = formatDetectedBy(res.strategy_used, res.detected_by);
      const hasPrice = res.price !== null && res.price !== undefined;
      const priceCurrency = res.currency || "USD";
      const priceMarkup = hasPrice
        ? `<p class="test-result-price">Detected Price: <strong>${escapeHtml(
            formatPrice(res.price, priceCurrency)
          )}</strong> (${escapeHtml(priceCurrency)})</p>`
        : "";

      if (res.error) {
        resultEl.className = "inline-feedback inline-feedback--error";
        resultEl.innerHTML = `
          <div class="test-result-header">
            <span class="badge badge--error"><span class="badge-dot"></span>Error</span>
            <span class="test-result-method">${escapeHtml(detectedLabel)}</span>
          </div>
          <p class="test-result-details">${escapeHtml(res.error)}</p>
          ${priceMarkup}
        `;
      } else if (res.in_stock) {
        const detailText =
          res.details || "Product currently appears in stock.";
        resultEl.className = "inline-feedback inline-feedback--success";
        resultEl.innerHTML = `
          <div class="test-result-header">
            <span class="badge badge--in-stock"><span class="badge-dot"></span>In Stock</span>
            <span class="test-result-method">${escapeHtml(detectedLabel)}</span>
          </div>
          <p class="test-result-details">${escapeHtml(detailText)}</p>
          ${priceMarkup}
        `;
      } else {
        const detailText =
          res.details || "Product appears out of stock for the selected strategy.";
        resultEl.className = "inline-feedback inline-feedback--warning";
        resultEl.innerHTML = `
          <div class="test-result-header">
            <span class="badge badge--out-of-stock"><span class="badge-dot"></span>Out of Stock</span>
            <span class="test-result-method">${escapeHtml(detectedLabel)}</span>
          </div>
          <p class="test-result-details">${escapeHtml(detailText)}</p>
          ${priceMarkup}
        `;
      }
    } catch (error) {
      if (resultEl) {
        resultEl.className = "inline-feedback inline-feedback--error";
        resultEl.textContent = error.message || "Stock detection test failed.";
      }
    } finally {
      setButtonLoading(btn, btnLabel, false, "Testing...", "Test Stock Detection");
    }
  }

  async function handleSaveProduct(event) {
    event.preventDefault();

    const nameInput = document.getElementById("product-name-input");
    const urlInput = document.getElementById("product-url-input");
    const strategySelect = document.getElementById("product-strategy-select");
    const selectorInput = document.getElementById("product-selector-input");
    const saveBtn = document.getElementById("btn-save-product");
    const saveLabel = document.getElementById("btn-save-product-label");
    const formError = document.getElementById("product-form-error");

    const isEditing = state.editingProductId !== null;
    const idleText = isEditing ? "Update Product" : "Save Product";
    const strategy = strategySelect ? strategySelect.value : "auto";
    const rawSelector = selectorInput ? selectorInput.value.trim() : "";

    const payload = {
      name: nameInput ? nameInput.value.trim() : "",
      url: urlInput ? urlInput.value.trim() : "",
      strategy,
      selector:
        strategy === "css_selector"
          ? rawSelector || DEFAULT_SELECTOR
          : rawSelector,
    };

    if (formError) {
      formError.classList.add("is-hidden");
      formError.textContent = "";
    }

    setButtonLoading(saveBtn, saveLabel, true, "Saving...", idleText);

    try {
      if (isEditing) {
        await apiFetch(
          "/api/products/" + encodeURIComponent(state.editingProductId),
          {
            method: "PUT",
            body: JSON.stringify(payload),
          }
        );
        showToast(`Updated "${payload.name}"`, "success");
      } else {
        await apiFetch("/api/products", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        showToast(`Added "${payload.name}" to monitor`, "success");
      }

      closeProductModal();
      await refreshDashboard({ silent: true });
    } catch (error) {
      if (formError) {
        formError.textContent = error.message || "Failed to save product.";
        formError.classList.remove("is-hidden");
      }
    } finally {
      setButtonLoading(saveBtn, saveLabel, false, "Saving...", idleText);
    }
  }

  // ---------------------------------------------------------------------------
  // Confirmation Modal
  // ---------------------------------------------------------------------------

  function openConfirmModal({ title, message, confirmLabel = "Confirm", onConfirm }) {
    const modal = document.getElementById("confirm-modal");
    const titleEl = document.getElementById("confirm-modal-title");
    const msgEl = document.getElementById("confirm-modal-message");
    const okBtn = document.getElementById("btn-confirm-ok");

    if (titleEl) titleEl.textContent = title;
    if (msgEl) msgEl.textContent = message;
    if (okBtn) okBtn.textContent = confirmLabel;

    state.confirmCallback = onConfirm;
    if (modal) {
      modal.classList.remove("is-hidden");
    }
  }

  function closeConfirmModal() {
    const modal = document.getElementById("confirm-modal");
    if (modal) {
      modal.classList.add("is-hidden");
    }
    state.confirmCallback = null;
  }

  // ---------------------------------------------------------------------------
  // Product & Monitoring Actions
  // ---------------------------------------------------------------------------

  async function handleCheckProduct(productId) {
    if (state.checkingProductIds.has(productId)) {
      return;
    }
    state.checkingProductIds.add(productId);
    renderProducts();

    try {
      const updated = await apiFetch(
        "/api/products/" + encodeURIComponent(productId) + "/check",
        { method: "POST" }
      );
      if (updated.last_error) {
        showToast(`${updated.name}: ${updated.last_error}`, "error");
      } else if (updated.in_stock) {
        showToast(`${updated.name} is IN STOCK!`, "success");
      } else {
        showToast(`${updated.name} checked — Out of stock`, "info");
      }
      await refreshDashboard({ silent: true });
    } catch (error) {
      showToast(error.message || "Failed to check product", "error");
    } finally {
      state.checkingProductIds.delete(productId);
      renderProducts();
    }
  }

  function handleDeleteProduct(productId) {
    const product = state.products.find((p) => p.id === productId);
    const label = product ? product.name : `Product #${productId}`;

    openConfirmModal({
      title: "Delete Product",
      message: `Remove "${label}" from monitoring? Its existing log entries will remain in the activity log unless cleared.`,
      confirmLabel: "Delete",
      onConfirm: async () => {
        try {
          await apiFetch("/api/products/" + encodeURIComponent(productId), {
            method: "DELETE",
          });
          showToast(`Deleted "${label}"`, "info");
          await refreshDashboard({ silent: true });
        } catch (error) {
          showToast(error.message || "Failed to delete product", "error");
        }
      },
    });
  }

  async function handleCheckAll() {
    if (state.isCheckingAll) {
      return;
    }
    const btn = document.getElementById("btn-check-all");
    const label = document.getElementById("btn-check-all-label");

    state.isCheckingAll = true;
    renderStatus();
    setButtonLoading(btn, label, true, "Checking All...", "Check All Now");

    try {
      const result = await apiFetch("/api/check-all", { method: "POST" });
      showToast(
        `Checked ${result.checked} product(s): ${result.in_stock_count} in stock, ${result.out_of_stock_count} out of stock.`,
        result.in_stock_count > 0 ? "success" : "info"
      );
      await refreshDashboard({ silent: true });
    } catch (error) {
      showToast(error.message || "Failed to check all products", "error");
    } finally {
      state.isCheckingAll = false;
      setButtonLoading(btn, label, false, "Checking All...", "Check All Now");
      renderStatus();
    }
  }

  function handleClearHistory() {
    openConfirmModal({
      title: "Clear Activity Log",
      message: "Permanently delete all recorded stock check and alert history?",
      confirmLabel: "Clear History",
      onConfirm: async () => {
        try {
          const res = await apiFetch("/api/history", { method: "DELETE" });
          showToast(`Cleared ${res.deleted ?? 0} history record(s)`, "info");
          await refreshDashboard({ silent: true });
        } catch (error) {
          showToast(error.message || "Failed to clear history", "error");
        }
      },
    });
  }

  // ---------------------------------------------------------------------------
  // Price History Modal
  // ---------------------------------------------------------------------------

  function closePriceHistoryModal() {
    const modal = document.getElementById("price-history-modal");
    if (modal) {
      modal.classList.add("is-hidden");
    }
  }

  async function openPriceHistoryModal(productName, productId = null) {
    const modal = document.getElementById("price-history-modal");
    const titleEl = document.getElementById("price-history-modal-title");
    const currentPriceEl = document.getElementById("price-history-current-price");
    const changeStatEl = document.getElementById("price-history-change-stat");
    const countStatEl = document.getElementById("price-history-count-stat");
    const loadingEl = document.getElementById("price-history-loading");
    const errorEl = document.getElementById("price-history-error");
    const listEl = document.getElementById("price-history-list");
    const tbodyEl = document.getElementById("price-history-tbody");
    const emptyEl = document.getElementById("price-history-empty");

    const matchedProduct =
      state.products.find((p) => productId !== null && p.id === productId) ||
      state.products.find((p) => p.name === productName) ||
      null;

    const displayProductName = matchedProduct
      ? matchedProduct.name
      : productName || "Product";

    if (titleEl) {
      titleEl.textContent = `Price History — ${displayProductName}`;
    }

    if (currentPriceEl) {
      currentPriceEl.textContent =
        matchedProduct &&
        matchedProduct.current_price !== null &&
        matchedProduct.current_price !== undefined
          ? formatPrice(matchedProduct.current_price, matchedProduct.currency)
          : "—";
    }

    if (changeStatEl) {
      changeStatEl.textContent = "—";
      changeStatEl.className = "price-stat-value price-stat-value--sm";
      if (
        matchedProduct &&
        matchedProduct.current_price !== null &&
        matchedProduct.current_price !== undefined &&
        matchedProduct.previous_price !== null &&
        matchedProduct.previous_price !== undefined &&
        Number(matchedProduct.previous_price) !==
          Number(matchedProduct.current_price)
      ) {
        const curr = Number(matchedProduct.current_price);
        const prev = Number(matchedProduct.previous_price);
        const diff = curr - prev;
        const pct = prev > 0 ? (diff / prev) * 100 : 0;
        const sign = diff > 0 ? "+" : "-";
        const formattedDiff = formatPrice(
          Math.abs(diff),
          matchedProduct.currency
        );
        changeStatEl.textContent = `${diff < 0 ? "▼" : "▲"} ${sign}${formattedDiff} (${sign}${Math.abs(pct).toFixed(1)}%)`;
        changeStatEl.className =
          diff < 0
            ? "price-stat-value price-stat-value--sm price-stat-value--emerald"
            : "price-stat-value price-stat-value--sm price-stat-value--rose";
      }
    }

    if (countStatEl) {
      countStatEl.textContent = "0";
    }
    if (tbodyEl) {
      tbodyEl.innerHTML = "";
    }
    if (listEl) listEl.classList.add("is-hidden");
    if (emptyEl) emptyEl.classList.add("is-hidden");
    if (errorEl) {
      errorEl.classList.add("is-hidden");
      errorEl.textContent = "";
    }
    if (loadingEl) loadingEl.classList.remove("is-hidden");
    if (modal) modal.classList.remove("is-hidden");

    try {
      const entries = await apiFetch(
        "/api/products/" + encodeURIComponent(displayProductName) + "/price-history"
      );
      const rows = Array.isArray(entries) ? entries : [];

      if (loadingEl) loadingEl.classList.add("is-hidden");
      if (countStatEl) countStatEl.textContent = String(rows.length);

      if (rows.length === 0) {
        if (emptyEl) emptyEl.classList.remove("is-hidden");
        return;
      }

      if (
        currentPriceEl &&
        (currentPriceEl.textContent === "—" || !matchedProduct)
      ) {
        currentPriceEl.textContent = formatPrice(
          rows[0].price,
          rows[0].currency
        );
      }

      if (changeStatEl && rows.length >= 2) {
        const latest = Number(rows[0].price);
        const prev = Number(rows[1].price);
        if (Number.isFinite(latest) && Number.isFinite(prev) && latest !== prev) {
          const diff = latest - prev;
          const pct = prev > 0 ? (diff / prev) * 100 : 0;
          const sign = diff > 0 ? "+" : "-";
          const formattedDiff = formatPrice(
            Math.abs(diff),
            rows[0].currency
          );
          changeStatEl.textContent = `${diff < 0 ? "▼" : "▲"} ${sign}${formattedDiff} (${sign}${Math.abs(pct).toFixed(1)}%)`;
          changeStatEl.className =
            diff < 0
              ? "price-stat-value price-stat-value--sm price-stat-value--emerald"
              : "price-stat-value price-stat-value--sm price-stat-value--rose";
        }
      }

      if (tbodyEl) {
        tbodyEl.innerHTML = rows
          .map((entry, idx) => {
            const relTime = formatRelativeTime(entry.recorded_at);
            const exactTime = formatExactTime(entry.recorded_at);
            const currency = entry.currency || "USD";
            const formattedPrice = formatPrice(entry.price, currency);

            let deltaMarkup = `<span class="price-delta price-delta--neutral">Initial</span>`;
            if (idx < rows.length - 1) {
              const prevEntry = rows[idx + 1];
              const diff = Number(entry.price) - Number(prevEntry.price);
              if (Number.isFinite(diff)) {
                if (diff < 0) {
                  const pct =
                    Number(prevEntry.price) > 0
                      ? (Math.abs(diff) / Number(prevEntry.price)) * 100
                      : 0;
                  deltaMarkup = `<span class="price-delta price-delta--drop">▼ -${escapeHtml(
                    formatPrice(Math.abs(diff), currency)
                  )} (-${pct.toFixed(1)}%)</span>`;
                } else if (diff > 0) {
                  const pct =
                    Number(prevEntry.price) > 0
                      ? (diff / Number(prevEntry.price)) * 100
                      : 0;
                  deltaMarkup = `<span class="price-delta price-delta--up">▲ +${escapeHtml(
                    formatPrice(diff, currency)
                  )} (+${pct.toFixed(1)}%)</span>`;
                } else {
                  deltaMarkup = `<span class="price-delta price-delta--neutral">— No change</span>`;
                }
              }
            }

            return `
              <tr>
                <td class="history-time" title="${escapeHtml(exactTime)}">${escapeHtml(relTime)}</td>
                <td class="price-history-price">${escapeHtml(formattedPrice)}</td>
                <td><span class="badge badge--neutral">${escapeHtml(currency)}</span></td>
                <td>${deltaMarkup}</td>
              </tr>
            `;
          })
          .join("");
      }

      if (listEl) listEl.classList.remove("is-hidden");
    } catch (error) {
      if (loadingEl) loadingEl.classList.add("is-hidden");
      if (errorEl) {
        errorEl.textContent = error.message || "Failed to load price history.";
        errorEl.classList.remove("is-hidden");
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Settings Actions
  // ---------------------------------------------------------------------------

  async function handleSaveSettings(event) {
    event.preventDefault();

    const tokenInput = document.getElementById("setting-discord-token");
    const channelInput = document.getElementById("setting-discord-channel-id");
    const intervalInput = document.getElementById("setting-check-interval");
    const notifCheckbox = document.getElementById("setting-notifications-enabled");

    const tgEnabledCheckbox = document.getElementById("setting-telegram-enabled");
    const tgTokenInput = document.getElementById("setting-telegram-token");
    const tgChatIdInput = document.getElementById("setting-telegram-chat-id");

    const webhookEnabledCheckbox = document.getElementById("setting-webhook-enabled");
    const webhookUrlInput = document.getElementById("setting-webhook-url");

    const priceAlertsCheckbox = document.getElementById("setting-price-alerts-enabled");
    const proxyEnabledCheckbox = document.getElementById("setting-proxy-enabled");
    const proxyListTextarea = document.getElementById("setting-proxy-list");
    const domainDelayInput = document.getElementById("setting-domain-rate-limit-delay");

    const saveBtn = document.getElementById("btn-save-settings");
    const saveLabel = document.getElementById("btn-save-settings-label");

    const intervalVal = Number.parseInt(intervalInput ? intervalInput.value : "15", 10);
    if (!Number.isFinite(intervalVal) || intervalVal < 1 || intervalVal > 86400) {
      showToast("Check interval must be between 1 and 86,400 seconds.", "error");
      return;
    }

    const delayVal = Number.parseFloat(
      domainDelayInput ? domainDelayInput.value : "1.0"
    );
    if (!Number.isFinite(delayVal) || delayVal < 0 || delayVal > 300) {
      showToast(
        "Domain rate limit delay must be between 0 and 300 seconds.",
        "error"
      );
      return;
    }

    const payload = {
      discord_channel_id: channelInput ? channelInput.value.trim() : "",
      check_stock_interval: intervalVal,
      notifications_enabled: notifCheckbox ? notifCheckbox.checked : true,
      telegram_enabled: tgEnabledCheckbox ? tgEnabledCheckbox.checked : false,
      telegram_chat_id: tgChatIdInput ? tgChatIdInput.value.trim() : "",
      webhook_enabled: webhookEnabledCheckbox
        ? webhookEnabledCheckbox.checked
        : false,
      webhook_url: webhookUrlInput ? webhookUrlInput.value.trim() : "",
      price_alerts_enabled: priceAlertsCheckbox
        ? priceAlertsCheckbox.checked
        : true,
      proxy_enabled: proxyEnabledCheckbox ? proxyEnabledCheckbox.checked : false,
      proxy_list: proxyListTextarea ? proxyListTextarea.value : "",
      domain_rate_limit_delay: delayVal,
    };

    const rawToken = tokenInput ? tokenInput.value.trim() : "";
    if (rawToken) {
      payload.discord_token = rawToken;
    }

    const rawTgToken = tgTokenInput ? tgTokenInput.value.trim() : "";
    if (rawTgToken) {
      payload.telegram_bot_token = rawTgToken;
    }

    setButtonLoading(saveBtn, saveLabel, true, "Saving...", "Save Settings");

    try {
      const updated = await apiFetch("/api/settings", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.settings = updated;
      renderSettingsForm(true);
      await refreshDashboard({ silent: true });
      showToast("Settings saved successfully", "success");
    } catch (error) {
      showToast(error.message || "Failed to save settings", "error");
    } finally {
      setButtonLoading(saveBtn, saveLabel, false, "Saving...", "Save Settings");
    }
  }

  async function handleTestDiscord() {
    const tokenInput = document.getElementById("setting-discord-token");
    const channelInput = document.getElementById("setting-discord-channel-id");
    const btn = document.getElementById("btn-test-discord");
    const label = document.getElementById("btn-test-discord-label");
    const feedback = document.getElementById("test-discord-feedback");

    const payload = {};
    const rawToken = tokenInput ? tokenInput.value.trim() : "";
    const rawChannel = channelInput ? channelInput.value.trim() : "";
    if (rawToken) {
      payload.discord_token = rawToken;
    }
    if (rawChannel) {
      payload.discord_channel_id = rawChannel;
    }

    setButtonLoading(
      btn,
      label,
      true,
      "Sending Test...",
      "Send Test Discord Notification"
    );

    if (feedback) {
      feedback.className = "inline-feedback";
      feedback.textContent = "Sending test notification to Discord...";
    }

    try {
      const res = await apiFetch("/api/settings/test-discord", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      if (res.success) {
        if (feedback) {
          feedback.className = "inline-feedback inline-feedback--success";
          feedback.textContent =
            "Test notification delivered! Check your Discord channel.";
        }
        showToast("Test Discord notification sent!", "success");
      } else {
        const errMsg = res.error || "Failed to send Discord notification.";
        if (feedback) {
          feedback.className = "inline-feedback inline-feedback--error";
          feedback.textContent = errMsg;
        }
        showToast(errMsg, "error");
      }
    } catch (error) {
      const errMsg = error.message || "Discord test request failed.";
      if (feedback) {
        feedback.className = "inline-feedback inline-feedback--error";
        feedback.textContent = errMsg;
      }
      showToast(errMsg, "error");
    } finally {
      setButtonLoading(
        btn,
        label,
        false,
        "Sending Test...",
        "Send Test Discord Notification"
      );
    }
  }

  async function handleTestTelegram() {
    const tokenInput = document.getElementById("setting-telegram-token");
    const chatIdInput = document.getElementById("setting-telegram-chat-id");
    const btn = document.getElementById("btn-test-telegram");
    const label = document.getElementById("btn-test-telegram-label");
    const feedback = document.getElementById("test-telegram-feedback");

    const payload = {};
    const rawToken = tokenInput ? tokenInput.value.trim() : "";
    const rawChatId = chatIdInput ? chatIdInput.value.trim() : "";
    if (rawToken) {
      payload.telegram_bot_token = rawToken;
    }
    if (rawChatId) {
      payload.telegram_chat_id = rawChatId;
    }

    setButtonLoading(
      btn,
      label,
      true,
      "Sending Test...",
      "Send Test Telegram Notification"
    );

    if (feedback) {
      feedback.className = "inline-feedback";
      feedback.textContent = "Sending test notification to Telegram...";
    }

    try {
      const res = await apiFetch("/api/settings/test-telegram", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      if (res.success) {
        if (feedback) {
          feedback.className = "inline-feedback inline-feedback--success";
          feedback.textContent =
            "Test notification delivered! Check your Telegram chat.";
        }
        showToast("Test Telegram notification sent!", "success");
      } else {
        const errMsg = res.error || "Failed to send Telegram notification.";
        if (feedback) {
          feedback.className = "inline-feedback inline-feedback--error";
          feedback.textContent = errMsg;
        }
        showToast(errMsg, "error");
      }
    } catch (error) {
      const errMsg = error.message || "Telegram test request failed.";
      if (feedback) {
        feedback.className = "inline-feedback inline-feedback--error";
        feedback.textContent = errMsg;
      }
      showToast(errMsg, "error");
    } finally {
      setButtonLoading(
        btn,
        label,
        false,
        "Sending Test...",
        "Send Test Telegram Notification"
      );
    }
  }

  async function handleTestWebhook() {
    const urlInput = document.getElementById("setting-webhook-url");
    const btn = document.getElementById("btn-test-webhook");
    const label = document.getElementById("btn-test-webhook-label");
    const feedback = document.getElementById("test-webhook-feedback");

    const payload = {};
    const rawUrl = urlInput ? urlInput.value.trim() : "";
    if (rawUrl) {
      payload.webhook_url = rawUrl;
    }

    setButtonLoading(
      btn,
      label,
      true,
      "Sending Test...",
      "Send Test Webhook Notification"
    );

    if (feedback) {
      feedback.className = "inline-feedback";
      feedback.textContent = "Sending test webhook payload...";
    }

    try {
      const res = await apiFetch("/api/settings/test-webhook", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      if (res.success) {
        if (feedback) {
          feedback.className = "inline-feedback inline-feedback--success";
          feedback.textContent =
            "Test webhook delivered! Endpoint responded successfully.";
        }
        showToast("Test webhook notification sent!", "success");
      } else {
        const errMsg = res.error || "Failed to deliver webhook notification.";
        if (feedback) {
          feedback.className = "inline-feedback inline-feedback--error";
          feedback.textContent = errMsg;
        }
        showToast(errMsg, "error");
      }
    } catch (error) {
      const errMsg = error.message || "Webhook test request failed.";
      if (feedback) {
        feedback.className = "inline-feedback inline-feedback--error";
        feedback.textContent = errMsg;
      }
      showToast(errMsg, "error");
    } finally {
      setButtonLoading(
        btn,
        label,
        false,
        "Sending Test...",
        "Send Test Webhook Notification"
      );
    }
  }

  // ---------------------------------------------------------------------------
  // Event Wiring & Bootstrap
  // ---------------------------------------------------------------------------

  function bindEvents() {
    // Tabs
    document.querySelectorAll(".nav-tab").forEach((tabBtn) => {
      tabBtn.addEventListener("click", () => {
        if (tabBtn.dataset.tab) {
          switchTab(tabBtn.dataset.tab);
        }
      });
    });

    // Header Actions
    const refreshBtn = document.getElementById("btn-refresh");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        refreshDashboard({ silent: false, includeSettings: true });
      });
    }

    const checkAllBtn = document.getElementById("btn-check-all");
    if (checkAllBtn) {
      checkAllBtn.addEventListener("click", handleCheckAll);
    }

    const addBtn = document.getElementById("btn-add-product");
    if (addBtn) {
      addBtn.addEventListener("click", () => openProductModal(null));
    }

    const emptyAddBtn = document.getElementById("btn-empty-add-product");
    if (emptyAddBtn) {
      emptyAddBtn.addEventListener("click", () => openProductModal(null));
    }

    // Filter Pills & Search
    document.querySelectorAll(".filter-pill").forEach((pill) => {
      pill.addEventListener("click", () => {
        state.statusFilter = pill.dataset.filter || "all";
        document.querySelectorAll(".filter-pill").forEach((p) => {
          p.classList.toggle("is-active", p === pill);
        });
        renderProducts();
      });
    });

    const searchInput = document.getElementById("product-search-input");
    if (searchInput) {
      searchInput.addEventListener("input", () => {
        state.searchQuery = searchInput.value;
        renderProducts();
      });
    }

    const clearFiltersBtn = document.getElementById("btn-clear-filters");
    if (clearFiltersBtn) {
      clearFiltersBtn.addEventListener("click", () => {
        state.statusFilter = "all";
        state.searchQuery = "";
        if (searchInput) searchInput.value = "";
        document.querySelectorAll(".filter-pill").forEach((p) => {
          p.classList.toggle("is-active", p.dataset.filter === "all");
        });
        renderProducts();
      });
    }

    // Product Grid Event Delegation
    const productsGrid = document.getElementById("products-grid");
    if (productsGrid) {
      productsGrid.addEventListener("click", (event) => {
        const priceHistoryBtn = event.target.closest(".js-view-price-history");
        if (priceHistoryBtn) {
          const productId = priceHistoryBtn.dataset.productId
            ? Number(priceHistoryBtn.dataset.productId)
            : null;
          const productName = priceHistoryBtn.dataset.productName || "";
          openPriceHistoryModal(productName, productId);
          return;
        }
        const checkBtn = event.target.closest(".js-check-product");
        if (checkBtn) {
          handleCheckProduct(Number(checkBtn.dataset.id));
          return;
        }
        const editBtn = event.target.closest(".js-edit-product");
        if (editBtn) {
          const id = Number(editBtn.dataset.id);
          const product = state.products.find((p) => p.id === id);
          if (product) {
            openProductModal(product);
          }
          return;
        }
        const deleteBtn = event.target.closest(".js-delete-product");
        if (deleteBtn) {
          handleDeleteProduct(Number(deleteBtn.dataset.id));
        }
      });
    }

    // History Clear
    const clearHistoryBtn = document.getElementById("btn-clear-history");
    if (clearHistoryBtn) {
      clearHistoryBtn.addEventListener("click", handleClearHistory);
    }

    // Product Modal
    const productForm = document.getElementById("product-form");
    if (productForm) {
      productForm.addEventListener("submit", handleSaveProduct);
    }

    const testSelectorBtn = document.getElementById("btn-test-selector");
    if (testSelectorBtn) {
      testSelectorBtn.addEventListener("click", handleTestSelector);
    }

    const strategySelect = document.getElementById("product-strategy-select");
    if (strategySelect) {
      strategySelect.addEventListener("change", () => {
        syncStrategyUI();
      });
    }

    const toggleOptionalSelectorBtn = document.getElementById(
      "btn-toggle-optional-selector"
    );
    if (toggleOptionalSelectorBtn) {
      toggleOptionalSelectorBtn.addEventListener("click", () => {
        state.optionalSelectorExpanded = !state.optionalSelectorExpanded;
        syncStrategyUI();
      });
    }

    document.querySelectorAll(".preset-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const selectorInput = document.getElementById("product-selector-input");
        if (selectorInput && btn.dataset.selector) {
          selectorInput.value = btn.dataset.selector;
          selectorInput.focus();
        }
      });
    });

    const closeProductBtn = document.getElementById("btn-close-product-modal");
    const cancelProductBtn = document.getElementById("btn-cancel-product");
    const productModal = document.getElementById("product-modal");
    if (closeProductBtn) closeProductBtn.addEventListener("click", closeProductModal);
    if (cancelProductBtn) cancelProductBtn.addEventListener("click", closeProductModal);
    if (productModal) {
      productModal.addEventListener("click", (event) => {
        if (event.target === productModal) {
          closeProductModal();
        }
      });
    }

    // Confirm Modal
    const closeConfirmBtn = document.getElementById("btn-close-confirm-modal");
    const cancelConfirmBtn = document.getElementById("btn-confirm-cancel");
    const okConfirmBtn = document.getElementById("btn-confirm-ok");
    const confirmModal = document.getElementById("confirm-modal");

    if (closeConfirmBtn) closeConfirmBtn.addEventListener("click", closeConfirmModal);
    if (cancelConfirmBtn) cancelConfirmBtn.addEventListener("click", closeConfirmModal);
    if (confirmModal) {
      confirmModal.addEventListener("click", (event) => {
        if (event.target === confirmModal) {
          closeConfirmModal();
        }
      });
    }
    if (okConfirmBtn) {
      okConfirmBtn.addEventListener("click", async () => {
        const cb = state.confirmCallback;
        closeConfirmModal();
        if (typeof cb === "function") {
          await cb();
        }
      });
    }

    // Price History Modal
    const closePriceHistoryBtn = document.getElementById(
      "btn-close-price-history-modal"
    );
    const donePriceHistoryBtn = document.getElementById("btn-done-price-history");
    const priceHistoryModal = document.getElementById("price-history-modal");
    if (closePriceHistoryBtn) {
      closePriceHistoryBtn.addEventListener("click", closePriceHistoryModal);
    }
    if (donePriceHistoryBtn) {
      donePriceHistoryBtn.addEventListener("click", closePriceHistoryModal);
    }
    if (priceHistoryModal) {
      priceHistoryModal.addEventListener("click", (event) => {
        if (event.target === priceHistoryModal) {
          closePriceHistoryModal();
        }
      });
    }

    // Escape closes open modals
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeProductModal();
        closeConfirmModal();
        closePriceHistoryModal();
      }
    });

    // Settings Form
    const settingsForm = document.getElementById("settings-form");
    if (settingsForm) {
      settingsForm.addEventListener("submit", handleSaveSettings);
      settingsForm.addEventListener("input", () => {
        state.settingsDirty = true;
      });
      settingsForm.addEventListener("change", () => {
        state.settingsDirty = true;
      });
    }

    function wireTokenVisibilityToggle(btnId, inputId) {
      const toggleBtn = document.getElementById(btnId);
      const inputEl = document.getElementById(inputId);
      if (toggleBtn && inputEl) {
        toggleBtn.addEventListener("click", () => {
          const isPassword = inputEl.type === "password";
          inputEl.type = isPassword ? "text" : "password";
          toggleBtn.textContent = isPassword ? "Hide" : "Show";
        });
      }
    }

    wireTokenVisibilityToggle(
      "btn-toggle-token-visibility",
      "setting-discord-token"
    );
    wireTokenVisibilityToggle(
      "btn-toggle-telegram-token",
      "setting-telegram-token"
    );

    const testDiscordBtn = document.getElementById("btn-test-discord");
    if (testDiscordBtn) {
      testDiscordBtn.addEventListener("click", handleTestDiscord);
    }

    const testTelegramBtn = document.getElementById("btn-test-telegram");
    if (testTelegramBtn) {
      testTelegramBtn.addEventListener("click", handleTestTelegram);
    }

    const testWebhookBtn = document.getElementById("btn-test-webhook");
    if (testWebhookBtn) {
      testWebhookBtn.addEventListener("click", handleTestWebhook);
    }
  }

  function startAutoRefresh() {
    if (state.pollTimerId !== null) {
      window.clearInterval(state.pollTimerId);
    }
    state.pollTimerId = window.setInterval(() => {
      refreshDashboard({ silent: true, includeSettings: false });
    }, POLL_INTERVAL_MS);
  }

  async function init() {
    bindEvents();
    await refreshDashboard({ silent: false, includeSettings: true });
    startAutoRefresh();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
