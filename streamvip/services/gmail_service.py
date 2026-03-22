from __future__ import annotations

import base64
import logging
import re
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

# Platform-specific email sender filters
PLATFORM_EMAIL_FILTERS = {
    "netflix": "from:info@account.netflix.com",
    "disney": "from:disneyplus@mail.disneyplus.com",
    "max": "from:hbomax@mail.max.com",
    "paramount": "from:no-reply@paramountplus.com",
    "prime": "from:no-reply@amazon.com",
}

# Platform-specific code regex patterns (digits length)
PLATFORM_CODE_PATTERNS = {
    "netflix": r"\b(\d{4})\b",    # Netflix: 4 digits
    "disney": r"\b(\d{6})\b",
    "max": r"\b(\d{6})\b",
    "paramount": r"\b(\d{6})\b",
    "prime": r"\b(\d{6})\b",
}

TIMEOUT_SECONDS = 25


async def get_verification_code(account_email: str, platform: str, credentials_json: dict) -> Optional[str]:
    """
    Search Gmail inbox for the most recent verification code email.
    Returns the code string or None.
    """
    try:
        creds = Credentials(
            token=credentials_json.get("token"),
            refresh_token=credentials_json.get("refresh_token"),
            token_uri=credentials_json.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=credentials_json.get("client_id"),
            client_secret=credentials_json.get("client_secret"),
            scopes=credentials_json.get("scopes", ["https://www.googleapis.com/auth/gmail.readonly"]),
        )

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)

        platform_slug = platform.lower().replace("+", "").replace(" ", "")
        sender_filter = PLATFORM_EMAIL_FILTERS.get(platform_slug, "")
        code_pattern = PLATFORM_CODE_PATTERNS.get(platform_slug, r"\b(\d{6})\b")

        # Search for recent verification emails
        query = f"{sender_filter} subject:código OR subject:verificación OR subject:verify OR subject:code newer_than:1h"
        if not sender_filter:
            query = f"subject:código OR subject:verificación OR subject:verify newer_than:1h"

        results = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=5,
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return None

        # Check the most recent message
        for msg_ref in messages[:3]:
            msg = service.users().messages().get(
                userId="me",
                id=msg_ref["id"],
                format="full",
            ).execute()

            # Try to get body text
            body_text = _extract_email_body(msg)
            if body_text:
                match = re.search(code_pattern, body_text)
                if match:
                    return match.group(1)

        return None
    except Exception as e:
        logger.error(f"Error in get_verification_code: {e}")
        return None


def _extract_email_body(message: dict) -> str:
    """Extract plain text or HTML body from a Gmail message."""
    try:
        payload = message.get("payload", {})
        parts = payload.get("parts", [])

        if not parts:
            # Single part message
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
            return ""

        # Multi-part message - prefer plain text
        for part in parts:
            mime_type = part.get("mimeType", "")
            if mime_type == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")

        # Fall back to HTML
        for part in parts:
            mime_type = part.get("mimeType", "")
            if mime_type == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
                    # Strip HTML tags
                    return re.sub(r"<[^>]+>", " ", html)

        return ""
    except Exception as e:
        logger.error(f"Error extracting email body: {e}")
        return ""
