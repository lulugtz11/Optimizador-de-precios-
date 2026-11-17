"""Modelo de pronóstico de precios de insumos.

Diseño
------
- Un modelo GLOBAL (todos los insumos juntos) de gradient boosting por horizonte h (semanas).
  Aprende patrones compartidos: reversión a la media tras picos, estacionalidad, efecto
  de temporadas (Cuaresma, fiestas), tipo de cambio e inflación.
- Objetivo: cambio en log-precio  y = log p(t+h) - log p(t)  → comparable entre insumos.
- Tres modelos por horizonte: mediana (pronóstico) y cuantiles 10% y 90% (intervalo 80%).
- Solo insumos con precio de MERCADO (SNIIM y SNIIM pecuarios). Los de "precio base x INPC"
  no se pronostican: se proyectan con su último precio.

Validación (backtest con origen móvil, sin fuga de información)
-------------------------------------------------------------
Cada 13 semanas se reentrena con TODO lo anterior al corte (solo filas cuyo objetivo
t+h ya se conocía al corte) y se pronostica las 13 semanas siguientes. Se compara contra:
  ingenuo (precio de hoy), ingenuo estacional (cambio del año pasado), media móvil 4 sem.
  y el método Theta (ganador de la competencia M3; benchmark estadístico estándar).
Métrica KPI 1: wRMSE en % del precio, ponderado por el peso del insumo en el costo de los menús.
"""
from __future__ import annotations

import logging
import pickle

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from . import config

log = logging.getLogger(__name__)

FUENTES_MERCADO = {"SNIIM", "SNIIM pecuarios"}
HORIZONTES_BACKTEST = [1, 4, 8, 12]
HORIZONTES_PRONOSTICO = list(range(1, 13))
CUANTILES = {"p10": 0.10, "p50": 0.50, "p90": 0.90}
SEMANAS_BACKTEST = 78       # ~18 meses de evaluación fuera de muestra
REENTRENAR_CADA = 13        # semanas
LAG_PUBLICACION_INPC = 4    # el INPC de un mes se conoce semanas después
CAL_COLS = ["cuaresma", "semana_santa", "fiestas_patrias", "muertos", "guadalupe_reyes", "sem_sin", "sem_cos"]
# elegido en backtest entre 4 variantes (más simple/regularizado ganó a más complejo)
PARAMS = dict(max_iter=350, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=80,
              l2_regularization=1.0, random_state=0)


# ---------------------------------------------------------------------------
# Datos y variables
# ---------------------------------------------------------------------------
def cargar_panel() -> pd.DataFrame:
    p = pd.read_csv(config.gold() / "panel_semanal.csv", parse_dates=["semana"])
    return p.sort_values(["insumo_id", "semana"]).reset_index(drop=True)


