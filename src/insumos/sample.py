"""Genera datos SIMULADOS con la misma forma que los reales, para probar el pipeline
sin internet. Se escriben en data_sample/ (nunca en data/). NO usar en entregas.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .catalogo import cargar_catalogo

# amplitud estacional y volatilidad semanal (log) por grupo — órdenes de magnitud razonables
PARAMS = {
    "verdura": (0.25, 0.08), "fruta": (0.22, 0.07), "grano": (0.05, 0.02),
    "aceite": (0.03, 0.015), "proteina": (0.05, 0.02), "lacteo": (0.03, 0.015), "basico": (0.02, 0.01),
}
ORIGENES = ["Michoacán", "Sinaloa", "Puebla", "Jalisco", "Veracruz", "Zacatecas"]


def generar(desde: str = "2020-01-01", hasta: str = "2026-11-13", seed: int = 7) -> None:
    assert config.es_sintetico(), "sample.generar() solo escribe en data_sample/"
    config.ensure_dirs()
    rng = np.random.default_rng(seed)
    dias = pd.bdate_range(desde, hasta)
    t = np.arange(len(dias))
    cat = cargar_catalogo()

    sniim, manual = [], []
    for _, ins in cat.iterrows():
        amp, vol = PARAMS.get(ins["grupo"], (0.05, 0.02))
        base = ins["precio_min_kg"] + 0.3 * (ins["precio_max_kg"] - ins["precio_min_kg"])
        fase = rng.uniform(0, 2 * np.pi)
        estacional = amp * np.sin(2 * np.pi * dias.dayofyear / 365.25 + fase)
        inflacion = 0.05 * t / 261
        ar = np.zeros(len(dias))
        for i in range(1, len(dias)):
            ar[i] = 0.97 * ar[i - 1] + rng.normal(0, vol / 2.2)
        choques = np.zeros(len(dias))
        if ins["grupo"] in ("verdura", "fruta"):
            for _ in range(rng.integers(1, 4)):  # picos tipo jitomate/limón
                c = rng.integers(0, len(dias) - 60)
                choques[c:c + 60] += rng.uniform(0.2, 0.7) * np.hanning(60)
        precio = base * np.exp(estacional + inflacion + ar + choques)
        mask = rng.random(len(dias)) > 0.06  # ~6% de días sin cotización
        if ins["fuente"] == "sniim":
            for origen in rng.choice(ORIGENES, size=2, replace=False):
                p = precio[mask] * rng.normal(1, 0.03, mask.sum())
                sniim.append(pd.DataFrame({
                    "fecha": dias[mask], "presentacion": "Kilogramo", "origen": origen,
                    "destino": "DF: Central de Abasto de Iztapalapa DF",
                    "precio_min": p * 0.9, "precio_max": p * 1.1, "precio_frec": p, "obs": "",
                    "insumo_id": ins["insumo_id"], "producto_sniim": ins["nombre"],
                    "archivo_bronze": "SINTETICO", "fuera_de_rango": False, "fuente": "SINTETICO",
                }))
        else:
            semanal = pd.Series(precio, index=dias).resample("W-MON").mean()
            manual.append(pd.DataFrame({"fecha": semanal.index, "insumo_id": ins["insumo_id"],
                                        "precio_kg": semanal.values, "fuente": "SINTETICO"}))

    pd.concat(sniim).to_csv(config.silver() / "sniim_diario.csv", index=False)
    pd.concat(manual).to_csv(config.external() / "precios_manual.csv", index=False)

    fix = 18 + np.cumsum(rng.normal(0, 0.08, len(dias)))
    meses = pd.date_range(desde, hasta, freq="MS")
    inpc = 108 * np.exp(0.045 * np.arange(len(meses)) / 12)
    macro = pd.concat([
        pd.DataFrame({"fecha": dias, "variable": "tc_fix", "valor": fix}),
        pd.DataFrame({"fecha": meses, "variable": "inpc", "valor": inpc}),
    ])
    macro.to_csv(config.silver() / "macro_banxico.csv", index=False)
