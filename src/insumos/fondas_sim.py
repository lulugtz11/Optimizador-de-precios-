"""Generador de fondas simuladas, amarradas a precios reales.

Las fondas, sus recetas, ventas y compras son inventadas; el precio al que compran cada
insumo es el precio REAL de la Central de Abasto esa semana (gold/panel_semanal.csv)
más un ruido de regateo. Con semilla fija, todo el equipo genera exactamente lo mismo.

Salidas en data/fondas/:
  fondas.csv          perfil de cada fonda (tipo, tamaño, alcaldía, presupuesto)
  menus.csv           fonda_id, platillo_id, tiempo, precio_venta, popularidad
  recetas.csv         fonda_id, platillo_id, insumo_id, gramos_por_porcion
  ventas_diarias.csv  fonda_id, fecha, platillo_id, porciones
  tickets.csv         fonda_id, semana, insumo_id, kg, precio_kg_pagado, total
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import config
from .features.calendario import features_semanales

log = logging.getLogger(__name__)

FONDAS = [
    # fonda_id, nombre, tipo, alcaldia, comensales/día, factor de precio vs base, merma
    ("F01", "Fonda Doña Mary", "comida_corrida", "Coyoacán", 70, 1.05, 0.12),
    ("F02", "Cocina Económica El Buen Sazón", "comida_corrida", "Iztapalapa", 90, 0.85, 0.15),
    ("F03", "Antojitos La Güera", "antojitos", "Cuauhtémoc", 60, 1.00, 0.10),
    ("F04", "Desayunos Lupita", "desayunos", "Benito Juárez", 50, 1.15, 0.10),
]

# qué platillos puede ofrecer cada tipo de fonda
MENU_POR_TIPO = {
    "comida_corrida": ["sopa_arroz", "sopa_fideo", "caldo_pollo", "frijoles_olla", "pollo_salsa_verde",
                       "bistec_mexicana", "tinga_pollo", "calabacitas_mexicana", "chile_relleno",
                       "enchiladas_verdes", "picadillo", "ensalada_nopales", "agua_limon", "agua_naranja"],
    "antojitos": ["quesadillas", "sopes", "tinga_pollo", "enchiladas_verdes", "guacamole",
                  "frijoles_olla", "agua_limon", "agua_naranja"],
    "desayunos": ["chilaquiles_verdes", "huevos_mexicana", "enchiladas_verdes", "quesadillas",
                  "frijoles_olla", "agua_naranja", "agua_limon"],
}
# platillos que se venden más en Cuaresma (sin carne roja / con huevo, nopal, chile relleno)
CUARESMA_ARRIBA = {"chile_relleno", "huevos_mexicana", "ensalada_nopales", "calabacitas_mexicana", "quesadillas"}
CUARESMA_ABAJO = {"bistec_mexicana", "picadillo", "sopes"}
DIA_SEMANA = np.array([1.0, 1.0, 1.0, 1.05, 1.15, 0.8, 0.0])  # lun..dom (domingo cerrado)
DESVIACION_GRAMAJE = 0.15
RUIDO_REGATEO = 0.05


def generar(desde: str = "2024-01-01", seed: int = 42) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    base_rec = pd.read_csv(config.PLANTILLAS_DIR / "recetas_ejemplo.csv")
    base_menu = pd.read_csv(config.PLANTILLAS_DIR / "menus_ejemplo.csv")
    panel = pd.read_csv(config.gold() / "panel_semanal.csv", parse_dates=["semana"])
    hasta = min(panel.loc[panel["observado"], "semana"].max() + pd.Timedelta(days=6),
                pd.Timestamp.today().normalize())

    fondas, menus, recetas = [], [], []
    for fid, nombre, tipo, alcaldia, comensales, f_precio, merma in FONDAS:
        platillos = MENU_POR_TIPO[tipo]
        m = base_menu[base_menu["platillo_id"].isin(platillos)].copy()
        m["fonda_id"] = fid
        # precio redondeado a $5, como en un pizarrón
        m["precio_venta"] = (m["precio_venta"] * f_precio * rng.normal(1, 0.05, len(m)) / 5).round() * 5
        m["popularidad"] = rng.dirichlet(np.ones(len(m)) * 5)
        menus.append(m)

        r = base_rec[base_rec["platillo_id"].isin(platillos)].copy()
        r["fonda_id"] = fid
        r["gramos_por_porcion"] = (r["gramos_por_porcion"]
                                   * rng.normal(1, DESVIACION_GRAMAJE, len(r)).clip(0.7, 1.3)).round()
        recetas.append(r)

        presupuesto = comensales * 6 * 45  # compra semanal aproximada, se recalcula abajo
        fondas.append({"fonda_id": fid, "nombre": nombre, "tipo": tipo, "alcaldia": alcaldia,
                       "comensales_dia": comensales, "merma": merma,
                       "presupuesto_semanal": presupuesto})

    fondas, menus, recetas = pd.DataFrame(fondas), pd.concat(menus), pd.concat(recetas)

    # ---- ventas diarias -------------------------------------------------------------
    dias = pd.date_range(desde, hasta)
    cal = features_semanales(pd.Series(dias - pd.to_timedelta(dias.weekday, unit="D")).drop_duplicates())
    cuaresma = dict(zip(cal["semana"], cal["cuaresma"] + cal["semana_santa"]))
    ventas = []
    for f in fondas.itertuples():
        m = menus[menus["fonda_id"] == f.fonda_id]
        for d in dias:
            factor = DIA_SEMANA[d.weekday()]
            if factor == 0:
                continue
            quincena = 1.08 if d.day in (15, 16, 30, 31, 1, 2) else 1.0
            clientes = rng.poisson(f.comensales_dia * factor * quincena)
            lunes = d - pd.Timedelta(days=d.weekday())
            pop = m["popularidad"].to_numpy().copy()
            c = min(cuaresma.get(lunes, 0.0), 1.0)
            if c:
                pop *= np.where(m["platillo_id"].isin(CUARESMA_ARRIBA), 1 + 0.6 * c, 1.0)
                pop *= np.where(m["platillo_id"].isin(CUARESMA_ABAJO), 1 - 0.5 * c, 1.0)
            porciones = rng.multinomial(clientes * 2, pop / pop.sum())  # ~2 platillos por cliente
            ventas.append(pd.DataFrame({"fonda_id": f.fonda_id, "fecha": d,
                                        "platillo_id": m["platillo_id"].to_numpy(), "porciones": porciones}))
    ventas = pd.concat(ventas)
    ventas = ventas[ventas["porciones"] > 0]

    # ---- tickets: compras semanales valuadas a precio real de la Central --------------
    v = ventas.assign(semana=ventas["fecha"] - pd.to_timedelta(ventas["fecha"].dt.weekday, unit="D"))
    v = v.groupby(["fonda_id", "semana", "platillo_id"], as_index=False)["porciones"].sum()
    uso = v.merge(recetas, on=["fonda_id", "platillo_id"])
    uso["kg"] = uso["porciones"] * uso["gramos_por_porcion"] / 1000
    uso = uso.groupby(["fonda_id", "semana", "insumo_id"], as_index=False)["kg"].sum()
    uso = uso.merge(fondas[["fonda_id", "merma"]], on="fonda_id")
    uso["kg"] = (uso["kg"] * (1 + uso["merma"])).round(2)
    precios = panel[["semana", "insumo_id", "precio_kg_imp"]]
    t = uso.merge(precios, on=["semana", "insumo_id"], how="left")
    sin_precio = sorted(t.loc[t["precio_kg_imp"].isna(), "insumo_id"].unique())
    if sin_precio:
        log.warning("Sin precio de mercado (no entran a tickets todavía): %s", ", ".join(sin_precio))
    t = t.dropna(subset=["precio_kg_imp"])
    t["precio_kg_pagado"] = (t["precio_kg_imp"] * rng.normal(1, RUIDO_REGATEO, len(t))).round(2)
    t["total"] = (t["kg"] * t["precio_kg_pagado"]).round(2)
    tickets = t[["fonda_id", "semana", "insumo_id", "kg", "precio_kg_pagado", "total"]]

    # presupuesto = gasto semanal mediano real de cada fonda redondeado a $500
    gasto = tickets.groupby(["fonda_id", "semana"])["total"].sum().groupby("fonda_id").median()
    fondas["presupuesto_semanal"] = fondas["fonda_id"].map((gasto / 500).round() * 500).fillna(
        fondas["presupuesto_semanal"])

    out = config.fondas()
    out.mkdir(parents=True, exist_ok=True)
    fondas.to_csv(out / "fondas.csv", index=False)
    menus[["fonda_id", "platillo_id", "tiempo", "precio_venta", "popularidad"]].to_csv(out / "menus.csv", index=False)
    recetas[["fonda_id", "platillo_id", "platillo", "insumo_id", "gramos_por_porcion"]].to_csv(
        out / "recetas.csv", index=False)
    ventas.to_csv(out / "ventas_diarias.csv", index=False)
    tickets.to_csv(out / "tickets.csv", index=False)
    log.info("Fondas: %d · platillos en menú: %d · ventas: %d filas · tickets: %d filas (%s a %s)",
             len(fondas), len(menus), len(ventas), len(tickets),
             ventas["fecha"].min().date(), ventas["fecha"].max().date())
    return {"fondas": fondas, "menus": menus, "recetas": recetas, "ventas": ventas, "tickets": tickets}
