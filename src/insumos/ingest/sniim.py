"""Ingesta de precios de la Central de Abasto (CDMX) desde SNIIM.

SNIIM no tiene API: se consultan las mismas páginas .aspx que usa el sitio y se parsea
la tabla `tblResultados`. Cada respuesta se guarda tal cual (bronze) para poder
re-parsear sin volver a descargar.

Flujo:
  1. listar_productos()  -> lee los dropdowns (productos y destinos) de cada categoría
  2. descargar()         -> baja HTML mes por mes por producto (cacheado en bronze)
  3. construir_silver()  -> parsea todo el bronze a un CSV diario homologado
"""
from __future__ import annotations

import gzip
import logging
import re
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .. import config
from ..catalogo import cargar_catalogo, emparejar_productos, normalizar

log = logging.getLogger(__name__)

_SESSION: requests.Session | None = None


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.headers["User-Agent"] = "Mozilla/5.0 (proyecto academico ITAM CDA I; precios de insumos)"
        _SESSION = s
    return _SESSION


def _get(url: str, params: dict | None = None, intentos: int = 4) -> str:
    ultimo = None
    for i in range(intentos):
        try:
            r = _session().get(url, params=params, timeout=60)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except requests.RequestException as e:  # red inestable del sitio de gobierno
            ultimo = e
            espera = 2 ** i
            log.warning("Fallo %s (%s). Reintento en %ss", url, e, espera)
            time.sleep(espera)
    raise RuntimeError(f"No se pudo descargar {url} {params}: {ultimo}")


# ---------------------------------------------------------------------------
# 1. Catálogo de productos y destinos
# ---------------------------------------------------------------------------
def _opciones_select(soup: BeautifulSoup, contiene: str) -> list[tuple[str, str]]:
    for sel in soup.find_all("select"):
        ident = (sel.get("id") or "") + (sel.get("name") or "")
        if contiene.lower() in ident.lower():
            return [
                (o.get_text(strip=True), o.get("value", ""))
                for o in sel.find_all("option")
                if o.get("value") not in (None, "", "-1")
            ]
    return []


def listar_productos() -> tuple[pd.DataFrame, pd.DataFrame]:
    prods, dests = [], []
    for cat, (url_consulta, _) in config.SNIIM_CATEGORIAS.items():
        soup = BeautifulSoup(_get(config.SNIIM_BASE + url_consulta), "lxml")
        for nombre, valor in _opciones_select(soup, "producto"):
            prods.append({"categoria": cat, "producto_id": valor, "producto": nombre})
        for nombre, valor in _opciones_select(soup, "destino"):
            dests.append({"categoria": cat, "destino_id": valor, "destino": nombre})
        time.sleep(config.SNIIM_PAUSA_SEG)
    prods, dests = pd.DataFrame(prods), pd.DataFrame(dests)
    out = config.bronze() / "sniim"
    out.mkdir(parents=True, exist_ok=True)
    prods.to_csv(out / "productos_sniim.csv", index=False)
    dests.to_csv(out / "destinos_sniim.csv", index=False)
    return prods, dests


def resolver_productos() -> pd.DataFrame:
    """Empareja catálogo maestro vs dropdown de SNIIM y guarda el resultado para revisión."""
    out = config.bronze() / "sniim"
    f_prod = out / "productos_sniim.csv"
    if f_prod.exists():
        prods = pd.read_csv(f_prod, dtype=str)
    else:
        prods, _ = listar_productos()
    match = emparejar_productos(prods, cargar_catalogo())
    match.to_csv(out / "match_catalogo.csv", index=False)
    faltan = match[match["producto_id"] == ""]
    if len(faltan):
        log.warning(
            "Sin match en SNIIM para: %s. Revisa bronze/sniim/productos_sniim.csv y ajusta "
            "patron_sniim en config/insumos.csv",
            ", ".join(faltan["insumo_id"]),
        )
    return match


