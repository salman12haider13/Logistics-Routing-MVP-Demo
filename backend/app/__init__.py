from flask import Flask
from flask_cors import CORS

from app.routes.routing import routing_bp


def create_app():
    app = Flask(__name__)
    CORS(app)

    app.register_blueprint(routing_bp)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "backend"}

    return app