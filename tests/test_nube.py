"""Pruebas de la parte en la nube sin conectarse a Supabase ni a Claude (todo simulado)."""
import pandas as pd
import pytest

from insumos import kpis_nube, menus_llm, nube

ANA, BETO = "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"
FONDAS = [{"id": ANA, "owner": "user-ana", "tipo": "comida_corrida", "comensales_dia": 60, "alcaldia": "Iztapalapa",
           "presupuesto_semanal": 9000, "merma": 0.12, "precio_comida": 90, "cancelada_en": None},
          {"id": BETO, "owner": "user-beto", "tipo": "antojitos", "comensales_dia": 40, "alcaldia": "Coyoacán",
           "presupuesto_semanal": 6000, "merma": 0.10, "precio_comida": 70, "cancelada_en": None}]
HIST = {ANA: [{"menu_titulo": "Semana de res", "platillos": ["propio_1"], "acepto": False, "motivo": "muy_caro"},
              {"menu_titulo": "Pollo y verduras", "platillos": ["propio_2"], "acepto": True, "motivo": None}],
        BETO: [{"menu_titulo": "Tacos de BETO", "platillos": [], "acepto": False, "motivo": "repetitivo"}]}
AUTORIZA = {ANA: True, BETO: False}
PLATILLOS = {ANA: [{"clave_local": "propio_1", "nombre": "Bistec en salsa", "tiempo": "plato_fuerte", "precio_venta": 95,
                    "ingredientes": [["res_bistec", 150], ["jitomate", 80]]},
                   {"clave_local": "propio_2", "nombre": "Pollo con verduras", "tiempo": "plato_fuerte", "precio_venta": 85,
                    "ingredientes": [["pollo_pierna_muslo", 180], ["calabacita", 80]]}],
             BETO: []}


def test_fondas_activas_descarta_owner(monkeypatch):
    monkeypatch.setattr(nube, "leer", lambda tabla, *a, **k: FONDAS)
    assert all("owner" not in f for f in nube.fondas_activas())


def test_bloque_historial_vacio_y_con_datos():
    assert menus_llm.bloque_historial([], []) == ""
    cat = [{"id": "propio_1", "nombre": "Bistec en salsa"}, {"id": "propio_2", "nombre": "Pollo con verduras"}]
    txt = menus_llm.bloque_historial(HIST[ANA], cat)
    assert "Bistec en salsa" in txt and "le pareció muy caro" in txt and "Aceptó" in txt


def test_cada_fonda_solo_usa_su_historial_y_solo_con_autorizacion(monkeypatch):
    prompts, guardadas = {}, {}
    monkeypatch.setattr(nube, "fondas_activas", lambda: [{k: v for k, v in f.items() if k != "owner"} for f in FONDAS])
    monkeypatch.setattr(nube, "platillos", lambda fid: PLATILLOS[fid])
    monkeypatch.setattr(nube, "autoriza_mejora", lambda fid: AUTORIZA[fid])
    monkeypatch.setattr(nube, "historial", lambda fid, n=30: HIST[fid])
    monkeypatch.setattr(nube, "guardar_propuestas", lambda fid, menus, h, personalizada: guardadas.__setitem__(fid, personalizada))
    precios = {i: 30.0 for i in ("res_bistec", "jitomate", "pollo_pierna_muslo", "calabacita")}
    monkeypatch.setattr(menus_llm, "_precios", lambda h: (precios, precios))
    monkeypatch.setattr(menus_llm, "_catalogo_nube", lambda pl: [
        {"id": p["clave_local"], "nombre": p["nombre"], "tiempo": p["tiempo"], "precio_venta": p["precio_venta"],
         "en_su_menu": True, "ing": [tuple(x) for x in p["ingredientes"]]} for p in pl])

    def falso_proponer(fonda, cat, comp, prompt, bitacora):
        prompts[fonda["fonda_id"]] = prompt
        return [{"titulo": "x", "platillos": [], "cabe": True}]
    monkeypatch.setattr(menus_llm, "_proponer", falso_proponer)

    menus_llm.generar_nube()
    # Ana autorizó: su prompt trae SU historial y nada de Beto
    assert "HISTORIAL DE ESTA FONDA" in prompts[ANA] and "Bistec en salsa" in prompts[ANA]
    assert "BETO" not in prompts[ANA]
    # Beto no autorizó: su prompt no trae historial de nadie
    assert "HISTORIAL" not in prompts[BETO] and "Semana de res" not in prompts[BETO]
    assert guardadas == {ANA: True, BETO: False}
    # Nunca llega al LLM el id de la fonda ni la liga con la cuenta
    for fid, p in prompts.items():
        assert fid not in p and "user-" not in p


def test_kpis_agregados_y_grupos_pequenos_ocultos():
    fondas = pd.DataFrame([{"id": f"f{i}", "tipo": "comida_corrida" if i < 3 else "antojitos"} for i in range(4)])
    gastos = pd.DataFrame([{"fonda_id": f"f{i}", "semana": "2026-11-16", "plan": 4000, "plan_min": 3600,
                            "plan_max": 4500, "real": 4200 if i % 2 else 5000} for i in range(4)])
    resp = pd.DataFrame([{"fonda_id": f"f{i}", "acepto": i != 1, "motivo": None if i != 1 else "muy_caro", "horizonte_sem": 8}
                         for i in range(4)])
    consent = pd.DataFrame([{"fonda_id": f"f{i}", "autoriza_mejora": i < 2} for i in range(4)])
    k = kpis_nube.calcular(fondas, gastos, resp, consent)
    assert k["fondas_activas"] == 4 and k["kpi2"]["semanas"] == 4
    assert k["kpi2"]["dentro_del_rango_pct"] == 50.0
    assert k["kpi3"]["aceptados_pct"] == 75.0
    # "antojitos" tiene 1 sola fonda: no se reporta por separado
    assert list(k["kpi2_por_tipo"].index) == ["comida_corrida"]
    assert k["autorizan_pct"] == 50.0
    md = kpis_nube._md(k)
    assert "f0" not in md and "f1" not in md


def test_sin_configuracion_da_error_claro(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert not nube.configurada()
    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_KEY"):
        nube.leer("fondas")


def test_limpia_bitacoras_al_retirar_autorizacion_o_cancelar(tmp_path):
    ana, beto, cancelada = ANA, BETO, "cccccccc-0000-0000-0000-000000000003"
    (tmp_path / f"{ana}_20261120_100000.json").write_text('{"prompt": "HISTORIAL DE ESTA FONDA ..."}', encoding="utf-8")
    (tmp_path / f"{beto}_20261120_100000.json").write_text('{"prompt": "HISTORIAL DE ESTA FONDA ..."}', encoding="utf-8")
    (tmp_path / f"{beto}_20261121_100000.json").write_text('{"prompt": "sin historial"}', encoding="utf-8")
    (tmp_path / f"{cancelada}_20261120_100000.json").write_text('{"prompt": "lo que sea"}', encoding="utf-8")
    (tmp_path / "f1_20261120_100000.json").write_text('{"prompt": "fonda simulada"}', encoding="utf-8")
    n = menus_llm.limpiar_bitacoras(tmp_path, activas={ana, beto}, autorizan={ana})
    quedan = sorted(p.name for p in tmp_path.iterdir())
    assert n == 2
    # Ana autoriza: se queda; Beto retiró: se borra la que tenía historial; la cancelada se borra; la simulada no se toca
    assert quedan == sorted([f"{ana}_20261120_100000.json", f"{beto}_20261121_100000.json", "f1_20261120_100000.json"])
