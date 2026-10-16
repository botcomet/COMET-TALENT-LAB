"""Maintenance planifiée : purge de conservation et consignation de la tête d'audit.

Tout est DÉSACTIVÉ par défaut (``maintenance_interval_hours = 0``) : la purge supprime des données de façon irréversible et la durée de
conservation (180 jours) est une valeur de départ non validée par le DPO. Une exécution est idempotente : plusieurs instances peuvent la lancer.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..db import session_scope

log = logging.getLogger("talentlab.maintenance")


def run_once(settings: Settings | None = None) -> dict[str, Any]:
    """Un passage : purge (si activée) puis consignation de la tête d'audit (si un chemin est configuré)."""
    s = settings or get_settings()
    out: dict[str, Any] = {}
    if s.retention_auto_purge:
        from . import matching as mt
        with session_scope() as db:
            out["purge"] = mt.purge_expired(db, "system")
    if s.audit_head_path:
        from ..audit import verify_chain
        with session_scope() as db:
            res = verify_chain(db)
        p = Path(s.audit_head_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(res, ensure_ascii=False, indent=2))
        tmp.replace(p)                                   # écriture atomique : jamais un fichier à moitié écrit
        out["audit_head"] = {"ok": res["ok"], "head_seq": res.get("head_seq")}
        if not res["ok"]:
            log.error("Chaîne d'audit ROMPUE à l'événement %s", res.get("broken_at_seq"))
    return out


class Scheduler:
    def __init__(self, settings: Settings | None = None, first_delay_s: float = 60.0) -> None:
        self.s = settings or get_settings()
        self.first_delay = first_delay_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.s.maintenance_interval_hours > 0 and (self.s.retention_auto_purge or bool(self.s.audit_head_path))

    def start(self) -> bool:
        if not self.enabled or self._thread is not None:
            return False
        self._thread = threading.Thread(target=self._loop, name="talentlab-maintenance", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        delay = self.first_delay
        while not self._stop.wait(delay):
            try:
                log.info("Maintenance : %s", run_once(self.s))
            except Exception:                            # noqa: BLE001 — une erreur ne tue jamais la boucle
                log.exception("Maintenance : échec du passage")
            delay = self.s.maintenance_interval_hours * 3600
