from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from insumos.catalogo import cargar_catalogo, emparejar_productos, normalizar
from insumos.features.calendario import features_semanales, pascua
from insumos.ingest.sniim import parsear_resultados

FIX = Path(__file__).parent / "fixtures"


def test_parser_tabla_estandar():
    df = parsear_resultados((FIX / "sniim_resultados.html").read_text(encoding="utf-8"))
    assert len(df) == 3
    assert df["fecha"].iloc[0] == pd.Timestamp("2026-11-03")
    assert df["precio_frec"].tolist() == [20.0, 19.0, 1210.0]  # maneja comas de miles
    assert df["origen"].iloc[1] == "Michoacán"


def test_parser_fecha_en_fila_separadora_y_columnas_desordenadas():
    df = parsear_resultados((FIX / "sniim_resultados_agrupado.html").read_text(encoding="utf-8"))
    assert len(df) == 2
    assert df["fecha"].dt.date.tolist() == [date(2026, 11, 5), date(2026, 11, 6)]
    assert df["precio_frec"].tolist() == [32.0, 31.5]
    assert df["presentacion"].iloc[0] == "Kilogramo"


def test_parser_html_sin_tabla():
    assert parsear_resultados("<html><body>Sin resultados</body></html>").empty


def test_normalizar():
    assert normalizar("  Chile  Jalapeño ") == "chile jalapeno"


def test_catalogo_valido():
    cat = cargar_catalogo()
    assert cat["insumo_id"].is_unique
    assert (cat["precio_min_kg"] < cat["precio_max_kg"]).all()
    sniim = cat[cat["fuente"] == "sniim"]
    assert (sniim["sniim_categoria"] != "").all() and (sniim["patron_sniim"] != "").all()


def test_emparejar_productos():
    # nombres reales del dropdown de SNIIM (nov 2026)
    prods = pd.DataFrame({
        "categoria": ["frutas_hortalizas"] * 6,
        "producto_id": ["836", "839", "417", "419", "183", "182"],
        "producto": ["Tomate Bola - Primera", "Tomate Saladette - Primera", "Limón c/semilla # 2 - Primera",
                     "Limón c/semilla # 4 - Primera", "Cebolla Bola - Primera",
                     "Cebolla Bola grande - Primera"],
    })
    m = emparejar_productos(prods, cargar_catalogo()).set_index("insumo_id")
    assert m.loc["jitomate", "producto_id"] == "839"
    assert m.loc["limon", "producto_id"] == "419"
    assert m.loc["cebolla", "producto_id"] == "183"
    assert m.loc["aguacate", "producto_id"] == ""  # sin match -> se reporta


@pytest.mark.parametrize("anio,esperado", [(2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)),
                                           (2026, date(2026, 4, 5))])
def test_pascua(anio, esperado):
    assert pascua(anio) == esperado


def test_features_calendario():
    f = features_semanales(pd.Series(pd.to_datetime(["2026-03-30", "2026-09-14", "2026-12-28"]))).set_index("semana")
    assert f.loc["2026-03-30", "semana_santa"] == 1.0
    assert f.loc["2026-09-14", "fiestas_patrias"] > 0
    assert f.loc["2026-12-28", "guadalupe_reyes"] > 0.5


def test_bronze_a_silver(tmp_path, monkeypatch):
    """Simula un HTML descargado y verifica que silver filtre Iztapalapa y marque fuera de rango."""
    import gzip

    from insumos.ingest import sniim

    monkeypatch.setenv("INSUMOS_DATA_DIR", str(tmp_path / "data"))
    carpeta = tmp_path / "data" / "bronze" / "sniim"
    (carpeta / "frutas_hortalizas" / "1").mkdir(parents=True)
    pd.DataFrame([{"insumo_id": "jitomate", "categoria": "frutas_hortalizas", "producto_id": "1",
                   "producto": "Jitomate Saladette"}]).to_csv(carpeta / "match_catalogo.csv", index=False)
    with gzip.open(carpeta / "frutas_hortalizas" / "1" / "2026-11.html.gz", "wt", encoding="utf-8") as fh:
        fh.write((FIX / "sniim_resultados.html").read_text(encoding="utf-8"))
    (tmp_path / "data" / "silver").mkdir(parents=True)

    df = sniim.construir_silver()
    assert len(df) == 2  # la fila de Guadalajara se descarta
    assert set(df["insumo_id"]) == {"jitomate"}
    assert not df["fuera_de_rango"].any()


def test_parser_estructura_real_sniim_fyh():
    df = parsear_resultados((FIX / "sniim_real_fyh.html").read_text(encoding="utf-8"))
    assert len(df) == 2  # la fila "Hortalizas" no cuenta
    assert df["precio_frec"].tolist() == [46.67, 34.62]
    assert df["presentacion"].iloc[0] == "Caja de 12 kg."
    assert df["destino"].isna().all()


def test_periodos_inegi():
    from insumos.ingest.inegi import _periodo

    assert _periodo("2026/10") == pd.Timestamp("2026-10-01")
    assert _periodo("2026/10/02") == pd.Timestamp("2026-10-16")


