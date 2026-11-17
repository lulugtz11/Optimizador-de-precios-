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
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="insumos", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("comando", choices=["productos", "ingesta", "silver", "gold", "costeo", "calidad", "fondas", "inegi", "pecuarios", "todo", "demo"])
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
