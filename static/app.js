(() => {
  "use strict";

  const POLL_INTERVAL_MS = 10000;
  const DEFAULT_SELECTOR = "button[label='Add to Cart']";

  const state = {
    activeTab: "products",
    statusFilter: "all",
    searchQuery: "",
    status: null,
    products: [],
    history: [],
    settings: null,
    editingProductId: null,
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
        const haystack = `${product.name || ""} ${product.url || ""} ${product.selector || ""}`.toLowerCase();
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
        const safeSelector = escapeHtml(product.selector);
        const cardStateClass = product.in_stock
          ? "product-card--in-stock"
          : "product-card--out-of-stock";

        const statusBadge = product.last_error
          ? `<span class="badge badge--error" title="${escapeHtml(product.last_error)}"><span class="badge-dot"></span>Error</span>`
          : product.in_stock
            ? `<span class="badge badge--in-stock"><span class="badge-dot"></span>In Stock</span>`
            : `<span class="badge badge--out-of-stock"><span class="badge-dot"></span>Out of Stock</span>`;

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
              ${statusBadge}
            </div>

            <div class="product-meta">
              <div class="selector-row" title="CSS Selector: ${safeSelector}">
                <span class="meta-label">Selector</span>
                <code class="selector-pill">${safeSelector}</code>
              </div>

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
  // Product Modal (Add / Edit) & Test Selector
  // ---------------------------------------------------------------------------

  function openProductModal(product = null) {
    const modal = document.getElementById("product-modal");
    const titleEl = document.getElementById("product-modal-title");
    const nameInput = document.getElementById("product-name-input");
    const urlInput = document.getElementById("product-url-input");
    const selectorInput = document.getElementById("product-selector-input");
    const saveLabel = document.getElementById("btn-save-product-label");
    const testResult = document.getElementById("selector-test-result");
    const formError = document.getElementById("product-form-error");

    state.editingProductId = product ? product.id : null;

    if (titleEl) {
      titleEl.textContent = product ? "Edit Product" : "Add Product";
    }
    if (saveLabel) {
      saveLabel.textContent = product ? "Update Product" : "Save Product";
    }
    if (nameInput) nameInput.value = product ? product.name : "";
    if (urlInput) urlInput.value = product ? product.url : "";
    if (selectorInput) {
      selectorInput.value = product ? product.selector : DEFAULT_SELECTOR;
    }

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
  }

  async function handleTestSelector() {
    const urlInput = document.getElementById("product-url-input");
    const selectorInput = document.getElementById("product-selector-input");
    const btn = document.getElementById("btn-test-selector");
    const btnLabel = document.getElementById("btn-test-selector-label");
    const resultEl = document.getElementById("selector-test-result");

    const url = urlInput ? urlInput.value.trim() : "";
    const selector = (selectorInput ? selectorInput.value.trim() : "") || DEFAULT_SELECTOR;

    if (!url) {
      if (resultEl) {
        resultEl.className = "inline-feedback inline-feedback--error";
        resultEl.textContent = "Please enter a valid product URL before testing.";
      }
      return;
    }

    setButtonLoading(btn, btnLabel, true, "Testing...", "Test Selector");
    if (resultEl) {
      resultEl.className = "inline-feedback";
      resultEl.textContent = "Fetching page and evaluating CSS selector...";
    }

    try {
      const res = await apiFetch("/api/products/test-selector", {
        method: "POST",
        body: JSON.stringify({ url, selector }),
      });

      if (!resultEl) {
        return;
      }

      if (res.error) {
        resultEl.className = "inline-feedback inline-feedback--error";
        resultEl.textContent = `Selector test error: ${res.error}`;
      } else if (res.found_selector && res.in_stock) {
        resultEl.className = "inline-feedback inline-feedback--success";
        resultEl.textContent =
          "Selector matched! Product currently appears IN STOCK.";
      } else {
        resultEl.className = "inline-feedback inline-feedback--warning";
        resultEl.textContent =
          "Page fetched successfully, but selector was NOT found (currently Out of Stock or selector mismatch).";
      }
    } catch (error) {
      if (resultEl) {
        resultEl.className = "inline-feedback inline-feedback--error";
        resultEl.textContent = error.message || "Selector test failed.";
      }
    } finally {
      setButtonLoading(btn, btnLabel, false, "Testing...", "Test Selector");
    }
  }

  async function handleSaveProduct(event) {
    event.preventDefault();

    const nameInput = document.getElementById("product-name-input");
    const urlInput = document.getElementById("product-url-input");
    const selectorInput = document.getElementById("product-selector-input");
    const saveBtn = document.getElementById("btn-save-product");
    const saveLabel = document.getElementById("btn-save-product-label");
    const formError = document.getElementById("product-form-error");

    const isEditing = state.editingProductId !== null;
    const idleText = isEditing ? "Update Product" : "Save Product";

    const payload = {
      name: nameInput ? nameInput.value.trim() : "",
      url: urlInput ? urlInput.value.trim() : "",
      selector: (selectorInput ? selectorInput.value.trim() : "") || DEFAULT_SELECTOR,
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
  // Settings Actions
  // ---------------------------------------------------------------------------

  async function handleSaveSettings(event) {
    event.preventDefault();

    const tokenInput = document.getElementById("setting-discord-token");
    const channelInput = document.getElementById("setting-discord-channel-id");
    const intervalInput = document.getElementById("setting-check-interval");
    const notifCheckbox = document.getElementById("setting-notifications-enabled");
    const saveBtn = document.getElementById("btn-save-settings");
    const saveLabel = document.getElementById("btn-save-settings-label");

    const intervalVal = Number.parseInt(intervalInput ? intervalInput.value : "15", 10);
    if (!Number.isFinite(intervalVal) || intervalVal < 1 || intervalVal > 86400) {
      showToast("Check interval must be between 1 and 86,400 seconds.", "error");
      return;
    }

    const payload = {
      discord_channel_id: channelInput ? channelInput.value.trim() : "",
      check_stock_interval: intervalVal,
      notifications_enabled: notifCheckbox ? notifCheckbox.checked : true,
    };

    const rawToken = tokenInput ? tokenInput.value.trim() : "";
    if (rawToken) {
      payload.discord_token = rawToken;
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

    // Escape closes open modals
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeProductModal();
        closeConfirmModal();
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

    const toggleTokenBtn = document.getElementById("btn-toggle-token-visibility");
    const tokenInput = document.getElementById("setting-discord-token");
    if (toggleTokenBtn && tokenInput) {
      toggleTokenBtn.addEventListener("click", () => {
        const isPassword = tokenInput.type === "password";
        tokenInput.type = isPassword ? "text" : "password";
        toggleTokenBtn.textContent = isPassword ? "Hide" : "Show";
      });
    }

    const testDiscordBtn = document.getElementById("btn-test-discord");
    if (testDiscordBtn) {
      testDiscordBtn.addEventListener("click", handleTestDiscord);
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
