"""Precios de proteínas, granos y básicos a partir del INPC por genérico (INEGI).

SNIIM no da estos productos en la Central, así que se reconstruye un precio por kg:

    precio_kg(t) = precio_base_kg * indice(t) / indice(fecha_base)

- indice(t): INPC del genérico (pollo, huevo, arroz...) vía API del INEGI
- precio_base_kg: UN precio por kg conocido en fecha_base (ticket, Profeco, SNIIM pecuarios)

Configuración en config/inegi_series.csv:  insumo_id, indicador_id, precio_base_kg, fecha_base
Token en .env:  INEGI_TOKEN=...
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date

import pandas as pd
import requests

from .. import config

log = logging.getLogger(__name__)

URL = ("https://www.inegi.org.mx/app/api/indicadores/desarrolladores/jsonxml/INDICATOR/"
       "{id}/es/00/false/BIE/2.0/{token}?type=json")


def _periodo(tp: str) -> pd.Timestamp:
    """'2026/10' -> 2026-10-01 ; '2026/10/02' (2a quincena) -> 2026-10-16."""
    partes = [int(p) for p in str(tp).split("/")]
    dia = 16 if len(partes) > 2 and partes[2] == 2 else 1
    return pd.Timestamp(partes[0], partes[1], dia)


def descargar() -> pd.DataFrame:
    token = os.environ.get("INEGI_TOKEN")
    if not token:
        raise RuntimeError("Falta INEGI_TOKEN en .env")
    cfg = pd.read_csv(config.CONFIG_DIR / "inegi_series.csv", dtype={"indicador_id": str})
    cfg = cfg[cfg["indicador_id"].notna() & (cfg["indicador_id"].str.strip() != "")]
    if cfg.empty:
        raise RuntimeError("config/inegi_series.csv no tiene indicador_id. Ver README (sección INEGI).")

    out_bronze = config.bronze() / "inegi"
    out_bronze.mkdir(parents=True, exist_ok=True)
    partes = []
    for row in cfg.itertuples():
        r = requests.get(URL.format(id=row.indicador_id.strip(), token=token), timeout=60)
        r.raise_for_status()
        payload = r.json()
        (out_bronze / f"{row.indicador_id}_{date.today():%Y%m%d}.json").write_text(json.dumps(payload))
        obs = payload["Series"][0]["OBSERVATIONS"]
        s = pd.DataFrame({
            "fecha": [_periodo(o["TIME_PERIOD"]) for o in obs],
            "indice": pd.to_numeric([o["OBS_VALUE"] for o in obs], errors="coerce"),
        }).dropna().sort_values("fecha")
        base = s.loc[s["fecha"] <= pd.Timestamp(row.fecha_base), "indice"]
        if base.empty:
            raise ValueError(f"{row.insumo_id}: no hay índice en o antes de fecha_base {row.fecha_base}")
        s["precio_frec"] = row.precio_base_kg * s["indice"] / base.iloc[-1]
        s["insumo_id"] = row.insumo_id
        s["fuente"] = f"INEGI INPC {row.indicador_id}"
        partes.append(s)
        log.info("INEGI %s (%s): %d observaciones, %s a %s", row.insumo_id, row.indicador_id,
                 len(s), s["fecha"].min().date(), s["fecha"].max().date())

    df = pd.concat(partes, ignore_index=True)
    df.to_csv(config.silver() / "inegi_precios.csv", index=False)
    return df
