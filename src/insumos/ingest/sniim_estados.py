"""Precios de frutas y hortalizas en las ~49 centrales de abasto de 31 estados (SNIIM).

Se pide semana por semana con Destino = Todos (una consulta trae todas las centrales).
Si una semana llega al tope de registros por página, se vuelve a pedir día por día.

bronze/sniim_estados/<producto_id>/<lunes>.html.gz
silver/sniim_estados_semanal.csv : semana, insumo_id, mercado, estado, precio_kg (mediana), n_obs
"""
from __future__ import annotations

import gzip
import logging
import time
from datetime import date, timedelta

import pandas as pd

from .. import config
from ..catalogo import cargar_catalogo, normalizar
from .sniim import _get, parsear_resultados, resolver_productos

log = logging.getLogger(__name__)

DESDE_DEFAULT = "2022-01-03"  # lunes; 2022+ alcanza para rezagos de 52 semanas y backtest
TOPE = config.SNIIM_REGISTROS_POR_PAGINA


def _params(pid: str, ini: date, fin: date) -> dict:
    return {"fechaInicio": ini.strftime("%d/%m/%Y"), "fechaFinal": fin.strftime("%d/%m/%Y"),
            "ProductoId": pid, "OrigenId": "-1", "Origen": "Todos", "DestinoId": "-1",
            "Destino": "Todos", "PreciosPorId": config.SNIIM_PRECIOS_POR_KG,
            "RegistrosPorPagina": str(TOPE)}


def descargar(desde: str = DESDE_DEFAULT, solo: list[str] | None = None) -> int:
    _, url_res = config.SNIIM_CATEGORIAS["frutas_hortalizas"]
    url = config.SNIIM_BASE + url_res
    match = resolver_productos()
    match = match[(match["producto_id"] != "") & (match["categoria"] == "frutas_hortalizas")]
    if solo:
        match = match[match["insumo_id"].isin(solo)]
    lunes0 = pd.Timestamp(desde).to_period("W-SUN").start_time.date()
    hoy = date.today()
    semanas = []
    d = lunes0
    while d <= hoy:
        semanas.append(d)
        d += timedelta(days=7)
    total = len(match) * len(semanas)
    n = hechos = 0
    for _, m in match.iterrows():
        for lunes in semanas:
            hechos += 1
            ruta = config.bronze() / "sniim_estados" / str(m["producto_id"]) / f"{lunes}.html.gz"
            semana_cerrada = lunes + timedelta(days=6) < hoy
            if ruta.exists() and semana_cerrada:
                continue
            fin = min(lunes + timedelta(days=6), hoy)
            html = _get(url, _params(m["producto_id"], lunes, fin))
            if len(parsear_resultados(html)) >= TOPE - 5:  # se llenó la página: pedir día por día
                partes = [_get(url, _params(m["producto_id"], lunes + timedelta(days=i), lunes + timedelta(days=i)))
                          for i in range((fin - lunes).days + 1)]
                html = "\n<!--DIA-->\n".join(partes)
            ruta.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(ruta, "wt", encoding="utf-8") as fh:
                fh.write(html)
            n += 1
            if n % 50 == 0:
                log.info("SNIIM estados: %d/%d (%s %s)", hechos, total, m["insumo_id"], lunes)
            time.sleep(config.SNIIM_PAUSA_SEG)
    log.info("SNIIM estados: %d archivos nuevos", n)
    return n


def _mercado_estado(destino: str) -> tuple[str, str]:
    destino = " ".join(str(destino).split())
    estado = destino.split(":")[0].strip() if ":" in destino else destino
    estado = {"DF": "Ciudad de México", "México": "Estado de México"}.get(estado, estado)
    return destino, estado


def construir_silver() -> pd.DataFrame:
    match = pd.read_csv(config.bronze() / "sniim" / "match_catalogo.csv", dtype=str).dropna(subset=["producto_id"])
    cat = cargar_catalogo().set_index("insumo_id")
    partes = []
    for _, m in match.iterrows():
        carpeta = config.bronze() / "sniim_estados" / str(m["producto_id"])
        for f in sorted(carpeta.glob("*.html.gz")):
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                texto = fh.read()
            dfs = [parsear_resultados(t) for t in texto.split("<!--DIA-->")]
            df = pd.concat([x for x in dfs if len(x)], ignore_index=True) if any(len(x) for x in dfs) else None
            if df is None:
                continue
            df["insumo_id"] = m["insumo_id"]
            partes.append(df[["fecha", "insumo_id", "destino", "origen", "presentacion", "precio_frec"]])
    if not partes:
        raise RuntimeError("No hay bronze de SNIIM estados. Corre `python -m insumos estados`.")
    df = pd.concat(partes, ignore_index=True).drop_duplicates()
    df = df[df["destino"].notna() & (df["destino"].map(normalizar) != "")]
    factor = df["insumo_id"].map(cat["factor_a_kg"]).fillna(1.0)
    df["precio_frec"] = df["precio_frec"] * factor
    lo, hi = df["insumo_id"].map(cat["precio_min_kg"]), df["insumo_id"].map(cat["precio_max_kg"])
    df = df[(df["precio_frec"] >= lo * 0.5) & (df["precio_frec"] <= hi * 1.5)]
    df[["mercado", "estado"]] = df["destino"].apply(lambda x: pd.Series(_mercado_estado(x)))
    df["semana"] = df["fecha"] - pd.to_timedelta(df["fecha"].dt.weekday, unit="D")
    sem = df.groupby(["semana", "insumo_id", "mercado", "estado"], as_index=False).agg(
        precio_kg=("precio_frec", "median"), n_obs=("precio_frec", "size"))
    sem.to_csv(config.silver() / "sniim_estados_semanal.csv", index=False)
    log.info("silver/sniim_estados_semanal.csv: %d filas · %d mercados · %d estados · %d insumos",
             len(sem), sem["mercado"].nunique(), sem["estado"].nunique(), sem["insumo_id"].nunique())
    return sem
