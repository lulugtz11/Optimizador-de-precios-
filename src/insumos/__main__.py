"""CLI del pipeline de datos.

    python -m insumos productos        # lista productos de SNIIM y revisa el match con el catálogo
    python -m insumos ingesta          # descarga SNIIM (bronze) + Banxico
    python -m insumos silver           # parsea bronze -> silver
    python -m insumos gold             # panel semanal (entrada del modelo)
    python -m insumos costeo           # costo por platillo + baselines de KPIs
    python -m insumos calidad          # reports/calidad_datos.md
    python -m insumos pecuarios        # solo pollo y res de SNIIM pecuarios (descarga + silver)
    python -m insumos inegi            # solo precios de proteínas/granos (INPC por genérico)
    python -m insumos fondas           # genera 4 fondas simuladas (necesita gold)
    python -m insumos estados          # precios en las ~49 centrales de 31 estados (desde 2022, ~1.5 h)
    python -m insumos modelo           # backtest + KPIs + pronóstico a 12 semanas (~3 min)
    python -m insumos dashboard        # genera app/dashboard.html (se abre con doble clic)
    python -m insumos menus_llm        # Claude propone menús; el código los verifica (ANTHROPIC_API_KEY)
    python -m insumos menus_nube       # lo mismo para fondas reales con cuenta (Supabase + ANTHROPIC_API_KEY)
    python -m insumos kpis_nube        # KPIs con fondas reales, agregados (reports/kpis_nube.md)
    python -m insumos todo             # todo lo anterior en orden
    python -m insumos demo             # corre gold/costeo/calidad con datos SIMULADOS (data_sample/)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from . import config


def _cargar_env() -> None:
    f = config.ROOT / ".env"
    if f.exists():
        for linea in f.read_text().splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                v = v.split(" #", 1)[0].strip().strip('"')  # quita comentarios al final de la línea
                if v:  # una línea vacía (p. ej. copiada de .env.example) no tapa a otra con valor
                    os.environ.setdefault(k.strip(), v)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="insumos", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("comando", choices=["productos", "ingesta", "silver", "gold", "costeo", "calidad", "fondas", "inegi", "pecuarios", "modelo", "estados", "dashboard", "menus_llm", "menus_nube", "kpis_nube", "todo", "demo"])
    p.add_argument("--desde", default=config.FECHA_INICIO_DEFAULT)
    p.add_argument("--hasta", default=None)
    p.add_argument("--solo", nargs="*", help="insumo_id a descargar (default: todos)")
    p.add_argument("--forzar", action="store_true", help="re-descargar meses ya guardados")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    log = logging.getLogger("insumos")
    _cargar_env()

    if a.comando == "demo":
        os.environ["INSUMOS_DATA_DIR"] = str(config.ROOT / "data_sample")
        from . import sample
        log.warning("MODO DEMO: datos SIMULADOS en data_sample/. No usar en entregas.")
        sample.generar()
        pasos = ["gold", "fondas", "costeo", "calidad"]
    elif a.comando == "todo":
        pasos = ["ingesta", "silver", "gold", "costeo", "calidad"]
    else:
        pasos = [a.comando]
    config.ensure_dirs()

    for paso in pasos:
        log.info("== %s ==", paso)
        if paso == "productos":
            from .ingest import sniim
            sniim.listar_productos()
            m = sniim.resolver_productos()
            print(m.to_string(index=False))
        elif paso == "ingesta":
            from .ingest import banxico, inegi, sniim, sniim_pecuarios
            sniim.descargar(a.desde, a.hasta, a.solo, a.forzar)
            for nombre, fn in (("SNIIM pecuarios", lambda: sniim_pecuarios.descargar(a.desde, a.forzar)),
                               ("Banxico", lambda: banxico.descargar(a.desde, a.hasta)),
                               ("INEGI", inegi.descargar)):
                try:
                    fn()
                except Exception as e:  # una fuente caída no detiene el pipeline
                    log.warning("%s omitido: %s", nombre, e)
        elif paso == "menus_llm":
            from . import menus_llm
            r = menus_llm.generar()
            for fid, props in r.items():
                for p in props:
                    print(f"{fid} · {p['titulo']}: {', '.join(p['platillos'])} · ${p['costo_semanal']:,.0f}/sem · "
                          f"{'CABE' if p['cabe'] else 'NO CABE'}{'' if p['composicion_ok'] else ' · composición incompleta'}")
        elif paso == "menus_nube":
            from . import menus_llm
            for nombre, r in menus_llm.generar_nube().items():
                print(f"{nombre}: {r['menus']} menús · {r['caben']} caben · "
                      f"{'personalizados con su historial' if r['personalizada'] else 'sin historial'}")
        elif paso == "kpis_nube":
            from . import kpis_nube
            k = kpis_nube.correr()
            print(f"Fondas activas: {k['fondas_activas']}")
            if "kpi2" in k:
                print(f"KPI 2 real: error medio {k['kpi2']['error_medio_pct']}% · "
                      f"dentro del rango {k['kpi2']['dentro_del_rango_pct']}%")
            if "kpi3" in k:
                print(f"KPI 3: {k['kpi3']['aceptados_pct']}% de menús aceptados")
        elif paso == "dashboard":
            from . import dashboard
            ruta = dashboard.construir()
            print("\nAbre en tu navegador:", ruta)
        elif paso == "estados":
            from .ingest import sniim_estados
            desde = a.desde if a.desde != config.FECHA_INICIO_DEFAULT else sniim_estados.DESDE_DEFAULT
            sniim_estados.descargar(desde, a.solo)
            sniim_estados.construir_silver()
        elif paso == "modelo":
            from . import modelo
            r = modelo.correr()
            k1 = r["kpi1"][r["kpi1"]["modelo"].isin(["modelo", "theta", "ingenuo", "cobertura_intervalo_80"])]
            print("\nKPI 1 (wRMSE %; cobertura en MAE_pct):\n", k1.round(2).to_string(index=False))
            print("\nKPI 2 (error % costo de menú):\n", r["kpi2"].round(2).to_string(index=False))
            log.info("Reporte en reports/modelo.md · pronósticos en gold/pronosticos.csv")
        elif paso == "pecuarios":
            from .ingest import sniim_pecuarios
            sniim_pecuarios.descargar(a.desde, a.forzar)
            sniim_pecuarios.construir_silver()
        elif paso == "inegi":
            from .ingest import inegi
            inegi.descargar()
        elif paso == "silver":
            from .ingest import sniim, sniim_pecuarios
            sniim.construir_silver()
            sniim_pecuarios.construir_silver()
        elif paso == "gold":
            from .build import semanal
            semanal.construir_panel()
        elif paso == "fondas":
            from . import fondas_sim
            fondas_sim.generar()
        elif paso == "costeo":
            from .build import costeo
            r = costeo.construir()
            print("\nKPI 1 baseline (wRMSE % del precio):\n", r["kpi1"].round(2).to_string(index=False))
            print("\nKPI 2 baseline (error % costo de menú, 'precio de un solo día'):\n",
                  r["kpi2"].round(2).to_string(index=False))
        elif paso == "calidad":
            from . import calidad
            calidad.reporte()
            log.info("Reporte en reports/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
