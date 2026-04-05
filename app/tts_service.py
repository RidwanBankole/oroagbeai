"""
Oro Agbe — Text-to-Speech Service
Converts Yoruba text to spoken audio using facebook/mms-tts-yor loaded
locally via HuggingFace Transformers (VitsModel + AutoTokenizer).

The model is downloaded from HuggingFace Hub on first run using HF_API_TOKEN
and cached in ~/.cache/huggingface/ for all subsequent calls.

Audio pipeline:
  Yoruba text
    → VitsModel tokenizer
    → VitsModel waveform (float32 tensor)
    → scipy writes raw WAV to disk
    → pydub converts WAV → MP3
    → optionally uploaded to Cloudinary
    → public URL returned

Dependencies:
    pip install transformers torch scipy pydub
    apt-get install -y ffmpeg    ← pydub needs ffmpeg for WAV → MP3

The model (~100 MB) is downloaded once and cached. Set HF_API_TOKEN in your
.env so the Hub client can download gated/private assets if needed.

Public interface (unchanged):
    synthesise_yoruba_speech(yoruba_text, ...) -> Optional[str]
"""

import io
import os
import uuid
import logging
import re
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MODEL_ID  = "facebook/mms-tts-yor"
AUDIO_DIR = Path(os.getenv("AUDIO_OUTPUT_DIR", "static/audio"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Module-level cache so the model is loaded once per worker process
_model     = None
_tokenizer = None


# ── Model loader ──────────────────────────────────────────────────────────────

def _load_model():
    """
    Load VitsModel and AutoTokenizer from HuggingFace Hub, caching them in
    module-level globals so they are only loaded once per Gunicorn worker.
    HF_API_TOKEN is passed to the Hub client for authentication.
    """
    global _model, _tokenizer
    if _model is not None and _tokenizer is not None:
        return _model, _tokenizer

    try:
        from transformers import VitsModel, AutoTokenizer
        import torch
    except ImportError as exc:
        logger.error("Missing dependency: %s — run: pip install transformers torch", exc)
        return None, None

    token = os.getenv("HF_API_TOKEN", "").strip() or None

    logger.info("Loading %s from HuggingFace Hub (first load may take a minute)...", MODEL_ID)
    try:
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, token=token)
        _model     = VitsModel.from_pretrained(MODEL_ID, token=token)
        _model.eval()
        logger.info("Model %s loaded successfully.", MODEL_ID)
        return _model, _tokenizer
    except Exception as exc:
        logger.error("Failed to load model %s: %s", MODEL_ID, exc)
        return None, None


# ── Text pre-processing ───────────────────────────────────────────────────────

def _clean_yoruba(text: str) -> str:
    """
    MMS-TTS-YOR was trained on lowercased, unpunctuated text.
    The VitsTokenizer normalises automatically, but we strip punctuation
    ourselves first to avoid any out-of-vocabulary edge cases.
    Yoruba diacritic characters (ẹ, ọ, ṣ, etc.) are preserved.
    """
    text = text.lower()
    # Remove punctuation except Yoruba-safe characters
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    # Collapse multiple spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Synthesis ─────────────────────────────────────────────────────────────────

def _synthesise_to_wav(yoruba_text: str) -> Optional[Path]:
    """
    Run TTS inference and save the waveform as a WAV file.
    Returns the saved Path, or None on failure.
    """
    model, tokenizer = _load_model()
    if model is None or tokenizer is None:
        return None

    try:
        import torch
        import scipy.io.wavfile as wav_writer

        clean_text = _clean_yoruba(yoruba_text)
        logger.info("Synthesising: %s...", clean_text[:80])

        inputs = tokenizer(clean_text, return_tensors="pt")

        with torch.no_grad():
            output = model(**inputs)

        # waveform shape: (1, samples) — squeeze to (samples,)
        waveform      = output.waveform.squeeze().cpu().numpy()
        sampling_rate = model.config.sampling_rate

        wav_path = AUDIO_DIR / f"weather_{uuid.uuid4().hex[:8]}.wav"
        wav_writer.write(str(wav_path), sampling_rate, waveform)
        logger.info("WAV saved to %s (%d Hz, %d samples)", wav_path, sampling_rate, len(waveform))
        return wav_path

    except Exception as exc:
        logger.error("TTS synthesis error: %s", exc)
        return None


def _wav_to_mp3(wav_path: Path) -> Optional[Path]:
    """
    Convert a WAV file to MP3 using pydub + ffmpeg.
    Returns the MP3 Path, or None on failure.
    The source WAV is deleted after successful conversion.
    """
    try:
        from pydub import AudioSegment
    except ImportError:
        logger.error("pydub not installed — run: pip install pydub")
        return None

    try:
        mp3_path = wav_path.with_suffix(".mp3")
        audio    = AudioSegment.from_wav(str(wav_path))
        audio.export(str(mp3_path), format="mp3", bitrate="64k")
        wav_path.unlink(missing_ok=True)   # remove intermediate WAV
        logger.info("MP3 saved to %s", mp3_path)
        return mp3_path
    except Exception as exc:
        logger.error("WAV → MP3 conversion failed: %s", exc)
        return None


# ── Cloudinary upload ─────────────────────────────────────────────────────────

def upload_to_cloudinary(
    filepath: Path,
    cloud_name: str,
    api_key: str,
    api_secret: str,
) -> Optional[str]:
    """
    Upload an audio file to Cloudinary and return the public HTTPS URL.
    Cloudinary requires resource_type='video' for audio files.
    """
    if not all([cloud_name, api_key, api_secret]):
        logger.warning("Cloudinary credentials incomplete. Skipping upload.")
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
            resource_type="video",   # Cloudinary treats audio as 'video'
            folder="oro_agbe",
            use_filename=True,
        )
        url = result.get("secure_url")
        logger.info("Audio uploaded to Cloudinary: %s", url)
        return url
    except Exception as exc:
        logger.error("Cloudinary upload error: %s", exc)
        return None