def variables(panel: pd.DataFrame, h: int) -> pd.DataFrame:
    """Una fila por (insumo, semana origen t) con variables conocidas en t y objetivo a t+h."""
    df = panel[panel["fuente"].isin(FUENTES_MERCADO)].copy()
    df["lp"] = np.log(df["precio_kg_imp"])
    g = df.groupby("insumo_id", group_keys=False)
    for k in (1, 2, 4, 8, 13, 26, 52):
        df[f"r{k}"] = df["lp"] - g["lp"].shift(k)
    d1 = g["lp"].diff()
    df["vol4"] = d1.groupby(df["insumo_id"]).transform(lambda s: s.rolling(4, min_periods=3).std())
    df["vol13"] = d1.groupby(df["insumo_id"]).transform(lambda s: s.rolling(13, min_periods=8).std())
    m52 = g["lp"].transform(lambda s: s.rolling(52, min_periods=26).mean())
    s52 = g["lp"].transform(lambda s: s.rolling(52, min_periods=26).std())
    df["z52"] = (df["lp"] - m52) / s52
    df["vs_max13"] = df["lp"] - g["lp"].transform(lambda s: s.rolling(13, min_periods=4).max())
    df["vs_min13"] = df["lp"] - g["lp"].transform(lambda s: s.rolling(13, min_periods=4).min())
    # estacionalidad: qué pasó el año pasado en esta misma ventana de h semanas
    df["estac_h"] = g["lp"].shift(52 - h) - g["lp"].shift(52)
    df["dias_con_precio"] = df["dias_con_precio"].fillna(0)
    # macro (con rezago de publicación)
    df["tc_r4"] = np.log(df["tc_fix"]) - np.log(g["tc_fix"].shift(4)) if "tc_fix" in df else np.nan
    df["tc_r13"] = np.log(df["tc_fix"]) - np.log(g["tc_fix"].shift(13)) if "tc_fix" in df else np.nan
    if "inpc" in df:
        inpc_lag = g["inpc"].shift(LAG_PUBLICACION_INPC)
        df["inpc_a"] = np.log(inpc_lag) - np.log(g["inpc"].shift(LAG_PUBLICACION_INPC + 52))
    # calendario de la semana OBJETIVO (se conoce de antemano)
    for c in CAL_COLS:
        df[f"obj_{c}"] = g[c].shift(-h)
    df["insumo_cod"] = df["insumo_id"].astype("category").cat.codes
    df["grupo_cod"] = df["grupo"].astype("category").cat.codes
    # objetivo
    df["y"] = g["lp"].shift(-h) - df["lp"]
    df["semana_obj"] = g["semana"].shift(-h)
    df["precio_obj"] = g["precio_kg_imp"].shift(-h)
    df["h"] = h
    return df


def columnas_x(df: pd.DataFrame) -> list[str]:
    rezagos = [f"r{k}" for k in (1, 2, 4, 8, 13, 26, 52)]
    resto = ["vol4", "vol13", "z52", "vs_max13", "vs_min13", "estac_h", "dias_con_precio",
             "tc_r4", "tc_r13", "inpc_a", "insumo_cod", "grupo_cod", "sem_sin", "sem_cos"]
    calendario = [f"obj_{c}" for c in CAL_COLS]
    return [c for c in rezagos + resto + calendario if c in df]


def _modelos(X: pd.DataFrame, y: pd.Series) -> dict:
    cat = [c in ("insumo_cod", "grupo_cod") for c in X.columns]
    out = {}
    for nombre, q in CUANTILES.items():
        loss = dict(loss="quantile", quantile=q)
        m = HistGradientBoostingRegressor(**PARAMS, **loss, categorical_features=cat)
        out[nombre] = m.fit(X, y)
    return out


def _predecir(mods: dict, X: pd.DataFrame) -> pd.DataFrame:
    pr = pd.DataFrame({k: m.predict(X) for k, m in mods.items()}, index=X.index)
    # los cuantiles no deben cruzarse
    pr["p10"] = np.minimum(pr["p10"], pr["p50"])
    pr["p90"] = np.maximum(pr["p90"], pr["p50"])
    return pr


# ---------------------------------------------------------------------------
# Benchmark estadístico: método Theta (ganador de la competencia M3)
# ---------------------------------------------------------------------------
ALFAS = np.linspace(0.05, 0.95, 19)
VENTANA_THETA = 156  # 3 años


