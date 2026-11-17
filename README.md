# ReFill

Share a cooking reel with the ReFill Instagram account and get back a
ready-to-checkout grocery basket on JioMart, Blinkit, Zepto, or Instamart.

## The Instagram flow

```
user shares a recipe reel  ->  DM: "Got the reel. Reading the recipe..."
                           ->  DM: "15 products found in "Paneer Butter Masala".
                                    Which app should I fill?"
                                    [JioMart] [Blinkit] [Zepto] [Instamart]
user taps Blinkit          ->  DM: "Your Blinkit basket is ready - 15 items."
                                    https://your-host/b/AbC123xyz
```

The basket link opens a mobile page listing every ingredient with quantity, a
one-tap **Add on Blinkit** button per item, and a tick-off progress counter.
Checkout always happens inside the store.

## What Works Now

- Instagram DM bot: reel share in, store picker, hosted basket out.
- Reel reading from the caption Meta ships in the webhook, with speech-to-text
  on the reel audio when the caption is too thin to trust.
- AI-based ingredient extraction through OpenAI structured outputs.
- Hosted basket page at `/b/{token}`, JSON at `/api/basket/{token}`.
- YouTube and YouTube Shorts transcript extraction via the `/recipe` API.
- Manual transcript override for testing and unsupported sources.
- Store search links for JioMart, Zepto, Instamart, and Blinkit.
- Optional product matching when you configure your own catalog/search API.
- Static no-login UI served at `/ui/` and deployable from `frontend/`.
- Qwen/OpenAI-compatible LLM mode through `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`.
- Optional Chrome extension handoff that opens Blinkit and fills the Blinkit web cart.

### Honest limits

No quick-commerce app in India exposes a public "create a cart" API. ReFill
therefore hands you a basket where every item is one tap from the store's own
search, plus a browser extension that writes the Blinkit web cart directly. It
never places an order and never touches payment.

Reels can only be read when they are **shared into the DM** (the paper-plane
button). A pasted `instagram.com/reel/...` link gives ReFill no media access, so
the bot replies asking for a real share.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export OPENAI_API_KEY="your_api_key"
.venv/bin/uvicorn app:app --reload
```

The API will be available at `http://127.0.0.1:8000`.
The UI will be available at `http://127.0.0.1:8000/ui/`.

## Connecting the Instagram account

Instagram only delivers DM webhooks to a **professional** (Business or Creator)
account that is attached to a Meta app. There is no way around this step.

1. Convert the `@refill` account to a professional account in the Instagram app,
   then enable **Settings → Messages and story replies → Connected tools →
   Allow access to messages**.
