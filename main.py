import os
import time
import threading
import requests
import logging
from flask import Flask
from app.ussd_handler import ussd_bp
from app.config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)

def keep_alive():
    def ping():
        time.sleep(60)
        while True:
            base_url = os.getenv("BASE_URL", "")
            if base_url:
                try:
                    resp = requests.get(f"{base_url}/health", timeout=10)
                    logger.info(f"Keep-alive ping → {resp.status_code}")
                except Exception as e:
                    logger.warning(f"Keep-alive ping failed: {e}")
            time.sleep(600)

    thread = threading.Thread(target=ping, name="keep-alive", daemon=True)
    thread.start()
    logger.info("Keep-alive thread started")

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)
    app.register_blueprint(ussd_bp)

    @app.route("/health")
    def health():
        return {"status": "ok", "project": "Oro Agbe - Farmer's Matter"}, 200

    if os.getenv("ENABLE_KEEP_ALIVE", "false").lower() == "true":
        keep_alive()

    return app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)