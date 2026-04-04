import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

GROQ_MODEL = "openai/gpt-oss-20b"

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
            temperature=0.3,              # low = more deterministic translation
            max_completion_tokens=2000,
            top_p=1,
            reasoning_effort="medium",
            stream=True,
            stop=None,
        )

        # Collect streamed chunks into a single string
        result = ""
        for chunk in completion:
            delta = chunk.choices[0].delta.content
            if delta:
                result += delta

        translated = result.strip()
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







# import os
# import time
# import logging
# from typing import Optional

# import requests

# logger = logging.getLogger(__name__)

# TRANSLATION_MODE = os.getenv("TRANSLATION_MODE", "api")

# HF_API_URL = "https://api-inference.huggingface.co/models/facebook/nllb-200-distilled-600M"

# _local_tokenizer = None
# _local_model = None


# def _translate_via_api(text: str, hf_token: str) -> Optional[str]:
#     headers = {"Authorization": f"Bearer {hf_token}"}
#     payload = {
#         "inputs": text,
#         "parameters": {
#             "src_lang": "eng_Latn",
#             "tgt_lang": "yor_Latn",
#             "max_length": 512,
#         },
#     }

#     for attempt in range(1, 4):
#         try:
#             logger.info("Translation API attempt %s/3...", attempt)
#             resp = requests.post(HF_API_URL, headers=headers, json=payload, timeout=60)

#             if resp.status_code == 503:
#                 wait = resp.json().get("estimated_time", 20)
#                 logger.warning("Model loading. Waiting %.0fs...", wait)
#                 time.sleep(min(wait, 30))
#                 continue

#             resp.raise_for_status()
#             result = resp.json()

#             if isinstance(result, list) and result:
#                 return result[0].get("translation_text", "")
#             logger.error("Unexpected API response: %s", result)
#             return None

#         except requests.RequestException as e:
#             logger.error("Translation API error (attempt %s): %s", attempt, e)
#             if attempt == 3:
#                 return None
#             time.sleep(5)

#     return None


# def _get_local_nllb():
#     global _local_tokenizer, _local_model

#     if _local_tokenizer is None or _local_model is None:
#         try:
#             import torch
#             from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

#             model_name = "facebook/nllb-200-distilled-600M"
#             logger.info("Loading NLLB model locally...")
#             _local_tokenizer = AutoTokenizer.from_pretrained(model_name)
#             _local_model = AutoModelForSeq2SeqLM.from_pretrained(model_name)

#             device = "cuda" if torch.cuda.is_available() else "cpu"
#             _local_model.to(device)
#             _local_model.eval()
#             logger.info("NLLB model loaded on %s.", device)

#         except ImportError:
#             logger.error("Install dependencies: pip install transformers sentencepiece torch")
#             raise

#     return _local_tokenizer, _local_model


# def _translate_locally(text: str) -> Optional[str]:
#     try:
#         import torch

#         tokenizer, model = _get_local_nllb()
#         device = next(model.parameters()).device

#         inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
#         inputs = {k: v.to(device) for k, v in inputs.items()}

#         forced_bos_token_id = tokenizer.convert_tokens_to_ids("yor_Latn")

#         with torch.inference_mode():
#             outputs = model.generate(
#                 **inputs,
#                 forced_bos_token_id=forced_bos_token_id,
#                 max_new_tokens=256,
#             )

#         return tokenizer.batch_decode(outputs, skip_special_tokens=True)[0]

#     except Exception as e:
#         logger.error("Local translation error: %s", e)
#         return None


# def translate_to_yoruba(english_text: str, hf_token: str = "") -> str:
#     if not english_text.strip():
#         return ""

#     logger.info("Translating (%s mode): %s...", TRANSLATION_MODE, english_text[:80])

#     translated = None

#     if TRANSLATION_MODE == "local":
#         translated = _translate_locally(english_text)
#     else:
#         if not hf_token:
#             logger.warning("No HF_API_TOKEN set. Falling back to local translation.")
#             translated = _translate_locally(english_text)
#         else:
#             translated = _translate_via_api(english_text, hf_token)

#     if translated:
#         logger.info("Translation successful: %s...", translated[:80])
#         return translated

#     logger.error("Translation failed. Using fallback Yoruba message.")
#     return "E kaabo, agbẹ wa. A ko le gba iroyin oju-ọjọ loni. Jọwọ pe pada lẹ́yìn ìgbà díẹ."


