"""Prueba si las páginas de precios pecuarios de SNIIM (pollo, res, huevo) siguen vivas.

    python scripts/diagnostico_pecuarios.py

Guarda cada respuesta en data/bronze/sniim_pecuarios_diag/ para revisarla.
"""
import re
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

OUT = Path("data/bronze/sniim_pecuarios_diag")
OUT.mkdir(parents=True, exist_ok=True)
hoy = date.today()
d = hoy - timedelta(days=7)

PAGINAS = {
    "bovino_cortes": "http://www.economia-sniim.gob.mx/SNIIM-Pecuarios-Nacionales/e_Cor.asp",
    "pollo_partes": "http://www.economia-sniim.gob.mx/SNIIM-Pecuarios-Nacionales/e_Pza.asp",
}
PARAMS = {"origen": "0", "destino": "0", "del": str(d.day), "al": str(hoy.day),
          "mes": f"{hoy.month:02d}", "anio": str(hoy.year), "RegPag": "1000", "x": "45", "y": "20", "Var": "Bov"}

s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0"

# 1) páginas conocidas del scraper de México Abierto
for nombre, url in PAGINAS.items():
    try:
        r = s.get(url, params=PARAMS, timeout=60)
        r.encoding = r.apparent_encoding
        (OUT / f"{nombre}.html").write_text(r.text, encoding="utf-8")
        texto = BeautifulSoup(r.text, "lxml").get_text(" ", strip=True)
        precios = re.findall(r"\$?\d{2,3}\.\d{2}", texto)
        print(f"== {nombre}: http={r.status_code} bytes={len(r.text)} numeros_tipo_precio={len(precios)}")
        print("   ", texto[:400])
    except Exception as e:
        print(f"== {nombre}: ERROR {e}")

# 2) buscar en el sitio links a pecuarios / pollo / huevo / carne
for url in ("http://www.economia-sniim.gob.mx/NUEVO/Home.aspx", "http://www.economia-sniim.gob.mx/mapa.asp"):
    try:
        r = s.get(url, timeout=60)
        r.encoding = r.apparent_encoding
        links = {(a.get_text(" ", strip=True), a.get("href")) for a in BeautifulSoup(r.text, "lxml").find_all("a")}
        hits = [l for l in links if l[1] and re.search(r"pecuar|pollo|huevo|bovin|carne|aves", (l[0] + l[1]).lower())]
        print(f"== links en {url} ({len(hits)})")
        for t, h in sorted(hits)[:40]:
            print(f"    {t[:50]:<50} {h}")
    except Exception as e:
        print(f"== {url}: ERROR {e}")