def test_costeo_no_ignora_ingredientes_sin_precio():
    from insumos.build.costeo import costo_semanal

    panel = pd.DataFrame({"semana": pd.to_datetime(["2026-11-02", "2026-11-09"]),
                          "insumo_id": "jitomate", "precio_kg_imp": [20.0, 30.0]})
    recetas = pd.DataFrame({"fonda_id": "F", "platillo_id": ["a", "a", "b"],
                            "insumo_id": ["jitomate", "pollo_pierna_muslo", "jitomate"],
                            "gramos_por_porcion": [100, 200, 100], "ligado": True})
    menus = pd.DataFrame({"fonda_id": "F", "platillo_id": ["a", "b"], "precio_venta": [90, 30]})
    c = costo_semanal(recetas, menus, panel).set_index(["platillo_id", "semana"])
    assert c.loc[("a", pd.Timestamp("2026-11-02")), "costo_porcion"] != c.loc[("a", pd.Timestamp("2026-11-02")), "costo_porcion"]  # NaN
    assert c.loc[("b", pd.Timestamp("2026-11-09")), "costo_porcion"] == 3.0


def test_parser_pecuarios_pollo_cdmx():
    from insumos.ingest.sniim_pecuarios import parsear_pollo

    df = parsear_pollo((FIX / "sniim_pecuarios_pollo.html").read_text(encoding="utf-8"))
    assert set(df["insumo_id"]) == {"pechuga_pollo", "pollo_pierna_muslo"}
    assert df["origen"].str.startswith("D.F.").all()  # La Paz, Querétaro, etc. se descartan
    becerra = df[(df["origen"].str.contains("Becerra")) & (df["fecha"] == "2026-10-09")].set_index("insumo_id")
    assert becerra.loc["pechuga_pollo", "precio_frec"] == 112.0
    assert becerra.loc["pollo_pierna_muslo", "precio_frec"] == 43.0


def test_parser_pecuarios_bovino():
    from insumos.ingest.sniim_pecuarios import parsear_bovino

    df = parsear_bovino((FIX / "sniim_pecuarios_bovino.html").read_text(encoding="utf-8"))
    assert set(df["insumo_id"]) <= {"res_bistec", "res_molida"} and len(df) > 0
    primera = df[(df["fecha"] == "2026-10-09") & (df["insumo_id"] == "res_bistec")]
    assert primera["precio_frec"].iloc[0] == 150.0  # Pulpa Bola, solo precio máximo


def test_nopal_se_convierte_de_pieza_a_kg():
    cat = cargar_catalogo().set_index("insumo_id")
    assert cat.loc["nopal", "factor_a_kg"] == 12.5
    assert 1.5 * cat.loc["nopal", "factor_a_kg"] >= cat.loc["nopal", "precio_min_kg"]


def test_precio_base_por_inflacion(tmp_path, monkeypatch):
    from insumos.ingest import manual

    monkeypatch.setenv("INSUMOS_DATA_DIR", str(tmp_path))
    (tmp_path / "silver").mkdir()
    pd.DataFrame({"fecha": pd.to_datetime(["2026-01-01", "2026-02-01"]), "variable": "inpc",
                  "valor": [100.0, 110.0]}).to_csv(tmp_path / "silver" / "macro_banxico.csv", index=False)
    df = manual.precios_base_inflacion().set_index(["insumo_id", "fecha"])
    huevo = df.loc["huevo", "precio_frec"]  # base 36 en sep-2026; INPC de ese mes = 110 (último)
    assert abs(huevo.loc[pd.Timestamp("2026-01-05")] - 36 * 100 / 110) < 1e-9
    assert abs(huevo.iloc[-1] - 36.0) < 1e-9


def test_calibracion_conformal_no_usa_el_futuro():
    from insumos.modelo import calibrar

    cortes = pd.to_datetime(["2026-01-05"] * 60 + ["2026-04-06"] * 60)
    bt = pd.DataFrame({"h": 4, "corte": cortes, "precio_obj": 100.0,
                       "modelo_p10": 95.0, "modelo_p90": 99.0})  # el real siempre queda fuera (arriba)
    c = calibrar(bt)
    assert (c.loc[c["corte"] == cortes[0], "ajuste_intervalo"] == 0).all()  # primer bloque: sin historia
    assert (c.loc[c["corte"] == cortes[-1], "modelo_p90"] >= 100).all()     # segundo: se ensancha


def test_mercado_estado():
    from insumos.ingest.sniim_estados import _mercado_estado

    assert _mercado_estado("DF: Central de Abasto de Iztapalapa DF")[1] == "Ciudad de México"
    assert _mercado_estado("Jalisco: Mercado de Abasto de Guadalajara")[1] == "Jalisco"


def test_llm_parsea_y_verifica_sin_confiar_en_el_llm():
    from insumos.menus_llm import parsear, verificar

    texto = 'Claro:\n[{"titulo": "Semana verde", "platillos": ["a", "b", "zzz"], "razon": "Baja el tomate."}]'
    props = parsear(texto)
    cat = {"a": {"tiempo": "plato_fuerte", "costo_proyectado": 20.0}, "b": {"tiempo": "bebida", "costo_proyectado": 2.0}}
    v = verificar(props, cat, {"plato_fuerte": 1, "bebida": 1}, presupuesto=10_000, comensales=50, merma=0.1)[0]
    assert v["ids_invalidos"] == ["zzz"] and v["composicion_ok"]
    assert abs(v["costo_semanal"] - 22 * 1.1 * 50 * 6) < 1e-6 and v["cabe"]
    v2 = verificar(props, cat, {"plato_fuerte": 1, "bebida": 1}, presupuesto=1_000, comensales=50, merma=0.1)[0]
    assert not v2["cabe"]


def test_llm_extrae_tool_use():
    from insumos.menus_llm import extraer

    resp = {"stop_reason": "tool_use", "content": [{"type": "tool_use", "name": "proponer_menus", "input": {
        "menus": [{"titulo": 'Semana "verde"', "platillos": ["a"], "razon": 'Con comillas "raras".'}]}}]}
    m = extraer(resp)
    assert m[0]["platillos"] == ["a"] and "comillas" in m[0]["razon"]
