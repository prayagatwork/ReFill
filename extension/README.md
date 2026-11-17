# ReFill Blinkit Cart Bridge

This Chrome extension is the handoff layer between ReFill and Blinkit.

Why it exists: a normal website on the ReFill domain cannot write cart data into `blinkit.com`. The extension has explicit user-granted permission for both ReFill and Blinkit, so it can carry the ingredient basket across origins.

## Install Locally

1. Open Chrome.
2. Go to `chrome://extensions`.
3. Turn on **Developer mode**.
4. Click **Load unpacked**.
5. Select this folder: `extension/`.

## Use

1. Run ReFill and open `http://127.0.0.1:8000/ui/`.
2. Build a basket from a video or transcript.
3. Click **Open Blinkit cart**.
4. The extension opens Blinkit and tries to add matching products to the web cart.
5. If Blinkit asks for login or location, complete that and click **Retry** in the ReFill overlay.
6. If Blinkit says **Currently unavailable**, choose a different serviceable delivery location first. ReFill cannot add products when Blinkit itself cannot serve the selected address.
7. If Blinkit returns `400`, open Blinkit home/search once, confirm the delivery location there, then click **Retry**. Being logged in is not enough; Blinkit search also needs active delivery-location/session context in the current browser tab.

## Current Behavior

- Product matching is best-effort based on Blinkit search results.
- One product pack is added per ingredient by default.
- Checkout and payment are still done by the user on Blinkit.
- The extension does not read passwords, OTPs, payment data, or place orders.
- If Blinkit search is blocked by missing location, unavailable pincode, missing web session headers, or login state, the overlay shows **Set location**, **Login**, and **Retry** actions.

## Custom ReFill Domain

The manifest currently allows:

- `http://127.0.0.1/*`
- `http://localhost/*`
- `https://*.vercel.app/*`

If ReFill is deployed to a custom domain, add that domain to:

- `host_permissions`
- the first `content_scripts.matches` list

Then reload the extension from `chrome://extensions`.
