"""Commandes d'exploitation : ``python -m talentlab.manage <commande>``.

  migrate      applique les migrations de base de données (Alembic) jusqu'à la dernière révision
  current      affiche la révision courante de la base et la révision attendue
  check        code de sortie 0 si la base est à jour, 1 sinon (à placer dans un contrôle de déploiement)
  purge        applique la politique de conservation (suppression des documents et candidats expirés)
  audit-head   vérifie la chaîne d'audit et écrit sa tête dans un fichier (à copier HORS de la base)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from .config import get_settings

ROOT = Path(__file__).resolve().parent.parent


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "talentlab" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", (url or get_settings().database_url).replace("%", "%%"))
    return cfg


def head_revision(cfg: Config | None = None) -> str:
    return ScriptDirectory.from_config(cfg or alembic_config()).get_current_head() or ""


def current_revision(url: str | None = None) -> str | None:
    eng = create_engine(url or get_settings().database_url)
    try:
        with eng.connect() as c:
            return MigrationContext.configure(c).get_current_revision()
    finally:
        eng.dispose()


def schema_is_current(url: str | None = None) -> bool:
    return current_revision(url) == head_revision(alembic_config(url))


def migrate(url: str | None = None) -> str:
    """Applique les migrations. Refuse une base non vide créée sans migrations (jeu de développement) : on ne « tamponne » jamais un schéma inconnu."""
    u = url or get_settings().database_url
    eng = create_engine(u)
    try:
        tables = set(inspect(eng).get_table_names())
    finally:
        eng.dispose()
    if tables and "alembic_version" not in tables:
        raise SystemExit("Base non vide et sans historique de migrations (créée en mode développement) : la recréer, ou migrer les données vers une base neuve.")
    command.upgrade(alembic_config(u), "head")
    return head_revision(alembic_config(u))


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "migrate":
        print("Base à jour : révision", migrate())
        return 0
    if cmd in ("current", "check"):
        cur, head = current_revision(), head_revision()
        if cmd == "current":
            print(json.dumps({"courante": cur, "attendue": head, "a_jour": cur == head}, ensure_ascii=False))
            return 0
        if cur != head:
            print(f"Base non à jour (courante : {cur}, attendue : {head}) : lancer « python -m talentlab.manage migrate ».", file=sys.stderr)
            return 1
        print("Base à jour.")
        return 0
    if cmd == "purge":
        from .db import init_engine, session_scope
        from .models import User
        from .services import matching as mt
        init_engine()
        with session_scope() as db:
            admin = db.query(User).filter(User.role == "admin", User.active.is_(True)).first()
            if admin is None:
                print("Aucun administrateur actif : la purge doit être attribuée à un compte.", file=sys.stderr)
                return 1
            print(json.dumps(mt.purge_expired(db, admin)))
        return 0
    if cmd == "audit-head":
        from .audit import verify_chain
        from .db import init_engine, session_scope
        init_engine()
        with session_scope() as db:
            res = verify_chain(db)
        out = Path(argv[1]) if len(argv) > 1 else Path(get_settings().data_dir) / "audit-head.json"
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2))
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["ok"] else 2
    print(__doc__)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
