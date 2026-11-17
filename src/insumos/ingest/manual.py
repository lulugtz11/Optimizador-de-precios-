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


def precios_base_inflacion() -> pd.DataFrame:
    """config/precios_base.csv + INPC (Banxico) -> serie semanal por insumo.

    precio(t) = precio_base_kg * INPC(mes de t) / INPC(mes de fecha_base)
    Respaldo para insumos sin fuente de mercado: no captura choques propios del producto,
    solo la tendencia general de precios.
    """
    cols = ["fecha", "insumo_id", "precio_frec", "fuente"]
    f_cfg, f_macro = config.CONFIG_DIR / "precios_base.csv", config.silver() / "macro_banxico.csv"
    if not f_cfg.exists() or not f_macro.exists():
        return pd.DataFrame(columns=cols)
    cfg = pd.read_csv(f_cfg).dropna(subset=["precio_base_kg", "fecha_base"])
    macro = pd.read_csv(f_macro, parse_dates=["fecha"])
    inpc = macro[macro["variable"] == "inpc"].set_index("fecha")["valor"].sort_index()
    if inpc.empty or cfg.empty:
        return pd.DataFrame(columns=cols)
    lunes = pd.date_range(inpc.index.min(), pd.Timestamp.today(), freq="W-MON")
    inpc_sem = inpc.reindex(inpc.index.union(lunes)).sort_index().ffill().reindex(lunes)
    partes = []
    for r in cfg.itertuples():
        base = inpc.loc[:pd.Timestamp(r.fecha_base)]
        if base.empty:
            continue
        partes.append(pd.DataFrame({"fecha": lunes, "insumo_id": r.insumo_id,
                                    "precio_frec": r.precio_base_kg * inpc_sem.values / base.iloc[-1],
                                    "fuente": "precio base x INPC"}))
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=cols)


def diesel() -> pd.DataFrame:
    f = config.external() / "diesel.csv"
    if not f.exists():
        return pd.DataFrame(columns=["fecha", "variable", "valor"])
    df = pd.read_csv(f, parse_dates=["fecha"])
    return pd.DataFrame({"fecha": df["fecha"], "variable": "diesel", "valor": df["precio_litro"]})
