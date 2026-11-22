"""Genera app/dashboard.html: un solo archivo autocontenido (se abre con doble clic, sin servidor).

    python -m insumos dashboard

Junta en un JSON lo que el dashboard necesita (precios, pronósticos, recetas, fondas, KPIs,
menús del LLM si existen) y lo inyecta en app/plantilla_dashboard.html.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date

import numpy as np
import pandas as pd

from . import config
from .modelo import FUENTES_MERCADO

log = logging.getLogger(__name__)
SEMANAS_HISTORIA = 156
APP = config.ROOT / "app"


def _r(x, n=2):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


def _insumos(panel: pd.DataFrame, pron: pd.DataFrame) -> tuple[list, str]:
    ultima = panel.loc[panel["observado"] & panel["fuente"].isin(FUENTES_MERCADO), "semana"].max()
    desde = ultima - pd.Timedelta(weeks=SEMANAS_HISTORIA)
    out = []
    for ins, d in panel.groupby("insumo_id"):
        d = d[(d["semana"] >= desde) & (d["semana"] <= ultima)].dropna(subset=["precio_kg_imp"])
        if d.empty:
            continue
        fuente = d["fuente"].dropna().iloc[-1] if d["fuente"].notna().any() else ""
        actual = float(d["precio_kg_imp"].iloc[-1])
        p = pron[pron["insumo_id"] == ins].sort_values("h")
        if len(p):
            serie = [[int(r.h), _r(r.p10), _r(r.p50), _r(r.p90)] for r in p.itertuples()]
        else:  # sin modelo (precio base x INPC): se proyecta plano
            serie = [[h, _r(actual), _r(actual), _r(actual)] for h in range(1, 13)]
        out.append({
            "id": ins, "nombre": d["nombre"].dropna().iloc[-1] if d["nombre"].notna().any() else ins,
            "grupo": d["grupo"].dropna().iloc[-1] if d["grupo"].notna().any() else "",
            "fuente": fuente, "modelado": fuente in FUENTES_MERCADO and len(p) > 0,
            "actual": _r(actual),
            "hist": [[s.strftime("%Y-%m-%d"), _r(v)] for s, v in zip(d["semana"], d["precio_kg_imp"])],
            "pron": serie,
        })
    return out, ultima.strftime("%Y-%m-%d")


def _recetario() -> dict:
    rec = pd.read_csv(config.PLANTILLAS_DIR / "recetas_ejemplo.csv")
    men = pd.read_csv(config.PLANTILLAS_DIR / "menus_ejemplo.csv").set_index("platillo_id")
    out = {}
    for pid, d in rec.groupby("platillo_id", sort=False):
        out[pid] = {"nombre": d["platillo"].iloc[0],
                    "tiempo": men.loc[pid, "tiempo"] if pid in men.index else "plato_fuerte",
                    "precio_ref": _r(men.loc[pid, "precio_venta"]) if pid in men.index else None,
                    "ing": [[r.insumo_id, int(r.gramos_por_porcion)] for r in d.itertuples()]}
    return out


def _fondas() -> list:
    f = config.fondas()
    if not (f / "fondas.csv").exists():
        return []
    fondas = pd.read_csv(f / "fondas.csv")
    menus = pd.read_csv(f / "menus.csv")
    rec = pd.read_csv(f / "recetas.csv")
    out = []
    for r in fondas.itertuples():
        m = menus[menus["fonda_id"] == r.fonda_id]
        fuertes = m.loc[m["tiempo"] == "plato_fuerte", "precio_venta"]
        recetas = {pid: [[x.insumo_id, int(x.gramos_por_porcion)] for x in d.itertuples()]
                   for pid, d in rec[rec["fonda_id"] == r.fonda_id].groupby("platillo_id")}
        out.append({
            "id": r.fonda_id, "nombre": r.nombre, "tipo": r.tipo, "alcaldia": r.alcaldia,
            "comensales": int(r.comensales_dia), "merma": float(r.merma),
            "presupuesto": _r(r.presupuesto_semanal, 0),
            "precio_comida": float(np.round((fuertes.median() if len(fuertes) else 90) / 5) * 5 + 10),
            "menu": [{"pid": x.platillo_id, "tiempo": x.tiempo, "precio": _r(x.precio_venta, 0),
                      "pop": _r(x.popularidad, 3)} for x in m.itertuples()],
            "recetas": recetas,
        })
    return out


def _kpis() -> dict:
    g = config.gold()
    out = {}
    for nombre in ("kpi1_modelo", "kpi2_modelo", "kpi1_baseline", "kpi2_baseline"):
        f = g / f"{nombre}.csv"
        if f.exists():
            out[nombre] = json.loads(pd.read_csv(f).round(3).to_json(orient="records"))
    f = g / "backtest_predicciones.csv"
    if f.exists():
        bt = pd.read_csv(f)
        grupos = pd.read_csv(g / "panel_semanal.csv", usecols=["insumo_id", "grupo"]).drop_duplicates("insumo_id")
        bt = bt.merge(grupos, on="insumo_id", how="left")
        filas = []
        for (h, grupo), d in bt[bt["h"].isin([4, 8])].groupby(["h", "grupo"]):
            e_m = (100 * (d["modelo_p50"] - d["precio_obj"]).abs() / d["precio_obj"]).mean()
            e_i = (100 * (d["ingenuo"] - d["precio_obj"]).abs() / d["precio_obj"]).mean()
            filas.append({"h": int(h), "grupo": grupo, "insumos": int(d["insumo_id"].nunique()),
                          "error_modelo": _r(e_m), "error_ingenuo": _r(e_i)})
        out["sesgo"] = filas
    f = g / "importancia_variables.csv"
    if f.exists():
        imp = pd.read_csv(f).iloc[:, :2]
        imp.columns = ["variable", "importancia"]
        out["importancia"] = json.loads(imp.head(10).round(5).to_json(orient="records"))
    return out


def construir() -> str:
    panel = pd.read_csv(config.gold() / "panel_semanal.csv", parse_dates=["semana"])
    f_pron = config.gold() / "pronosticos.csv"
    pron = pd.read_csv(f_pron) if f_pron.exists() else pd.DataFrame(columns=["insumo_id", "h"])
    insumos, semana = _insumos(panel, pron)
    f_llm = config.gold() / "menus_llm.json"
    data = {
        "generado": date.today().isoformat(), "semana_actual": semana,
        "insumos": insumos, "recetario": _recetario(), "fondas": _fondas(), "kpis": _kpis(),
        "menus_llm": json.loads(f_llm.read_text(encoding="utf-8")) if f_llm.exists() else {},
        "food_cost_ref": 0.32,
        # Cuentas en la nube (opcional). La llave anon es pública por diseño: los datos los
        # protegen los permisos por fila de supabase/esquema.sql. NUNCA pongas aquí la llave de servicio.
        "nube": ({"url": os.environ["SUPABASE_URL"], "anon": os.environ["SUPABASE_ANON_KEY"]}
                 if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_ANON_KEY") else None),
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    plantilla = (APP / "plantilla_dashboard.html").read_text(encoding="utf-8")
    html = plantilla.replace("__DATA_JSON__", payload)
    out = APP / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    log.info("app/dashboard.html (%d KB) · %d insumos · %d fondas · semana %s",
             len(html) // 1024, len(insumos), len(data["fondas"]), semana)
    return str(out)
