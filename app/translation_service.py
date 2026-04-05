import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

GROQ_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "You are a professional translator specializing in Yoruba. "
    "When given English text, translate it accurately into Yoruba (Yoruba Latin script). "
    "Return ONLY the translated Yoruba text — no explanations, no notes, no English."
)

FALLBACK_YORUBA = (
    "E kaabo, agbẹ wa. A ko le gba iroyin oju-ọjọ loni. "
    "Jọwọ pe pada lẹ́yìn ìgbà díẹ."
)


def _translate_via_groq(text: str, groq_token: str) -> Optional[str]:
    """Translate English text to Yoruba using the Groq SDK."""
    try:
        from groq import Groq, APIError, AuthenticationError, RateLimitError
    except ImportError:
        logger.error("Groq SDK not installed. Run: pip install groq")
        return None

    try:
        client = Groq(api_key=groq_token)
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": text},
            ],
            temperature=0.3,
            max_tokens=2000,
            top_p=1,
            stream=False,  # llama-3.3-70b-versatile supports streaming but
                           # non-streaming is simpler and avoids delta=None edge cases
            stop=None,
        )

        translated = completion.choices[0].message.content
        if translated:
            translated = translated.strip()

        if translated:
            return translated

        logger.error("Groq returned an empty translation.")
        return None

    except AuthenticationError:
        logger.error("Groq authentication failed — check your GROQ_API_KEY.")
        return None
    except RateLimitError:
        logger.error("Groq rate limit hit — consider adding a delay or upgrading plan.")
        return None
    except APIError as e:
        logger.error("Groq API error: %s", e)
        return None
    except Exception as e:
        logger.error("Unexpected error during Groq translation: %s", e)
        return None


def translate_to_yoruba(english_text: str) -> str:
    """
    Translate English text to Yoruba using the Groq API.
    Args:
        english_text: The English text to translate.

                      API key is read from the GROQ_API_KEY environment variable.
    Returns:
        Yoruba translation string, or a hardcoded fallback message on failure.
    """
    if not english_text.strip():
        return ""

    groq_token = os.getenv("GROQ_API_KEY", "")
    logger.info("Translating via Groq (%s): %s...", GROQ_MODEL, english_text[:80])

    if not groq_token:
        logger.error("No GROQ_API_KEY set. Cannot translate.")
        return FALLBACK_YORUBA

    translated = _translate_via_groq(english_text, groq_token)
    if translated:
        logger.info("Translation successful: %s...", translated[:80])
        return translated

    logger.error("Translation failed. Returning fallback Yoruba message.")
    return FALLBACK_YORUBA