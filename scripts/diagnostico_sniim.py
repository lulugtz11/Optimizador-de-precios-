"""Prueba combinaciones de parámetros contra SNIIM para ver cuál regresa datos.

    python scripts/diagnostico_sniim.py
"""
import requests
from bs4 import BeautifulSoup

BASE = "http://www.economia-sniim.gob.mx/NUEVO/Consultas/MercadosNacionales/PreciosDeMercado/Agricolas"
PRODUCTOS = [
    ("frutas_hortalizas", "/ResultadosConsultaFechaFrutasYHortalizas.aspx", "839", "Tomate Saladette"),
    ("granos", "/ResultadosConsultaFechaGranos.aspx", "3", "Arroz sin especificar"),
]
DESTINOS = [
    ("100", ""),
    ("100", "DF: Central de Abasto de Iztapalapa DF"),
    ("-1", "Todos"),
]
FECHAS = [("01/10/2026", "31/10/2026"), ("03/11/2026", "13/11/2026")]

s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0"
for cat, url, pid, nombre in PRODUCTOS:
    for dest_id, dest in DESTINOS:
        for precio_por in ("2", "1"):
            for ini, fin in FECHAS:
                p = {"fechaInicio": ini, "fechaFinal": fin, "ProductoId": pid, "OrigenId": "-1",
                     "Origen": "Todos", "DestinoId": dest_id, "Destino": dest,
                     "PreciosPorId": precio_por, "RegistrosPorPagina": "1000"}
                r = s.get(BASE + url, params=p, timeout=60)
                soup = BeautifulSoup(r.text, "lxml")
                t = soup.find("table", id="tblResultados")
                filas = t.find_all("tr") if t else []
                vacio = "NO HAY REGISTROS" in r.text
                print(f"{nombre:22} dest={dest_id:>3} '{dest[:12]:12}' precio={precio_por} {ini}-{fin} "
                      f"http={r.status_code} filas={len(filas):4} {'VACIO' if vacio else 'OK'}")
                if filas and not vacio:
                    for tr in filas[:4]:
                        print("    ", " | ".join(td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])))
