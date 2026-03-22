from __future__ import annotations

import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# TMDB provider IDs for Venezuela
PROVIDER_IDS = {
    "netflix": 8,
    "disney": 337,
    "max": 1843,
    "paramount": 531,
    "prime": 119,
}


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.TMDB_API_KEY}", "accept": "application/json"}


async def scan_new_releases_venezuela() -> list[dict]:
    """Discover new releases available in Venezuela region."""
    results = []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for content_type in ["movie", "tv"]:
                url = f"{TMDB_BASE_URL}/discover/{content_type}"
                params = {
                    "watch_region": "VE",
                    "with_watch_monetization_types": "flatrate",
                    "sort_by": "popularity.desc",
                    "page": 1,
                }
                response = await client.get(url, params=params, headers=_headers())
                if response.status_code == 200:
                    data = response.json()
                    for item in (data.get("results") or [])[:10]:
                        item["content_type"] = content_type
                        results.append(item)
    except Exception as e:
        logger.error(f"Error in scan_new_releases_venezuela: {e}")
    return results


async def get_content_details(tmdb_id: int, content_type: str) -> Optional[dict]:
    """Get detailed content info including watch providers."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            endpoint = "movie" if content_type == "movie" else "tv"
            url = f"{TMDB_BASE_URL}/{endpoint}/{tmdb_id}"
            params = {"append_to_response": "watch/providers,credits", "language": "es-VE"}
            response = await client.get(url, params=params, headers=_headers())
            if response.status_code == 200:
                return response.json()
    except Exception as e:
        logger.error(f"Error in get_content_details: {e}")
    return None


async def check_venezuela_availability(tmdb_id: int, content_type: str) -> dict:
    """Check which streaming providers have this content in Venezuela."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            endpoint = "movie" if content_type == "movie" else "tv"
            url = f"{TMDB_BASE_URL}/{endpoint}/{tmdb_id}/watch/providers"
            response = await client.get(url, headers=_headers())
            if response.status_code == 200:
                data = response.json()
                ve_data = (data.get("results") or {}).get("VE", {})
                flatrate = ve_data.get("flatrate", [])
                available_providers = [p["provider_name"] for p in flatrate]
                return {
                    "available": len(available_providers) > 0,
                    "providers": available_providers,
                    "provider_ids": [p["provider_id"] for p in flatrate],
                }
    except Exception as e:
        logger.error(f"Error in check_venezuela_availability: {e}")
    return {"available": False, "providers": [], "provider_ids": []}


async def get_poster_url(tmdb_id: int, content_type: str) -> Optional[str]:
    """Get poster URL for content."""
    try:
        details = await get_content_details(tmdb_id, content_type)
        if details and details.get("poster_path"):
            return f"{TMDB_IMAGE_BASE}{details['poster_path']}"
    except Exception as e:
        logger.error(f"Error in get_poster_url: {e}")
    return None


async def download_poster(url: str) -> Optional[bytes]:
    """Download poster image bytes."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
            if response.status_code == 200:
                return response.content
    except Exception as e:
        logger.error(f"Error in download_poster: {e}")
    return None


async def search_content(query: str, content_type: str = "multi") -> list[dict]:
    """Search for movies or TV shows by title."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            url = f"{TMDB_BASE_URL}/search/{content_type}"
            params = {"query": query, "language": "es-VE", "page": 1}
            response = await client.get(url, params=params, headers=_headers())
            if response.status_code == 200:
                return (response.json().get("results") or [])[:5]
    except Exception as e:
        logger.error(f"Error in search_content: {e}")
    return []
