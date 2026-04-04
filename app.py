"""
Oro Agbe - Farmer's Matter
Main application entry point
"""

from flask import Flask
from app.ivr_handler import ivr_bp
from app.ussd_handler import ussd_bp 
from app.config import Config
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)

    # Register blueprints
    app.register_blueprint(ivr_bp)
    app.register_blueprint(ussd_bp)  

    @app.route("/health")
    def health():
        return {"status": "ok", "project": "Oro Agbe - Farmer's Matter"}, 200

    return app

# Expose app at module level so gunicorn can find it on Render.
# Render runs: gunicorn "app:create_app()" --bind 0.0.0.0:$PORT
app = create_app()

if __name__ == "__main__":
    # app = create_app()
    app.run(debug=True, port=5000)
