"""Extraction de documents dans un PROCESSUS ISOLÉ, avec limites de mémoire, de CPU et de temps.

Un PDF ou un DOCX est un format complexe fourni par un tiers : il peut être conçu pour consommer des gigaoctets (archives, flux compressés) ou des
minutes de CPU. Dans le processus du serveur, un seul document piégé suffirait à figer tous les utilisateurs. L'extraction se fait donc dans un
processus enfant ``spawn`` (sans état hérité) dont la mémoire (``RLIMIT_AS``) et le CPU (``RLIMIT_CPU``) sont bornés, et que le parent tue au
bout de ``timeout_s`` secondes. Les fichiers texte, sans risque, restent traités dans le processus.
"""
from __future__ import annotations

import multiprocessing as mp
from typing import Any

from ..domain.cv_extract import Extraction, ExtractionError, extract_text, sniff


def _worker(conn: Any, data: bytes, filename: str, kwargs: dict[str, Any], memory_mb: int, cpu_s: int) -> None:
    try:
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 2))
        except (ImportError, ValueError, OSError):
            pass                                              # plateforme sans resource : le délai du parent reste la protection
        ex = extract_text(data, filename, **kwargs)
        conn.send(("ok", ex))
    except ExtractionError as e:
        conn.send(("err", e.code, e.message))
    except MemoryError:
        conn.send(("err", "too_large", "Le document dépasse la mémoire autorisée pour son analyse : probablement pas un CV."))
    except BaseException:                                     # noqa: BLE001 — jamais de trace interne dans la réponse
        conn.send(("err", "corrupt", "Le document est illisible."))
    finally:
        conn.close()


def extract_isolated(data: bytes, filename: str, *, timeout_s: int = 30, memory_mb: int = 1536, **kwargs: Any) -> Extraction:
    """Même contrat que ``extract_text`` ; lève ``ExtractionError('timeout' | 'too_large' | …)`` si le document épuise ses ressources."""
    if sniff(data) == "txt":
        return extract_text(data, filename, **kwargs)
    ctx = mp.get_context("spawn")
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_worker, args=(send, data, filename, kwargs, memory_mb, timeout_s), daemon=True)
    proc.start()
    send.close()
    try:
        if recv.poll(timeout_s):
            try:
                msg = recv.recv()
            except EOFError:
                msg = None
        else:
            msg = "timeout"
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(5)
        recv.close()
    if msg == "timeout":
        raise ExtractionError("timeout", f"L'extraction du document a dépassé {timeout_s} s : document anormal (contenu démesuré). Aucun score n'est calculé.")
    if msg is None:                                           # processus mort sans réponse : mémoire ou CPU épuisés
        raise ExtractionError("too_large", "Le document a dépassé les ressources autorisées pour son analyse : probablement pas un CV.")
    if msg[0] == "ok":
        return msg[1]
    raise ExtractionError(msg[1], msg[2])
