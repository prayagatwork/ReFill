const form = document.querySelector("#recipeForm");
const sourceInput = document.querySelector("#sourceInput");
const sourceLabel = document.querySelector("#sourceLabel");
const submitButton = document.querySelector("#submitButton");
const demoButton = document.querySelector("#demoButton");
const modeButtons = [...document.querySelectorAll(".mode-option")];
const storeSelect = document.querySelector("#storeSelect");
const apiBaseInput = document.querySelector("#apiBaseInput");
const healthStatus = document.querySelector("#healthStatus");
const errorBox = document.querySelector("#errorBox");
const emptyState = document.querySelector("#emptyState");
const resultView = document.querySelector("#resultView");
const recipeName = document.querySelector("#recipeName");
const servingChip = document.querySelector("#servingChip");
const ingredientList = document.querySelector("#ingredientList");
const blinkitCartButton = document.querySelector("#blinkitCartButton");
const handoffStatus = document.querySelector("#handoffStatus");

const demoTranscript =
  "Make a quick paneer curry. Add 200 grams paneer, two tomatoes, one onion, cumin seeds, turmeric, salt, and one tablespoon oil.";

let inputMode = "url";
let latestRecipeResult = null;

apiBaseInput.value = getStoredApiBase();
checkHealth();

modeButtons.forEach((button) => {
  button.addEventListener("click", () => setMode(button.dataset.mode));
});

demoButton.addEventListener("click", () => {
  setMode("transcript");
  sourceInput.value = demoTranscript;
  sourceInput.focus();
});

apiBaseInput.addEventListener("change", () => {
  localStorage.setItem("refillApiBase", normalizeApiBase(apiBaseInput.value));
  apiBaseInput.value = getStoredApiBase();
  checkHealth();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  hideError();

  const value = sourceInput.value.trim();
  if (!value) {
    showError("Add a link or transcript first.");
    return;
  }

  const payload = {
    store: storeSelect.value,
  };

  if (inputMode === "url") {
    payload.url = value;
  } else {
    payload.transcript = value;
  }

  setLoading(true);

  try {
    const response = await fetch(`${getStoredApiBase()}/recipe`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    const body = await response.json();

    if (!response.ok) {
      const apiError = readApiError(body);

      if (isTranscriptFallbackError(apiError.code)) {
        setMode("transcript");
        sourceInput.focus();
        throw new Error(`${apiError.message} Paste the video transcript below and build again.`);
      }

      throw new Error(apiError.message);
    }

    renderResult(body);
  } catch (error) {
    showError(error.message || "Could not build basket.");
  } finally {
    setLoading(false);
  }
});

blinkitCartButton.addEventListener("click", () => {
  if (!latestRecipeResult) {
    showHandoffStatus("Build a basket first.", "bad");
    return;
  }

  const payload = buildBlinkitPayload(latestRecipeResult);
  if (!payload.items.length) {
    showHandoffStatus("No ingredients are available to send.", "bad");
    return;
  }

  showHandoffStatus("Sending basket to the ReFill extension...", "neutral");

  let acknowledged = false;
  const timeout = window.setTimeout(() => {
    if (!acknowledged) {
      showHandoffStatus(
        "Install or enable the ReFill Blinkit extension, then click again.",
        "bad",
      );
    }
  }, 1600);

  const handleMessage = (event) => {
    if (event.source !== window) {
      return;
    }

    const message = event.data || {};
    if (message.source !== "refill-extension") {
      return;
    }

    if (message.type === "REFILL_BLINKIT_CART_ACCEPTED") {
      acknowledged = true;
      window.clearTimeout(timeout);
      window.removeEventListener("message", handleMessage);
      showHandoffStatus("Opening Blinkit. The extension will fill the cart there.", "good");
    }

    if (message.type === "REFILL_BLINKIT_CART_FAILED") {
      acknowledged = true;
      window.clearTimeout(timeout);
      window.removeEventListener("message", handleMessage);
      showHandoffStatus(message.error || "The extension could not start the handoff.", "bad");
    }
  };

  window.addEventListener("message", handleMessage);
  window.postMessage(
    {
      source: "refill",
      type: "REFILL_BLINKIT_CART",
      payload,
    },
    window.location.origin,
  );
});

function setMode(mode) {
  inputMode = mode;

  modeButtons.forEach((button) => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });

  if (mode === "url") {
    sourceLabel.textContent = "YouTube or Shorts link";
    sourceInput.placeholder = "https://youtube.com/shorts/...";
    sourceInput.value = "";
    sourceInput.rows = 5;
  } else {
    sourceLabel.textContent = "Recipe transcript";
    sourceInput.placeholder = "Add the spoken recipe text here...";
    sourceInput.value = "";
    sourceInput.rows = 8;
  }
}

