"""
Gemini 2.0 Flash via OpenRouter API (OpenAI-compatible).
Todas las funciones de IA del bot usan este módulo.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Optional

import httpx
import redis

from config import settings

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-2.0-flash-001"
CONV_TTL = 7200        # 2 horas de contexto en Redis
MAX_CONV_MESSAGES = 10


def _get_redis() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


async def _call(
    messages: list[dict],
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> str:
    """Llamada base a OpenRouter. Retorna el texto de respuesta."""
    headers = {
        "Authorization": f"Bearer {settings.GEMINI_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://streamvip.app",
        "X-Title": "StreamVip Bot",
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


# ─────────────────────────────────────────────────────────────────
# FUNCIONES PÚBLICAS
# ─────────────────────────────────────────────────────────────────

async def extract_user_name(message_text: str) -> Optional[str]:
    """Extrae el nombre propio de un mensaje de texto libre."""
    try:
        result = await _call(
            messages=[{
                "role": "user",
                "content": (
                    "Extrae SOLO el nombre propio de la siguiente respuesta de un usuario venezolano. "
                    "El usuario está respondiendo a '¿Cómo te llamas?'. "
                    "Responde ÚNICAMENTE con el nombre, sin puntuación ni palabras adicionales. "
                    "Si no hay un nombre claro, responde con NONE.\n\n"
                    f"Respuesta del usuario: {message_text}"
                ),
            }],
            temperature=0.1,
        )
        if result.upper() == "NONE" or len(result) > 50:
            return None
        return result
    except Exception as e:
        logger.error(f"Error in extract_user_name: {e}")
        return None


async def validate_payment_image(image_bytes: bytes) -> dict:
    """
    Valida un comprobante de pago venezolano usando visión.
    Retorna dict con los datos extraídos.
    """
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Analiza esta imagen de comprobante de pago venezolano "
                        "(Pago Móvil o transferencia bancaria). "
                        "Extrae la información y responde SOLO con JSON válido:\n"
                        "{\n"
                        '  "is_comprobante_valido": true/false,\n'
                        '  "monto": "número como string, ej: 1250.50",\n'
                        '  "referencia": "número de referencia",\n'
                        '  "fecha": "fecha en formato DD/MM/YYYY",\n'
                        '  "hora": "hora HH:MM si está visible",\n'
                        '  "banco_origen": "nombre del banco",\n'
                        '  "banco_destino": "banco destino si aparece",\n'
                        '  "tipo": "pago_movil o transferencia",\n'
                        '  "confianza": "alta/media/baja"\n'
                        "}\n"
                        "Si no es comprobante válido: is_comprobante_valido=false, demás campos vacíos."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                },
            ],
        }]
        text = await _call(messages, temperature=0.1)
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error in validate_payment_image: {e}")
        return {"is_comprobante_valido": None, "error": "parse_error"}
    except Exception as e:
        logger.error(f"Error in validate_payment_image: {e}")
        return {"is_comprobante_valido": None, "error": str(e)}


async def generate_synopsis_vzla(
    title: str,
    content_type: str,
    year: int,
    yo_la_vi: bool = False,
) -> str:
    """Genera una sinopsis al estilo venezolano para recomendar contenido."""
    try:
        tipo = "película" if content_type == "movie" else "serie"
        yo_vi = (
            "Ya la vi y la recomiendo personalmente. "
            "Inclúye una referencia natural a que ya la viste."
        ) if yo_la_vi else ""
        result = await _call(
            messages=[{
                "role": "user",
                "content": (
                    f"Eres un venezolano apasionado del entretenimiento que le recomienda "
                    f"contenido a sus panas con entusiasmo genuino.\n\n"
                    f"Escribe una sinopsis de la {tipo} '{title}' ({year}) para recomendar "
                    f"a suscriptores de un servicio de streaming en Venezuela.\n\n"
                    f"REGLAS:\n"
                    f"- Máximo 4 oraciones, máximo 80 palabras\n"
                    f"- CERO spoilers\n"
                    f"- Lenguaje venezolano: usa 'pana', 'chévere', 'brutal', 'bestial', "
                    f"'de una sentada', 'pegado', 'tremenda', etc.\n"
                    f"- NO uses frases genéricas como 'una historia apasionante'\n"
                    f"- Genera FOMO real, que quieran verla esta noche\n"
                    f"- Termina con una frase gancho corta (máx 8 palabras)\n"
                    f"{yo_vi}\n"
                    f"Devuelve SOLO el texto. Sin comillas. Sin explicaciones."
                ),
            }],
            temperature=0.7,
        )
        return result
    except Exception as e:
        logger.error(f"Error in generate_synopsis_vzla: {e}")
        return f"🎬 {title} ({year}) — ¡Una {content_type} que no te puedes perder!"


async def generate_personalized_message(
    name: str,
    synopsis: str,
    platform: str,
    yo_la_vi: bool = False,
) -> str:
    """Genera un mensaje promocional personalizado para un usuario."""
    try:
        yo_vi = (
            "Menciona naturalmente que ya la viste: "
            "'La vi el fin de semana y...', 'Me la acabé de una...', etc."
        ) if yo_la_vi else ""
        result = await _call(
            messages=[{
                "role": "user",
                "content": (
                    f"Eres el dueño de StreamVip Venezuela.\n"
                    f"Escribe un mensaje personal y cercano para tu cliente '{name}' "
                    f"recomendándole contenido en {platform}.\n\n"
                    f"REGLAS:\n"
                    f"- Empieza con el nombre de forma variada: '{name} 👀', "
                    f"'Mira {name}', 'Oye {name} 🎬', '{name} ¿ya sabes lo que hay?'\n"
                    f"- Máximo 5 líneas en total\n"
                    f"- Suena como mensaje del dueño del servicio, no de una empresa\n"
                    f"- Incluye la sinopsis: {synopsis}\n"
                    f"- CTA casual venezolano al final\n"
                    f"- Sin asteriscos ni markdown visible\n"
                    f"{yo_vi}\n"
                    f"Devuelve SOLO el mensaje listo para enviar."
                ),
            }],
            temperature=0.7,
        )
        return result
    except Exception as e:
        logger.error(f"Error in generate_personalized_message: {e}")
        return f"¡Hola {name}! 👋 Te recomiendo este contenido en {platform}. ¡No te lo pierdas!"


async def verify_content_venezuela(title: str, platform: str) -> dict:
    """Verifica si un contenido está disponible en Venezuela."""
    try:
        result = await _call(
            messages=[{
                "role": "user",
                "content": (
                    f"¿Está disponible '{title}' en {platform} Venezuela actualmente? "
                    f"Responde SOLO con JSON: "
                    '{"disponible": true/false, "confianza": "alta/media/baja", '
                    '"nota": "observación breve"}'
                ),
            }],
            temperature=0.1,
        )
        text = result.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        logger.error(f"Error in verify_content_venezuela: {e}")
        return {"disponible": True, "confianza": "baja", "nota": "No se pudo verificar"}


async def interpret_user_intent(
    message_text: str,
    conversation_context: list[dict],
) -> dict:
    """Detecta la intención del usuario en un mensaje libre."""
    try:
        context_str = "\n".join(
            [f"{m['role']}: {m['content']}" for m in conversation_context[-5:]]
        )
        result = await _call(
            messages=[{
                "role": "user",
                "content": (
                    f"Eres asistente de StreamVip Venezuela (servicio de streaming).\n"
                    f"Contexto:\n{context_str}\n\n"
                    f"Nuevo mensaje: {message_text}\n\n"
                    f"Determina la intención. Responde SOLO con JSON:\n"
                    '{"intent": "subscribe/express/week/support/renewal/cancel/info/other", '
                    '"platform": "netflix/disney/max/paramount/prime/null", '
                    '"confidence": "alta/media/baja", '
                    '"suggested_response": "respuesta breve en español venezolano"}'
                ),
            }],
            temperature=0.1,
        )
        text = result.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        logger.error(f"Error in interpret_user_intent: {e}")
        return {"intent": "other", "platform": None, "confidence": "baja", "suggested_response": ""}


async def generate_troubleshooting_response(
    platform: str,
    problem_description: str,
) -> str:
    """Genera una respuesta de soporte técnico para un problema de plataforma."""
    try:
        result = await _call(
            messages=[{
                "role": "user",
                "content": (
                    f"Eres soporte técnico de StreamVip Venezuela para {platform}.\n"
                    f"El cliente reporta: {problem_description}\n\n"
                    f"Proporciona pasos claros para resolver el problema en lenguaje venezolano amigable. "
                    f"Máximo 5 pasos numerados con emojis. "
                    f"Si no se puede resolver desde el usuario, indica que contacten soporte."
                ),
            }],
            temperature=0.5,
        )
        return result
    except Exception as e:
        logger.error(f"Error in generate_troubleshooting_response: {e}")
        return "Lo sentimos, hubo un error. Por favor contacta a soporte directamente."


# ─────────────────────────────────────────────────────────────────
# CONTEXTO DE CONVERSACIÓN (Redis)
# ─────────────────────────────────────────────────────────────────

def store_conversation_message(telegram_id: int, role: str, content: str) -> None:
    """Guarda un mensaje en el historial de conversación en Redis."""
    try:
        r = _get_redis()
        key = f"conv:{telegram_id}"
        raw = r.get(key)
        messages: list = json.loads(raw) if raw else []
        messages.append({"role": role, "content": content})
        if len(messages) > MAX_CONV_MESSAGES:
            messages = messages[-MAX_CONV_MESSAGES:]
        r.setex(key, CONV_TTL, json.dumps(messages))
    except Exception as e:
        logger.warning(f"Error storing conversation message: {e}")


def get_conversation_context(telegram_id: int) -> list[dict]:
    """Obtiene el historial de conversación desde Redis."""
    try:
        r = _get_redis()
        key = f"conv:{telegram_id}"
        raw = r.get(key)
        return json.loads(raw) if raw else []
    except Exception as e:
        logger.warning(f"Error getting conversation context: {e}")
        return []
