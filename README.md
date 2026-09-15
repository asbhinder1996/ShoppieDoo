# ShoppieDoo

A Telegram bot that turns a product photo and a casual caption into a real Shopify product listing, drafted by Claude.

Send a photo with a price and description as the caption → Claude drafts a title, description, and tags → you approve with one tap → a real, active product appears in your Shopify store.

![Telegram draft with Approve button](docs/Telegram%20Draft%20with%20Approve%20Button.png)

## Command flow

```
/create_listing
  -> "Send me a photo of your product, with the price and a short
      description as the caption."
```
![Create listing initiation step](docs/Create%20Listing%20Initiation%20Step.png)

```
[send a photo captioned "$35, hand-poured soy candle, lavender"]
  -> "Drafting your listing..."
```
![User submission](docs/User%20Submission.png)

```
  -> Title / Price / Tags / Description / Image, with a ✅ Approve button
```
![Telegram draft with Approve button](docs/Telegram%20Draft%20with%20Approve%20Button.png)

```
[tap Approve]
  -> "Creating the product in Shopify..."
  -> "Product created: https://your-store.myshopify.com/admin/products/..."
```
![Shopify admin product page](docs/Shopify%20Admin%20Product%20Page.png)
![Customer facing product listing](docs/Customer%20facing%20Product%20Listing.png)

## Setup

1. **Clone and install dependencies**
   ```powershell
   pip install -r requirements.txt
   ```

2. **Create `.env`** from the template and fill in the values (see below for where each one comes from):
   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

3. **Run it**
   ```powershell
   python bot.py
   ```
   It polls Telegram in a loop — no server, no public URL needed. `Ctrl+C` to stop.

### Where the `.env` values come from

| Variable | Source |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `SHOPIFY_STORE_DOMAIN` | Your dev store's `*.myshopify.com` domain |
| `SHOPIFY_API_VERSION` | A recent dated API version, e.g. `2026-07` |
| `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` | Shopify admin → Settings → Apps and sales channels → Develop apps → your app → API credentials. This store's app issues client credentials rather than a static admin token, so the bot exchanges them for an access token at request time (`get_shopify_token()` in `bot.py`) instead of reading one static token from env. The app needs the `write_products` scope. |
| `CLOUDINARY_CLOUD_NAME` / `CLOUDINARY_API_KEY` / `CLOUDINARY_API_SECRET` | [cloudinary.com/console](https://cloudinary.com/console) |

### Standalone test scripts

Two scripts exist outside `bot.py` to verify third-party auth in isolation, without running the whole bot:

- `python test_upload.py <path-to-image>` — confirms Cloudinary upload works and prints back a hosted URL.
- `python test_shopify_auth.py` — confirms the Shopify client-credentials exchange works and prints the shop's name via a read-only query.

Both are safe to run and share output from — neither ever prints a secret value, only derived results (a URL, a shop name).

## What's built

Stage 1, the full MVP: `/create_listing` → photo+caption → Claude-drafted listing → Approve button → real Shopify product via `productSet`, with the admin URL sent back. In-memory state only (`AWAITING_PHOTO`, `DRAFTS`, `LAST_PRODUCT`); a restart clears it.

## What's designed but not built

**Stage 2 — `/create_photo`.** Reads the last created product, generates a styled lifestyle photo with `gpt-image-1` (`images.edit`, portrait `1024x1536`, `input_fidelity="high"` so the actual product is preserved), uploads it, and attaches it to the Shopify product. No approval step — rerunning the command is the regenerate button.

**Stage 3 — `/create_ad`, stretch goal.** Renders a 9:16 story-format video ad via Plainly's Designs API (`ecommerce-flair@v1`), with Claude generating five short on-screen strings (headline, mini-headline, two outro lines, a CTA) and the rest of the parameters pulled from the stored product. Polls `/api/v2/renders/{id}` every ~10s until done, then delivers the output video to Telegram.

## Non-goals

No database, no webhooks/ngrok, no multi-user support, no edit/cancel flow, no storefront publishing (the admin URL is the deliverable, not a live storefront listing), no tests/Docker/CI.

## License

All rights reserved. This repo is shared publicly to showcase my work — please don't copy, reuse, or redistribute the code without my permission.