def theta_log(panel: pd.DataFrame) -> pd.DataFrame:
    """Para cada (insumo, semana t): nivel SES y tendencia, para pronosticar log p(t+h) = nivel + 0.5*b*h.

    Theta(0, 2): la línea theta=0 es la tendencia lineal (pendiente b) y la theta=2 se suaviza
    con SES. El pronóstico combina ambas con igual peso. Alfa se elige por insumo y origen
    minimizando el error a 1 paso en la ventana previa (sin ver el futuro).
    """
    df = panel[panel["fuente"].isin(FUENTES_MERCADO)][["insumo_id", "semana", "precio_kg_imp"]].copy()
    df["lp"] = np.log(df["precio_kg_imp"])
    filas = []
    for ins, d in df.groupby("insumo_id"):
        x = d["lp"].ffill().bfill().to_numpy()
        n = len(x)
        # SES para todas las alfas a la vez
        niveles = np.empty((len(ALFAS), n))
        niveles[:, 0] = x[0]
        for t in range(1, n):
            niveles[:, t] = ALFAS * x[t] + (1 - ALFAS) * niveles[:, t - 1]
        err = (x[1:] - niveles[:, :-1]) ** 2  # error a 1 paso
        err_acum = np.cumsum(err, axis=1)
        t_idx = np.arange(n)
        for t in range(26, n):
            ini = max(0, t - VENTANA_THETA)
            sse = err_acum[:, t - 1] - (err_acum[:, ini - 1] if ini > 0 else 0)
            a = int(np.argmin(sse))
            tt, xx = t_idx[ini:t + 1], x[ini:t + 1]
            b = np.polyfit(tt, xx, 1)[0]
            filas.append((ins, d["semana"].iloc[t], niveles[a, t], b))
    return pd.DataFrame(filas, columns=["insumo_id", "semana", "theta_nivel", "theta_b"])


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------
def backtest(panel: pd.DataFrame) -> pd.DataFrame:
    semanas = np.sort(panel["semana"].unique())
    inicio = semanas[-SEMANAS_BACKTEST]
    cortes = [pd.Timestamp(s) for s in semanas[(semanas >= inicio)][::REENTRENAR_CADA]]
    filas = []
    for h in HORIZONTES_BACKTEST:
        df = variables(panel, h)
        cols = columnas_x(df)
        for i, corte in enumerate(cortes):
            fin = cortes[i + 1] if i + 1 < len(cortes) else df["semana"].max() + pd.Timedelta(days=1)
            entren = df[(df["semana_obj"] < corte) & df["y"].notna() & df["r52"].notna()]
            prueba = df[(df["semana"] >= corte) & (df["semana"] < fin) & df["y"].notna()]
            if prueba.empty:
                continue
            mods = _modelos(entren[cols], entren["y"])
            pr = _predecir(mods, prueba[cols])
            precio_hoy = prueba["precio_kg_imp"]
            res = prueba[["insumo_id", "semana", "semana_obj", "h", "precio_kg_imp", "precio_obj"]].copy()
            res["corte"] = corte
            for k in CUANTILES:
                res[f"modelo_{k}"] = precio_hoy * np.exp(pr[k])
            res["ingenuo"] = precio_hoy
            res["ingenuo_estacional"] = precio_hoy * np.exp(prueba["estac_h"].fillna(0))
            res["media_movil_4"] = np.exp(prueba.groupby("insumo_id")["lp"].transform(
                lambda s: s.rolling(4, min_periods=1).mean()))
            # la media móvil necesita historia previa: recalcular sobre el df completo
            filas.append(res)
            log.info("backtest h=%d corte=%s entren=%d prueba=%d", h, corte.date(), len(entren), len(prueba))
    bt = pd.concat(filas, ignore_index=True)
    th = theta_log(panel)
    bt = bt.merge(th, on=["insumo_id", "semana"], how="left")
    bt["theta"] = np.exp(bt["theta_nivel"] + 0.5 * bt["theta_b"] * bt["h"])
    bt["theta"] = bt["theta"].fillna(bt["ingenuo"])
    # media móvil bien calculada con toda la historia
    mm = panel.assign(mm4=panel.groupby("insumo_id")["precio_kg_imp"].transform(
        lambda s: s.rolling(4, min_periods=1).mean()))[["insumo_id", "semana", "mm4"]]
    bt = bt.drop(columns="media_movil_4").merge(mm, on=["insumo_id", "semana"], how="left").rename(
        columns={"mm4": "media_movil_4"})
    return calibrar(bt)


