"""Environnement Alembic : le schéma de référence est celui des modèles ; l'URL vient de la configuration de l'application."""
from __future__ import annotations

from alembic import context
from sqlalchemy import LargeBinary, create_engine, pool
from sqlalchemy.types import TypeDecorator

from talentlab import models  # noqa: F401  (enregistre les tables)
from talentlab.config import get_settings
from talentlab.db import Base

target_metadata = Base.metadata


def _render_item(type_, obj, autogen_context):
    """Les colonnes chiffrées (EncText, EncJSON, EncBytes) sont des octets en base : les migrations ne dépendent pas du code de l'application."""
    if type_ == "type" and isinstance(obj, TypeDecorator):
        autogen_context.imports.add("import sqlalchemy as sa")
        return "sa.LargeBinary()"
    return False


def _url() -> str:
    return context.config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True, render_item=_render_item)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = context.config.attributes.get("connection")
    if connectable is None:
        engine = create_engine(_url(), poolclass=pool.NullPool)
        with engine.connect() as connection:
            _run(connection)
        return
    _run(connectable)


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True, render_item=_render_item, render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
