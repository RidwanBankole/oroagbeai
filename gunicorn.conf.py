# Gunicorn production config for Oro Agbe
# Render sets $PORT automatically — gunicorn reads it via render.yaml startCommand

workers = 2          # 2 is enough for Render free tier
timeout = 120        # Allow up to 120s for AI model calls (translation + TTS can be slow)
worker_class = "sync"
loglevel = "info"
accesslog = "-"      # Log to stdout (Render captures this)
errorlog  = "-"