def _no_conformidad(d: pd.DataFrame) -> pd.Series:
    """Cuánto se sale el precio real del intervalo (en log); negativo = quedó dentro con margen."""
    y = np.log(d["precio_obj"])
    return np.maximum(np.log(d["modelo_p10"]) - y, y - np.log(d["modelo_p90"]))


def calibrar(bt: pd.DataFrame) -> pd.DataFrame:
    """Calibración conformal secuencial: cada bloque se ajusta solo con errores de bloques ANTERIORES."""
    bt = bt.copy()
    bt["ajuste_intervalo"] = 0.0
    for h, d in bt.groupby("h"):
        cortes = sorted(d["corte"].unique())
        for i, c in enumerate(cortes):
            prev = d[d["corte"].isin(cortes[:i])]
            q = float(np.quantile(_no_conformidad(prev), 0.80)) if len(prev) >= 50 else 0.0
            bt.loc[(bt["h"] == h) & (bt["corte"] == c), "ajuste_intervalo"] = q
    bt["modelo_p10_bruto"], bt["modelo_p90_bruto"] = bt["modelo_p10"], bt["modelo_p90"]
    bt["modelo_p10"] = bt["modelo_p10"] * np.exp(-bt["ajuste_intervalo"])
    bt["modelo_p90"] = bt["modelo_p90"] * np.exp(bt["ajuste_intervalo"])
    return bt


def ajuste_final(bt: pd.DataFrame) -> dict[int, float]:
    """Ajuste conformal para el pronóstico real: usa TODOS los errores fuera de muestra del backtest."""
    d = bt.assign(modelo_p10=bt["modelo_p10_bruto"], modelo_p90=bt["modelo_p90_bruto"])
    q = {h: float(np.quantile(_no_conformidad(g), 0.80)) for h, g in d.groupby("h")}
    hs = np.array(sorted(q))
    return {h: float(np.interp(h, hs, [q[x] for x in hs])) for h in HORIZONTES_PRONOSTICO}


def metricas_kpi1(bt: pd.DataFrame, pesos: pd.Series) -> pd.DataFrame:
    filas = []
    for h, d in bt.groupby("h"):
        for nombre in ("modelo_p50", "theta", "ingenuo", "ingenuo_estacional", "media_movil_4"):
            err = 100 * (d[nombre] - d["precio_obj"]) / d["precio_obj"]
            rmse = np.sqrt((err ** 2).groupby(d["insumo_id"]).mean())
            w = pesos.reindex(rmse.index).fillna(0)
            ok = w > 0
            filas.append({"horizonte_sem": h, "modelo": {"modelo_p50": "boosting"}.get(nombre, nombre),
                          "wRMSE_pct": float(np.sqrt((w[ok] * rmse[ok] ** 2).sum() / w[ok].sum())),
                          "RMSE_pct_sin_ponderar": float(rmse.mean()),
                          "MAE_pct": float(err.abs().mean())})
        # cobertura medida solo en bloques que ya tenían calibración previa
        dd = d[d["corte"] > d["corte"].min()]
        cob = ((dd["precio_obj"] >= dd["modelo_p10"]) & (dd["precio_obj"] <= dd["modelo_p90"])).mean()
        filas.append({"horizonte_sem": h, "modelo": "cobertura_intervalo_80", "wRMSE_pct": np.nan,
                      "RMSE_pct_sin_ponderar": np.nan, "MAE_pct": 100 * cob})
    t = pd.DataFrame(filas)
    base = t[t["modelo"] == "ingenuo"].set_index("horizonte_sem")["wRMSE_pct"]
    t["modelo"] = t["modelo"].replace({"boosting": "modelo"})
    t["mejora_vs_ingenuo_pct"] = 100 * (1 - t["wRMSE_pct"] / t["horizonte_sem"].map(base))
    return t


