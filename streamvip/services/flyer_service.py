from __future__ import annotations

import io
import logging
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

PLATFORM_COLORS = {
    "netflix": "#E50914",
    "disney": "#113CCF",
    "max": "#5822A9",
    "paramount": "#0064FF",
    "prime": "#00A8E0",
}

FLYER_SIZE = (1080, 1080)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))  # type: ignore


def _create_gradient_background(size: tuple, color1: tuple, color2: tuple) -> Image.Image:
    """Create a vertical gradient background."""
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(size[1]):
        ratio = y / size[1]
        r = int(color1[0] * (1 - ratio) + color2[0] * ratio)
        g = int(color1[1] * (1 - ratio) + color2[1] * ratio)
        b = int(color1[2] * (1 - ratio) + color2[2] * ratio)
        draw.line([(0, y), (size[0], y)], fill=(r, g, b))
    return img


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Load a font, falling back to default if not available."""
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    except Exception:
        try:
            return ImageFont.truetype("arial.ttf", size)
        except Exception:
            return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Wrap text to fit within max_width pixels."""
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return lines


async def create_flyer(
    platform_slug: str,
    title: str,
    synopsis: str,
    content_type: str,
    year: int,
    poster_bytes: Optional[bytes],
    platform_color: Optional[str] = None,
) -> bytes:
    """
    Generate a 1080x1080 promotional flyer image.
    Returns image bytes (PNG).
    """
    try:
        color_hex = platform_color or PLATFORM_COLORS.get(platform_slug, "#1a1a2e")
        primary_color = _hex_to_rgb(color_hex)
        dark_color = tuple(max(0, c - 80) for c in primary_color)

        # 1. Gradient background
        img = _create_gradient_background(FLYER_SIZE, dark_color, (10, 10, 20))  # type: ignore
        draw = ImageDraw.Draw(img)

        # 2. Poster with blur and opacity overlay
        if poster_bytes:
            try:
                poster = Image.open(io.BytesIO(poster_bytes)).convert("RGBA")
                # Resize and position poster (right side)
                poster_height = int(FLYER_SIZE[1] * 0.65)
                poster_width = int(poster.width * poster_height / poster.height)
                poster = poster.resize((poster_width, poster_height), Image.LANCZOS)
                # Create blurred version for background
                blurred = poster.filter(ImageFilter.GaussianBlur(radius=15))
                blurred = blurred.convert("RGBA")
                # Make semi-transparent
                r_ch, g_ch, b_ch, a_ch = blurred.split()
                a_ch = a_ch.point(lambda x: int(x * 0.35))
                blurred = Image.merge("RGBA", (r_ch, g_ch, b_ch, a_ch))
                img_rgba = img.convert("RGBA")
                # Place blurred poster centered
                paste_x = (FLYER_SIZE[0] - blurred.width) // 2
                paste_y = (FLYER_SIZE[1] - blurred.height) // 2
                img_rgba.paste(blurred, (paste_x, paste_y), blurred)
                # Place sharp poster (right side, smaller)
                sharp_height = int(FLYER_SIZE[1] * 0.55)
                sharp_width = int(poster.width * sharp_height / poster.height)
                sharp = poster.resize((sharp_width, sharp_height), Image.LANCZOS).convert("RGBA")
                sharp_x = FLYER_SIZE[0] - sharp_width - 40
                sharp_y = (FLYER_SIZE[1] - sharp_height) // 2
                img_rgba.paste(sharp, (sharp_x, sharp_y), sharp)
                img = img_rgba.convert("RGB")
                draw = ImageDraw.Draw(img)
            except Exception as e:
                logger.warning(f"Could not process poster image: {e}")

        # 3. Dark overlay for text readability (left side)
        overlay = Image.new("RGBA", FLYER_SIZE, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle([(0, 0), (int(FLYER_SIZE[0] * 0.65), FLYER_SIZE[1])], fill=(0, 0, 0, 160))
        img_rgba = img.convert("RGBA")
        img_rgba.paste(overlay, (0, 0), overlay)
        img = img_rgba.convert("RGB")
        draw = ImageDraw.Draw(img)

        # 4. Platform badge (top left)
        platform_name = platform_slug.upper()
        badge_font = _load_font(28)
        badge_bg_rect = [20, 20, 180, 65]
        draw.rounded_rectangle(badge_bg_rect, radius=10, fill=primary_color)
        draw.text((30, 30), platform_name, fill="white", font=badge_font)

        # 5. Content type badge
        type_label = "PELÍCULA" if content_type == "movie" else "SERIE"
        type_font = _load_font(22)
        draw.rounded_rectangle([20, 75, 130, 110], radius=8, fill=(255, 255, 255, 40))
        draw.text((30, 80), type_label, fill="white", font=type_font)

        # 6. Title (large, white)
        title_font = _load_font(52)
        title_lines = _wrap_text(title, title_font, 580, draw)
        title_y = 200
        for line in title_lines[:3]:
            draw.text((40, title_y), line, fill="white", font=title_font)
            title_y += 62

        # 7. Year
        year_font = _load_font(30)
        draw.text((40, title_y + 10), str(year), fill=tuple(min(255, c + 60) for c in primary_color), font=year_font)  # type: ignore

        # 8. Synopsis (wrapped)
        synopsis_font = _load_font(26)
        synopsis_lines = _wrap_text(synopsis, synopsis_font, 570, draw)
        synopsis_y = title_y + 60
        for line in synopsis_lines[:5]:
            draw.text((40, synopsis_y), line, fill=(220, 220, 220), font=synopsis_font)
            synopsis_y += 34

        # 9. CTA button area
        cta_y = FLYER_SIZE[1] - 180
        draw.rounded_rectangle([40, cta_y, 440, cta_y + 70], radius=15, fill=primary_color)
        cta_font = _load_font(30)
        draw.text((70, cta_y + 15), "▶ Ver ahora en StreamVip", fill="white", font=cta_font)

        # 10. Footer
        footer_font = _load_font(22)
        draw.text((40, FLYER_SIZE[1] - 80), "🇻🇪 StreamVip Venezuela | @StreamVipVE", fill=(180, 180, 180), font=footer_font)

        # Save to bytes
        output = io.BytesIO()
        img.save(output, format="PNG", optimize=True)
        return output.getvalue()

    except Exception as e:
        logger.error(f"Error creating flyer: {e}")
        # Return a simple placeholder image
        img = Image.new("RGB", FLYER_SIZE, (26, 26, 46))
        draw = ImageDraw.Draw(img)
        font = _load_font(40)
        draw.text((100, 500), title[:40], fill="white", font=font)
        output = io.BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()


async def create_flyer_campaign(
    platform: dict,
    title: str,
    audience: str,
    yo_la_vi: bool,
    admin_telegram_id: int,
) -> dict:
    """
    Full campaign creation flow.
    Returns campaign data dict with flyer bytes and message template.
    """
    from services.gemini_service import verify_content_venezuela, generate_synopsis_vzla, generate_personalized_message
    from services.tmdb_service import search_content, download_poster, get_poster_url
    from database import get_supabase

    try:
        platform_slug = platform.get("slug", "")
        platform_name = platform.get("name", "")

        # 1. Verify content availability
        availability = await verify_content_venezuela(title, platform_name)

        # 2. Search TMDB for content details
        tmdb_results = await search_content(title, "multi")
        tmdb_item = tmdb_results[0] if tmdb_results else None

        content_type = "movie"
        year = 2024
        poster_bytes = None
        tmdb_id = None

        if tmdb_item:
            content_type = "movie" if tmdb_item.get("media_type") == "movie" else "tv"
            release = tmdb_item.get("release_date") or tmdb_item.get("first_air_date", "2024-01-01")
            year = int(release[:4]) if release else 2024
            tmdb_id = tmdb_item.get("id")
            # Download poster
            if tmdb_item.get("poster_path"):
                poster_url = f"https://image.tmdb.org/t/p/w500{tmdb_item['poster_path']}"
                poster_bytes = await download_poster(poster_url)

        # 3. Generate Venezuelan synopsis
        synopsis = await generate_synopsis_vzla(title, content_type, year, yo_la_vi)

        # 4. Generate flyer
        platform_color = platform.get("color_hex")
        flyer_bytes = await create_flyer(
            platform_slug=platform_slug,
            title=title,
            synopsis=synopsis,
            content_type=content_type,
            year=year,
            poster_bytes=poster_bytes,
            platform_color=platform_color,
        )

        # 5. Generate message template
        msg_template = await generate_personalized_message("{name}", synopsis, platform_name, yo_la_vi)

        # 6. Save campaign to DB
        sb = get_supabase()
        campaign_data = {
            "title": f"Promo: {title}",
            "platform_id": platform.get("id"),
            "content_title": title,
            "content_type": content_type,
            "content_year": year,
            "synopsis_vzla": synopsis,
            "audience": audience,
            "created_by": admin_telegram_id,
            "status": "draft",
        }
        campaign_result = sb.table("campaigns").insert(campaign_data).execute()
        campaign = campaign_result.data[0] if campaign_result.data else {}

        return {
            "campaign": campaign,
            "flyer_bytes": flyer_bytes,
            "message_template": msg_template,
            "synopsis": synopsis,
            "availability": availability,
            "content_type": content_type,
            "year": year,
        }
    except Exception as e:
        logger.error(f"Error in create_flyer_campaign: {e}")
        raise