def _destino_id(categoria: str) -> str:
    f = config.bronze() / "sniim" / "destinos_sniim.csv"
    if f.exists():
        d = pd.read_csv(f, dtype=str)
        d = d[(d["categoria"] == categoria) & d["destino"].map(normalizar).str.contains(config.SNIIM_DESTINO_PATRON)]
        if len(d):
            return d["destino_id"].iloc[0]
    return "-1"  # todos los destinos; se filtra Iztapalapa al parsear


# ---------------------------------------------------------------------------
# 2. Descarga a bronze
# ---------------------------------------------------------------------------
def _meses(desde: date, hasta: date):
    p = pd.Period(desde, "M")
    fin = pd.Period(hasta, "M")
    while p <= fin:
        ini = max(p.start_time.date(), desde)
        f = min(p.end_time.date(), hasta)
        yield p, ini, f
        p += 1


def _ruta_bronze(categoria: str, producto_id: str, periodo: pd.Period) -> Path:
    return config.bronze() / "sniim" / categoria / str(producto_id) / f"{periodo}.html.gz"


def descargar(desde: str = config.FECHA_INICIO_DEFAULT, hasta: str | None = None,
              solo: list[str] | None = None, forzar: bool = False) -> int:
    desde_d = pd.Timestamp(desde).date()
    hasta_d = pd.Timestamp(hasta).date() if hasta else date.today()
    match = resolver_productos()
    match = match[match["producto_id"] != ""]
    if solo:
        match = match[match["insumo_id"].isin(solo)]
    mes_actual = pd.Period(date.today(), "M")
    n = 0
    for _, m in match.iterrows():
        _, url_res = config.SNIIM_CATEGORIAS[m["categoria"]]
        dest = _destino_id(m["categoria"])
        for periodo, ini, fin in _meses(desde_d, hasta_d):
            ruta = _ruta_bronze(m["categoria"], m["producto_id"], periodo)
            # meses cerrados ya descargados no se vuelven a pedir; el mes en curso sí
            if ruta.exists() and not forzar and periodo < mes_actual:
                continue
            params = {
                "fechaInicio": ini.strftime("%d/%m/%Y"),
                "fechaFinal": fin.strftime("%d/%m/%Y"),
                "ProductoId": m["producto_id"],
                "OrigenId": "-1",
                "Origen": "Todos",
                "DestinoId": dest,
                "Destino": "Todos" if dest == "-1" else "",
                "PreciosPorId": config.SNIIM_PRECIOS_POR_KG,
                "RegistrosPorPagina": str(config.SNIIM_REGISTROS_POR_PAGINA),
            }
            html = _get(config.SNIIM_BASE + url_res, params)
            ruta.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(ruta, "wt", encoding="utf-8") as fh:
                fh.write(html)
            n += 1
            log.info("SNIIM %s %s -> %s", m["insumo_id"], periodo, ruta.name)
            time.sleep(config.SNIIM_PAUSA_SEG)
    log.info("SNIIM: %d archivos nuevos en bronze", n)
    return n


# ---------------------------------------------------------------------------
# 3. Parseo a silver
# ---------------------------------------------------------------------------
_FECHA_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
_COLMAP = [
    ("fecha", "fecha"),
    ("presentacion", "presentacion"),
    ("origen", "origen"),
    ("destino", "destino"),
    ("min", "precio_min"),
    ("max", "precio_max"),
    ("frec", "precio_frec"),
    ("obs", "obs"),
]
_POSICIONAL = ["fecha", "presentacion", "origen", "destino", "precio_min", "precio_max", "precio_frec", "obs"]


def _num(x: str) -> float:
    x = (x or "").replace("$", "").replace(",", "").strip()
    try:
        return float(x)
    except ValueError:
        return float("nan")


