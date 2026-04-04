"""
Oro Agbe - Farmer's Matter
Main application entry point
"""

import os
import time
import threading
import requests
import logging
from flask import Flask
from app.ivr_handler import ivr_bp
from app.ussd_handler import ussd_bp
from app.config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# KEEP-ALIVE  — prevents Render free tier from spinning down
# ══════════════════════════════════════════════════════════════════════════════

def keep_alive():
    """
    Pings our own /health endpoint every 10 minutes.
    Render's free tier spins down after 15 minutes of inactivity,
    causing the first incoming call to wait 30-50 seconds for the
    server to wake up — too long for a USSD or IVR session.
    This keeps the server warm so every farmer's call is instant.
    Only runs when BASE_URL is set (i.e. on Render, not local dev).
    """
    def ping():
        # Wait 1 minute after startup before first ping
        time.sleep(60)
        while True:
            base_url = os.getenv("BASE_URL", "")
            if base_url:
                try:
                    resp = requests.get(f"{base_url}/health", timeout=10)
                    logger.info(f"Keep-alive ping → {resp.status_code}")
                except Exception as e:
                    logger.warning(f"Keep-alive ping failed: {e}")
            else:
                logger.debug("BASE_URL not set — skipping keep-alive ping (local dev mode)")
            # Sleep 10 minutes before next ping
            time.sleep(600)

    # Run as a background daemon thread — it dies automatically when the app stops
    thread = threading.Thread(target=ping, name="keep-alive", daemon=True)
    thread.start()
    logger.info("Keep-alive thread started (pings every 10 minutes)")


# ══════════════════════════════════════════════════════════════════════════════
# APP FACTORY
# ══════════════════════════════════════════════════════════════════════════════

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)

    # Register blueprints
    app.register_blueprint(ivr_bp)
    app.register_blueprint(ussd_bp)

    @app.route("/health")
    def health():
        return {"status": "ok", "project": "Oro Agbe - Farmer's Matter"}, 200

    # Start keep-alive background thread
    keep_alive()

    return app


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

# Expose app at module level so gunicorn can find it on Render.
# Render runs: gunicorn "app:create_app()" --bind 0.0.0.0:$PORT
app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)