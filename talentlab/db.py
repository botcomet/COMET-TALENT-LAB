"""Base de données : moteur, sessions et colonnes chiffrées au repos.

Les textes de CV, les extraits de preuves, les notes d'appel et les évaluations
sont chiffrés au niveau applicatif (Fernet) : une fuite de la base ou d'une
sauvegarde ne livre pas de données candidat en clair.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import LargeBinary, Text, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import Settings, get_settings


class Base(DeclarativeBase):
    pass


class _Crypto:
    def __init__(self) -> None:
        self._fernet: Fernet | None = None

    def configure(self, key: str) -> None:
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    @property
    def f(self) -> Fernet:
        if self._fernet is None:
            self.configure(get_settings().resolved_encryption_key())
        return self._fernet  # type: ignore[return-value]

    def enc(self, data: bytes) -> bytes:
        return self.f.encrypt(data)

    def dec(self, token: bytes) -> bytes:
        try:
            return self.f.decrypt(token)
        except InvalidToken as e:     # clé différente : on ne renvoie jamais de données illisibles comme valides
            raise RuntimeError("Déchiffrement impossible : clé de chiffrement différente de celle des données.") from e


crypto = _Crypto()


class EncText(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect):
        return None if value is None else crypto.enc(value.encode("utf-8"))

    def process_result_value(self, value: bytes | None, dialect):
        return None if value is None else crypto.dec(bytes(value)).decode("utf-8")


class EncJSON(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: Any, dialect):
        return None if value is None else crypto.enc(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))

    def process_result_value(self, value: bytes | None, dialect):
        return None if value is None else json.loads(crypto.dec(bytes(value)).decode("utf-8"))


class EncBytes(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: bytes | None, dialect):
        return None if value is None else crypto.enc(value)

    def process_result_value(self, value: bytes | None, dialect):
        return None if value is None else crypto.dec(bytes(value))


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_engine(settings: Settings | None = None) -> Engine:
    global _engine, _SessionLocal
    s = settings or get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    crypto.configure(s.resolved_encryption_key())
    kwargs: dict[str, Any] = {}
    if s.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    _engine = create_engine(s.database_url, future=True, **kwargs)
    if s.database_url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _fk(dbapi_conn, _):          # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    from . import models  # noqa: F401  (enregistre les tables)
    Base.metadata.create_all(_engine)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    return _engine  # type: ignore[return-value]


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        init_engine()
    return _SessionLocal  # type: ignore[return-value]


@contextmanager
def session_scope() -> Iterator[Session]:
    s = session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """Dépendance FastAPI : une transaction par requête."""
    s = session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
