"""Catálogo maestro de insumos: liga cada ingrediente de las recetas con su fuente de precio."""
from __future__ import annotations

import re
import unicodedata

import pandas as pd

from . import config


def normalizar(texto: str) -> str:
    """minúsculas, sin acentos, espacios colapsados."""
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().lower()


def cargar_catalogo() -> pd.DataFrame:
    cat = pd.read_csv(config.CONFIG_DIR / "insumos.csv", dtype=str).fillna("")
    for col in ("precio_min_kg", "precio_max_kg"):
        cat[col] = pd.to_numeric(cat[col], errors="coerce")
    if cat["insumo_id"].duplicated().any():
        dup = cat.loc[cat["insumo_id"].duplicated(), "insumo_id"].tolist()
        raise ValueError(f"insumo_id duplicado en config/insumos.csv: {dup}")
    return cat


def emparejar_productos(productos: pd.DataFrame, catalogo: pd.DataFrame) -> pd.DataFrame:
    """Empareja el dropdown de SNIIM (categoria, producto_id, producto) con el catálogo.

    Devuelve una fila por insumo SNIIM con el producto elegido (o vacío si no hubo match),
    y la lista de candidatos para revisar a mano.
    """
    productos = productos.copy()
    productos["norm"] = productos["producto"].map(normalizar)
    filas = []
    for _, ins in catalogo[catalogo["fuente"] == "sniim"].iterrows():
        cands = productos[
            (productos["categoria"] == ins["sniim_categoria"])
            & productos["norm"].map(lambda s, pat=ins["patron_sniim"]: re.search(pat, s) is not None)
        ]
        filas.append(
            {
                "insumo_id": ins["insumo_id"],
                "categoria": ins["sniim_categoria"],
                "producto_id": cands["producto_id"].iloc[0] if len(cands) else "",
                "producto": cands["producto"].iloc[0] if len(cands) else "",
                "n_candidatos": len(cands),
                "candidatos": " | ".join(cands["producto"].tolist()[:6]),
            }
        )
    return pd.DataFrame(filas)
