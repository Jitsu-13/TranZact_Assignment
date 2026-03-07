"""
PDF Generation Microservice — Flask Application.

A microservice that generates PDFs for purchase orders and invoices
in an ERP application. Supports single and bulk generation with:
- Playwright-based HTML-to-PDF rendering with browser pooling
- Celery + Redis async queue for bulk jobs
- Chunked rendering for large documents (500+ line items)
- SHA-256 tamper evidence
- SSE progress streaming for bulk operations
- Resumable downloads (HTTP Range) for unreliable connections
"""

import os

from flask import Flask
from flask_cors import CORS

from src.config import Config
from src.logger import logger


def create_app(testing=False) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    if testing:
        app.config["TESTING"] = True

    # CORS — allow Django monolith to call this service
    CORS(app)

    # Ensure storage directory exists
    os.makedirs(Config.PDF_STORAGE_DIR, exist_ok=True)

    # Register blueprints
    from src.routes.pdf_routes import pdf_bp
    from src.routes.health_routes import health_bp
    app.register_blueprint(pdf_bp)
    app.register_blueprint(health_bp)

    # Global error handlers
    @app.errorhandler(404)
    def not_found(e):
        return {"error": "Endpoint not found"}, 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return {"error": "Method not allowed"}, 405

    @app.errorhandler(500)
    def internal_error(e):
        logger.error(f"Internal server error: {e}", exc_info=True)
        return {"error": "Internal server error"}, 500

    @app.errorhandler(429)
    def rate_limited(e):
        return {"error": "Rate limit exceeded. Please try again later."}, 429

    logger.info("PDF Generation Microservice initialized")
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(
        host=Config.HOST,
        port=Config.PORT,
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
