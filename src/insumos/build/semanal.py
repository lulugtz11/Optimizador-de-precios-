"""silver -> gold/panel_semanal.csv

Una fila por (semana, insumo) con precio por kg, variables macro y calendario.
La semana se etiqueta con su lunes. Esta tabla es la entrada del modelo de pronóstico.
"""
from __future__ import annotations

import logging

import pandas as pd

from .. import config
from ..catalogo import cargar_catalogo
from ..features.calendario import features_semanales
from ..ingest import manual

log = logging.getLogger(__name__)

MAX_SEMANAS_FFILL = 3  # huecos más largos se dejan como NaN (el modelo decide qué hacer)


def _lunes(fechas: pd.Series) -> pd.Series:
    fechas = pd.to_datetime(fechas)
    return (fechas - pd.to_timedelta(fechas.dt.weekday, unit="D")).dt.normalize()


def _precios_diarios() -> pd.DataFrame:
    partes = []
    f = config.silver() / "sniim_diario.csv"
    if f.exists():
        s = pd.read_csv(f, parse_dates=["fecha"])
        s = s[~s["fuera_de_rango"].astype(bool)]
        partes.append(s[["fecha", "insumo_id", "precio_min", "precio_max", "precio_frec", "origen", "fuente"]])
    else:
        log.warning("No hay silver/sniim_diario.csv")
    f_pec = config.silver() / "sniim_pecuarios.csv"
    if f_pec.exists():
        pec = pd.read_csv(f_pec, parse_dates=["fecha"])
        pec = pec[~pec["fuera_de_rango"].astype(bool)]
        partes.append(pec[["fecha", "insumo_id", "precio_min", "precio_max", "precio_frec", "origen", "fuente"]])
    f_inegi = config.silver() / "inegi_precios.csv"
    if f_inegi.exists():
        i = pd.read_csv(f_inegi, parse_dates=["fecha"])
        partes.append(i[["fecha", "insumo_id", "precio_frec", "fuente"]])
    m = manual.precios_manual()
    if len(m):
        partes.append(m)
    if not partes:
        raise RuntimeError("No hay precios en silver ni en external/precios_manual.csv")
    return pd.concat(partes, ignore_index=True)


def _origen_principal(df: pd.DataFrame) -> pd.DataFrame:
    o = df.dropna(subset=["origen"])
    if o.empty:
        return pd.DataFrame(columns=["semana", "insumo_id", "origen_principal"])
    o = o.groupby(["semana", "insumo_id"])["origen"].agg(lambda x: x.value_counts().index[0])
    return o.rename("origen_principal").reset_index()


def _macro_semanal(semanas: pd.Series) -> pd.DataFrame:
    partes = []
    f = config.silver() / "macro_banxico.csv"
    if f.exists():
        partes.append(pd.read_csv(f, parse_dates=["fecha"]))
    else:
        log.warning("No hay silver/macro_banxico.csv (corre la ingesta con BANXICO_TOKEN)")
    d = manual.diesel()
    if len(d):
        partes.append(d)
    base = pd.DataFrame({"semana": pd.to_datetime(semanas).drop_duplicates().sort_values()})
    if not partes:
        return base
    m = pd.concat(partes, ignore_index=True)
    m["semana"] = _lunes(m["fecha"])
    w = m.pivot_table(index="semana", columns="variable", values="valor", aggfunc="mean")
    # series mensuales (INPC) y huecos de días inhábiles: se arrastra el último dato publicado
    w = w.reindex(w.index.union(pd.DatetimeIndex(base["semana"]))).sort_index().ffill()
    w.index.name = "semana"
    return base.merge(w.reset_index(), on="semana", how="left")


def construir_panel() -> pd.DataFrame:
    cat = cargar_catalogo()
    d = _precios_diarios()
    d["semana"] = _lunes(d["fecha"])

    # 1) precio diario representativo: mediana entre orígenes/presentaciones
    diario = d.groupby(["insumo_id", "fecha", "semana"], as_index=False).agg(
        precio_frec=("precio_frec", "median"),
        precio_min=("precio_min", "min"),
        precio_max=("precio_max", "max"),
        fuente=("fuente", "first"),
    )
    # 2) semanal
    sem = diario.groupby(["insumo_id", "semana"], as_index=False).agg(
        precio_kg=("precio_frec", "median"),
        precio_min_kg=("precio_min", "min"),
        precio_max_kg=("precio_max", "max"),
        dias_con_precio=("fecha", "nunique"),
        fuente=("fuente", "first"),
    )
    # 3) malla completa semana x insumo, para que los huecos sean explícitos
    semanas = pd.date_range(sem["semana"].min(), sem["semana"].max(), freq="W-MON")
    malla = pd.MultiIndex.from_product([sem["insumo_id"].unique(), semanas], names=["insumo_id", "semana"])
    panel = sem.set_index(["insumo_id", "semana"]).reindex(malla).reset_index()
    panel["observado"] = panel["precio_kg"].notna()
    panel["precio_kg_imp"] = panel.groupby("insumo_id")["precio_kg"].transform(
        lambda s: s.ffill(limit=MAX_SEMANAS_FFILL)
    )
    panel["dias_con_precio"] = panel["dias_con_precio"].fillna(0).astype(int)
    panel["fuente"] = panel.groupby("insumo_id")["fuente"].transform(lambda s: s.ffill().bfill())

    panel = panel.merge(_origen_principal(d), on=["semana", "insumo_id"], how="left")
    panel = panel.merge(cat[["insumo_id", "nombre", "grupo"]], on="insumo_id", how="left")
    panel = panel.merge(_macro_semanal(panel["semana"]), on="semana", how="left")
    panel = panel.merge(features_semanales(panel["semana"]), on="semana", how="left")
    panel["es_sintetico"] = config.es_sintetico()

    panel = panel.sort_values(["insumo_id", "semana"]).reset_index(drop=True)
    out = config.gold() / "panel_semanal.csv"
    panel.to_csv(out, index=False)
    log.info("gold/panel_semanal.csv: %d filas, %d insumos, %s a %s",
             len(panel), panel["insumo_id"].nunique(),
             panel["semana"].min().date(), panel["semana"].max().date())
    return panel
