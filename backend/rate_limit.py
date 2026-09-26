"""Rate limiter compartido (slowapi) para evitar importaciones circulares.

El ``Limiter`` vive aqui para que `backend/main.py` (estado + handler 429) y
los routers (decoradores) lo importen sin ciclos.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
