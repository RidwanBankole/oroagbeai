"""
Oro Agbe — Text-to-Speech Service
Converts Yoruba text to spoken audio locally using facebook/mms-tts-yor.

Audio is saved as a .wav file and can optionally be uploaded to Cloudinary.
This version is local-only and keeps the same public function name
`synthesise_yoruba_speech()` so existing code will continue to work.
"""

import os
import uuid
import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

AUDIO_DIR = Path(os.getenv("AUDIO_OUTPUT_DIR", "static/audio"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

_local_tts_model = None
_local_tts_tokenizer = None


# ══════════════════════════════════════════════════════════════════════════════
# AUDIO UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def _numpy_to_wav(audio_array: np.ndarray, filepath: Path, sample_rate: int) -> bool:
    """
    Convert a numpy float waveform to a 16-bit PCM WAV file.
    """
    try:
        import soundfile as sf

        sf.write(str(filepath), audio_array, sample_rate, subtype="PCM_16")
        return True

    except ImportError:
        # Fallback if soundfile is not installed
        try:
            import wave

            audio_array = np.clip(audio_array, -1.0, 1.0)
            audio_int16 = (audio_array * 32767).astype(np.int16)

            with wave.open(str(filepath), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio_int16.tobytes())

            return True

        except Exception as e:
            logger.error(f"Fallback WAV save failed: {e}")
            return False

    except Exception as e:
        logger.error(f"Error saving WAV with soundfile: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# LOCAL MMS-TTS-YOR
# ══════════════════════════════════════════════════════════════════════════════

def _get_local_tts():
    """
    Lazy-load local MMS Yoruba TTS model and tokenizer.
    Downloads the model on first run.
    """
    global _local_tts_model, _local_tts_tokenizer

    if _local_tts_model is None or _local_tts_tokenizer is None:
        try:
            import torch
            from transformers import VitsModel, AutoTokenizer

            model_name = "facebook/mms-tts-yor"

            logger.info("Loading MMS-TTS-YOR locally (first run may download model files)...")
            _local_tts_tokenizer = AutoTokenizer.from_pretrained(model_name)
            _local_tts_model = VitsModel.from_pretrained(model_name)

            device = "cuda" if torch.cuda.is_available() else "cpu"
            _local_tts_model.to(device)
            _local_tts_model.eval()

            logger.info(f"TTS model loaded successfully on {device}.")

        except ImportError:
            logger.error("Required packages not installed. Run: pip install transformers torch soundfile")
            raise

    return _local_tts_model, _local_tts_tokenizer


def _tts_locally(yoruba_text: str) -> Optional[Path]:
    """
    Generate Yoruba speech locally and save it as a WAV file.
    Returns the saved file path or None on failure.
    """
    try:
        import torch

        model, tokenizer = _get_local_tts()
        device = next(model.parameters()).device

        inputs = tokenizer(yoruba_text, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            output = model(**inputs).waveform

        audio_np = output.squeeze().cpu().numpy()
        sample_rate = model.config.sampling_rate

        filename = AUDIO_DIR / f"weather_{uuid.uuid4().hex[:8]}.wav"

        success = _numpy_to_wav(audio_np, filename, sample_rate)
        if not success:
            logger.error("Failed to save generated audio.")
            return None

        logger.info(f"Audio saved locally to {filename}")
        return filename

    except Exception as e:
        logger.error(f"Local TTS error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# CLOUDINARY UPLOAD  (optional)
# ══════════════════════════════════════════════════════════════════════════════

def upload_to_cloudinary(filepath: Path, cloud_name: str, api_key: str, api_secret: str) -> Optional[str]:
    """
    Upload an audio file to Cloudinary and return the public URL.
    Cloudinary uses resource_type='video' for audio files.
    """
    if not all([cloud_name, api_key, api_secret]):
        logger.warning("Cloudinary credentials not set. Serving audio locally.")
        return None

    try:
        import cloudinary
        import cloudinary.uploader

        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
        )

        result = cloudinary.uploader.upload(
            str(filepath),
            resource_type="video",
            folder="oro_agbe",
            use_filename=True,
        )

        url = result.get("secure_url")
        logger.info(f"Audio uploaded to Cloudinary: {url}")
        return url

    except Exception as e:
        logger.error(f"Cloudinary upload error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC INTERFACE
# ══════════════════════════════════════════════════════════════════════════════

def synthesise_yoruba_speech(
    yoruba_text: str,
    hf_token: str = "",
    base_url: str = "http://localhost:5000",
    cloudinary_creds: dict = None,
) -> Optional[str]:
    """
    Convert Yoruba text to speech and return a publicly accessible audio URL.

    Args:
        yoruba_text: Yoruba text to synthesise.
        hf_token: Unused, kept only to preserve backward compatibility.
        base_url: Your app's public base URL.
        cloudinary_creds: Optional dict with cloud_name, api_key, api_secret.

    Returns:
        Public URL to the audio file, or None on failure.
    """
    if not yoruba_text or not yoruba_text.strip():
        logger.warning("Empty Yoruba text received for TTS.")
        return None

    logger.info("Synthesising Yoruba speech (local mode)...")

    filepath = _tts_locally(yoruba_text)

    if not filepath or not filepath.exists():
        logger.error("TTS failed — no audio file produced.")
        return None

    # Try Cloudinary first if credentials are supplied
    if cloudinary_creds:
        url = upload_to_cloudinary(
            filepath,
            cloudinary_creds.get("cloud_name", ""),
            cloudinary_creds.get("api_key", ""),
            cloudinary_creds.get("api_secret", ""),
        )
        if url:
            return url

    # Fall back to serving from local Flask static folder
    url = f"{base_url}/static/audio/{filepath.name}"
    logger.info(f"Serving audio locally: {url}")
    return url


# ══════════════════════════════════════════════════════════════════════════════
# QUICK TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    sample_yoruba = (
        "E kaaro, agbẹ wa. Oju ojo loni dara fun iṣẹ oko. "
        "O le lọ si oko ni kutukutu. Ṣugbọn ṣọra fun ojo to le rọ nigbamii."
    )

    url = synthesise_yoruba_speech(sample_yoruba)
    print(f"\nAudio URL: {url}")




























# """
# Oro Agbe — Text-to-Speech Service
# Converts Yoruba text → spoken audio using:
#   - Hugging Face Inference API (cloud, free tier)  — MODE: "api"
#   - Local transformers pipeline                    — MODE: "local"

# Audio is saved as a .wav file and optionally uploaded to Cloudinary.
# """

# import os
# import uuid
# import time
# import logging
# import requests
# import numpy as np
# from pathlib import Path
# from typing import Optional

# logger = logging.getLogger(__name__)

# TTS_MODE = os.getenv("TTS_MODE", "api")   # "api" or "local"

# HF_TTS_URL = "https://api-inference.huggingface.co/models/facebook/mms-tts-yor"

# AUDIO_DIR = Path(os.getenv("AUDIO_OUTPUT_DIR", "static/audio"))
# AUDIO_DIR.mkdir(parents=True, exist_ok=True)


# # ══════════════════════════════════════════════════════════════════════════════
# # AUDIO UTILITIES
# # ══════════════════════════════════════════════════════════════════════════════

# def _save_wav(audio_bytes: bytes, filepath: Path, sample_rate: int = 16000) -> bool:
#     """Save raw audio bytes as a proper WAV file."""
#     try:
#         import wave, struct

#         # If the bytes already start with RIFF header, write directly
#         if audio_bytes[:4] == b"RIFF":
#             filepath.write_bytes(audio_bytes)
#             return True

#         # Otherwise wrap raw PCM in a WAV container
#         num_samples = len(audio_bytes) // 2   # 16-bit samples
#         with wave.open(str(filepath), "wb") as wf:
#             wf.setnchannels(1)
#             wf.setsampwidth(2)
#             wf.setframerate(sample_rate)
#             wf.writeframes(audio_bytes)
#         return True

#     except Exception as e:
#         logger.error(f"Error saving WAV: {e}")
#         # Last resort — save raw bytes
#         filepath.write_bytes(audio_bytes)
#         return True


# def _numpy_to_wav(audio_array: np.ndarray, filepath: Path, sample_rate: int) -> bool:
#     """Convert a numpy float32 array to a 16-bit PCM WAV file."""
#     try:
#         import soundfile as sf
#         sf.write(str(filepath), audio_array, sample_rate, subtype="PCM_16")
#         return True
#     except ImportError:
#         # Fallback: manual conversion without soundfile
#         import wave
#         audio_int16 = (audio_array * 32767).astype(np.int16)
#         with wave.open(str(filepath), "wb") as wf:
#             wf.setnchannels(1)
#             wf.setsampwidth(2)
#             wf.setframerate(sample_rate)
#             wf.writeframes(audio_int16.tobytes())
#         return True
#     except Exception as e:
#         logger.error(f"Error saving numpy audio: {e}")
#         return False


# # ══════════════════════════════════════════════════════════════════════════════
# # HUGGING FACE INFERENCE API  (MODE = "api")
# # ══════════════════════════════════════════════════════════════════════════════

# def _tts_via_api(yoruba_text: str, hf_token: str) -> Optional[Path]:
#     """
#     Call HF Inference API for MMS-TTS-YOR.
#     Returns path to saved .wav file or None on failure.
#     """
#     headers = {"Authorization": f"Bearer {hf_token}"}

#     for attempt in range(1, 4):
#         try:
#             logger.info(f"TTS API attempt {attempt}/3...")
#             resp = requests.post(
#                 HF_TTS_URL,
#                 headers=headers,
#                 json={"inputs": yoruba_text},
#                 timeout=90
#             )

#             if resp.status_code == 503:
#                 wait = resp.json().get("estimated_time", 20)
#                 logger.warning(f"TTS model loading. Waiting {wait:.0f}s...")
#                 time.sleep(min(wait, 30))
#                 continue

#             resp.raise_for_status()

#             # API returns raw audio bytes
#             filename = AUDIO_DIR / f"weather_{uuid.uuid4().hex[:8]}.wav"
#             _save_wav(resp.content, filename)
#             logger.info(f"Audio saved to {filename}")
#             return filename

#         except requests.RequestException as e:
#             logger.error(f"TTS API error (attempt {attempt}): {e}")
#             if attempt == 3:
#                 return None
#             time.sleep(5)

#     return None


# # ══════════════════════════════════════════════════════════════════════════════
# # LOCAL TRANSFORMERS PIPELINE  (MODE = "local")
# # ══════════════════════════════════════════════════════════════════════════════

# _local_tts_model    = None
# _local_tts_tokenizer = None

# def _get_local_tts():
#     """Lazy-load local MMS-TTS-YOR model."""
#     global _local_tts_model, _local_tts_tokenizer
#     if _local_tts_model is None:
#         try:
#             from transformers import VitsModel, AutoTokenizer
#             import torch
#             logger.info("Loading MMS-TTS-YOR locally (first run downloads ~400 MB)...")
#             _local_tts_tokenizer = AutoTokenizer.from_pretrained("facebook/mms-tts-yor")
#             _local_tts_model     = VitsModel.from_pretrained("facebook/mms-tts-yor")
#             _local_tts_model.eval()
#             logger.info("TTS model loaded successfully.")
#         except ImportError:
#             logger.error("transformers / torch not installed. Run: pip install transformers torch")
#             raise
#     return _local_tts_model, _local_tts_tokenizer


# def _tts_locally(yoruba_text: str) -> Optional[Path]:
#     """Generate Yoruba speech locally."""
#     try:
#         import torch
#         model, tokenizer = _get_local_tts()

#         inputs = tokenizer(yoruba_text, return_tensors="pt")
#         with torch.no_grad():
#             output = model(**inputs).waveform

#         audio_np = output.squeeze().cpu().numpy()
#         sample_rate = model.config.sampling_rate

#         filename = AUDIO_DIR / f"weather_{uuid.uuid4().hex[:8]}.wav"
#         _numpy_to_wav(audio_np, filename, sample_rate)
#         logger.info(f"Audio saved locally to {filename}")
#         return filename

#     except Exception as e:
#         logger.error(f"Local TTS error: {e}")
#         return None


# # ══════════════════════════════════════════════════════════════════════════════
# # CLOUDINARY UPLOAD  (optional — for serving audio via public URL)
# # ══════════════════════════════════════════════════════════════════════════════

# def upload_to_cloudinary(filepath: Path, cloud_name: str, api_key: str, api_secret: str) -> Optional[str]:
#     """
#     Upload an audio file to Cloudinary and return the public URL.
#     Cloudinary free tier = 25 GB storage, 25 GB bandwidth/month.
#     """
#     if not all([cloud_name, api_key, api_secret]):
#         logger.warning("Cloudinary credentials not set. Serving audio locally.")
#         return None

#     try:
#         import cloudinary
#         import cloudinary.uploader

#         cloudinary.config(
#             cloud_name=cloud_name,
#             api_key=api_key,
#             api_secret=api_secret,
#         )
#         result = cloudinary.uploader.upload(
#             str(filepath),
#             resource_type="video",   # Cloudinary uses "video" for audio
#             folder="oro_agbe",
#             use_filename=True,
#         )
#         url = result.get("secure_url")
#         logger.info(f"Audio uploaded to Cloudinary: {url}")
#         return url

#     except Exception as e:
#         logger.error(f"Cloudinary upload error: {e}")
#         return None


# # ══════════════════════════════════════════════════════════════════════════════
# # PUBLIC INTERFACE
# # ══════════════════════════════════════════════════════════════════════════════

# def synthesise_yoruba_speech(
#     yoruba_text: str,
#     hf_token: str = "",
#     base_url: str = "http://localhost:5000",
#     cloudinary_creds: dict = None,
# ) -> Optional[str]:
#     """
#     Convert Yoruba text to speech and return a publicly accessible audio URL.

#     Args:
#         yoruba_text:      Yoruba text to synthesise.
#         hf_token:         HF API token (for API mode).
#         base_url:         Your app's public base URL (ngrok or deployed URL).
#         cloudinary_creds: Dict with cloud_name, api_key, api_secret (optional).

#     Returns:
#         Public URL to the audio file, or None on failure.
#     """
#     if not yoruba_text.strip():
#         return None

#     logger.info(f"Synthesising speech ({TTS_MODE} mode)...")

#     filepath = None

#     if TTS_MODE == "local":
#         filepath = _tts_locally(yoruba_text)
#     else:
#         if not hf_token:
#             logger.warning("No HF_API_TOKEN. Falling back to local TTS.")
#             filepath = _tts_locally(yoruba_text)
#         else:
#             filepath = _tts_via_api(yoruba_text, hf_token)

#     if not filepath or not filepath.exists():
#         logger.error("TTS failed — no audio file produced.")
#         return None

#     # ── Try Cloudinary first, fall back to local serving ──────────────────
#     if cloudinary_creds:
#         url = upload_to_cloudinary(
#             filepath,
#             cloudinary_creds.get("cloud_name", ""),
#             cloudinary_creds.get("api_key", ""),
#             cloudinary_creds.get("api_secret", ""),
#         )
#         if url:
#             return url

#     # Serve from local Flask static folder
#     relative = filepath.name
#     url = f"{base_url}/static/audio/{relative}"
#     logger.info(f"Serving audio locally: {url}")
#     return url


# # ── Quick test ─────────────────────────────────────────────────────────────────
# if __name__ == "__main__":
#     sample_yoruba = (
#         "Ekaaro, agbẹ wa. Oju ojo ni Ibadan loni jẹ iwọn otutu 31 iwọn Celsius. "
#         "Afẹfẹ n fẹ lati ariwa-ila-oorun. Ọjọ rere fun iṣẹ oko."
#     )
#     token = os.getenv("HF_API_TOKEN", "")
#     url = synthesise_yoruba_speech(sample_yoruba, hf_token=token)
#     print(f"\nAudio URL: {url}")
