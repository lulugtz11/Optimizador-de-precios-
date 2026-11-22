"""Conexión del EQUIPO con la base en la nube (Supabase). Solo corre en la computadora del equipo.

Necesita en .env:
    SUPABASE_URL=https://<proyecto>.supabase.co
    SUPABASE_SERVICE_KEY=<secret key: sb_secret_...  (o la antigua service_role)>

La llave de servicio ve los datos de todas las fondas (se salta los permisos por fila), por eso:
  * NUNCA va en el tablero, en el código ni en git: solo en el .env de quien corre los comandos.
  * Este módulo nunca lee ni devuelve el correo de nadie: las tablas no lo tienen, y de `fondas`
    se descarta la columna `owner` (la liga con la cuenta) en cuanto se lee.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)
PAGINA = 1000


def configurada() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))


def _cfg() -> tuple[str, str]:
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError("Faltan SUPABASE_URL y SUPABASE_SERVICE_KEY en .env (ver supabase/LEEME.md)")
    return url.rstrip("/"), key


def _headers(key: str, **extra) -> dict:
    h = {"apikey": key, "Content-Type": "application/json", **extra}
    # Las llaves nuevas (sb_secret_...) no son JWT: van solo en "apikey".
    # Las antiguas (service_role, eyJ...) también necesitan "Authorization".
    if not key.startswith("sb_"):
        h["Authorization"] = f"Bearer {key}"
    return h


def leer(tabla: str, columnas: str = "*", **filtros) -> list[dict]:
    """Lee una tabla completa (paginando). Filtros en sintaxis PostgREST: fonda_id="eq.<uuid>"."""
    url, key = _cfg()
    filas, desde = [], 0
    while True:
        r = requests.get(f"{url}/rest/v1/{tabla}", timeout=60,
                         headers=_headers(key, Range=f"{desde}-{desde + PAGINA - 1}"),
                         params={"select": columnas, **filtros})
        if r.status_code not in (200, 206):
            raise RuntimeError(f"Supabase respondió {r.status_code} al leer {tabla}: {r.text[:300]}")
        lote = r.json()
        filas.extend(lote)
        if len(lote) < PAGINA:
            return filas
        desde += PAGINA


def insertar(tabla: str, filas: list[dict]) -> None:
    url, key = _cfg()
    r = requests.post(f"{url}/rest/v1/{tabla}", json=filas, timeout=60,
                      headers=_headers(key, Prefer="return=minimal"))
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"Supabase respondió {r.status_code} al escribir {tabla}: {r.text[:300]}")


def fondas_activas() -> list[dict]:
    """Perfil anónimo de las fondas con cuenta vigente (sin la columna owner)."""
    return [{k: v for k, v in f.items() if k != "owner"} for f in leer("fondas", cancelada_en="is.null")]


def autoriza_mejora(fonda_id: str) -> bool:
    """True solo si el consentimiento MÁS RECIENTE de la fonda autoriza usar su historial."""
    filas = leer("consentimiento_actual", fonda_id=f"eq.{fonda_id}")
    return bool(filas and filas[0]["autoriza_mejora"])


def historial(fonda_id: str, n: int = 30) -> list[dict]:
    """Últimas respuestas de ESA fonda a los menús (nunca mezcla fondas)."""
    url, key = _cfg()
    r = requests.get(f"{url}/rest/v1/respuestas_menu", timeout=60, headers=_headers(key),
                     params={"select": "registrada_en,menu_titulo,platillos,acepto,motivo",
                             "fonda_id": f"eq.{fonda_id}", "order": "registrada_en.desc", "limit": str(n)})
    if r.status_code != 200:
        raise RuntimeError(f"Supabase respondió {r.status_code} al leer el historial: {r.text[:300]}")
    return r.json()


def platillos(fonda_id: str) -> list[dict]:
    return leer("platillos", fonda_id=f"eq.{fonda_id}")


def guardar_propuestas(fonda_id: str, menus: list[dict], horizonte: int, personalizada: bool) -> None:
    insertar("propuestas_menu", [{"fonda_id": fonda_id, "horizonte_sem": horizonte,
                                  "personalizada": personalizada, "menus": menus}])