# ── Public interface ──────────────────────────────────────────────────────────

def synthesise_yoruba_speech(
    yoruba_text: str,
    hf_token: str = "",           # kept for backward compatibility, not used
    base_url: str = os.getenv("BASE_URL"),
    cloudinary_creds: dict = None,
) -> Optional[str]:
    """
    Convert Yoruba text to speech and return a publicly accessible audio URL.

    The model is loaded locally using HuggingFace Transformers.
    HF_API_TOKEN from your .env is used automatically by the HF Hub client
    when downloading the model on first run.

    Args:
        yoruba_text:      Yoruba text to synthesise.
        hf_token:         Unused — kept only for backward compatibility.
        base_url:         Your app's public base URL (for local fallback URL).
        cloudinary_creds: Optional dict with keys: cloud_name, api_key, api_secret.

    Returns:
        Public URL to the MP3 file, or None on failure.
    """
    if not yoruba_text or not yoruba_text.strip():
        logger.warning("Empty Yoruba text received for TTS.")
        return None

    logger.info("Synthesising Yoruba speech via local MMS-TTS-YOR...")

    # Step 1: synthesise → WAV
    wav_path = _synthesise_to_wav(yoruba_text)
    if not wav_path or not wav_path.exists():
        logger.error("TTS failed — no WAV file produced.")
        return None

    # Step 2: WAV → MP3
    mp3_path = _wav_to_mp3(wav_path)
    if not mp3_path or not mp3_path.exists():
        logger.error("TTS failed — WAV to MP3 conversion failed.")
        return None

    # Step 3: upload to Cloudinary if credentials supplied
    if cloudinary_creds:
        print("credentials found")
        url = upload_to_cloudinary(
            mp3_path,
            cloudinary_creds.get("cloud_name", ""),
            cloudinary_creds.get("api_key", ""),
            cloudinary_creds.get("api_secret", ""),
        )
        if url:
            return url

    # Step 4: fall back to serving from Flask static folder
    url = f"{base_url}/static/audio/{mp3_path.name}"
    print("Credentials not found")
    logger.info("Serving audio locally: %s", url)
    return url


# ── Quick test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample = (
        "E kaaro, agbẹ wa. Oju ojo loni dara fun iṣẹ oko. "
        "O le lọ si oko ni kutukutu. Ṣugbọn ṣọra fun ojo to le rọ nigbamii."
    )
    cloudinary_creds = {
        "cloud_name": os.getenv("CLOUDINARY_CLOUD_NAME", ""),
        "api_key":    os.getenv("CLOUDINARY_API_KEY", ""),
        "api_secret": os.getenv("CLOUDINARY_API_SECRET", ""),
    }
    result = synthesise_yoruba_speech(sample, cloudinary_creds=cloudinary_creds)
    print(f"\nAudio URL: {result}")