async function checkHealth() {
  healthStatus.textContent = "Checking API";
  healthStatus.className = "status-pill";

  try {
    const response = await fetch(`${getStoredApiBase()}/health`);
    const body = await response.json();

    if (!response.ok) {
      throw new Error("API check failed");
    }

    const model = body.ai?.model || "configured";
    healthStatus.textContent = body.ai?.configured ? `API ready: ${model}` : "Add LLM key";
    healthStatus.classList.add(body.ai?.configured ? "ok" : "bad");
  } catch {
    healthStatus.textContent = "API offline";
    healthStatus.classList.add("bad");
  }
}

function renderResult(data) {
  latestRecipeResult = data;
  const recipe = data.recipe || {};
  const items = data.cart?.items || [];

  recipeName.textContent = recipe.recipe_name || "Recipe basket";
  servingChip.textContent = recipe.servings ? `${recipe.servings} servings` : "Servings flexible";
  ingredientList.innerHTML = "";

  items.forEach((item) => {
    const ingredient = item.ingredient || {};
    const card = document.createElement("article");
    card.className = "ingredient-card";

    const quantity = formatQuantity(ingredient.quantity, ingredient.unit);
    const confidence = formatConfidence(ingredient.confidence);
    const links = item.search_links || {};
    const matched = item.matched_product?.name || item.matched_product?.title;

    card.innerHTML = `
      <div class="ingredient-top">
        <h3 class="ingredient-name">${escapeHtml(ingredient.name || "Ingredient")}</h3>
        <div class="ingredient-meta">
          <span class="mini-chip">${escapeHtml(quantity)}</span>
          <span class="mini-chip">${escapeHtml(confidence)}</span>
        </div>
      </div>
      <div class="store-links">
        ${Object.entries(links)
          .map(
            ([store, url]) =>
              `<a class="store-link" href="${escapeAttribute(url)}" target="_blank" rel="noreferrer">${formatStore(store)}</a>`,
          )
          .join("")}
      </div>
      ${
        matched
          ? `<p class="matched-product">Matched: ${escapeHtml(matched)}</p>`
          : ""
      }
    `;

    ingredientList.appendChild(card);
  });

  emptyState.hidden = true;
  resultView.hidden = false;
  hideHandoffStatus();
}

function setLoading(isLoading) {
  submitButton.disabled = isLoading;
  submitButton.textContent = isLoading ? "Building basket..." : "Build basket";
}

function showError(message) {
  errorBox.hidden = false;
  errorBox.textContent = message;
}

function buildBlinkitPayload(data) {
  const recipe = data.recipe || {};
  const items = (data.cart?.items || [])
    .map((item) => {
      const ingredient = item.ingredient || {};
      const name = String(ingredient.name || "").trim();
      if (!name) {
        return null;
      }

      return {
        name,
        requested_quantity: ingredient.quantity ?? null,
        requested_unit: ingredient.unit ?? null,
        confidence: ingredient.confidence ?? null,
        cart_quantity: 1,
      };
    })
    .filter(Boolean);

  return {
    app: "ReFill",
    version: 1,
    created_at: new Date().toISOString(),
    recipe_name: recipe.recipe_name || "Recipe basket",
    items,
  };
}

function showHandoffStatus(message, tone) {
  handoffStatus.hidden = false;
  handoffStatus.textContent = message;
  handoffStatus.className = `handoff-status ${tone}`;
}

function hideHandoffStatus() {
  handoffStatus.hidden = true;
  handoffStatus.textContent = "";
  handoffStatus.className = "handoff-status";
}

function hideError() {
  errorBox.hidden = true;
  errorBox.textContent = "";
}

function getStoredApiBase() {
  const stored = localStorage.getItem("refillApiBase");
  if (stored) {
    return stored;
  }

  if (window.location.protocol === "http:" || window.location.protocol === "https:") {
    return window.location.origin;
  }

  return "http://127.0.0.1:8000";
}

function normalizeApiBase(value) {
  const trimmed = value.trim().replace(/\/$/, "");
  return trimmed || "http://127.0.0.1:8000";
}

function readApiError(body) {
  if (body?.detail?.message) {
    return {
      code: body.detail.code || "api_error",
      message: body.detail.message,
    };
  }

  if (typeof body?.detail === "string") {
    return {
      code: "api_error",
      message: body.detail,
    };
  }

  return {
    code: "api_error",
    message: "Could not build basket.",
  };
}

function isTranscriptFallbackError(code) {
  return code === "youtube_transcript_blocked" || code === "transcript_unavailable";
}

function formatQuantity(quantity, unit) {
  if (quantity === null || quantity === undefined) {
    return "Quantity flexible";
  }

  const compact = Number.isInteger(quantity) ? String(quantity) : Number(quantity).toFixed(1);
  return unit ? `${compact} ${unit}` : compact;
}

function formatConfidence(confidence) {
  if (confidence === null || confidence === undefined) {
    return "Confidence unknown";
  }

  return `${Math.round(Number(confidence) * 100)}% sure`;
}

function formatStore(store) {
  const names = {
    jiomart: "Find on JioMart",
    zepto: "Find on Zepto",
    instamart: "Find on Instamart",
    blinkit: "Find on Blinkit",
  };

  return names[store] || store;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}
