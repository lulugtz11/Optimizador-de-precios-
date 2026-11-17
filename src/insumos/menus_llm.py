"""Recomendador de menús con Claude: prompting + recuperación de recetas (plan B del E3/E4).

    python -m insumos menus_llm          # necesita ANTHROPIC_API_KEY en .env

Flujo (lo que prometimos en la E2):
1. RECUPERACIÓN: para cada fonda se juntan sus platillos y los del recetario que le quedan
   a su tipo de cocina; se priorizan los que usan insumos que van a BAJAR de precio.
2. CÓDIGO: se calcula el costo proyectado de cada platillo con el pronóstico (el LLM no hace cuentas).
3. LLM: Claude propone 3 menús semanales en JSON (solo ids de platillos + razón).
4. VERIFICACIÓN: el código recalcula el costo semanal y marca cuáles caben en el presupuesto.
   El dashboard vuelve a verificar con el horizonte y presupuesto que elija el usuario.

Al LLM nunca le llegan datos personales: solo el perfil (tipo, tamaño, alcaldía), los platillos y costos.
Salida: data/gold/menus_llm.json  ·  bitácora de prompts/respuestas en data/bronze/llm/
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime

import pandas as pd
import requests

from . import config

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
MODELO_DEFAULT = "claude-sonnet-5-5"
HORIZONTE = 8           # semanas: el menú se planea a 2 meses
DIAS = 6
COMPOSICION = {"comida_corrida": {"plato_fuerte": 3, "entrada": 2, "bebida": 1, "guarnicion": 1},
               "antojitos": {"plato_fuerte": 3, "entrada": 0, "bebida": 1, "guarnicion": 1},
               "desayunos": {"plato_fuerte": 3, "entrada": 0, "bebida": 1, "guarnicion": 1}}


def _precios(h: int) -> tuple[dict, dict]:
    panel = pd.read_csv(config.gold() / "panel_semanal.csv", parse_dates=["semana"])
    hoy = panel.dropna(subset=["precio_kg_imp"]).sort_values("semana").groupby("insumo_id")["precio_kg_imp"].last()
    p_h = hoy.copy()
    f = config.gold() / "pronosticos.csv"
    if f.exists():
        pr = pd.read_csv(f)
        pr = pr[pr["h"] == h].set_index("insumo_id")["p50"]
        p_h.update(pr)
    return hoy.to_dict(), p_h.to_dict()


def _catalogo(fonda: pd.Series, recetas_f: pd.DataFrame, menus_f: pd.DataFrame) -> list[dict]:
    base_rec = pd.read_csv(config.PLANTILLAS_DIR / "recetas_ejemplo.csv")
    base_men = pd.read_csv(config.PLANTILLAS_DIR / "menus_ejemplo.csv").set_index("platillo_id")
    out = []
    for pid, d in recetas_f.groupby("platillo_id"):
        m = menus_f[menus_f["platillo_id"] == pid].iloc[0]
        out.append({"id": pid, "nombre": d["platillo"].iloc[0], "tiempo": m["tiempo"], "precio_venta": float(m["precio_venta"]),
                    "ing": list(zip(d["insumo_id"], d["gramos_por_porcion"])), "en_su_menu": True})
    ya = {p["id"] for p in out}
    for pid, d in base_rec.groupby("platillo_id"):  # recuperación del recetario
        if pid in ya or pid not in base_men.index:
            continue
        out.append({"id": pid, "nombre": d["platillo"].iloc[0], "tiempo": base_men.loc[pid, "tiempo"],
                    "precio_venta": float(base_men.loc[pid, "precio_venta"]),
                    "ing": list(zip(d["insumo_id"], d["gramos_por_porcion"])), "en_su_menu": False})
    return out


def _costear(cat: list[dict], hoy: dict, p_h: dict) -> list[dict]:
    for p in cat:
        if all(i in p_h for i, _ in p["ing"]):
            p["costo_hoy"] = round(sum(g / 1000 * hoy[i] for i, g in p["ing"]), 2)
            p["costo_proyectado"] = round(sum(g / 1000 * p_h[i] for i, g in p["ing"]), 2)
        else:
            p["costo_proyectado"] = None
    return [p for p in cat if p["costo_proyectado"] is not None]


def costo_semanal(ids: list[str], cat: dict, comensales: int, merma: float) -> float:
    """Igual que el dashboard: cada tiempo se reparte parejo entre sus opciones."""
    por_t: dict[str, list[float]] = {}
    for pid in ids:
        p = cat[pid]
        por_t.setdefault(p["tiempo"], []).append(p["costo_proyectado"])
    por_comida = sum(sum(v) / len(v) for v in por_t.values())
    return por_comida * (1 + merma) * comensales * DIAS


def _prompt(fonda: pd.Series, cat: list[dict], hoy: dict, p_h: dict, comp: dict) -> str:
    usados = {i for p in cat for i, _ in p["ing"]}
    cambios = sorted(((i, 100 * (p_h[i] / hoy[i] - 1)) for i in usados if i in hoy and hoy[i]), key=lambda x: x[1])
    baja = ", ".join(f"{i} ({c:+.0f}%)" for i, c in cambios[:5] if c < -3) or "ninguno"
    sube = ", ".join(f"{i} ({c:+.0f}%)" for i, c in reversed(cambios[-5:]) if c > 3) or "ninguno"
    lineas = [f'- {p["id"]} | {p["nombre"]} | {p["tiempo"]} | costo insumos/porción en {HORIZONTE} sem: ${p["costo_proyectado"]} '
              f'| precio venta ref: ${p["precio_venta"]:.0f} | {"YA LO VENDE" if p["en_su_menu"] else "del recetario"} '
              f'| ingredientes: {", ".join(i for i, _ in p["ing"])}' for p in cat]
    composicion = ", ".join(f"{n} {t}" for t, n in comp.items() if n)
    return f"""Eres asesor de una fonda en la Ciudad de México que va empezando. Propón menús semanales rentables.