def metricas_kpi2(bt: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """Error % del costo de cada platillo: pronóstico del modelo vs 'precio de hoy'."""
    from .build.costeo import cargar_recetas

    recetas, _ = cargar_recetas()
    recetas = recetas[recetas["ligado"]]
    filas = []
    for h, d in bt.groupby("h"):
        precios = panel[["insumo_id", "semana", "precio_kg_imp"]]
        # insumos sin modelo (precio base x INPC): plan = precio de hoy, real = precio en t+h
        semanas = d[["semana", "semana_obj"]].drop_duplicates()
        otros = precios[~precios["insumo_id"].isin(d["insumo_id"].unique())]
        o = otros.merge(semanas, on="semana").merge(
            precios.rename(columns={"semana": "semana_obj", "precio_kg_imp": "precio_obj"}),
            on=["insumo_id", "semana_obj"])
        o["modelo_p50"] = o["ingenuo"] = o["precio_kg_imp"]
        todo = pd.concat([d[["insumo_id", "semana", "precio_obj", "modelo_p50", "ingenuo"]],
                          o[["insumo_id", "semana", "precio_obj", "modelo_p50", "ingenuo"]]])
        r = recetas.merge(todo, on="insumo_id")
        kg = r["gramos_por_porcion"] / 1000
        r = r.assign(real=kg * r["precio_obj"], plan_modelo=kg * r["modelo_p50"], plan_hoy=kg * r["ingenuo"])
        c = r.groupby(["fonda_id", "platillo_id", "semana"]).agg(
            n=("insumo_id", "size"), real=("real", "sum"), plan_modelo=("plan_modelo", "sum"),
            plan_hoy=("plan_hoy", "sum"))
        completos = recetas.groupby(["fonda_id", "platillo_id"]).size()
        c = c[c["n"] == c.index.droplevel("semana").map(completos)]
        for nombre, col in (("modelo", "plan_modelo"), ("precio_de_un_dia", "plan_hoy")):
            ape = 100 * (c[col] - c["real"]).abs() / c["real"]
            filas.append({"horizonte_sem": h, "modelo": nombre, "MAPE_costo_menu_pct": float(ape.mean()),
                          "P90_error_pct": float(ape.quantile(0.9)), "n": int(len(c))})
    t = pd.DataFrame(filas)
    base = t[t["modelo"] == "precio_de_un_dia"].set_index("horizonte_sem")["MAPE_costo_menu_pct"]
    t["reduccion_error_pct"] = 100 * (1 - t["MAPE_costo_menu_pct"] / t["horizonte_sem"].map(base))
    return t


# ---------------------------------------------------------------------------
# Modelo final y pronóstico
# ---------------------------------------------------------------------------
def pronosticar(panel: pd.DataFrame, ajuste: dict[int, float] | None = None) -> pd.DataFrame:
    filas, modelos = [], {}
    ultima = panel.loc[panel["observado"] & panel["fuente"].isin(FUENTES_MERCADO), "semana"].max()
    for h in HORIZONTES_PRONOSTICO:
        df = variables(panel, h)
        cols = columnas_x(df)
        entren = df[df["y"].notna() & df["r52"].notna()]
        mods = _modelos(entren[cols], entren["y"])
        modelos[h] = {"modelos": mods, "columnas": cols}
        hoy = df[df["semana"] == ultima]
        # el calendario de la semana objetivo no existe aún en el panel: calcularlo
        from .features.calendario import features_semanales
        cal = features_semanales(pd.Series([ultima + pd.Timedelta(weeks=h)])).iloc[0]
        hoy = hoy.copy()
        for c in CAL_COLS:
            hoy[f"obj_{c}"] = cal[c]
        pr = _predecir(mods, hoy[cols])
        res = hoy[["insumo_id", "nombre", "grupo", "semana", "precio_kg_imp"]].copy()
        res["h"], res["semana_obj"] = h, ultima + pd.Timedelta(weeks=h)
        for k in CUANTILES:
            res[k] = hoy["precio_kg_imp"] * np.exp(pr[k])
        a = (ajuste or {}).get(h, 0.0)
        res["p10"], res["p90"] = res["p10"] * np.exp(-a), res["p90"] * np.exp(a)
        filas.append(res)
        log.info("modelo final h=%d entrenado con %d filas", h, len(entren))
    out = config.ROOT / "models"
    out.mkdir(exist_ok=True)
    with open(out / "modelos.pkl", "wb") as fh:
        pickle.dump(modelos, fh)
    return pd.concat(filas, ignore_index=True).rename(columns={"precio_kg_imp": "precio_actual"})


def importancia(panel: pd.DataFrame, h: int = 4) -> pd.Series:
    """Importancia por permutación (en el último año) del modelo a h semanas."""
    from sklearn.inspection import permutation_importance

    df = variables(panel, h)
    cols = columnas_x(df)
    corte = df["semana"].max() - pd.Timedelta(weeks=52)
    entren = df[(df["semana_obj"] < corte) & df["y"].notna() & df["r52"].notna()]
    prueba = df[(df["semana"] >= corte) & df["y"].notna()]
    m = _modelos(entren[cols], entren["y"])["p50"]
    r = permutation_importance(m, prueba[cols], prueba["y"], n_repeats=5, random_state=0,
                               scoring="neg_mean_absolute_error")
    return pd.Series(r.importances_mean, index=cols).sort_values(ascending=False)


def correr() -> dict:
    panel = cargar_panel()
    pesos = pd.read_csv(config.gold() / "pesos_insumos.csv").set_index("insumo_id")["peso"]
    bt = backtest(panel)
    k1 = metricas_kpi1(bt, pesos)
    k2 = metricas_kpi2(bt, panel)
    pron = pronosticar(panel, ajuste_final(bt))
    imp = importancia(panel)
    bt.to_csv(config.gold() / "backtest_predicciones.csv", index=False)
    k1.to_csv(config.gold() / "kpi1_modelo.csv", index=False)
    k2.to_csv(config.gold() / "kpi2_modelo.csv", index=False)
    pron.to_csv(config.gold() / "pronosticos.csv", index=False)
    imp.rename("importancia").to_csv(config.gold() / "importancia_variables.csv")
    _reporte(k1, k2, imp, pron)
    return {"kpi1": k1, "kpi2": k2, "pronosticos": pron, "importancia": imp}


def _reporte(k1: pd.DataFrame, k2: pd.DataFrame, imp: pd.Series, pron: pd.DataFrame) -> None:
    def md(df: pd.DataFrame) -> str:
        df = df.round(2)
        return "\n".join(["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
                         + ["| " + " | ".join(map(str, r)) + " |" for r in df.itertuples(index=False)])

    txt = ["# Modelo de pronóstico — resultados del backtest\n",
           f"Evaluación fuera de muestra: últimas {SEMANAS_BACKTEST} semanas, reentrenando cada "
           f"{REENTRENAR_CADA}. Gradient boosting global con cuantiles 10/50/90.\n",
           "## KPI 1 — error del pronóstico (wRMSE % del precio)\n", md(k1), "\n",
           "## KPI 2 — error del costo del menú\n", md(k2), "\n",
           "## Variables más importantes (h = 4 semanas)\n", md(imp.head(12).rename("importancia").reset_index()),
           "\n", f"## Pronóstico desde la semana {pron['semana'].max().date()}\n",
           md(pron[pron["h"].isin([1, 4, 8, 12])][["insumo_id", "h", "precio_actual", "p10", "p50", "p90"]])]
    config.REPORTS_DIR.mkdir(exist_ok=True)
    (config.REPORTS_DIR / "modelo.md").write_text("\n".join(txt), encoding="utf-8")
