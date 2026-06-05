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


async def get_netflix_household_link(account_email: str, master_credentials_json: str) -> Optional[str]:
    """
    Busca en el Gmail maestro el link de verificación de hogar de Netflix
    para la cuenta indicada (filtrado por header To:).
    Retorna el link 'Obtener código' o None si no lo encuentra.
    """
    import json
    if not master_credentials_json:
        logger.warning("[gmail] GMAIL_MASTER_CREDENTIALS_JSON no configurado")
        return None

    try:
        creds_data = json.loads(master_credentials_json)
        creds = Credentials(
            token=creds_data.get('token'),
            refresh_token=creds_data.get('refresh_token'),
            token_uri=creds_data.get('token_uri', 'https://oauth2.googleapis.com/token'),
            client_id=creds_data.get('client_id'),
            client_secret=creds_data.get('client_secret'),
            scopes=creds_data.get('scopes', ['https://www.googleapis.com/auth/gmail.readonly']),
        )

        # Forzar refresh del token antes de usarlo
        from google.auth.transport.requests import Request as GoogleRequest
        if creds.expired or not creds.valid:
            logger.info("[gmail] Token expirado, refrescando...")
            creds.refresh(GoogleRequest())
            logger.info("[gmail] Token refrescado exitosamente")

        service = build('gmail', 'v1', credentials=creds, cache_discovery=False)
        logger.info(f"[gmail] Buscando email Netflix para cuenta: {account_email}")

        query = 'from:info@account.netflix.com newer_than:1h'
        results = service.users().messages().list(userId='me', q=query, maxResults=20).execute()
        messages = results.get('messages', [])
        logger.info(f"[gmail] Emails Netflix encontrados en bandeja: {len(messages)}")

        link_patterns = [
            r'https://www\.netflix\.com/account/travel/[^\s"\'<>\]]+',
            r'https://www\.netflix\.com[^\s"\'<>\]]*travel[^\s"\'<>\]]*',
        ]

        for msg_ref in messages:
            msg = service.users().messages().get(
                userId='me', id=msg_ref['id'], format='full'
            ).execute()

            headers = msg.get('payload', {}).get('headers', [])
            to_header = next(
                (h['value'] for h in headers if h['name'].lower() == 'to'), ''
            )

            # Validar que este email es para la cuenta del cliente
            logger.info(f"[gmail] Email To: '{to_header}' vs cuenta: '{account_email}'")
            if account_email.lower() not in to_header.lower():
                continue

            body = _extract_email_body(msg)
            for pattern in link_patterns:
                match = re.search(pattern, body)
                if match:
                    return match.group(0).rstrip('.')

        return None
    except Exception as e:
        logger.error(f"[gmail] get_netflix_household_link: {e}", exc_info=True)
        return None


async def get_netflix_access_code(account_email: str, master_credentials_json: str) -> dict:
    """
    Busca en Gmail maestro el código o link de acceso de Netflix para hogar/viaje.
    Retorna: {'type': 'code'|'link'|None, 'value': '662727'|'https://...'|None}
    """
    import json, re
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleRequest
    from googleapiclient.discovery import build

    if not master_credentials_json:
        logger.warning("[gmail] GMAIL_MASTER_CREDENTIALS_JSON no configurado")
        return {'type': None, 'value': None}

    try:
        creds_data = json.loads(master_credentials_json)
        creds = Credentials(
            token=creds_data.get('token'),
            refresh_token=creds_data.get('refresh_token'),
            token_uri=creds_data.get('token_uri', 'https://oauth2.googleapis.com/token'),
            client_id=creds_data.get('client_id'),
            client_secret=creds_data.get('client_secret'),
            scopes=creds_data.get('scopes', ['https://www.googleapis.com/auth/gmail.readonly']),
        )
        if creds.expired or not creds.valid:
            creds.refresh(GoogleRequest())

        service = build('gmail', 'v1', credentials=creds, cache_discovery=False)
        logger.info(f"[gmail] get_netflix_access_code para: {account_email}")

        query = 'from:info@account.netflix.com newer_than:1h'
        results = service.users().messages().list(userId='me', q=query, maxResults=20).execute()
        messages = results.get('messages', [])
        logger.info(f"[gmail] Emails Netflix encontrados: {len(messages)}")

        link_patterns = [
            r'https://www\.netflix\.com/account/travel/[^\s"\'<>\]]+',
            r'https://www\.netflix\.com[^\s"\'<>\]]*travel[^\s"\'<>\]]*',
        ]
        code_patterns = [
            r'[Cc]ódigo(?:\s+de\s+verificaci[oó]n)?[:\s]+([0-9]{6})\b',
            r'[Vv]erification\s+[Cc]ode[:\s]+([0-9]{6})\b',
            r'(?:^|\n)\s*([0-9]{6})\s*(?:\n|$)',
        ]

        for msg_ref in messages:
            msg = service.users().messages().get(
                userId='me', id=msg_ref['id'], format='full'
            ).execute()

            headers = msg.get('payload', {}).get('headers', [])
            to_header = next(
                (h['value'] for h in headers if h['name'].lower() == 'to'), ''
            )
            if account_email.lower() not in to_header.lower():
                continue

            body = _extract_email_body(msg)
            logger.info(f"[gmail] Body preview: {body[:300]}")

            # Buscar link primero (email tipo "Obtener código")
            for pattern in link_patterns:
                match = re.search(pattern, body)
                if match:
                    link = match.group(0).rstrip('.')
                    logger.info(f"[gmail] Link encontrado: {link}")
                    return {'type': 'link', 'value': link}

            # Si no hay link, buscar código de 6 dígitos (email tipo "Código de verificación")
            for cp in code_patterns:
                code_match = re.search(cp, body, re.MULTILINE)
                if code_match:
                    code = code_match.group(1)
                    # Excluir si está en línea con SRC: del footer
                    code_pos = code_match.start()
                    surrounding = body[max(0, code_pos-30):code_pos+40]
                    if 'SRC:' in surrounding or 'src:' in surrounding:
                        continue
                    logger.info(f"[gmail] Código encontrado: {code}")
                    return {'type': 'code', 'value': code}

        return {'type': None, 'value': None}

    except Exception as e:
        logger.error(f"[gmail] get_netflix_access_code: {e}", exc_info=True)
        return {'type': None, 'value': None}
