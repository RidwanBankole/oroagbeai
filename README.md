# Oro Agbe — Farmer's Matter

> An AI-powered voice weather service for Yoruba-speaking farmers.
> Farmers dial a number on any basic phone and hear today's weather forecast
> spoken to them in Yoruba — no internet, no smartphone, no English required.

---

## Architecture

```
Farmer dials → Africa's Talking IVR
                      ↓
              Flask Backend (app.py)
                      ↓
         Open-Meteo Weather API (free)
                      ↓
         NLLB-200 Translation Model
         (English → Yoruba, HF API)
                      ↓
         MMS-TTS-YOR Speech Model
         (Yoruba text → audio, HF API)
                      ↓
         Audio served via Cloudinary
         or local Flask /static/audio/
                      ↓
        Farmer hears Yoruba weather 
```

---

## Project Structure

```
oro_agbe/
├── app.py                   ← Flask entry point
├── test_pipeline.py         ← CLI test tool (no phone needed)
├── requirements.txt
├── .env             
├── app/
│   ├── __init__.py
│   ├── config.py            ← All settings & env vars
│   ├── ivr_handler.py       ← Africa's Talking IVR webhook routes
│   ├── location_service.py  ← Phone number → GPS coordinates
│   ├── weather_service.py   ← Open-Meteo API client
│   ├── translation_service.py ← NLLB-200 English→Yoruba
│   └── tts_service.py       ← MMS-TTS-YOR Yoruba speech synthesis
├── tests/
│   └── test_oro_agbe.py     ← Full test suite
└── static/
    └── audio/               ← Generated audio files stored here
```

---

## Quick Start

### 1. Clone & Install

```bash
git clone <your-repo>
cd oro_agbe

# Create virtual environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
# Edit .env with your API keys 
```

### 3. Run the App

```bash
python app.py
# Flask starts on http://localhost:5000
```

### 4. Expose with ngrok (for Africa's Talking to reach your local server)

```bash
# In a separate terminal
ngrok http 5000

# Copy the https://xxxxx.ngrok.io URL and set it in .env:
# BASE_URL=https://xxxxx.ngrok.io
```

### 5. Test the Pipeline Locally (without a real phone call)

```bash
# Full pipeline test
python test_pipeline.py

# Test for a specific phone number
python test_pipeline.py --phone 08031234567

# Test for a specific city
python test_pipeline.py --city "Akure"

# Test only the weather fetch
python test_pipeline.py --weather-only

# Test only translation
python test_pipeline.py --translate-only "Good day farmer, it is sunny today."
```

---

## Configuration

| Variable | Where to Get It | Required |
|---|---|---|
| `HF_API_TOKEN` | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) | Yes (for API mode) |
| `AT_API_KEY` | [africastalking.com](https://africastalking.com) dashboard | Yes (for real calls) |
| `AT_USERNAME` | Use `sandbox` for testing | Yes |
| `BASE_URL` | Your ngrok or deployed URL | Yes |
| `CLOUDINARY_*` | [cloudinary.com](https://cloudinary.com) free account | Optional |

### AI Mode

Set `TRANSLATION_MODE` and `TTS_MODE` in `.env`:

| Mode | Description | When to use |
|---|---|---|
| `api` | Hugging Face Inference API | Default. Free, no local GPU needed. Can be slow on cold start. |
| `local` | Run models on your machine | Faster after first load. Needs ~1.5 GB disk + CPU/GPU. |

---

## Africa's Talking IVR Setup

1. Sign up at [africastalking.com](https://africastalking.com)
2. Create a voice number
3. In the AT dashboard, set the **callback URL** for your number to:
   ```
   https://your-ngrok-url.ngrok.io/ivr/voice
   ```
4. Set the **hangup callback** to:
   ```
   https://your-ngrok-url.ngrok.io/ivr/hangup
   ```

### IVR Call Flow

```
Farmer dials number
    ↓
/ivr/voice  →  Greeting + menu (Press 1 for weather, Press 0 to repeat)
    ↓
/ivr/action →  Handle key press
    ↓ (key=1)
/ivr/weather →  Run full pipeline → play Yoruba audio
    ↓
/ivr/hangup  →  Log call end
```

---

## Running Tests

```bash
# Unit tests (no API keys needed)
python -m pytest tests/ -v

# Integration tests (requires HF_API_TOKEN and internet)
INTEGRATION_TEST=true python -m pytest tests/ -v -m integration
```

---

## IVR Webhook Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /health` | GET | Health check |
| `POST /ivr/voice` | POST | Entry point — farmer dials |
| `POST /ivr/action` | POST | Handle DTMF key press |
| `POST /ivr/weather` | POST | Full AI pipeline |
| `POST /ivr/hangup` | POST | Call ended logging |

---

## Deployment (Render.com — free tier)

```bash
# 1. Push code to GitHub
git push origin main

# 2. Create a new Web Service on render.com
# Build command: pip install -r requirements.txt
# Start command: gunicorn app:create_app() --bind 0.0.0.0:$PORT

# 3. Add all .env variables in Render's Environment settings

# 4. Update BASE_URL in Render environment to your .onrender.com URL

# 5. Update Africa's Talking callback URLs to your .onrender.com URL
```

---

## Future: Fine-tuning Plan

The MVP uses base NLLB-200 and MMS-TTS-YOR. The next phase involves:

1. **Collect data** — Curate English-Yoruba sentence pairs in weather/agriculture domain
2. **Fine-tune NLLB-200** — Domain-specific translation for farming vocabulary
3. **Fine-tune MMS-TTS-YOR** — Record native Yoruba agricultural speech; train for better prosody
4. **Evaluate** — Compare BLEU scores and native speaker ratings against base models
5. **Deploy fine-tuned models** — Replace HF API calls with fine-tuned model endpoints

Resources:
- [Masakhane NLP](https://www.masakhane.io/) — African NLP community + Yoruba datasets
- [HF Fine-tuning Guide (NLLB)](https://huggingface.co/docs/transformers/model_doc/nllb)
- [HF Fine-tuning Guide (VITS/MMS)](https://huggingface.co/docs/transformers/model_doc/vits)

---

## License

MIT — Built for Nigerian farmers. Ara ilu, ẹ jẹ ka ṣiṣẹ papọ̀.
