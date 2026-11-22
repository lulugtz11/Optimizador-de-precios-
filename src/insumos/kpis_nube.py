"""KPIs con fondas REALES (datos de la nube). Solo los corre el equipo.

    python -m insumos kpis_nube      # necesita SUPABASE_URL y SUPABASE_SERVICE_KEY en .env

Mide lo que el backtest no puede: cómo le va a fonderos de verdad.
  * KPI 2 real: error % entre lo planeado y lo que de verdad gastaron, y qué tan seguido el gasto
    real cayó dentro del rango pronosticado.
  * KPI 3: % de menús sugeridos que aceptan, y por qué rechazan los demás.
  * Adopción: fondas activas, cuántas registran gasto y cuántas autorizaron usar su historial.

Privacidad: el reporte solo trae cifras agregadas. Ningún grupo con menos de 3 fondas se reporta
por separado (para que nadie pueda reconocer a una fonda por sus números), y no se usan correos
ni la liga con la cuenta.
Salida: reports/kpis_nube.md
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from . import config, nube

log = logging.getLogger(__name__)
MIN_FONDAS_GRUPO = 3
MOTIVOS = {"muy_caro": "Muy caro", "no_le_gusta": "No le gusta a sus clientes", "repetitivo": "Muy repetitivo",
           "no_se_preparar": "No sabe prepararlo", "otro": "Otro"}


def calcular(fondas: pd.DataFrame, gastos: pd.DataFrame, resp: pd.DataFrame, consent: pd.DataFrame) -> dict:
    out = {"fondas_activas": len(fondas)}
    tipo = fondas.set_index("id")["tipo"] if len(fondas) else pd.Series(dtype=str)

    if len(gastos):
        g = gastos.copy()
        for c in ("plan", "plan_min", "plan_max", "real"):
            g[c] = g[c].astype(float)
        g["error_pct"] = 100 * (g["real"] - g["plan"]).abs() / g["real"]
        g["dentro"] = g["real"].between(g["plan_min"], g["plan_max"])
        g["tipo"] = g["fonda_id"].map(tipo)
        out["kpi2"] = {"semanas": len(g), "fondas": g["fonda_id"].nunique(),
                       "error_medio_pct": round(g["error_pct"].mean(), 1),
                       "error_mediano_pct": round(g["error_pct"].median(), 1),
                       "dentro_del_rango_pct": round(100 * g["dentro"].mean(), 1)}
        por_tipo = g.groupby("tipo").agg(fondas=("fonda_id", "nunique"), semanas=("error_pct", "size"),
                                         error_medio_pct=("error_pct", "mean"))
        out["kpi2_por_tipo"] = por_tipo[por_tipo["fondas"] >= MIN_FONDAS_GRUPO].round(1)

    if len(resp):
        r = resp.copy()
        r["tipo"] = r["fonda_id"].map(tipo)
        out["kpi3"] = {"respuestas": len(r), "fondas": r["fonda_id"].nunique(),
                       "aceptados_pct": round(100 * r["acepto"].mean(), 1)}
        motivos = r.loc[~r["acepto"], "motivo"].map(MOTIVOS).value_counts(normalize=True).mul(100).round(1)
        out["motivos_rechazo"] = motivos
        if len(consent):
            autoriza = consent.set_index("fonda_id")["autoriza_mejora"]
            r["personalizada"] = r["fonda_id"].map(autoriza).fillna(False)
            grupos = r.groupby("personalizada").agg(fondas=("fonda_id", "nunique"), aceptados_pct=("acepto", "mean"))
            grupos["aceptados_pct"] = (100 * grupos["aceptados_pct"]).round(1)
            out["kpi3_personalizacion"] = grupos[grupos["fondas"] >= MIN_FONDAS_GRUPO]

    out["con_gasto_pct"] = round(100 * gastos["fonda_id"].nunique() / len(fondas), 1) if len(fondas) and len(gastos) else 0.0
    out["autorizan_pct"] = round(100 * consent["autoriza_mejora"].mean(), 1) if len(consent) else 0.0
    return out


def _tabla(df: pd.DataFrame) -> str:
    """Tabla markdown sin dependencias extra."""
    df = df.reset_index()
    cab = "| " + " | ".join(map(str, df.columns)) + " |"
    sep = "|" + "---|" * len(df.columns)
    filas = ["| " + " | ".join(map(str, r)) + " |" for r in df.itertuples(index=False)]
    return "\n".join([cab, sep, *filas])


def _md(k: dict) -> str:
    l = [f"# KPIs con fondas reales · {date.today().isoformat()}", "",
         "Datos agregados de la nube. Grupos con menos de 3 fondas no se muestran por separado.", "",
         "## Adopción", "",
         f"- Fondas con cuenta activa: **{k['fondas_activas']}**",
         f"- Fondas que registran su gasto: **{k['con_gasto_pct']}%**",
         f"- Fondas que autorizan usar su historial para personalizar: **{k['autorizan_pct']}%**", ""]
    if "kpi2" in k:
        x = k["kpi2"]
        l += ["## KPI 2 · Error del costo planeado vs. gasto real", "",
              f"- {x['semanas']} semanas registradas por {x['fondas']} fondas",
              f"- Error medio: **{x['error_medio_pct']}%** · mediano: {x['error_mediano_pct']}%",
              f"- El gasto real cayó dentro del rango pronosticado el **{x['dentro_del_rango_pct']}%** de las veces", ""]
        if len(k.get("kpi2_por_tipo", [])):
            l += ["Por tipo de fonda:", "", _tabla(k["kpi2_por_tipo"]), ""]
    else:
        l += ["## KPI 2", "", "Todavía no hay gastos registrados.", ""]
    if "kpi3" in k:
        x = k["kpi3"]
        l += ["## KPI 3 · Menús aceptados", "",
              f"- {x['respuestas']} respuestas de {x['fondas']} fondas · aceptados: **{x['aceptados_pct']}%**", ""]
        if len(k.get("motivos_rechazo", [])):
            l += ["Motivos de rechazo (% de los rechazos):", "", _tabla(k["motivos_rechazo"].rename_axis("motivo").to_frame("%")), ""]
        if len(k.get("kpi3_personalizacion", [])):
            l += ["Aceptación según si la fonda autorizó personalizar con su historial:", "",
                  _tabla(k["kpi3_personalizacion"]), ""]
    else:
        l += ["## KPI 3", "", "Todavía no hay respuestas a menús.", ""]
    return "\n".join(l)


def correr() -> dict:
    fondas = pd.DataFrame(nube.fondas_activas())
    ids = set(fondas["id"]) if len(fondas) else set()
    gastos = pd.DataFrame(nube.leer("gastos", "fonda_id,semana,plan,plan_min,plan_max,real"))
    resp = pd.DataFrame(nube.leer("respuestas_menu", "fonda_id,acepto,motivo,horizonte_sem"))
    consent = pd.DataFrame(nube.leer("consentimiento_actual", "fonda_id,autoriza_mejora"))
    # solo cuentas vigentes
    gastos, resp, consent = (d[d["fonda_id"].isin(ids)] if len(d) else d for d in (gastos, resp, consent))
    k = calcular(fondas, gastos, resp, consent)
    ruta = config.ROOT / "reports" / "kpis_nube.md"
    ruta.parent.mkdir(exist_ok=True)
    ruta.write_text(_md(k), encoding="utf-8")
    log.info("Reporte en %s", ruta)
    return k
