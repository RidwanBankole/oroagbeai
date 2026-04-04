"""
Oro Agbe — Text-to-Speech Service
Converts Yoruba text to spoken audio using gTTS (Google Text-to-Speech).
Audio is saved as an .mp3 file and can optionally be uploaded to Cloudinary.
Keeps the same public function name `synthesise_yoruba_speech()` so existing
code continues to work without changes.

Dependencies:
    pip install gTTS
"""

import os
import uuid
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

AUDIO_DIR = Path(os.getenv("AUDIO_OUTPUT_DIR", "static/audio"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# GTTS — YORUBA SPEECH SYNTHESIS
# ══════════════════════════════════════════════════════════════════════════════

def _tts_via_gtts(yoruba_text: str) -> Optional[Path]:
    """
    Generate Yoruba speech via gTTS (Google Translate TTS) and save as MP3.
    gTTS supports Yoruba with lang='yo'. No API key required.
    Returns the saved file path, or None on failure.
    """
    try:
        from gtts import gTTS
    except ImportError:
        logger.error("gTTS not installed. Run: pip install gTTS")
        return None

    try:
        filename = AUDIO_DIR / f"weather_{uuid.uuid4().hex[:8]}.mp3"
        # tts = gTTS(text=yoruba_text, lang="yo", slow=False)
        tts = gTTS(text=yoruba_text, lang="yo", slow=False, lang_check=False)
        tts.save(str(filename))
        logger.info("Audio saved to %s", filename)
        return filename
    except Exception as e:
        logger.error("gTTS synthesis error: %s", e)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# CLOUDINARY UPLOAD  (optional)
# ══════════════════════════════════════════════════════════════════════════════

def upload_to_cloudinary(
    filepath: Path,
    cloud_name: str,
    api_key: str,
    api_secret: str,
) -> Optional[str]:
    """
    Upload an audio file to Cloudinary and return the public URL.
    Cloudinary uses resource_type='video' for audio files.
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
    except Exception as e:
        logger.error("Cloudinary upload error: %s", e)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC INTERFACE
# ══════════════════════════════════════════════════════════════════════════════

def synthesise_yoruba_speech(
    yoruba_text: str,
    hf_token: str = "",          # kept for backward compatibility, no longer used
    base_url: str = "http://localhost:5000",
    cloudinary_creds: dict = None,
) -> Optional[str]:
    """
    Convert Yoruba text to speech and return a publicly accessible audio URL.

    Args:
        yoruba_text:      Yoruba text to synthesise.
        hf_token:         Unused — kept only for backward compatibility.
        base_url:         Your app's public base URL (used for local fallback URL).
        cloudinary_creds: Optional dict with keys: cloud_name, api_key, api_secret.

    Returns:
        Public URL to the audio file (.mp3), or None on failure.
    """
    if not yoruba_text or not yoruba_text.strip():
        logger.warning("Empty Yoruba text received for TTS.")
        return None

    logger.info("Synthesising Yoruba speech via gTTS...")

    filepath = _tts_via_gtts(yoruba_text)

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
    logger.info("Serving audio locally: %s", url)
    return url


# ══════════════════════════════════════════════════════════════════════════════
# QUICK TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    sample_yoruba = (
        "E kaaro, agbẹ wa. Oju ojo loni dara fun iṣẹ oko. "
        "O le lọ si oko ni kutukutu. Ṣugbọn ṣọra fun ojo to le rọ nigbamii."
    )
    result_url = synthesise_yoruba_speech(sample_yoruba)
    print(f"\nAudio URL: {result_url}")