def parsear_resultados(html: str) -> pd.DataFrame:
    """Parsea la tabla de resultados de SNIIM a un DataFrame.

    Usa los encabezados para ubicar columnas (robusto a cambios de orden) y cae a
    orden posicional si no los encuentra. Soporta tablas donde la fecha viene en
    una fila-separador en lugar de en cada fila.
    """
    soup = BeautifulSoup(html, "lxml")
    tabla = soup.find("table", id="tblResultados")
    if tabla is None:
        tablas = [t for t in soup.find_all("table") if "origen" in normalizar(t.get_text()[:2000])]
        tabla = max(tablas, key=lambda t: len(t.find_all("tr")), default=None)
    if tabla is None:
        return pd.DataFrame(columns=_POSICIONAL)

    cols: list[str] | None = None
    fecha_actual = None
    filas = []
    for tr in tabla.find_all("tr"):
        celdas = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if not celdas:
            continue
        norm = [normalizar(c) for c in celdas]
        if cols is None and len(celdas) >= 4 and any("origen" in c for c in norm):
            cols = []
            for c in norm:
                nombre = next((dst for key, dst in _COLMAP if key in c), c or "x")
                cols.append(nombre)
            continue
        if len(celdas) == 1 and _FECHA_RE.match(celdas[0]):
            fecha_actual = celdas[0]
            continue
        if len(celdas) < 4:
            continue
        nombres = cols if cols and len(cols) == len(celdas) else _POSICIONAL[: len(celdas)]
        row = dict(zip(nombres, celdas))
        if "fecha" not in row or not _FECHA_RE.match(row.get("fecha", "")):
            if fecha_actual is None:
                continue
            row["fecha"] = fecha_actual
        filas.append(row)

    df = pd.DataFrame(filas)
    for c in _POSICIONAL:
        if c not in df:
            df[c] = None
    df = df[_POSICIONAL].copy()
    df["fecha"] = pd.to_datetime(df["fecha"], format="%d/%m/%Y", errors="coerce")
    for c in ("precio_min", "precio_max", "precio_frec"):
        df[c] = df[c].map(_num)
    return df.dropna(subset=["fecha", "precio_frec"])


def construir_silver() -> pd.DataFrame:
    """bronze/sniim/**.html.gz -> silver/sniim_diario.csv (precio por kg, Central de Abasto CDMX)."""
    match_f = config.bronze() / "sniim" / "match_catalogo.csv"
    if not match_f.exists():
        raise FileNotFoundError("Corre primero `insumos ingesta` (no hay match_catalogo.csv)")
    match = pd.read_csv(match_f, dtype=str).dropna(subset=["producto_id"])
    catalogo = cargar_catalogo().set_index("insumo_id")
    partes = []
    for _, m in match.iterrows():
        carpeta = config.bronze() / "sniim" / m["categoria"] / str(m["producto_id"])
        for f in sorted(carpeta.glob("*.html.gz")):
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                df = parsear_resultados(fh.read())
            if df.empty:
                continue
            df["insumo_id"] = m["insumo_id"]
            df["producto_sniim"] = m["producto"]
            df["archivo_bronze"] = str(f.relative_to(config.bronze()))
            partes.append(df)
    if not partes:
        raise RuntimeError("No se parseó ningún registro de SNIIM. Revisa bronze/sniim/")
    df = pd.concat(partes, ignore_index=True)

    # Solo Central de Abasto de Iztapalapa (por si se descargó con destino = Todos)
    dest = df["destino"].map(normalizar)
    if dest.str.contains(config.SNIIM_DESTINO_PATRON).any():
        df = df[dest.str.contains(config.SNIIM_DESTINO_PATRON) | dest.eq("")]

    # Calidad: duplicados y precios fuera de rango plausible
    df = df.drop_duplicates(subset=["insumo_id", "fecha", "presentacion", "origen", "precio_frec"])
    lo = df["insumo_id"].map(catalogo["precio_min_kg"])
    hi = df["insumo_id"].map(catalogo["precio_max_kg"])
    df["fuera_de_rango"] = (df["precio_frec"] < lo) | (df["precio_frec"] > hi)
    df["fuente"] = "SNIIM"
    df = df.sort_values(["insumo_id", "fecha"]).reset_index(drop=True)
    out = config.silver() / "sniim_diario.csv"
    df.to_csv(out, index=False)
    log.info("silver/sniim_diario.csv: %d filas, %d insumos, %.1f%% fuera de rango",
             len(df), df["insumo_id"].nunique(), 100 * df["fuera_de_rango"].mean())
    return df
