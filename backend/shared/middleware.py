"""
Shared middleware registration.

Responsibilities:
- Global exception handlers
"""

from fastapi import FastAPI
from shared.exception_handlers import register_exception_handlers


def register_middleware(app: FastAPI) -> None:
    """Attach all middleware to the FastAPI application."""
    register_exception_handlers(app)
