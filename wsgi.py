"""WSGI entry point for Gunicorn."""

from src.app import create_app

app = create_app()