PERFIL (anónimo): tipo {fonda['tipo'].replace('_', ' ')}, ~{fonda['comensales_dia']} comensales al día, alcaldía {fonda['alcaldia']},
presupuesto semanal de insumos ${fonda['presupuesto_semanal']:.0f}. El menú es para dentro de {HORIZONTE} semanas.

PRONÓSTICO DE PRECIOS (Central de Abasto, a {HORIZONTE} semanas):
- Bajan: {baja}
- Suben: {sube}

PLATILLOS DISPONIBLES (id | nombre | tiempo | costo | precio | origen | ingredientes):
{chr(10).join(lineas)}

REGLAS:
- Cada menú lleva exactamente: {composicion} (usa los ids tal cual).
- Prefiere platillos que la fonda ya vende; puedes sugerir 1 del recetario si conviene por precio.
- Aprovecha insumos que bajan, evita depender de los que suben, y da variedad de proteína.
- NO calcules totales ni costos semanales: el sistema los calcula y descarta lo que no cabe.
- Usa la herramienta proponer_menus para responder con exactamente 3 menús: "titulo" (máx 5 palabras),
  "platillos" (lista de ids) y "razon" (1-2 frases cortas en español sencillo para el fondero).
"""


HERRAMIENTA = {
    "name": "proponer_menus",
    "description": "Entrega las propuestas de menú semanal para la fonda.",
    "input_schema": {
        "type": "object",
        "properties": {"menus": {"type": "array", "minItems": 1, "maxItems": 5, "items": {
            "type": "object",
            "properties": {"titulo": {"type": "string"}, "platillos": {"type": "array", "items": {"type": "string"}},
                           "razon": {"type": "string"}},
            "required": ["titulo", "platillos", "razon"]}}},
        "required": ["menus"]},
}


def _llamar(prompt: str) -> dict:
    """Llama a Claude con tool use forzado: la respuesta llega como JSON válido según el esquema."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("Falta ANTHROPIC_API_KEY en .env")
    r = requests.post(API_URL, timeout=180, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": os.environ.get("ANTHROPIC_MODEL", MODELO_DEFAULT), "max_tokens": 4000,
              "tools": [HERRAMIENTA], "tool_choice": {"type": "tool", "name": "proponer_menus"},
              "messages": [{"role": "user", "content": prompt}]})
    if r.status_code != 200:
        raise RuntimeError(f"API de Claude respondió {r.status_code}: {r.text[:300]}")
    return r.json()


