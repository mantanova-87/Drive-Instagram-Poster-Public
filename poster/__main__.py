from __future__ import annotations

import logging
import json
import mimetypes
import os
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import boto3
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("drive-instagram-poster")
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
LEDGER_PATH = Path("state/posted.json")
SUPPORTED_TYPES = {"image/jpeg"}
CAROUSEL_SIZE = 10
CAPTION = "A few favorites from lately. 📸"


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required setting: {name}")
    return value


def google_drive():
    service_account_info = json.loads(required("GOOGLE_SERVICE_ACCOUNT_JSON"))
    creds = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=[DRIVE_SCOPE],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def next_photos(service, limit: int = CAROUSEL_SIZE) -> list[dict]:
    folder_id = required("GOOGLE_DRIVE_FOLDER_ID").replace("'", "")
    query = (
        f"'{folder_id}' in parents and trashed = false "
        "and mimeType = 'image/jpeg'"
    )
    posted_ids = load_ledger()
    page_token = None
    photos = []
    while True:
        result = service.files().list(
            q=query,
            orderBy="name,createdTime",
            pageSize=100,
            pageToken=page_token,
            fields="nextPageToken,files(id,name,mimeType)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        for item in result.get("files", []):
            if item["id"] not in posted_ids:
                photos.append(item)
                if len(photos) == limit:
                    return photos
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    if len(photos) < limit:
        LOG.info(
            "Need %d unposted JPEG photos for a carousel; found %d. "
            "Leaving them in Drive until a full batch is available.",
            limit,
            len(photos),
        )
        return []
    return photos


def download_photo(service, photo) -> bytes:
    stream = BytesIO()
    request = service.files().get_media(fileId=photo["id"])
    downloader = MediaIoBaseDownload(stream, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return stream.getvalue()


def r2_client():
    account = required("R2_ACCOUNT_ID")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=required("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=required("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def upload_to_r2(client, data: bytes, mime_type: str) -> tuple[str, str]:
    key = f"ig-temporary/{uuid.uuid4().hex}.{mimetypes.guess_extension(mime_type).lstrip('.')}"
    base = required("R2_PUBLIC_BASE_URL").rstrip("/")
    client.put_object(
        Bucket=required("R2_BUCKET"),
        Key=key,
        Body=data,
        ContentType=mime_type,
        CacheControl="no-store, max-age=60",
    )
    return f"{base}/{quote(key, safe='/')}", key


def ig_request(path: str, *, params=None, data=None):
    version = required("INSTAGRAM_API_VERSION")
    url = f"https://graph.instagram.com/{version}/{path.lstrip('/')}"
    response = requests.post(url, params=params, data=data, timeout=60)
    try:
        body = response.json()
    except ValueError:
        body = {"error": response.text[:500]}
    if not response.ok or "error" in body:
        raise RuntimeError(f"Instagram API request failed ({response.status_code}): {body}")
    return body


def wait_until_finished(container_ids: list[str], token: str, *, label: str):
    version = required("INSTAGRAM_API_VERSION")
    pending = set(container_ids)
    for attempt in range(12):
        for container_id in list(pending):
            status_url = f"https://graph.instagram.com/{version}/{container_id}"
            response = requests.get(
                status_url,
                params={"fields": "status_code", "access_token": token},
                timeout=30,
            )
            response.raise_for_status()
            state = response.json().get("status_code")
            if state == "FINISHED":
                pending.remove(container_id)
            elif state in {"ERROR", "EXPIRED"}:
                raise RuntimeError(
                    f"Instagram {label} container {container_id} ended in state {state}."
                )
        if not pending:
            return
        if attempt < 11:
            time.sleep(5)
    raise RuntimeError(
        f"Instagram {label} containers did not finish in time: {', '.join(sorted(pending))}"
    )


def publish_carousel(image_urls: list[str], caption: str) -> str:
    if len(image_urls) != CAROUSEL_SIZE:
        raise ValueError(f"A carousel requires exactly {CAROUSEL_SIZE} images.")
    user_id = required("INSTAGRAM_USER_ID")
    token = required("INSTAGRAM_ACCESS_TOKEN")
    child_ids = []
    for image_url in image_urls:
        child = ig_request(
            f"{user_id}/media",
            params={
                "image_url": image_url,
                "is_carousel_item": "true",
                "access_token": token,
            },
        )
        child_ids.append(child["id"])

    wait_until_finished(child_ids, token, label="carousel item")
    carousel = ig_request(
        f"{user_id}/media",
        params={
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
            "access_token": token,
        },
    )
    carousel_id = carousel["id"]
    wait_until_finished([carousel_id], token, label="carousel")

    result = ig_request(
        f"{user_id}/media_publish",
        params={"creation_id": carousel_id, "access_token": token},
    )
    return result["id"]


def load_ledger() -> dict:
    if not LEDGER_PATH.exists():
        return {}
    with LEDGER_PATH.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError("state/posted.json must contain a JSON object.")
    return value


def record_published(photo_ids: list[str], instagram_media_id: str):
    ledger = load_ledger()
    posted_at = datetime.now(timezone.utc).isoformat()
    for photo_id in photo_ids:
        ledger[photo_id] = {
            "instagram_media_id": instagram_media_id,
            "posted_at_utc": posted_at,
        }
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(ledger, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    drive = google_drive()
    photos = next_photos(drive)
    if not photos:
        return 0

    photo_data = []
    for photo in photos:
        mime_type = photo["mimeType"]
        if mime_type not in SUPPORTED_TYPES:
            raise RuntimeError(f"Unsupported photo format: {mime_type}")
        photo_data.append((download_photo(drive, photo), mime_type))

    caption = CAPTION
    r2 = r2_client()
    object_keys = []
    try:
        image_urls = []
        for data, mime_type in photo_data:
            public_url, object_key = upload_to_r2(r2, data, mime_type)
            image_urls.append(public_url)
            object_keys.append(object_key)

        media_id = publish_carousel(image_urls, caption)
        LOG.info("Instagram confirmed carousel publication (media id %s).", media_id)
        record_published([photo["id"] for photo in photos], media_id)
        LOG.info(
            "Recorded all %d carousel photos (%s); Drive originals remain untouched.",
            len(photos),
            ", ".join(photo["name"] for photo in photos),
        )
    finally:
        # Remove the temporary public copy whether publishing succeeds or fails.
        for object_key in object_keys:
            try:
                r2.delete_object(Bucket=required("R2_BUCKET"), Key=object_key)
            except Exception:
                LOG.exception("Could not delete temporary public R2 object %s.", object_key)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        LOG.exception("Posting run failed; the Drive original is left in place unless Instagram had already confirmed publication.")
        raise
