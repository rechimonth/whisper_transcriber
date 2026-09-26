"""Modelos SQLAlchemy del SaaS.

Fase 1: solo se define ``Base`` aqui (re-exportada desde database.py).
Los modelos User/CreditWallet/Transaction se implementan en la Fase 2.
"""
from backend.database.database import Base

__all__ = ["Base"]
