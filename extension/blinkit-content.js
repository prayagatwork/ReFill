(() => {
  const PENDING_CART_KEY = "refillPendingBlinkitCart";
  const LAST_HANDOFF_KEY = "refillLastBlinkitHandoff";
  const CART_STORAGE_KEY = "cart";
  const BLINKIT_CART_URL = "https://blinkit.com/cart";
  const SEARCH_LIMIT = 12;

  if (window.__refillBlinkitCartRunnerInstalled) {
    return;
  }

  window.__refillBlinkitCartRunnerInstalled = true;

  chrome.storage.local.get(PENDING_CART_KEY, (result) => {
    const pendingCart = result[PENDING_CART_KEY];
    if (!pendingCart?.items?.length) {
      return;
    }

    runHandoff(pendingCart);
  });

  async function runHandoff(pendingCart) {
    const overlay = createOverlay(pendingCart);
    overlay.setStatus("Resolving Blinkit products...");

    try {
      assertBlinkitCanSearch();

      const resolved = [];
      const unmatched = [];

      for (const item of pendingCart.items) {
        overlay.setStatus(`Finding ${item.name}...`);
        const product = await searchBestProduct(item.name);

        if (product) {
          resolved.push({
            ingredient: item,
            product,
            quantity: item.cart_quantity || 1,
          });
        } else {
          unmatched.push(item);
        }
      }

      if (!resolved.length) {
        overlay.showError(
          "No matching Blinkit products were found. Confirm your delivery location, then retry.",
        );
        overlay.showRecoveryActions(() => runHandoff(pendingCart));
        return;
      }

      overlay.setStatus("Writing Blinkit web cart...");
      const cart = writeBlinkitCart(resolved);
      const summary = {
        recipe_name: pendingCart.recipe_name,
        resolved_count: resolved.length,
        unmatched: unmatched.map((item) => item.name),
        cart,
        completed_at: new Date().toISOString(),
      };

      chrome.storage.local.set({ [LAST_HANDOFF_KEY]: summary }, () => {
        chrome.storage.local.remove(PENDING_CART_KEY);
      });

      overlay.showSuccess(resolved.length, unmatched);
      setTimeout(() => {
        window.location.assign(BLINKIT_CART_URL);
      }, 1400);
    } catch (error) {
      overlay.showError(error.message || "ReFill could not fill the Blinkit cart.");
      overlay.showRecoveryActions(() => runHandoff(pendingCart));
    }
  }

  async function searchBestProduct(query) {
    const context = readBlinkitSessionContext();
    assertBlinkitCanSearch(context);

    const payload = {
      applied_filters: null,
      sort: "",
      postback_meta: {},
      previous_search_query: "",
      vertical_cards_processed: 0,
    };
    const attempts = buildSearchAttempts(query);
    const headers = buildBlinkitHeaders(context);
    let data = null;
    let lastFailure = null;

    for (const url of attempts) {
      let response;

      try {
        response = await fetch(url, {
          method: "POST",
          credentials: "include",
          headers,
          body: JSON.stringify(payload),
        });
      } catch {
        lastFailure = {
          status: "network",
          body: "",
        };
        continue;
      }

      if (response.ok) {
        data = await response.json();
        break;
      }

      lastFailure = {
        status: response.status,
        body: await readResponseSnippet(response),
      };
    }

    if (!data) {
      throw handoffError(describeSearchFailure(lastFailure, context));
    }

    const cartItems = findCartItems(data);
    const candidates = cartItems
      .map((cartItem) => normalizeCartItem(cartItem))
      .filter((cartItem) => cartItem.product_id && cartItem.name);

    if (!candidates.length) {
      return null;
    }

    candidates.sort((a, b) => scoreProduct(query, b) - scoreProduct(query, a));
    return candidates[0];
  }

  function buildSearchAttempts(query) {
    const encoded = encodeURIComponent(query);
    return [
      `/v1/layout/search?q=${encoded}&search_type=type_to_search`,
      `/v1/layout/search?offset=0&limit=${SEARCH_LIMIT}&q=${encoded}&search_type=type_to_search&search_method=basic`,
      `/v1/layout/search?offset=0&limit=${SEARCH_LIMIT}&actual_query=${encoded}&page_index=1&q=${encoded}&search_count=0&search_method=basic&search_type=type_to_search`,
    ];
  }

  function buildBlinkitHeaders(context) {
    const headers = {
      "content-type": "application/json",
      accept: "application/json, text/plain, */*",
      app_client: "consumer_web",
      platform: "web",
      "x-requested-with": "XMLHttpRequest",
    };

    if (context.lat) {
      headers.lat = context.lat;
    }
    if (context.lon) {
      headers.lon = context.lon;
    }
    if (context.authKey) {
      headers.auth_key = context.authKey;
    }
    if (context.accessToken) {
      headers.access_token = context.accessToken;
    }
    if (context.deviceId) {
      headers.device_id = context.deviceId;
    }
    if (context.sessionUuid) {
      headers.session_uuid = context.sessionUuid;
    }

    return headers;
  }

  async function readResponseSnippet(response) {
    const contentType = response.headers.get("content-type") || "";
    let value = "";

    try {
      if (contentType.includes("application/json")) {
        value = JSON.stringify(await response.json());
      } else {
        value = await response.text();
      }
    } catch {
      return "";
    }

    return String(value || "")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 260);
  }

  function describeSearchFailure(failure, context) {
    const status = failure?.status || "unknown";
    const details = failure?.body ? ` Blinkit said: ${failure.body}` : "";
    const missingContext = [];

    if (!context.lat || !context.lon) {
      missingContext.push("delivery coordinates");
    }
    if (!context.authKey) {
      missingContext.push("web auth key");
    }

    const contextHint = missingContext.length
      ? ` ReFill could not read ${missingContext.join(" and ")} from this Blinkit tab.`
      : "";

    if (status === 400) {
      return (
        "Blinkit rejected the search request with 400." +
        contextHint +
        " Login alone is not enough; Blinkit must have a serviceable delivery location selected." +
        details +
        " Open Blinkit home/search once, confirm delivery location, then click Retry."
      );
    }

    if (status === "network") {
      return (
        "Blinkit search could not run in this session." +
        contextHint +
        " Refresh Blinkit, confirm delivery location, then retry."
      );
    }

    return (
      `Blinkit search returned ${status}.` +
      contextHint +
      " Set a serviceable delivery location, log in if Blinkit asks, then retry." +
      details
    );
  }

  function assertBlinkitCanSearch(context = readBlinkitSessionContext()) {
    const text = document.body?.innerText || "";
    if (/currently unavailable/i.test(text)) {
      throw handoffError(
        "Blinkit is currently unavailable for the selected location. Choose a serviceable delivery location on Blinkit, then retry.",
      );
    }

    if (/select location/i.test(text) && !/my cart/i.test(text)) {
      throw handoffError(
        "Blinkit needs a delivery location before ReFill can resolve products. Set location, then retry.",
      );
    }

    if (!context.lat || !context.lon) {
      const locationText = findLocationText();
      if (!locationText || /select location|currently unavailable/i.test(locationText)) {
        throw handoffError(
          "Blinkit is logged in, but no serviceable delivery location is active in this tab. Open Blinkit, set delivery location, then retry.",
        );
      }
    }
  }

  function handoffError(message) {
    const error = new Error(message);
    error.name = "ReFillBlinkitHandoffError";
    return error;
  }

  function findCartItems(value, output = []) {
    if (!value || typeof value !== "object") {
      return output;
    }

    const cartItem = value?.atc_action?.add_to_cart?.cart_item;
    if (cartItem && typeof cartItem === "object") {
      output.push(cartItem);
    }

    if (Array.isArray(value)) {
      value.forEach((item) => findCartItems(item, output));
      return output;
    }

    Object.values(value).forEach((item) => findCartItems(item, output));
    return output;
  }

  function normalizeCartItem(cartItem) {
    const productId = String(cartItem.product_id || cartItem.id || "").trim();
    const name = String(
      cartItem.product_name ||
        cartItem.display_name ||
        cartItem.name ||
        cartItem.group_name ||
        "",
    ).trim();
    const price = numberFromRupees(
      cartItem.price || cartItem.normal_price || cartItem.final_price,
    );
    const mrp = numberFromRupees(cartItem.mrp || cartItem.price || price);
    const imageUrl =
      cartItem.image_url ||
      cartItem.image ||
      cartItem.asset_url ||
      cartItem.assets?.[0]?.url ||
      "";

    return {
      product_id: productId,
      name,
      unit: String(cartItem.unit || cartItem.variant || cartItem.quantity || "").trim(),
      price,
      mrp,
      image_url: imageUrl,
      group_id: String(cartItem.group_id || cartItem.groupId || productId),
      brand: cartItem.brand || cartItem.brand_name || "",
      raw: cartItem,
    };
  }

  function writeBlinkitCart(resolved) {
    const existingCart = readExistingCart();
    const items = {};
    let count = 0;
    let total = 0;

    resolved.forEach(({ product, quantity }) => {
      const productId = String(product.product_id);
      const cartQuantity = Math.max(1, Math.round(Number(quantity) || 1));
      count += cartQuantity;
      total += product.price * cartQuantity;
      items[productId] = {
        product: {
          product_id: productId,
          id: productId,
          price: product.price,
          image_url: product.image_url,
          name: product.name,
          unit: product.unit,
          mrp: product.mrp,
          group_id: product.group_id,
        },
        quantity: cartQuantity,
      };
    });

    const cart = {
      ...existingCart,
      count,
      total,
      items,
      uniqueSkuInCart: Object.keys(items).length,
      version: Number(existingCart.version || 0) + 1,
      updated_at: Date.now(),
      refill_source: "ReFill",
    };

    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify(cart));
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: CART_STORAGE_KEY,
        newValue: JSON.stringify(cart),
      }),
    );

    return cart;
  }

  function readExistingCart() {
    try {
      const rawCart = localStorage.getItem(CART_STORAGE_KEY);
      if (!rawCart) {
        return {};
      }

      const parsed = JSON.parse(rawCart);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      return {};
    }
  }

  function scoreProduct(query, product) {
    const queryTokens = tokenize(query);
    const nameTokens = tokenize(`${product.name} ${product.brand} ${product.unit}`);
    const overlap = queryTokens.filter((token) => nameTokens.includes(token)).length;
    const exact = product.name.toLowerCase().includes(query.toLowerCase()) ? 4 : 0;
    return exact + overlap * 2 - Math.max(0, nameTokens.length - queryTokens.length) * 0.04;
  }

  function tokenize(value) {
    return String(value || "")
      .toLowerCase()
      .match(/[a-z0-9]+/g) || [];
  }

  function numberFromRupees(value) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }

    const match = String(value || "").match(/\d+(?:\.\d+)?/);
    return match ? Number(match[0]) : 0;
  }

  function readBlinkitSessionContext() {
    const values = readBrowserStateValues();
    const cookies = parseCookieMap();

    return {
      lat: findCoordinate(values, /(^|[._-])(lat|latitude)([._-]|$)/i, -90, 90),
      lon: findCoordinate(values, /(^|[._-])(lon|lng|longitude)([._-]|$)/i, -180, 180),
      authKey: findStringValue(values, /(^|[._-])auth[_-]?key([._-]|$)/i),
      accessToken:
        cookies.gr_1_accessToken ||
        cookies.access_token ||
        findStringValue(values, /(^|[._-])(access[_-]?token|accessToken)([._-]|$)/i),
      deviceId: findStringValue(values, /(^|[._-])device[_-]?id([._-]|$)/i),
      sessionUuid: findStringValue(
        values,
        /(^|[._-])(session[_-]?uuid|sessionUuid)([._-]|$)/i,
      ),
    };
  }

  function readBrowserStateValues() {
    const values = [];
    addStorageValues(values, window.localStorage, "localStorage");
    addStorageValues(values, window.sessionStorage, "sessionStorage");

    const cookies = parseCookieMap();
    Object.entries(cookies).forEach(([key, value]) => {
      values.push({
        key,
        path: `cookie.${key}`,
        value,
      });
    });

    return values;
  }

  function addStorageValues(values, storage, storageName) {
    try {
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (!key) {
          continue;
        }

        const value = storage.getItem(key);
        values.push({
          key,
          path: `${storageName}.${key}`,
          value,
        });

        const parsed = parseMaybeJson(value);
        if (parsed !== value) {
          flattenState(values, parsed, `${storageName}.${key}`, 0);
        }
      }
    } catch {
      // Ignore storage access failures. Blinkit can still reject with a useful error.
    }
  }

  function flattenState(values, value, path, depth) {
    if (depth > 5 || value === null || value === undefined) {
      return;
    }

    if (typeof value !== "object") {
      const key = path.split(".").pop() || path;
      values.push({
        key,
        path,
        value,
      });
      return;
    }

    if (Array.isArray(value)) {
      value.slice(0, 25).forEach((item, index) => {
        flattenState(values, item, `${path}.${index}`, depth + 1);
      });
      return;
    }

    Object.entries(value).forEach(([key, child]) => {
      flattenState(values, child, `${path}.${key}`, depth + 1);
    });
  }

  function parseCookieMap() {
    return Object.fromEntries(
      document.cookie
        .split(";")
        .map((part) => part.trim())
        .filter(Boolean)
        .map((part) => {
          const index = part.indexOf("=");
          if (index === -1) {
            return [part, ""];
          }

          const key = part.slice(0, index);
          const value = decodeURIComponent(part.slice(index + 1));
          return [key, value];
        }),
    );
  }

  function parseMaybeJson(value) {
    if (typeof value !== "string") {
      return value;
    }

    const trimmed = value.trim();
    if (!trimmed || !/^[{[]/.test(trimmed)) {
      return value;
    }

    try {
      return JSON.parse(trimmed);
    } catch {
      return value;
    }
  }

  function findCoordinate(values, pattern, min, max) {
    for (const item of values) {
      if (!pattern.test(item.path) && !pattern.test(item.key)) {
        continue;
      }

      const number = Number(String(item.value).trim());
      if (Number.isFinite(number) && number >= min && number <= max) {
        return String(number);
      }
    }

    return "";
  }

  function findStringValue(values, pattern) {
    const found = values.find((item) => {
      if (!pattern.test(item.path) && !pattern.test(item.key)) {
        return false;
      }

      const value = String(item.value || "").trim();
      return value && value.length < 600 && value !== "null" && value !== "undefined";
    });

    return found ? String(found.value).trim() : "";
  }

  function findLocationText() {
    const target = findClickableByText(/currently unavailable|select location|delivery location|deliver to/i);
    return (target?.innerText || target?.textContent || "").trim();
  }

  function createOverlay(pendingCart) {
    const existing = document.querySelector("#refill-blinkit-overlay");
    if (existing) {
      existing.remove();
    }

    const root = document.createElement("div");
    root.id = "refill-blinkit-overlay";
    root.innerHTML = `
      <div class="refill-bridge-card">
        <div class="refill-bridge-title">ReFill is preparing your Blinkit cart</div>
        <div class="refill-bridge-recipe">${escapeHtml(pendingCart.recipe_name || "Recipe basket")}</div>
        <div class="refill-bridge-status">Starting...</div>
        <div class="refill-bridge-actions"></div>
      </div>
    `;

    const style = document.createElement("style");
    style.textContent = `
      #refill-blinkit-overlay {
        position: fixed;
        inset: 18px 18px auto auto;
        z-index: 2147483647;
        max-width: min(420px, calc(100vw - 36px));
        font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      .refill-bridge-card {
        border: 1px solid rgba(12, 131, 31, 0.18);
        border-radius: 12px;
        background: #ffffff;
        box-shadow: 0 22px 70px rgba(0, 0, 0, 0.16);
        padding: 16px;
        color: #17201c;
      }
      .refill-bridge-title {
        font-size: 16px;
        font-weight: 850;
        line-height: 1.2;
      }
      .refill-bridge-recipe {
        margin-top: 4px;
        color: #65726d;
        font-size: 13px;
        font-weight: 700;
      }
      .refill-bridge-status {
        margin-top: 12px;
        padding: 11px 12px;
        border-radius: 9px;
        background: rgba(12, 131, 31, 0.08);
        color: #0c831f;
        font-size: 13px;
        font-weight: 750;
        line-height: 1.45;
      }
      .refill-bridge-status.error {
        background: rgba(239, 77, 63, 0.1);
        color: #94271f;
      }
      .refill-bridge-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 12px;
      }
      .refill-bridge-actions button {
        min-height: 36px;
        border: 0;
        border-radius: 8px;
        padding: 0 12px;
        color: #ffffff;
        background: #17201c;
        font: inherit;
        font-weight: 800;
        cursor: pointer;
      }
      .refill-bridge-actions button.secondary {
        color: #17201c;
        background: rgba(12, 131, 31, 0.1);
      }
    `;

    document.documentElement.appendChild(style);
    document.documentElement.appendChild(root);

    const status = root.querySelector(".refill-bridge-status");
    const actions = root.querySelector(".refill-bridge-actions");

    return {
      setStatus(message) {
        status.classList.remove("error");
        status.textContent = message;
        actions.innerHTML = "";
      },
      showError(message) {
        status.classList.add("error");
        status.textContent = message;
      },
      showSuccess(count, unmatched) {
        status.classList.remove("error");
        const suffix = unmatched.length
          ? ` ${unmatched.length} item(s) need manual search: ${unmatched.map((item) => item.name).join(", ")}.`
          : " Redirecting to cart.";
        status.textContent = `Added ${count} matching item(s) to Blinkit web cart.${suffix}`;
        actions.innerHTML = "";
      },
      showRecoveryActions(onRetry) {
        actions.innerHTML = "";
        actions.appendChild(makeActionButton("Set location", clickBlinkitLocationControl));
        actions.appendChild(makeActionButton("Login", clickBlinkitLogin, true));
        actions.appendChild(makeActionButton("Retry", onRetry));
      },
    };
  }

  function makeActionButton(label, onClick, secondary = false) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    if (secondary) {
      button.className = "secondary";
    }
    button.addEventListener("click", onClick);
    return button;
  }

  function clickBlinkitLocationControl() {
    const target = findClickableByText(/currently unavailable|select location|delivery location|deliver to/i);
    if (target) {
      target.click();
      return;
    }

    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function clickBlinkitLogin() {
    const target = findClickableByText(/^login$|log in/i);
    if (target) {
      target.click();
    }
  }

  function findClickableByText(pattern) {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    let node = walker.currentNode;

    while (node) {
      const text = (node.innerText || node.textContent || "").trim();
      if (text && text.length < 180 && pattern.test(text)) {
        return (
          node.closest("button, a, [role='button'], [tabindex], div") || node
        );
      }

      node = walker.nextNode();
    }

    return null;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }
})();
