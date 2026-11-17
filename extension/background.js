const PENDING_CART_KEY = "refillPendingBlinkitCart";

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== "REFILL_PREPARE_BLINKIT_CART") {
    return false;
  }

  const payload = sanitizePayload(message.payload);
  if (!payload.items.length) {
    sendResponse({
      ok: false,
      error: "No valid ingredients were sent to the extension.",
    });
    return false;
  }

  const pendingCart = {
    ...payload,
    received_at: new Date().toISOString(),
    source_tab_id: sender.tab?.id || null,
  };

  chrome.storage.local.set({ [PENDING_CART_KEY]: pendingCart }, () => {
    if (chrome.runtime.lastError) {
      sendResponse({
        ok: false,
        error: chrome.runtime.lastError.message,
      });
      return;
    }

    chrome.tabs.create({ url: "https://blinkit.com/cart?refill=1" }, () => {
      if (chrome.runtime.lastError) {
        sendResponse({
          ok: false,
          error: chrome.runtime.lastError.message,
        });
        return;
      }

      sendResponse({ ok: true });
    });
  });

  return true;
});

function sanitizePayload(payload) {
  const rawItems = Array.isArray(payload?.items) ? payload.items : [];
  const items = rawItems
    .map((item) => ({
      name: String(item?.name || "").trim(),
      requested_quantity: item?.requested_quantity ?? null,
      requested_unit: item?.requested_unit ?? null,
      confidence: item?.confidence ?? null,
      cart_quantity: clampQuantity(item?.cart_quantity),
    }))
    .filter((item) => item.name);

  return {
    app: "ReFill",
    version: 1,
    recipe_name: String(payload?.recipe_name || "Recipe basket").trim(),
    created_at: String(payload?.created_at || new Date().toISOString()),
    items,
  };
}

function clampQuantity(value) {
  const quantity = Number(value);
  if (!Number.isFinite(quantity)) {
    return 1;
  }

  return Math.max(1, Math.min(12, Math.round(quantity)));
}
