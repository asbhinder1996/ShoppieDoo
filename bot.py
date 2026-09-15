"""
Telegram -> Claude -> Shopify product bot. Stage 1.

Polling mode: no server, no public URL. Telegram is asked "any new
messages for me?" in a loop instead of Telegram calling us.
"""

import base64
import json
import logging
import os
import time
import uuid

import httpx
from anthropic import APIError, AsyncAnthropic
from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CLOUDINARY_CLOUD_NAME = os.environ["CLOUDINARY_CLOUD_NAME"]
CLOUDINARY_API_KEY = os.environ["CLOUDINARY_API_KEY"]
CLOUDINARY_API_SECRET = os.environ["CLOUDINARY_API_SECRET"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SHOPIFY_STORE_DOMAIN = os.environ["SHOPIFY_STORE_DOMAIN"]
SHOPIFY_API_VERSION = os.environ["SHOPIFY_API_VERSION"]
SHOPIFY_CLIENT_ID = os.environ["SHOPIFY_CLIENT_ID"]
SHOPIFY_CLIENT_SECRET = os.environ["SHOPIFY_CLIENT_SECRET"]

VENDOR = "ShoppieDoo"
PRODUCT_TYPE = "General"

anthropic_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

CLAUDE_SYSTEM_PROMPT = """\
Return a single JSON object and nothing else. No preamble, no markdown \
fences, no explanation before or after it.

The object must have exactly these four keys:
- title: string, under 60 characters
- description_html: string, 2-3 short paragraphs of simple HTML (e.g. <p> tags)
- tags: array of short strings
- price: string, decimal only, e.g. "34.99" — no currency symbol

You will be given a product photo and a caption written by the seller. \
The caption contains the price in some casual form ("$35", "35 bucks", \
"35") and a rough description. Extract the price as a plain decimal \
string, and write a clean listing title, description, and tags from \
the photo and caption together.\
"""

logging.basicConfig(level=logging.INFO)
# httpx logs full request URLs at INFO level, and Telegram's API embeds
# the bot token directly in the URL path (.../bot<TOKEN>/method). Keep
# httpx quiet so the token never lands in logs or a terminal.
logging.getLogger("httpx").setLevel(logging.WARNING)

# user_ids who ran /create_listing and owe a photo
AWAITING_PHOTO: set[int] = set()
# draft_id -> listing dict + image_url
DRAFTS: dict[str, dict] = {}
# telegram_user_id -> {product_id, admin_url, title, description, price, image_url}
LAST_PRODUCT: dict[int, dict] = {}

_shopify_token: str | None = None
_shopify_token_expires_at: float = 0


async def get_shopify_token() -> str:
    """Return a cached access token, refreshing via client credentials when it's stale.

    This store's app issues a Client ID + Client secret rather than a
    pre-generated admin token, so the token is fetched at request time
    via OAuth's client credentials grant instead of read from env.
    """
    global _shopify_token, _shopify_token_expires_at
    if _shopify_token and time.time() < _shopify_token_expires_at:
        return _shopify_token

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://{SHOPIFY_STORE_DOMAIN}/admin/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": SHOPIFY_CLIENT_ID,
                "client_secret": SHOPIFY_CLIENT_SECRET,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    _shopify_token = data["access_token"]
    _shopify_token_expires_at = time.time() + data["expires_in"] - 60
    return _shopify_token


async def create_shopify_product(listing: dict) -> dict:
    """Create a product via productSet. Returns the product dict on success.

    Raises RuntimeError with Shopify's own message on any userErrors —
    silent Shopify failures are the main risk here, so nothing is
    swallowed.
    """
    token = await get_shopify_token()
    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"

    mutation = """
    mutation productSet($input: ProductSetInput!, $synchronous: Boolean!) {
      productSet(input: $input, synchronous: $synchronous) {
        product {
          id
          title
        }
        userErrors {
          field
          message
        }
      }
    }
    """
    variables = {
        "synchronous": True,
        "input": {
            "title": listing["title"],
            "descriptionHtml": listing["description_html"],
            "productType": PRODUCT_TYPE,
            "vendor": VENDOR,
            "tags": listing["tags"],
            "status": "ACTIVE",
            # Shopify requires an explicit option even for a single,
            # no-variant-choice product — a variant without optionValues
            # pointing at one is rejected.
            "productOptions": [{
                "name": "Title",
                "values": [{"name": "Default Title"}],
            }],
            "variants": [{
                "price": listing["price"],
                "optionValues": [{"optionName": "Title", "name": "Default Title"}],
            }],
            "files": [{
                "originalSource": listing["image_url"],
                "alt": listing["title"],
                "filename": "product.jpg",
                "contentType": "IMAGE",
            }],
        },
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            json={"query": mutation, "variables": variables},
            headers={"X-Shopify-Access-Token": token},
            timeout=30,
        )
    response.raise_for_status()
    body = response.json()

    if "errors" in body:
        raise RuntimeError(f"Shopify GraphQL error: {body['errors']}")

    result = body["data"]["productSet"]
    if result["userErrors"]:
        raise RuntimeError(f"Shopify rejected the product: {result['userErrors']}")

    return result["product"]


async def upload_image(data: bytes, filename: str) -> str:
    """POST the bytes to Cloudinary, return secure_url."""
    url = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload"
    files = {"file": (filename, data)}

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            files=files,
            auth=(CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET),
            timeout=30,
        )

    response.raise_for_status()
    return response.json()["secure_url"]


