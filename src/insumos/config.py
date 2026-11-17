"""Rutas y parámetros globales del proyecto.

La carpeta de datos se puede cambiar con la variable de entorno INSUMOS_DATA_DIR
(el comando `demo` la apunta a data_sample/ para no mezclar datos simulados con reales).
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
PLANTILLAS_DIR = ROOT / "plantillas"
REPORTS_DIR = ROOT / "reports"


def data_dir() -> Path:
    return Path(os.environ.get("INSUMOS_DATA_DIR", ROOT / "data"))


def bronze() -> Path:
    return data_dir() / "bronze"


def silver() -> Path:
    return data_dir() / "silver"


def gold() -> Path:
    return data_dir() / "gold"


def fondas() -> Path:
    return data_dir() / "fondas"


def external() -> Path:
    return data_dir() / "external"


def ensure_dirs() -> None:
    for p in (bronze(), silver(), gold(), fondas(), external()):
        p.mkdir(parents=True, exist_ok=True)


def es_sintetico() -> bool:
    return data_dir().name == "data_sample"


# --- SNIIM -----------------------------------------------------------------
SNIIM_BASE = (
    "http://www.economia-sniim.gob.mx/NUEVO/Consultas/MercadosNacionales/"
    "PreciosDeMercado/Agricolas"
)
# categoria -> (página de consulta con el dropdown, página de resultados)
SNIIM_CATEGORIAS = {
    "frutas_hortalizas": ("/ConsultaFrutasYHortalizas.aspx", "/ResultadosConsultaFechaFrutasYHortalizas.aspx"),
    "granos": ("/ConsultaGranos.aspx?SubOpcion=6", "/ResultadosConsultaFechaGranos.aspx"),
    "aceites": ("/ConsultaAceites.aspx?SubOpcion=8", "/ResultadosConsultaFechaAceites.aspx"),
}
SNIIM_DESTINO_PATRON = "iztapalapa"  # Central de Abasto de la CDMX
SNIIM_PRECIOS_POR_KG = "2"  # PreciosPorId: 1 = presentación comercial, 2 = kilogramo
SNIIM_REGISTROS_POR_PAGINA = 1000
SNIIM_PAUSA_SEG = 0.6  # no saturar al servidor de gobierno

# --- Banxico SIE -----------------------------------------------------------
BANXICO_URL = "https://www.banxico.org.mx/SieAPIRest/service/v1/series/{series}/datos/{ini}/{fin}"
BANXICO_SERIES = {
    "SF43718": "tc_fix",  # Tipo de cambio FIX MXN/USD (diario)
    "SP1": "inpc",        # INPC general (mensual)
}

FECHA_INICIO_DEFAULT = "2020-01-01"
