"""Costeo de platillos y baselines de los KPIs.

- costo_platillo_semanal.csv : costo por porción de cada platillo cada semana (código, no LLM)
- kpi1_baseline.csv          : wRMSE del pronóstico ingenuo y ingenuo estacional por horizonte
- kpi2_baseline.csv          : error % del costo del menú usando "el precio de un solo día"
                               (práctica actual del fondero) por horizonte

Estos baselines son la vara que el modelo tiene que superar.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .. import config
from ..catalogo import cargar_catalogo

log = logging.getLogger(__name__)

HORIZONTES = [1, 4, 8, 12]  # semanas


def _leer(nombre: str) -> pd.DataFrame:
    real = config.fondas() / f"{nombre}.csv"
    if real.exists():
        return pd.read_csv(real)
    ejemplo = config.PLANTILLAS_DIR / f"{nombre}_ejemplo.csv"
    log.warning("No hay data/fondas/%s.csv: usando plantillas/%s (EJEMPLO, no son datos de fondas)",
                nombre, ejemplo.name)
    return pd.read_csv(ejemplo)


def cargar_recetas() -> tuple[pd.DataFrame, pd.DataFrame]:
    recetas, menus = _leer("recetas"), _leer("menus")
    for df in (recetas, menus):  # el recetario de ejemplo no trae fonda: se trata como una sola
        if "fonda_id" not in df:
            df["fonda_id"] = "EJEMPLO"
    cat = set(cargar_catalogo()["insumo_id"])
    sin_liga = sorted(set(recetas["insumo_id"]) - cat)
    if sin_liga:
        log.warning("Ingredientes sin liga al catálogo (se ignoran en el costo): %s", sin_liga)
    recetas["ligado"] = recetas["insumo_id"].isin(cat)
    return recetas, menus


def pesos_insumos(recetas: pd.DataFrame, panel: pd.DataFrame) -> pd.Series:
    """Peso de cada insumo en el costo total de los menús (para ponderar el wRMSE)."""
    precio_medio = panel.groupby("insumo_id")["precio_kg_imp"].median()
    r = recetas[recetas["ligado"]].copy()
    r["costo"] = r["gramos_por_porcion"] / 1000 * r["insumo_id"].map(precio_medio)
    w = r.groupby("insumo_id")["costo"].sum()
    return (w / w.sum()).rename("peso")


def costo_semanal(recetas: pd.DataFrame, menus: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    precios = panel[["semana", "insumo_id", "precio_kg_imp"]]
    # cada ingrediente de cada receta en CADA semana: si un insumo no tiene precio, el platillo
    # queda sin costo (NaN) en vez de costearse solo con los ingredientes que sí tienen precio
    semanas = pd.DataFrame({"semana": sorted(panel["semana"].unique())})
    r = recetas[recetas["ligado"]].merge(semanas, how="cross")
    r = r.merge(precios, on=["semana", "insumo_id"], how="left")
    r["costo_ing"] = r["gramos_por_porcion"] / 1000 * r["precio_kg_imp"]
    g = r.groupby(["fonda_id", "platillo_id", "semana"]).agg(
        costo_porcion=("costo_ing", lambda s: s.sum(min_count=len(s))),  # NaN si falta algún precio
        ingredientes=("insumo_id", "size"),
        ingredientes_con_precio=("precio_kg_imp", "count"),
    ).reset_index()
    g = g.merge(menus, on=["fonda_id", "platillo_id"], how="left")
    g["margen_pct"] = 100 * (g["precio_venta"] - g["costo_porcion"]) / g["precio_venta"]
    g["food_cost_pct"] = 100 * g["costo_porcion"] / g["precio_venta"]
    return g.sort_values(["fonda_id", "platillo_id", "semana"]).reset_index(drop=True)


def kpi1_baseline(panel: pd.DataFrame, pesos: pd.Series) -> pd.DataFrame:
    """wRMSE (en % del precio, para comparar insumos de distinto nivel) de ingenuo y ingenuo estacional."""
    filas = []
    p = panel.pivot(index="semana", columns="insumo_id", values="precio_kg_imp").sort_index()
    for h in HORIZONTES:
        for nombre, lag in (("ingenuo", h), ("ingenuo_estacional", 52)):
            if lag < h:
                continue
            pred = p.shift(lag)
            err_pct = 100 * (p - pred) / p
            rmse = np.sqrt((err_pct ** 2).mean())
            w = pesos.reindex(rmse.index).fillna(0)
            ok = rmse.notna() & (w > 0)
            filas.append({
                "modelo": nombre, "horizonte_sem": h,
                "wRMSE_pct": float(np.sqrt((w[ok] * rmse[ok] ** 2).sum() / w[ok].sum())) if ok.any() else np.nan,
                "RMSE_pct_sin_ponderar": float(rmse.mean()),
                "n_insumos": int(ok.sum()),
            })
    return pd.DataFrame(filas)


def kpi2_baseline(costos: pd.DataFrame) -> pd.DataFrame:
    """Error % del costo del menú si el fondero usa el costo de hoy para planear h semanas adelante."""
    filas = []
    for h in HORIZONTES:
        c = costos[["fonda_id", "platillo_id", "semana", "costo_porcion"]].copy()
        c["costo_planeado"] = c.groupby(["fonda_id", "platillo_id"])["costo_porcion"].shift(h)
        c = c.dropna()
        ape = 100 * (c["costo_porcion"] - c["costo_planeado"]).abs() / c["costo_porcion"]
        sub = 100 * ((c["costo_porcion"] > c["costo_planeado"]).mean())
        filas.append({
            "modelo": "precio_de_un_dia", "horizonte_sem": h,
            "MAPE_costo_menu_pct": float(ape.mean()),
            "P90_error_pct": float(ape.quantile(0.9)),
            "pct_semanas_costo_subestimado": float(sub),
            "n": int(len(c)),
        })
    return pd.DataFrame(filas)


def construir() -> dict[str, pd.DataFrame]:
    panel = pd.read_csv(config.gold() / "panel_semanal.csv", parse_dates=["semana"])
    recetas, menus = cargar_recetas()
    costos = costo_semanal(recetas, menus, panel)
    pesos = pesos_insumos(recetas, panel)
    k1, k2 = kpi1_baseline(panel, pesos), kpi2_baseline(costos)

    costos.to_csv(config.gold() / "costo_platillo_semanal.csv", index=False)
    pesos.reset_index().to_csv(config.gold() / "pesos_insumos.csv", index=False)
    k1.to_csv(config.gold() / "kpi1_baseline.csv", index=False)
    k2.to_csv(config.gold() / "kpi2_baseline.csv", index=False)
    log.info("Costeo: %d fondas, %d platillos. KPI2 baseline (4 sem): MAPE %.1f%%",
             costos["fonda_id"].nunique(), costos["platillo_id"].nunique(),
             k2.loc[k2["horizonte_sem"] == 4, "MAPE_costo_menu_pct"].squeeze())
    return {"costos": costos, "kpi1": k1, "kpi2": k2, "pesos": pesos}
