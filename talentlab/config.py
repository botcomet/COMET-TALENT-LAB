"""Configuration — tout est surchargeable par variables d'environnement ``TALENTLAB_*``.

Aucun secret n'est présent dans le code ni dans le dépôt (public). En production,
les secrets viennent du gestionnaire de secrets de Comet.
"""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TALENTLAB_", env_file=".env", extra="ignore")

    env: str = "dev"                                   # dev | test | prod
    database_url: str = "sqlite:///./data/talentlab.db"
    data_dir: Path = Path("./data")

    # --- authentification
    auth_mode: str = "dev"                             # dev | gateway
    gateway_email_header: str = "X-Comet-User-Email"
    gateway_secret_header: str = "X-Gateway-Secret"
    gateway_secret: str = ""                           # secret partagé avec la passerelle SSO (jamais en clair dans Git)
    session_secret: str = ""                           # signature du cookie de session
    session_hours: int = 10
    encryption_key: str = ""                           # clé Fernet (base64 urlsafe, 32 octets)

    # --- limites d'import (§21) — configurables
    max_batch_size: int = 20
    max_file_mb: int = 10
    max_pages: int = 40
    min_chars: int = 250
    processing_mode: str = "background"                # background | inline (tests)
    worker_threads: int = 4

    # --- plateforme de sourcing (§5.2) — paramètres de départ, non universels
    platform: str = "turnover"
    platform_max_length: int = 250
    result_target_min: int = 30
    result_target_max: int = 100
    result_too_broad: int = 10_000

    # --- seuils de présentation (§6.9)
    very_interesting_min: int = 96
    work_view_min: int = 95

    # --- conservation (à valider avec le DPO : valeur de départ, non approuvée)
    retention_days: int = 180

    # --- IA (optionnelle : l'application fonctionne sans)
    llm_provider: str = "none"                         # none | anthropic
    llm_api_key: str = ""
    llm_model: str = ""

    @model_validator(mode="after")
    def _guard(self) -> "Settings":
        if self.env == "prod":
            problems = []
            if self.auth_mode != "gateway":
                problems.append("auth_mode doit être « gateway » en production (le mode dev est interdit)")
            for name in ("gateway_secret", "session_secret", "encryption_key"):
                if not getattr(self, name):
                    problems.append(f"{name} est obligatoire en production")
            if self.database_url.startswith("sqlite"):
                problems.append("une base PostgreSQL est attendue en production")
            if problems:
                raise ValueError("Configuration de production invalide : " + " ; ".join(problems))
        return self

    # --- résolution des secrets de développement
    def resolved_session_secret(self) -> str:
        return self.session_secret or _dev_secret(self.data_dir, "session")

    def resolved_encryption_key(self) -> str:
        if self.encryption_key:
            return self.encryption_key
        from cryptography.fernet import Fernet
        return _dev_secret(self.data_dir, "fernet", generator=lambda: Fernet.generate_key().decode())


def _dev_secret(data_dir: Path, name: str, generator=None) -> str:
    """Secret éphémère de développement, stocké hors Git (data/ est ignoré)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    p = data_dir / f".dev_{name}"
    if p.exists():
        return p.read_text().strip()
    val = generator() if generator else secrets.token_urlsafe(48)
    p.write_text(val)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return val


@lru_cache
def get_settings() -> Settings:
    return Settings()
