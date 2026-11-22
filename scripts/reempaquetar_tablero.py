"""Toma los datos de un tablero YA GENERADO (por ejemplo, el index.html publicado con una corrida
anterior) y los mete en la plantilla actual, para tener los resultados de esa corrida con las
funciones nuevas (aviso de privacidad, cuentas en la nube).

Uso (desde la carpeta del repo):
    python scripts/reempaquetar_tablero.py ..\\fondas-dashboard\\index.html app\\dashboard.html

- No vuelve a entrenar nada ni toca la carpeta data/: solo copia los datos del tablero viejo.
- Agrega la conexión a la nube si SUPABASE_URL y SUPABASE_ANON_KEY están en .env.
- Nunca incluye la llave secreta.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))


def main(origen: str, destino: str) -> None:
    from insumos.__main__ import _cargar_env
    _cargar_env()
    viejo = Path(origen).read_text(encoding="utf-8")
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', viejo, re.S)
    if not m:
        sys.exit(f"No encontré los datos dentro de {origen}. ¿Es un tablero generado por este proyecto?")
    data = json.loads(m.group(1))
    url, anon = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_ANON_KEY")
    data["nube"] = {"url": url, "anon": anon} if url and anon else None
    if anon and anon.startswith("sb_secret_"):
        sys.exit("SUPABASE_ANON_KEY tiene una llave SECRETA. Pon ahí la publishable (sb_publishable_...).")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    plantilla = (RAIZ / "app" / "plantilla_dashboard.html").read_text(encoding="utf-8")
    html = plantilla.replace("__DATA_JSON__", payload)
    if "sb_secret_" in html:
        sys.exit("Se detectó una llave secreta en el resultado; no se escribió nada.")
    Path(destino).write_text(html, encoding="utf-8")
    print(f"Listo: {destino} · semana {data.get('semana_actual')} · {len(data.get('fondas', []))} fondas · "
          f"nube {'activada' if data['nube'] else 'desactivada'}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