2. Create an app at [developers.facebook.com](https://developers.facebook.com/apps)
   and add the **Instagram** product with *Instagram API setup with Instagram
   login*. Generate a token with `instagram_business_basic` and
   `instagram_business_manage_messages`.
3. Put the backend on a public HTTPS URL. For local development:
   `ngrok http 8000` or `cloudflared tunnel --url http://localhost:8000`.
4. Fill in the Instagram block of `.env` (see `.env.example`):
   `PUBLIC_BASE_URL`, `IG_APP_SECRET`, `IG_VERIFY_TOKEN`, `IG_ACCESS_TOKEN`, and
   optionally `IG_ACCOUNT_ID`.
5. In the app dashboard, set the webhook callback URL to
   `https://your-host/webhook/instagram`, paste the same `IG_VERIFY_TOKEN` as the
   verify token, and subscribe to the **messages** field.
6. Check the wiring: `curl https://your-host/health` reports an `instagram`
   block. Everything under it should read `true`.
7. DM the account from a different Instagram account and share a cooking reel.

### Security notes

- Every `POST /webhook/instagram` request is rejected unless its
  `X-Hub-Signature-256` header matches an HMAC of the raw body under
  `IG_APP_SECRET`. With no app secret configured, the endpoint refuses
  everything.
- `IG_ALLOW_UNSIGNED=true` disables that check so you can `curl` the webhook
  while developing. Never set it on a deployed backend — it lets anyone drive
  the bot and spend your LLM budget.
- Basket links use a 12-character random token and are unguessable, but they are
  also unauthenticated: anyone with the link sees the ingredient list. Nothing
  personal, payment-related, or account-related is stored in a basket.

## Environment

- `OPENAI_API_KEY`: required for OpenAI recipe extraction.
- `OPENAI_MODEL`: optional, defaults to `gpt-5.6`.
- `LLM_API_KEY`: API key for an OpenAI-compatible provider.
- `LLM_BASE_URL`: provider base URL for OpenAI-compatible chat completions.
- `LLM_MODEL`: model for OpenAI-compatible mode, for example `Qwen/Qwen3-8B` if your host uses that ID.
- `MAX_TRANSCRIPT_CHARS`: optional, defaults to `20000`.
- `CORS_ALLOW_ORIGINS`: optional comma-separated frontend origins, required when the UI is hosted separately on Vercel.
- `WEBSHARE_PROXY_USERNAME` / `WEBSHARE_PROXY_PASSWORD`: optional Webshare residential proxy credentials for YouTube transcript retrieval on cloud/server IPs.
- `WEBSHARE_PROXY_COUNTRIES`: optional comma-separated Webshare country filter, for example `gb`. Leave unset by default; a wider rotating pool can avoid country-specific 429s.
- `YOUTUBE_TRANSCRIPT_PROXY_HTTP` / `YOUTUBE_TRANSCRIPT_PROXY_HTTPS`: optional custom proxy URLs if you do not use Webshare.
- `ECOMMERCE_SEARCH_URL`: optional catalog API endpoint that accepts `?q=ingredient`.
- `ECOMMERCE_CART_URL`: optional cart API endpoint for a partner/user-authenticated integration.
- `ECOMMERCE_TOKEN`: optional bearer token for configured commerce endpoints.

Instagram bot:

- `PUBLIC_BASE_URL`: public origin used to build basket links sent over DM.
- `IG_APP_SECRET`: Meta app secret, used to verify webhook signatures.
- `IG_VERIFY_TOKEN`: shared string for Meta's webhook subscription handshake.
- `IG_ACCESS_TOKEN`: Instagram access token with `instagram_business_manage_messages`.
- `IG_ACCOUNT_ID`: Instagram professional account id, or Page id on the Facebook path. Defaults to `me`.
- `IG_GRAPH_BASE` / `IG_GRAPH_VERSION`: Graph host and version overrides.
- `IG_ALLOW_UNSIGNED`: dev-only bypass of webhook signature checks.
- `TRANSCRIBE_API_KEY` / `TRANSCRIBE_MODEL` / `TRANSCRIBE_BASE_URL`: reel audio transcription. Key falls back to `OPENAI_API_KEY`, then `LLM_API_KEY`.
- `REEL_CAPTION_TRUST_CHARS`: caption length above which audio transcription is skipped. Defaults to `220`.
- `REEL_MAX_MEDIA_BYTES`: download cap for reel media. Defaults to 25 MB.
- `REFILL_DB_PATH`: SQLite file for conversation state and baskets. Defaults to `refill.db`.
- `REFILL_SESSION_TTL_SECONDS` / `REFILL_BASKET_TTL_SECONDS`: expiry windows.

For Qwen3-8B, use an OpenAI-compatible hosting provider and set:

```bash
export LLM_API_KEY="your_provider_key"
export LLM_BASE_URL="https://your-provider.example/v1"
export LLM_MODEL="Qwen/Qwen3-8B"
```

The exact model ID depends on the provider.

## YouTube Transcript Blocking

If URL mode returns `youtube_transcript_blocked`, YouTube has blocked transcript retrieval from the backend server IP. This is common on cloud deployments and cannot be fixed only in frontend code.

Reliable options:

1. Use the UI's **Transcript** mode and paste the video's transcript text.
2. Configure a rotating residential proxy for the backend.

Webshare example:

```bash
export WEBSHARE_PROXY_USERNAME="your_webshare_username"
export WEBSHARE_PROXY_PASSWORD="your_webshare_password"
# Optional. Leave unset unless you specifically need a country filter.
# export WEBSHARE_PROXY_COUNTRIES="gb"
```

Custom proxy example:

```bash
export YOUTUBE_TRANSCRIPT_PROXY_HTTP="http://user:pass@proxy.example.com:8080"
export YOUTUBE_TRANSCRIPT_PROXY_HTTPS="https://user:pass@proxy.example.com:8080"
```

Check `/health`; it reports `transcript.youtube_proxy_configured` without exposing secrets.

## Example Request

```bash
curl -X POST http://127.0.0.1:8000/recipe \
  -H "Content-Type: application/json" \
  -d '{
    "transcript": "Add 500 g tomato, 200 g paneer, cumin seeds, and salt.",
    "store": "all"
  }'
```

For a YouTube video:

```bash
curl -X POST http://127.0.0.1:8000/recipe \
  -H "Content-Type: application/json" \
  -d '{"url": "https://youtube.com/shorts/VIDEO_ID_HERE"}'
```

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Config report for AI, transcripts, Instagram, and reels. |
| `GET` | `/webhook/instagram` | Meta subscription handshake. |
| `POST` | `/webhook/instagram` | DM events. Signature-checked, processed after the 200. |
| `GET` | `/b/{token}` | Hosted basket page, the link sent over DM. |
| `GET` | `/api/basket/{token}` | Basket JSON behind that page. |
| `POST` | `/recipe` | Direct transcript/YouTube pipeline, unchanged. |

## Test

```bash
.venv/bin/python -m unittest
```

## Frontend

The frontend is intentionally static: no login, no build step, no framework lock-in.

Local backend + UI:

```bash
.venv/bin/uvicorn app:app --reload
```

Then open `http://127.0.0.1:8000/ui/`.

Vercel UI:

1. Create a Vercel project using `frontend/` as the root directory.
2. Set the UI's API URL field to your backend URL.
3. Set backend `CORS_ALLOW_ORIGINS` to the Vercel URL.

## Blinkit Cart Handoff

True Blinkit cart prefill requires code running with permission on `blinkit.com`. ReFill includes a local Chrome extension for that handoff.

Install:

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Click **Load unpacked**.
4. Select the `extension/` folder.

Use:

1. Build a basket in ReFill, or open a basket link the bot DM'd you.
2. Click **Open Blinkit cart** in the `/ui/` builder, or **Auto-fill Blinkit
   cart** on a `/b/{token}` basket page.
3. The extension opens Blinkit, searches matching products, writes the Blinkit web cart, and redirects to the cart page.
4. If Blinkit requires login or location, complete it on Blinkit and retry from the overlay.
5. If Blinkit says **Currently unavailable**, choose a serviceable delivery location first. The extension cannot resolve or add products while Blinkit itself is unavailable for that address.

The extension only listens on `localhost` and `*.vercel.app` out of the box. If
your backend serves basket pages from another host, add that origin to both
`host_permissions` and the first `content_scripts.matches` block in
`extension/manifest.json` and reload the extension.

The extension does not place orders or handle payment. The user always checks out on Blinkit.
# ReFill
