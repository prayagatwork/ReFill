(() => {
  if (window.__refillBlinkitBridgeInstalled) {
    return;
  }

  window.__refillBlinkitBridgeInstalled = true;

  window.addEventListener("message", (event) => {
    if (event.source !== window) {
      return;
    }

    const message = event.data || {};
    if (message.source !== "refill" || message.type !== "REFILL_BLINKIT_CART") {
      return;
    }

    chrome.runtime.sendMessage(
      {
        type: "REFILL_PREPARE_BLINKIT_CART",
        payload: message.payload,
      },
      (response) => {
        if (chrome.runtime.lastError) {
          postToPage("REFILL_BLINKIT_CART_FAILED", {
            error: chrome.runtime.lastError.message,
          });
          return;
        }

        if (!response?.ok) {
          postToPage("REFILL_BLINKIT_CART_FAILED", {
            error: response?.error || "The extension could not start the handoff.",
          });
          return;
        }

        postToPage("REFILL_BLINKIT_CART_ACCEPTED");
      },
    );
  });

  function postToPage(type, extra = {}) {
    window.postMessage(
      {
        source: "refill-extension",
        type,
        ...extra,
      },
      window.location.origin,
    );
  }
})();
