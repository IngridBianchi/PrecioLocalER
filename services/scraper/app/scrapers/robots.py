"""Enforcement conservador de robots.txt.

Cumple el disclaimer del README: volúmenes bajos, delays configurados y sin
eludir medidas técnicas. Si no podemos leer robots.txt y ``strict`` está
activo, negamos el acceso (fail-safe).

Por qué parsing propio y no ``urllib.robotparser``
--------------------------------------------------
La stdlib resuelve reglas con *first-match-wins* y sin comodines:

- Un ``robots.txt`` con ``Allow: /`` seguido de ``Disallow: /buscar`` devolvía
  ``True`` para ``/buscar`` con la stdlib, es decir: **scrapearíamos una ruta
  prohibida**. La RFC 9309 manda la regla *más específica* (más larga) y, en
  empate, ``Allow``.
- La stdlib no implementa ``*`` ni el ancla ``$`` de la RFC 9309 §2.2.3, que son
  habituales (``Disallow: /*.pdf$``).

Como el disclaimer del proyecto es explícito sobre no eludir medidas del
sitio, el matcher se implementa aquí y se testea contra la spec.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

_DIRECTIVE_RE = re.compile(r"^([A-Za-z-]+)\s*:\s*(.*)$")


@dataclass(frozen=True)
class _Rule:
    """Una directiva ``Allow:``/``Disallow:`` ya compilada."""

    allow: bool
    path: str
    pattern: re.Pattern[str]

    @property
    def specificity(self) -> int:
        """Longitud de la regla: define cuál es "más específica" (RFC 9309)."""
        return len(self.path)


@dataclass
class _Group:
    """Bloque ``user-agent`` + sus reglas."""

    agents: list[str] = field(default_factory=list)
    rules: list[_Rule] = field(default_factory=list)


@dataclass
class _RobotsEntry:
    groups: list[_Group]
    fetched_at: float


def _compile(path: str) -> re.Pattern[str]:
    """Traduce una ruta de robots.txt a regex (RFC 9309 §2.2.3).

    ``*`` casa con cualquier secuencia y un ``$`` final ancla al fin del path.
    """
    anchored = path.endswith("$")
    body = path[:-1] if anchored else path
    regex = ".*".join(re.escape(chunk) for chunk in body.split("*"))
    return re.compile(f"^{regex}$" if anchored else f"^{regex}")


def _parse(text: str) -> list[_Group]:
    """Parsea robots.txt en grupos. Ignora `sitemap:` y comentarios."""
    groups: list[_Group] = []
    current: _Group | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            current = None
            continue
        match = _DIRECTIVE_RE.match(line)
        if match is None:
            continue
        directive, value = match.group(1).lower(), match.group(2).strip()
        if directive == "user-agent":
            # Varios `user-agent:` seguidos comparten reglas; uno nuevo después
            # de reglas ya vistas abre un grupo nuevo.
            if current is None or current.rules:
                current = _Group()
                groups.append(current)
            current.agents.append(value.lower())
        elif directive in ("allow", "disallow"):
            if current is None:
                continue
            if not value:
                # `Disallow:` vacío significa "todo permitido" (RFC 9309 §2.2.2);
                # como regex matchearía cualquier path, la regla se descarta.
                continue
            current.rules.append(
                _Rule(allow=directive == "allow", path=value, pattern=_compile(value))
            )
    return groups


def _matching_groups(groups: list[_Group], user_agent: str) -> list[_Group]:
    """Grupos que aplican a `user_agent`; los exactos ganan sobre ``*``."""
    agent = user_agent.lower()
    exact = [g for g in groups if any(a != "*" and a in agent for a in g.agents)]
    return exact or [g for g in groups if "*" in g.agents]


def _allows(groups: list[_Group], user_agent: str, target: str) -> bool:
    """RFC 9309 §2.2.2: gana la regla más larga; en empate, ``Allow``."""
    best: _Rule | None = None
    for group in _matching_groups(groups, user_agent):
        for rule in group.rules:
            if rule.pattern.match(target) is None:
                continue
            if (
                best is None
                or rule.specificity > best.specificity
                or (rule.specificity == best.specificity and rule.allow and not best.allow)
            ):
                best = rule
    if best is None:
        return True
    return best.allow


def _target(url: str) -> str:
    """Path + query de la URL, que es contra lo que compara robots.txt."""
    parsed = urlparse(url)
    return f"{parsed.path}?{parsed.query}" if parsed.query else (parsed.path or "/")


def _deny_all() -> list[_Group]:
    return _parse("User-agent: *\nDisallow: /")


class RobotsEnforcer:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        user_agent: str,
        strict_on_failure: bool = True,
        cache_ttl_seconds: int = 86400,
        aliases: dict[str, str] | None = None,
    ) -> None:
        """Verifica robots.txt por origin, con opción de alias de origin.

        ``aliases`` mapea origin -> origin cuyo robots.txt debe gobernar.
        Sirve para APIs de la misma organización sin robots propio (ej. el
        BFF de Coto Digital en ``api.coto.com.ar`` queda cubierto por el
        robots.txt de ``www.cotodigital.com.ar``).
        """
        self._client = client
        self._user_agent = user_agent
        self._strict = strict_on_failure
        self._ttl = cache_ttl_seconds
        self._aliases = aliases or {}
        self._entries: dict[str, _RobotsEntry] = {}

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    async def _load(self, origin: str) -> list[_Group]:
        robots_origin = self._aliases.get(origin, origin)
        robots_url = f"{robots_origin}/robots.txt"
        # Se manda el UA configurado: sin él, sitios con protección de bots
        # responden 403 al propio robots.txt y el fail-safe bloquea la cadena
        # entera. Y se siguen redirecciones: `www.cotodigital.com.ar/robots.txt`
        # responde 301 a `www.coto.com.ar/robots.txt`; sin seguirlas se
        # parsearía el HTML de la redirección como si no hubiera reglas.
        response = await self._client.get(
            robots_url,
            headers={"User-Agent": self._user_agent},
            follow_redirects=True,
        )
        if response.status_code in (401, 403):
            # Acceso al robots restringido: es un fallo, no "sin restricciones".
            response.raise_for_status()
        if 400 <= response.status_code < 500:
            # RFC 9309 §2.3.1.2: sin robots.txt no hay restricciones.
            return []
        response.raise_for_status()
        return _parse(response.text)

    async def can_fetch(self, url: str) -> bool:
        origin = self._origin(url)
        entry = self._entries.get(origin)
        now = time.monotonic()
        if entry is None or now - entry.fetched_at > self._ttl:
            try:
                groups = await self._load(origin)
            except Exception:
                if self._strict:
                    # Fail-safe: sin robots.txt confiable no scrapeamos.
                    self._entries[origin] = _RobotsEntry(_deny_all(), now)
                    return False
                return True
            entry = _RobotsEntry(groups, now)
            self._entries[origin] = entry
        return _allows(entry.groups, self._user_agent, _target(url))
