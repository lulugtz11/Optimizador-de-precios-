"""Para los insumos que SNIIM no reporta en Iztapalapa, prueba otras variedades del mismo producto.

    python scripts/buscar_variedades.py
"""
import csv

import requests
from bs4 import BeautifulSoup

BASE = ("http://www.economia-sniim.gob.mx/NUEVO/Consultas/MercadosNacionales/PreciosDeMercado/"
        "Agricolas/ResultadosConsultaFechaFrutasYHortalizas.aspx")
BUSCAR = {"ajo": "ajo", "chayote": "chayote", "nopal": "nopal", "lechuga": "lechuga",
          "ejote": "ejote", "limon": "lim"}

prods = [p for p in csv.DictReader(open("data/bronze/sniim/productos_sniim.csv", encoding="utf-8"))
         if p["categoria"] == "frutas_hortalizas"]
s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0"
for insumo, clave in BUSCAR.items():
    print(f"== {insumo}")
    for p in prods:
        if not p["producto"].lower().startswith(clave):
            continue
        params = {"fechaInicio": "01/10/2026", "fechaFinal": "31/10/2026", "ProductoId": p["producto_id"],
                  "OrigenId": "-1", "Origen": "Todos", "DestinoId": "100", "Destino": "",
                  "PreciosPorId": "2", "RegistrosPorPagina": "1000"}
        r = s.get(BASE, params=params, timeout=60)
        t = BeautifulSoup(r.text, "lxml").find("table", id="tblResultados")
        filas = len(t.find_all("tr")) - 2 if t and "NO HAY REGISTROS" not in r.text else 0
        print(f"   {p['producto_id']:>5}  {p['producto']:<45} filas oct-2026: {max(filas, 0)}")