def _extract_json(raw: str) -> dict:
    # Belt-and-suspenders even with the "{" prefill below: strip anything
    # before the first { and after the last }, in case the model still
    # wraps its answer in markdown fences or adds stray text.
    start = raw.find("{")
    end = raw.rfind("}")
    return json.loads(raw[start:end + 1])


async def draft_listing(image_bytes: bytes, caption: str) -> dict:
    """Ask Claude to turn a photo + caption into a listing dict."""
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    # claude-sonnet-5 rejects assistant-message prefill outright ("the
    # conversation must end with a user message"), so JSON-only output
    # relies on the system prompt plus the strip-to-braces fallback below
    # instead of a "{" prefill.
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_b64,
                    },
                },
                {"type": "text", "text": caption},
            ],
        },
    ]

    last_error = None
    for attempt in range(2):
        response = await anthropic_client.messages.create(
            model="claude-sonnet-5",
            max_tokens=1024,
            system=CLAUDE_SYSTEM_PROMPT,
            messages=messages,
        )
        try:
            # claude-sonnet-5 is an extended-thinking model: content[0] is
            # a ThinkingBlock, not the answer. Find the actual text block.
            text_block = next(b for b in response.content if b.type == "text")
            return _extract_json(text_block.text)
        except (json.JSONDecodeError, ValueError, StopIteration) as e:
            last_error = e

    raise RuntimeError(f"Claude didn't return valid JSON after 2 tries: {last_error}")


async def handle_create_listing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    AWAITING_PHOTO.add(user_id)
    await update.message.reply_text(
        "Send me a photo of your product, with the price and a short "
        "description as the caption."
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if user_id not in AWAITING_PHOTO:
        await update.message.reply_text("Run /create_listing first.")
        return
    AWAITING_PHOTO.discard(user_id)

    # message.text is empty for photo messages; the caption is a
    # separate field.
    caption = update.message.caption
    if not caption:
        await update.message.reply_text(
            "I need the price and a short description as the photo's "
            "caption. Run /create_listing and try again."
        )
        return

    # message.photo is a list of sizes, smallest first — take the largest.
    photo = update.message.photo[-1]
    telegram_file = await photo.get_file()
    data = bytes(await telegram_file.download_as_bytearray())

    await update.message.reply_text("Drafting your listing...")
    secure_url = await upload_image(data, "product.jpg")

    try:
        listing = await draft_listing(data, caption)
    except (RuntimeError, KeyError, APIError) as e:
        await update.message.reply_text(f"Claude couldn't draft a listing: {e}")
        return

    draft_id = uuid.uuid4().hex[:8]
    DRAFTS[draft_id] = {**listing, "image_url": secure_url}

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"ok:{draft_id}"),
    ]])
    await update.message.reply_text(
        f"Title: {listing['title']}\n"
        f"Price: {listing['price']}\n"
        f"Tags: {', '.join(listing['tags'])}\n"
        f"Description: {listing['description_html']}\n"
        f"Image: {secure_url}",
        reply_markup=keyboard,
    )


async def handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    # Must be called even before anything else, or the button spins on
    # the user's end forever.
    await query.answer()

    draft_id = query.data.split(":", 1)[1]
    draft = DRAFTS.get(draft_id)
    if draft is None:
        await query.message.reply_text("This draft is no longer available.")
        return

    await query.message.reply_text("Creating the product in Shopify...")

    try:
        product = await create_shopify_product(draft)
    except (RuntimeError, KeyError) as e:
        await query.message.reply_text(f"Shopify couldn't create the product: {e}")
        return

    product_numeric_id = product["id"].rsplit("/", 1)[-1]
    admin_url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/products/{product_numeric_id}"

    user_id = query.from_user.id
    LAST_PRODUCT[user_id] = {
        "product_id": product["id"],
        "admin_url": admin_url,
        "title": draft["title"],
        "description": draft["description_html"],
        "price": draft["price"],
        "image_url": draft["image_url"],
    }

    await query.message.reply_text(f"Product created: {admin_url}")


async def post_init(application: Application) -> None:
    # Registers the command so it shows up in Telegram's menu button
    # (the "/" icon next to the message box).
    await application.bot.set_my_commands([
        BotCommand("create_listing", "Create a new product listing"),
    ])


def main() -> None:
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("create_listing", handle_create_listing))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_approve))
    app.run_polling()


if __name__ == "__main__":
    main()
