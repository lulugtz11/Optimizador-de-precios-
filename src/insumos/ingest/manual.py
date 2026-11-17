"""Fuentes que entran a mano (CSV) mientras no haya scraper:

- data/external/precios_manual.csv : fecha,insumo_id,precio_kg,fuente
    Proteínas, lácteos, tortilla, etc. (fuente: tickets de las fondas, Profeco QQP, SNIIM pecuarios).
- data/external/diesel.csv          : fecha,precio_litro
    Precio promedio de diésel (CNE/CRE). Opcional.
"""
from __future__ import annotations

import logging

import pandas as pd

from .. import config
from ..catalogo import cargar_catalogo

log = logging.getLogger(__name__)


def precios_manual() -> pd.DataFrame:
    f = config.external() / "precios_manual.csv"
    if not f.exists():
        log.warning("No existe %s: los insumos con fuente=manual quedarán sin precio", f)
        return pd.DataFrame(columns=["fecha", "insumo_id", "precio_frec", "fuente"])
    df = pd.read_csv(f, parse_dates=["fecha"])
    faltan = {"fecha", "insumo_id", "precio_kg"} - set(df.columns)
    if faltan:
        raise ValueError(f"{f} le faltan columnas: {faltan}")
    desconocidos = set(df["insumo_id"]) - set(cargar_catalogo()["insumo_id"])
    if desconocidos:
        raise ValueError(f"insumo_id no está en config/insumos.csv: {sorted(desconocidos)}")
    df = df.rename(columns={"precio_kg": "precio_frec"})
    df["fuente"] = df.get("fuente", "manual")
    return df[["fecha", "insumo_id", "precio_frec", "fuente"]]


def diesel() -> pd.DataFrame:
    f = config.external() / "diesel.csv"
    if not f.exists():
        return pd.DataFrame(columns=["fecha", "variable", "valor"])
    df = pd.read_csv(f, parse_dates=["fecha"])
    return pd.DataFrame({"fecha": df["fecha"], "variable": "diesel", "valor": df["precio_litro"]})
