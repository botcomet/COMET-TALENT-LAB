"""Limitation de débit applicative : fenêtre glissante par identité, en mémoire du processus.

Seconde ligne de défense (la première est la passerelle). L'identité est celle de la requête : en-tête d'identité de la passerelle, sinon cookie de
session, sinon adresse. Ce n'est PAS un quota partagé entre instances : avec plusieurs instances, la limite effective est multipliée d'autant.
"""
from __future__ import annotations

import hashlib
import time
from collections import deque

_HEAVY_SUFFIXES = ("/cvs", "/tools/extract-text", "/assistant", "/feedback", "/auth/dev-login", "/boond/import")


def _identity(scope) -> str:
    headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
    raw = headers.get("x-comet-user-email") or headers.get("cookie") or (scope.get("client") or ("", 0))[0]
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class RateLimit:
    def __init__(self, app, settings_fn, clock=time.monotonic) -> None:
        self.app, self.settings_fn, self.clock = app, settings_fn, clock
        self.hits: dict[tuple[str, str], deque[float]] = {}

    async def __call__(self, scope, receive, send):
        s = self.settings_fn()
        if scope["type"] != "http" or not s.rate_limit_enabled or not scope["path"].startswith("/api/") or scope["path"] == "/api/health":
            return await self.app(scope, receive, send)
        heavy = scope["method"] == "POST" and scope["path"].endswith(_HEAVY_SUFFIXES)
        limit = s.rate_limit_heavy_per_minute if heavy else s.rate_limit_general_per_minute
        key = (_identity(scope), "heavy" if heavy else "general")
        now = self.clock()
        q = self.hits.setdefault(key, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            retry = max(1, int(60 - (now - q[0])) + 1)
            body = b'{"detail":"Trop de requ\xc3\xaates : r\xc3\xa9essayer dans quelques secondes."}'
            await send({"type": "http.response.start", "status": 429, "headers": [(b"content-type", b"application/json"), (b"retry-after", str(retry).encode()),
                                                                                   (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        q.append(now)
        if len(self.hits) > 20_000:                      # borne mémoire : on oublie les identités inactives
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] > 60]:
                self.hits.pop(k, None)
        await self.app(scope, receive, send)
