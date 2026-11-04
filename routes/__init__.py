from __future__ import annotations

from .api import bp as api_bp
from .documents import bp as documents_bp
from .public_forms import bp as public_forms_bp

__all__ = ["api_bp", "documents_bp", "public_forms_bp"]
