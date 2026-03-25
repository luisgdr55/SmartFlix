"""
IMAP-based verification code reader.

All platform account emails forward their verification code emails to one
central Gmail inbox (configured via IMAP_EMAIL / IMAP_PASSWORD in settings).
This module polls that inbox and extracts codes automatically.
"""
from __future__ import annotations

import asyncio
import email
import email.utils
import imaplib
import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Known sender domains per platform slug.
# An email matches if any of its domains appears in the From header.
PLATFORM_SENDERS: dict[str, list[str]] = {
    "netflix":     ["account.netflix.com", "netflix.com"],
    "disney":      ["disneyplus.com", "mail.disneyplus.com"],
    "max":         ["max.com", "hbomax.com"],
    "paramount":   ["paramountplus.com"],
    "prime":       ["amazon.com"],
    "crunchyroll": ["crunchyroll.com"],
    "apple":       ["apple.com"],
    "hulu":        ["hulu.com"],
    "peacock":     ["peacocktv.com"],
    "spotify":     ["spotify.com"],
}

# Verification code regex per platform.
# Netflix uses 4 digits; most others use 6.
CODE_PATTERNS: dict[str, str] = {
    "netflix": r"\b(\d{4})\b",
    "default": r"\b(\d{4,8})\b",
}

POLL_INTERVAL_SECONDS = 15
FALLBACK_TIMEOUT_SECONDS = 240  # 4 minutes → escalate to admin


def _get_code_pattern(platform_slug: str) -> str:
    return CODE_PATTERNS.get(platform_slug, CODE_PATTERNS["default"])


def _imap_search_once(
    platform_slug: str,
    since_ts: float,
    imap_email: str,
    imap_password: str,
    imap_host: str,
    imap_port: int,
) -> Optional[str]:
    """
    Blocking IMAP call — always run via asyncio.to_thread().
    Connects to the central inbox and looks for the most recent
    verification email for the given platform received after since_ts.
    Returns the code string or None.
    """
    if not imap_email or not imap_password:
        logger.warning("IMAP credentials not configured — skipping search")
        return None

    mail = None
    try:
        mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        mail.login(imap_email, imap_password)
        mail.select("INBOX")

        _, data = mail.search(None, "ALL")
        if not data or not data[0]:
            return None

        msg_ids = data[0].split()
        if not msg_ids:
            return None

        # Check up to the 40 most recent messages, newest first
        recent = list(reversed(msg_ids[-40:]))

        senders = PLATFORM_SENDERS.get(platform_slug, [])
        pattern = _get_code_pattern(platform_slug)

        for msg_id in recent:
            try:
                _, msg_data = mail.fetch(msg_id, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue

                msg = email.message_from_bytes(msg_data[0][1])

                # Must be a recent email (received after the request was made)
                date_str = msg.get("Date", "")
                if date_str:
                    try:
                        msg_ts = email.utils.parsedate_to_datetime(date_str).timestamp()
                        if msg_ts < since_ts - 60:  # 60 s grace for clock drift
                            continue
                    except Exception:
                        pass  # if date parse fails, still try the email

                # Must be from the correct platform sender
                from_header = msg.get("From", "").lower()
                if senders and not any(domain in from_header for domain in senders):
                    continue

                # Extract plain-text body
                body = _extract_body(msg)
                if not body:
                    continue

                # Extract the verification code
                match = re.search(pattern, body)
                if match:
                    return match.group(1)

            except Exception as e:
                logger.debug(f"Error processing IMAP message {msg_id}: {e}")
                continue

        return None

    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP auth/connection error: {e}")
        return None
    except Exception as e:
        logger.error(f"IMAP search error: {e}")
        return None
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass


def _extract_body(msg: email.message.Message) -> str:
    """Extract plain text from an email message object."""
    parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    raw = part.get_payload(decode=True)
                    if raw:
                        parts.append(raw.decode("utf-8", errors="ignore"))
                except Exception:
                    pass
    else:
        try:
            raw = msg.get_payload(decode=True)
            if raw:
                parts.append(raw.decode("utf-8", errors="ignore"))
        except Exception:
            pass

    return " ".join(parts)


async def poll_for_code(
    platform_slug: str,
    since_ts: float,
    timeout: int = FALLBACK_TIMEOUT_SECONDS,
) -> Optional[str]:
    """
    Async IMAP poller.
    Checks every POLL_INTERVAL_SECONDS for up to `timeout` seconds.
    Returns the code string, or None if the timeout expires without finding one.
    """
    from config import settings

    imap_email = getattr(settings, "IMAP_EMAIL", "")
    imap_password = getattr(settings, "IMAP_PASSWORD", "")
    imap_host = getattr(settings, "IMAP_HOST", "imap.gmail.com")
    imap_port = int(getattr(settings, "IMAP_PORT", 993))

    elapsed = 0
    while elapsed < timeout:
        code = await asyncio.to_thread(
            _imap_search_once,
            platform_slug,
            since_ts,
            imap_email,
            imap_password,
            imap_host,
            imap_port,
        )
        if code:
            logger.info(f"Verification code found for '{platform_slug}': {code}")
            return code

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS

    logger.info(f"IMAP timeout: no code found for '{platform_slug}' after {timeout}s")
    return None
