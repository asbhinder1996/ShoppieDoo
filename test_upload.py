"""
Standalone test for Cloudinary image hosting.

What this proves: given raw image bytes, we can get back a permanent
public URL. Shopify needs a URL (not bytes) to attach a photo to a
product, so this is the one piece everything else in the bot depends on.

Run it with a path to any image file on your machine — see the
PowerShell command below the script.
"""

import asyncio
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Reads the .env file in this folder and loads its values as environment
# variables, so os.environ.get(...) below can see them.
load_dotenv()

CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME")
API_KEY = os.environ.get("CLOUDINARY_API_KEY")
API_SECRET = os.environ.get("CLOUDINARY_API_SECRET")


async def upload_image(data: bytes, filename: str) -> str:
    """POST the bytes to Cloudinary, return secure_url."""
    url = f"https://api.cloudinary.com/v1_1/{CLOUD_NAME}/image/upload"

    # "files" here means: send this as a multipart/form-data upload, the
    # same shape a browser uses for a <input type="file"> form. Cloudinary
    # expects the file part to be named "file".
    files = {"file": (filename, data)}

    async with httpx.AsyncClient() as client:
        # HTTP Basic auth: httpx builds the Authorization header from this
        # tuple. No signature or timestamp needed for this auth method.
        response = await client.post(
            url,
            files=files,
            auth=(API_KEY, API_SECRET),
            timeout=30,
        )

    # Raises an exception if Cloudinary returned an error status (4xx/5xx),
    # so failures aren't silent.
    response.raise_for_status()

    body = response.json()
    return body["secure_url"]


async def main():
    if len(sys.argv) != 2:
        print("Usage: python test_upload.py <path-to-image-file>")
        sys.exit(1)

    if not CLOUD_NAME or not API_KEY or not API_SECRET:
        print("Missing one of CLOUDINARY_CLOUD_NAME / CLOUDINARY_API_KEY / "
              "CLOUDINARY_API_SECRET. Check your .env file.")
        sys.exit(1)

    image_path = Path(sys.argv[1])
    data = image_path.read_bytes()

    print(f"Uploading {image_path.name} ({len(data)} bytes)...")
    secure_url = await upload_image(data, image_path.name)
    print(f"Success. URL:\n{secure_url}")


if __name__ == "__main__":
    asyncio.run(main())
