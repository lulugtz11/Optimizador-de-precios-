"""Precios de mayoreo de pollo y res desde SNIIM Pecuarios (páginas .asp, distintas a las agrícolas).

- Pollo por pieza (e_Pza.asp): pechuga y pierna/muslo en $/kg por centro de distribución.
  Se usan los 4 centros de la CDMX ("D.F. : Centro de distribución ...").
- Bovinos cortes (e_Cor.asp): cortes en $/kg por empacadora. No hay CDMX: se usan todas
  las empacadoras reportadas como referencia nacional de mayoreo.

Mapeo corte SNIIM -> insumo del catálogo (aproximación declarada):
  Pulpa Bola -> res_bistec ; Paleta -> res_molida
"""
from __future__ import annotations

import gzip
import logging
import re
import time
from datetime import date

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .. import config
from ..catalogo import cargar_catalogo

log = logging.getLogger(__name__)

BASE = "http://www.economia-sniim.gob.mx/SNIIM-Pecuarios-Nacionales"
PAGINAS = {"pollo": "/e_Pza.asp", "bovino": "/e_Cor.asp"}
CORTES = {"pulpa bola": "res_bistec", "paleta": "res_molida"}
LUGAR_POLLO = "D.F."
_FECHA = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")


def _num(x: str) -> float:
    try:
        return float(str(x).replace("$", "").replace(",", "").strip())
    except ValueError:
        return float("nan")


def _filas(html: str):
    """(lugar_actual, celdas) por cada fila de datos con fecha."""
    lugar = None
    for tr in BeautifulSoup(html, "lxml").find_all("tr"):
        c = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"], recursive=False)]
        if len(c) == 1 and ":" in c[0] and not _FECHA.match(c[0]) and "Registros" not in c[0]:
            lugar = re.sub(r"\s+", " ", c[0])
        elif c and _FECHA.match(c[0]):
            yield lugar, c


def parsear_pollo(html: str) -> pd.DataFrame:
    filas = []
    for lugar, c in _filas(html):
        if len(c) < 7 or not lugar or not lugar.startswith(LUGAR_POLLO):
            continue
        for insumo, i in (("pechuga_pollo", 1), ("pollo_pierna_muslo", 4)):
            filas.append({"fecha": c[0], "insumo_id": insumo, "origen": lugar,
                          "precio_min": _num(c[i]), "precio_max": _num(c[i + 1]), "precio_frec": _num(c[i + 2])})
    return _limpiar(filas)


def parsear_bovino(html: str) -> pd.DataFrame:
    filas = []
    for lugar, c in _filas(html):
        if len(c) < 5:
            continue
        insumo = CORTES.get(c[2].strip().lower())
        if not insumo:
            continue
        lo, hi = _num(c[3]), _num(c[4])
        frec = hi if pd.isna(lo) else (lo + hi) / 2 if not pd.isna(hi) else lo
        filas.append({"fecha": c[0], "insumo_id": insumo, "origen": lugar,
                      "precio_min": lo, "precio_max": hi, "precio_frec": frec})
    return _limpiar(filas)


def _limpiar(filas: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(filas, columns=["fecha", "insumo_id", "origen", "precio_min", "precio_max", "precio_frec"])
    df["fecha"] = pd.to_datetime(df["fecha"], format="%d/%m/%Y", errors="coerce")
    return df.dropna(subset=["fecha", "precio_frec"])


PARSERS = {"pollo": parsear_pollo, "bovino": parsear_bovino}


def descargar(desde: str = config.FECHA_INICIO_DEFAULT, forzar: bool = False) -> int:
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (proyecto academico ITAM CDA I)"
    mes_actual = pd.Period(date.today(), "M")
    n = 0
    for nombre, url in PAGINAS.items():
        p = pd.Period(desde, "M")
        while p <= mes_actual:
            ruta = config.bronze() / "sniim_pecuarios" / nombre / f"{p}.html.gz"
            if ruta.exists() and not forzar and p < mes_actual:
                p += 1
                continue
            fin = p.end_time.day if p < mes_actual else date.today().day
            params = {"origen": "0", "destino": "0", "del": "1", "al": str(fin), "mes": f"{p.month:02d}",
                      "anio": str(p.year), "RegPag": "1000", "x": "45", "y": "20", "Var": "Bov"}
            r = s.get(BASE + url, params=params, timeout=90)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "latin-1"
            ruta.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(ruta, "wt", encoding="utf-8") as fh:
                fh.write(r.text)
            pag = re.search(r"P[áa]gina\s+1\s+de\s+(\d+)", r.text)
            if pag and int(pag.group(1)) > 1:
                log.warning("SNIIM pecuarios %s %s trae %s páginas; solo se guarda la 1", nombre, p, pag.group(1))
            log.info("SNIIM pecuarios %s %s", nombre, p)
            n += 1
            time.sleep(config.SNIIM_PAUSA_SEG)
            p += 1
    return n


def construir_silver() -> pd.DataFrame:
    partes = []
    for nombre, parser in PARSERS.items():
        for f in sorted((config.bronze() / "sniim_pecuarios" / nombre).glob("*.html.gz")):
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                partes.append(parser(fh.read()))
    if not partes:
        log.warning("No hay bronze de SNIIM pecuarios")
        return pd.DataFrame()
    df = pd.concat(partes, ignore_index=True).drop_duplicates(subset=["fecha", "insumo_id", "origen"])
    cat = cargar_catalogo().set_index("insumo_id")
    lo, hi = df["insumo_id"].map(cat["precio_min_kg"]), df["insumo_id"].map(cat["precio_max_kg"])
    df["fuera_de_rango"] = (df["precio_frec"] < lo) | (df["precio_frec"] > hi)
    df["fuente"] = "SNIIM pecuarios"
    df = df.sort_values(["insumo_id", "fecha"]).reset_index(drop=True)
    df.to_csv(config.silver() / "sniim_pecuarios.csv", index=False)
    log.info("silver/sniim_pecuarios.csv: %d filas (%s)", len(df),
             ", ".join(f"{k}={v}" for k, v in df["insumo_id"].value_counts().items()))
    return df
