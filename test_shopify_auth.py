"""
Standalone test for Shopify auth.

This store's app issues a Client ID + Client secret, not a pre-generated
admin token. So instead of a static token, we exchange the client
credentials for an access token at request time (OAuth's "client
credentials" grant), cache it, and refresh it once it's close to expiry.

This script does two things:
1. Fetches a token via get_shopify_token().
2. Uses it for one cheap read-only GraphQL query ({ shop { name } }) to
   prove the token actually works against the Admin API, before we build
   the real productSet mutation on top of it.
"""

import asyncio
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

SHOPIFY_STORE_DOMAIN = os.environ.get("SHOPIFY_STORE_DOMAIN")
SHOPIFY_API_VERSION = os.environ.get("SHOPIFY_API_VERSION")
SHOPIFY_CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET")

_token: str | None = None
_expires_at: float = 0


async def get_shopify_token() -> str:
    """Return a cached access token, refreshing via client credentials when it's stale."""
    global _token, _expires_at
    if _token and time.time() < _expires_at:
        return _token

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

    _token = data["access_token"]
    _expires_at = time.time() + data["expires_in"] - 60  # refresh a minute early
    return _token


async def main():
    missing = [
        name
        for name, value in [
            ("SHOPIFY_STORE_DOMAIN", SHOPIFY_STORE_DOMAIN),
            ("SHOPIFY_API_VERSION", SHOPIFY_API_VERSION),
            ("SHOPIFY_CLIENT_ID", SHOPIFY_CLIENT_ID),
            ("SHOPIFY_CLIENT_SECRET", SHOPIFY_CLIENT_SECRET),
        ]
        if not value
    ]
    if missing:
        print(f"Missing from .env: {', '.join(missing)}")
        return

    print("Requesting access token via client credentials...")
    token = await get_shopify_token()
    print("Token received.")

    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
    query = "{ shop { name } }"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            json={"query": query},
            headers={"X-Shopify-Access-Token": token},
            timeout=30,
        )

    response.raise_for_status()
    body = response.json()

    if "errors" in body:
        print(f"GraphQL errors: {body['errors']}")
        return

    print(f"Success. Shop name: {body['data']['shop']['name']}")


if __name__ == "__main__":
    asyncio.run(main())
