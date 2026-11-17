"""Variables macro desde la API SIE de Banxico (tipo de cambio FIX e INPC).

Necesita un token gratuito: https://www.banxico.org.mx/SieAPIRest/service/v1/token
Guárdalo en .env como BANXICO_TOKEN=... (no se sube al repo).
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


def descargar(desde: str = config.FECHA_INICIO_DEFAULT, hasta: str | None = None) -> pd.DataFrame:
    token = os.environ.get("BANXICO_TOKEN")
    if not token:
        raise RuntimeError("Falta BANXICO_TOKEN en el entorno (.env). Ver README.")
    hasta = hasta or date.today().isoformat()
    url = config.BANXICO_URL.format(series=",".join(config.BANXICO_SERIES), ini=desde, fin=hasta)
    r = requests.get(url, headers={"Bmx-Token": token, "Accept": "application/json"}, timeout=60)
    r.raise_for_status()
    payload = r.json()

    out_bronze = config.bronze() / "banxico"
    out_bronze.mkdir(parents=True, exist_ok=True)
    (out_bronze / f"sie_{date.today():%Y%m%d}.json").write_text(json.dumps(payload), encoding="utf-8")
    return a_silver(payload)


def a_silver(payload: dict) -> pd.DataFrame:
    filas = []
    for serie in payload["bmx"]["series"]:
        var = config.BANXICO_SERIES.get(serie["idSerie"], serie["idSerie"])
        for d in serie.get("datos", []):
            valor = pd.to_numeric(str(d["dato"]).replace(",", ""), errors="coerce")  # "N/E" -> NaN
            filas.append({"fecha": pd.to_datetime(d["fecha"], format="%d/%m/%Y"), "variable": var, "valor": valor})
    df = pd.DataFrame(filas).dropna()
    df.to_csv(config.silver() / "macro_banxico.csv", index=False)
    log.info("silver/macro_banxico.csv: %d filas (%s)", len(df), ", ".join(df["variable"].unique()))
    return df