def extraer(resp: dict) -> list[dict]:
    for b in resp.get("content", []):
        if b.get("type") == "tool_use" and isinstance(b.get("input"), dict):
            return _limpiar(b["input"].get("menus", []))
    texto = "".join(b.get("text", "") for b in resp.get("content", []))
    return parsear(texto)  # respaldo por si el modelo respondió en texto


def parsear(texto: str) -> list[dict]:
    m = re.search(r"\[.*\]", texto, flags=re.S)
    if not m:
        raise ValueError("La respuesta no trae una lista JSON")
    return _limpiar(json.loads(m.group(0)))


def _limpiar(datos: list) -> list[dict]:
    return [{"titulo": str(d.get("titulo", ""))[:60], "platillos": [str(x) for x in d.get("platillos", [])],
             "razon": str(d.get("razon", ""))[:400]} for d in datos if isinstance(d, dict)]


def verificar(props: list[dict], cat: dict, comp: dict, presupuesto: float, comensales: int, merma: float) -> list[dict]:
    out = []
    for p in props:
        ids = [i for i in p["platillos"] if i in cat]
        conteo = {t: sum(cat[i]["tiempo"] == t for i in ids) for t in comp}
        semanal = costo_semanal(ids, cat, comensales, merma) if ids else float("inf")
        out.append({**p, "platillos": ids, "ids_invalidos": [i for i in p["platillos"] if i not in cat],
                    "composicion_ok": all(conteo[t] == n for t, n in comp.items()),
                    "costo_semanal": round(semanal, 2), "cabe": semanal <= presupuesto})
    return out


def generar() -> dict:
    f = config.fondas()
    fondas, menus, recetas = (pd.read_csv(f / x) for x in ("fondas.csv", "menus.csv", "recetas.csv"))
    hoy, p_h = _precios(HORIZONTE)
    bitacora = config.bronze() / "llm"
    bitacora.mkdir(parents=True, exist_ok=True)
    salida = {}
    for _, fonda in fondas.iterrows():
        cat = _costear(_catalogo(fonda, recetas[recetas["fonda_id"] == fonda["fonda_id"]],
                                 menus[menus["fonda_id"] == fonda["fonda_id"]]), hoy, p_h)
        comp = COMPOSICION.get(fonda["tipo"], COMPOSICION["comida_corrida"])
        comp = {t: min(n, sum(p["tiempo"] == t for p in cat)) for t, n in comp.items()}
        prompt = _prompt(fonda, cat, hoy, p_h, comp)
        crudos = None
        for intento in (1, 2):
            try:
                resp = _llamar(prompt)
                sello = datetime.now().strftime("%Y%m%d_%H%M%S")
                (bitacora / f"{fonda['fonda_id']}_{sello}.json").write_text(json.dumps(
                    {"prompt": prompt, "stop_reason": resp.get("stop_reason"), "usage": resp.get("usage"),
                     "respuesta": resp.get("content")}, ensure_ascii=False, indent=1), encoding="utf-8")
                crudos = extraer(resp)
                break
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                log.warning("%s: respuesta inválida (intento %d): %s", fonda["fonda_id"], intento, e)
        if not crudos:
            log.error("%s: sin menús de Claude; se omite esta fonda", fonda["fonda_id"])
            continue
        props = verificar(crudos, {p["id"]: p for p in cat}, comp, float(fonda["presupuesto_semanal"]),
                          int(fonda["comensales_dia"]), float(fonda["merma"]))
        salida[fonda["fonda_id"]] = props
        log.info("%s: %d menús de Claude · %d caben en el presupuesto", fonda["nombre"], len(props),
                 sum(p["cabe"] for p in props))
    (config.gold() / "menus_llm.json").write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
    return salida
