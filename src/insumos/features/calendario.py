"""Variables de calendario por semana: temporadas de alta demanda en México."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd


def pascua(anio: int) -> date:
    """Domingo de Pascua (algoritmo gregoriano anónimo)."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(anio, mes, dia + 1)


def _en_rango(dias: pd.DatetimeIndex, ini: date, fin: date) -> pd.Series:
    return pd.Series((dias >= pd.Timestamp(ini)) & (dias <= pd.Timestamp(fin)), index=dias)


def features_semanales(semanas: pd.Series) -> pd.DataFrame:
    """Para cada lunes de inicio de semana, fracción de días de la semana dentro de cada temporada."""
    semanas = pd.to_datetime(pd.Series(semanas).drop_duplicates().sort_values())
    filas = []
    for s in semanas:
        dias = pd.date_range(s, s + timedelta(days=6))
        y = s.year
        flags = {"cuaresma": 0.0, "semana_santa": 0.0, "fiestas_patrias": 0.0,
                 "muertos": 0.0, "guadalupe_reyes": 0.0}
        for anio in {d.year for d in dias}:
            p = pascua(anio)
            flags["cuaresma"] += _en_rango(dias, p - timedelta(days=46), p - timedelta(days=1)).mean()
            flags["semana_santa"] += _en_rango(dias, p - timedelta(days=7), p).mean()
            flags["fiestas_patrias"] += _en_rango(dias, date(anio, 9, 1), date(anio, 9, 16)).mean()
            flags["muertos"] += _en_rango(dias, date(anio, 10, 25), date(anio, 11, 2)).mean()
            flags["guadalupe_reyes"] += _en_rango(dias, date(anio, 12, 12), date(anio, 12, 31)).mean()
            flags["guadalupe_reyes"] += _en_rango(dias, date(anio, 1, 1), date(anio, 1, 6)).mean()
        filas.append({
            "semana": s,
            "anio": y,
            "mes": s.month,
            "semana_iso": int(s.isocalendar().week),
            **{k: round(min(v, 1.0), 3) for k, v in flags.items()},
        })
    df = pd.DataFrame(filas)
    # codificación cíclica para que el modelo vea que la semana 52 está junto a la 1
    df["sem_sin"] = np.sin(2 * np.pi * df["semana"].dt.day_of_year / 365.25)
    df["sem_cos"] = np.cos(2 * np.pi * df["semana"].dt.day_of_year / 365.25)
    return df
