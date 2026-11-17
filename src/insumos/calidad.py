"""Reporte de calidad de datos (los 5 datos críticos de la E2: completitud, unicidad, vigencia)."""
from __future__ import annotations

from datetime import date

import pandas as pd

from . import config
from .build.costeo import cargar_recetas


def _tabla_md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lineas = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lineas.append("| " + " | ".join(
            f"{v:.1f}" if isinstance(v, float) else str(v) for v in r.tolist()) + " |")
    return "\n".join(lineas)


def reporte() -> str:
    panel = pd.read_csv(config.gold() / "panel_semanal.csv", parse_dates=["semana"])
    por_insumo = panel.groupby(["insumo_id", "grupo", "fuente"], dropna=False).agg(
        semanas=("semana", "size"),
        completitud_pct=("observado", lambda s: 100 * s.mean()),
        ultima_semana=("semana", lambda s: s[panel.loc[s.index, "observado"]].max()),
        precio_kg_mediano=("precio_kg", "median"),
    ).reset_index()
    por_insumo["ultima_semana"] = por_insumo["ultima_semana"].dt.date
    por_insumo = por_insumo.sort_values("completitud_pct")

    secciones = [f"# Reporte de calidad de datos — {date.today():%Y-%m-%d}\n"]
    if config.es_sintetico():
        secciones.append("> **DATOS SIMULADOS.** Este reporte se generó con `insumos demo` "
                         "para probar el pipeline. No usar en entregas.\n")

    secciones.append("## 1. Precios de la Central de Abasto (completitud y vigencia)\n")
    secciones.append(f"Rango: {panel['semana'].min().date()} a {panel['semana'].max().date()} · "
                     f"{panel['insumo_id'].nunique()} insumos\n")
    secciones.append(_tabla_md(por_insumo) + "\n")

    f_silver = config.silver() / "sniim_diario.csv"
    if f_silver.exists():
        s = pd.read_csv(f_silver)
        dup = s.duplicated(subset=["insumo_id", "fecha", "presentacion", "origen"]).sum()
        secciones.append("## 2. Unicidad y valores atípicos (SNIIM diario)\n")
        secciones.append(f"- Registros: {len(s):,}\n- Duplicados: {dup:,}\n"
                         f"- Fuera de rango plausible (excluidos del panel): "
                         f"{100 * s['fuera_de_rango'].mean():.2f}%\n")

    macro_cols = [c for c in ("tc_fix", "inpc", "diesel") if c in panel]
    if macro_cols:
        secciones.append("## 3. Variables explicativas (completitud semanal)\n")
        sem = panel.drop_duplicates("semana")
        for c in macro_cols:
            secciones.append(f"- {c}: {100 * sem[c].notna().mean():.1f}% de semanas con dato\n")
    else:
        secciones.append("## 3. Variables explicativas\n- Sin datos macro todavía (falta BANXICO_TOKEN).\n")

    recetas, _ = cargar_recetas()
    secciones.append("## 4. Recetas ligadas al catálogo\n")
    secciones.append(f"- {100 * recetas['ligado'].mean():.1f}% de ingredientes ligados a un insumo con precio\n")
    sin_precio = sorted(set(recetas["insumo_id"]) - set(panel.loc[panel["observado"], "insumo_id"]))
    if sin_precio:
        secciones.append(f"- Ingredientes sin ninguna observación de precio: {', '.join(sin_precio)}\n")

    texto = "\n".join(secciones)
    config.REPORTS_DIR.mkdir(exist_ok=True)
    nombre = "calidad_datos_SINTETICO.md" if config.es_sintetico() else "calidad_datos.md"
    (config.REPORTS_DIR / nombre).write_text(texto, encoding="utf-8")
    return texto
