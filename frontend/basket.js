const STORE_HOME = {
  jiomart: "https://www.jiomart.com/",
  blinkit: "https://blinkit.com/",
  zepto: "https://www.zeptonow.com/",
  instamart: "https://www.swiggy.com/instamart",
};

const storeChip = document.querySelector("#storeChip");
const recipeTitle = document.querySelector("#recipeTitle");
const recipeSub = document.querySelector("#recipeSub");
const noticeBox = document.querySelector("#noticeBox");
const itemList = document.querySelector("#itemList");
const progressCount = document.querySelector("#progressCount");
const openStoreButton = document.querySelector("#openStoreButton");
const extensionBlock = document.querySelector("#extensionBlock");
const autofillButton = document.querySelector("#autofillButton");
const handoffNote = document.querySelector("#handoffNote");

const token = window.location.pathname.split("/").filter(Boolean).pop() || "";
const progressKey = `refillBasketProgress:${token}`;

let basket = null;
let done = loadProgress();

load();

async function load() {
  if (!token) {
    showNotice("This basket link is missing its id.");
    return;
  }

  try {
    const response = await fetch(`/api/basket/${encodeURIComponent(token)}`);
    const body = await response.json();

    if (!response.ok) {
      throw new Error(body?.detail?.message || "This basket link has expired.");
    }

    basket = body;
    render();
  } catch (error) {
    recipeTitle.textContent = "Basket unavailable";
    recipeSub.textContent = "Share the recipe reel again and ReFill will rebuild it.";
    showNotice(error.message);
  }
}

function render() {
  const items = basket.items || [];
  const storeName = basket.store_name || basket.store || "your store";

  storeChip.textContent = storeName;
  recipeTitle.textContent = basket.recipe_name || "Recipe basket";
  recipeSub.textContent = [
    `${items.length} ${items.length === 1 ? "item" : "items"}`,
    basket.servings ? `${basket.servings} servings` : null,
    `for ${storeName}`,
  ]
    .filter(Boolean)
    .join(" · ");

  openStoreButton.textContent = `Open ${storeName}`;
  openStoreButton.href = basket.capability?.home_url || STORE_HOME[basket.store] || "#";

  itemList.replaceChildren(...items.map((item, index) => buildItem(item, index, storeName)));

  const canCartFill = Boolean(basket.capability?.can_cart_fill);
  extensionBlock.hidden = !canCartFill;
  if (canCartFill) {
    extensionBlock.querySelector("h2").textContent = `Fill the ${storeName} cart automatically`;
    extensionBlock.querySelector("p").textContent =
      `Install the ReFill browser extension and it will search ${storeName} for ` +
      `each item and write them straight into your ${storeName} cart.`;
    autofillButton.textContent = `Auto-fill ${storeName} cart`;
  }

  updateProgress();
}

function buildItem(item, index, storeName) {
  const row = document.createElement("li");
  row.className = "item";

  const check = document.createElement("input");
  check.type = "checkbox";
  check.checked = done.includes(index);
  check.setAttribute("aria-label", `Mark ${item.name} as added`);
  check.addEventListener("change", () => toggle(index, check.checked));

  const body = document.createElement("div");
  body.className = "item-body";

  const name = document.createElement("span");
  name.className = "item-name";
  name.textContent = item.name;
  body.appendChild(name);

  const meta = document.createElement("div");
  meta.className = "item-meta";
  const quantity = formatQuantity(item.quantity, item.unit);
  if (quantity) {
    meta.appendChild(chip(quantity));
  }
  const matched = item.matched_product?.name || item.matched_product?.title;
  if (matched) {
    meta.appendChild(chip(matched));
  }
  if (meta.childElementCount) {
    body.appendChild(meta);
  }

  const add = document.createElement("a");
  add.className = "item-add";
  add.href = item.search_url || STORE_HOME[basket.store] || "#";
  add.target = "_blank";
  add.rel = "noreferrer";
  add.textContent = `Add on ${storeName}`;
  add.addEventListener("click", () => {
    if (!done.includes(index)) {
      check.checked = true;
      toggle(index, true);
    }
  });

  row.append(check, body, add);
  row.classList.toggle("done", check.checked);
  return row;
}

function chip(text) {
  const node = document.createElement("span");
  node.textContent = text;
  return node;
}

function toggle(index, isDone) {
  done = isDone ? [...new Set([...done, index])] : done.filter((value) => value !== index);
  localStorage.setItem(progressKey, JSON.stringify(done));

  const row = itemList.children[index];
  if (row) {
    row.classList.toggle("done", isDone);
  }
  updateProgress();
}

function updateProgress() {
  const total = (basket?.items || []).length;
  progressCount.textContent = `${done.filter((i) => i < total).length}/${total}`;
}

function loadProgress() {
  try {
    const stored = JSON.parse(localStorage.getItem(progressKey) || "[]");
    return Array.isArray(stored) ? stored.filter((value) => Number.isInteger(value)) : [];
  } catch {
    return [];
  }
}

function formatQuantity(quantity, unit) {
  if (quantity === null || quantity === undefined) {
    return unit ? String(unit) : "";
  }

  const compact = Number.isInteger(quantity) ? String(quantity) : Number(quantity).toFixed(1);
  return unit ? `${compact} ${unit}` : compact;
}

function showNotice(message) {
  noticeBox.hidden = false;
  noticeBox.textContent = message;
}

autofillButton.addEventListener("click", () => {
  if (!basket) {
    return;
  }

  setHandoff("Handing the basket to the ReFill extension…", "");

  let acknowledged = false;
  const timer = window.setTimeout(() => {
    if (!acknowledged) {
      setHandoff("Install or enable the ReFill extension, then tap again.", "bad");
    }
  }, 1600);

  const onMessage = (event) => {
    if (event.source !== window || event.data?.source !== "refill-extension") {
      return;
    }

    if (event.data.type === "REFILL_BLINKIT_CART_ACCEPTED") {
      acknowledged = true;
      window.clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      setHandoff("Opening Blinkit. The extension will fill the cart there.", "good");
    }

    if (event.data.type === "REFILL_BLINKIT_CART_FAILED") {
      acknowledged = true;
      window.clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      setHandoff(event.data.error || "The extension could not start the handoff.", "bad");
    }
  };

  window.addEventListener("message", onMessage);
  window.postMessage(
    {
      source: "refill",
      type: "REFILL_BLINKIT_CART",
      payload: {
        app: "ReFill",
        version: 1,
        created_at: new Date().toISOString(),
        recipe_name: basket.recipe_name || "Recipe basket",
        items: (basket.items || []).map((item) => ({
          name: item.name,
          requested_quantity: item.quantity ?? null,
          requested_unit: item.unit ?? null,
          confidence: item.confidence ?? null,
          cart_quantity: 1,
        })),
      },
    },
    window.location.origin,
  );
});

function setHandoff(message, tone) {
  handoffNote.hidden = false;
  handoffNote.textContent = message;
  handoffNote.className = `handoff-note ${tone}`;
}
