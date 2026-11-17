# Insumos para fondas — Capstone CDA I (ITAM, otoño 2026)

Pronóstico de precios de insumos de la Central de Abasto (CDMX) y costeo de menús para
fondas que van empezando. Pipeline completo: **datos → modelo → recomendador → dashboard**.

## Todo en 5 comandos

```bash
python -m insumos todo --desde 2020-01-01   # datos (SNIIM, pecuarios, Banxico) + panel + costeo
python -m insumos fondas                     # 4 fondas simuladas valuadas a precio real
python -m insumos modelo                     # backtest, KPIs y pronóstico a 12 semanas (~3 min)
python -m insumos menus_llm                  # Claude propone menús; el código los verifica (opcional)
python -m insumos dashboard                  # app/dashboard.html → ábrelo con doble clic
```

## Modelo

Gradient boosting global (todos los insumos) con regresión por cuantiles (10/50/90) y
calibración conformal del intervalo. Objetivo: cambio en log-precio a h semanas. Backtest con
origen móvil (18 meses, reentreno trimestral) contra ingenuo, ingenuo estacional, media móvil y
el método **Theta** (ganador de la competencia M3). Resultados en `reports/modelo.md`.

## Recomendador de menús

1. Recuperación: platillos de la fonda + recetario de su tipo de cocina.
2. El **código** costea cada platillo con el pronóstico (el LLM no hace cuentas).
3. **Claude** propone 3 menús en JSON (ids + razón), con prompting sobre ese contexto.
4. El **código** recalcula el costo semanal y descarta lo que no cabe en el presupuesto.
El dashboard además trae un optimizador que prueba todas las combinaciones en vivo.

## Dashboard (`app/dashboard.html`)

Un solo archivo, sin servidor: Mi fonda (alertas de qué sube/baja), Precios y pronóstico,
Mis platillos (costeo, margen y desglose; captura de platillos propios), Armar menú (presupuesto →
menús que caben; aceptar/rechazar = KPI 3), Compras y gasto (lista para la Central y registro del
gasto real = KPI 2) y El modelo (KPIs vs baselines, sesgo por grupo, importancia de variables).
Lo que captura el usuario se guarda en su navegador y se exporta a CSV.

```
fuentes públicas ──► bronze (crudo, tal cual llegó)
                     ──► silver (limpio, homologado, precio por kg diario)
                         ──► gold (panel semanal + costeo + baselines de KPIs)
                             ──► modelo (pendiente) ──► dashboard (pendiente)
```

## Arranque rápido

```bash
git clone <url-del-repo> && cd fondas-insumos
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # pega tu token de Banxico (ver abajo)

python -m insumos demo        # prueba TODO con datos simulados (no necesita internet)
pytest                        # 11 pruebas
```

### Datos reales

```bash
python -m insumos productos   # 1) baja la lista de productos de SNIIM y la empata con el catálogo
                              #    revisa data/bronze/sniim/match_catalogo.csv
python -m insumos todo        # 2) descarga + silver + gold + costeo + reporte de calidad
```

La primera descarga desde 2020 tarda (≈25 insumos × ~83 meses, con pausa entre peticiones
para no saturar al servidor). Es incremental: los meses cerrados ya descargados no se vuelven
a pedir. Para probar rápido: `python -m insumos ingesta --solo jitomate cebolla --desde 2025-01-01`.

**Token de Banxico (gratis, 1 minuto):** https://www.banxico.org.mx/SieAPIRest/service/v1/token
→ ponlo en `.env` como `BANXICO_TOKEN=...`. Sin token el pipeline corre igual, solo sin variables macro.

## Fuentes

| Fuente | Qué | Cómo entra | Estado |
|---|---|---|---|
| SNIIM (Secretaría de Economía) | Precio diario por kg en la Central de Abasto de Iztapalapa: frutas, hortalizas, granos, aceites | scraper (`ingest/sniim.py`) | listo |
| Banxico SIE | Tipo de cambio FIX (diario), INPC (mensual) | API (`ingest/banxico.py`) | listo |
| Proteínas, lácteos, tortilla | Pollo, res, cerdo, huevo, queso, crema, tortilla | CSV manual `data/external/precios_manual.csv` | **falta llenar** |
| Diésel (CNE) | Precio por litro | CSV manual `data/external/diesel.csv` | opcional |
| Fondas | Recetas, precios de venta, tickets | CSV en `data/fondas/` | **falta conseguir** |
| Clima / sequía / SIAP / delitos | Variables explicativas extra | — | siguiente iteración, si mejoran el pronóstico |

SNIIM no tiene API: el scraper consulta las páginas `ResultadosConsultaFecha*.aspx` y parsea
la tabla `tblResultados`. Si SNIIM cambia el HTML, el parser ubica columnas por encabezado y
hay pruebas con HTML de ejemplo en `tests/fixtures/`.

## Datos de las fondas

**Por ahora son simuladas:** `python -m insumos fondas` (después de `gold`) genera 4 fondas
(comida corrida ×2, antojitos, desayunos) con menús, recetas, ventas diarias y tickets.
Las fondas son inventadas, pero sus compras se valúan con el **precio real** de la Central de
Abasto de esa semana (± regateo). Semilla fija: todo el equipo obtiene lo mismo.

Para usar datos reales en lugar de los simulados:

- `recetas.csv` — `platillo_id, platillo, insumo_id, gramos_por_porcion` (el `insumo_id` debe existir en `config/insumos.csv`)
- `menus.csv` — `platillo_id, tiempo, precio_venta`
- `tickets.csv` — ver `plantillas/tickets_template.csv` (sin RFC, nombres ni direcciones: privacidad por diseño, E2)

Mientras no existan, el costeo usa `plantillas/*_ejemplo.csv` y lo avisa en el log.
**Las recetas de ejemplo son aproximaciones nuestras, no datos de fondas.**

## Salidas (`data/gold/`)

| Archivo | Para qué |
|---|---|
| `panel_semanal.csv` | Entrada del modelo: semana × insumo, precio/kg, huecos explícitos, macro, calendario (Cuaresma, Semana Santa, fiestas patrias, Muertos, Guadalupe-Reyes) |
| `costo_platillo_semanal.csv` | Costo por porción, margen y food cost de cada platillo cada semana |
| `kpi1_baseline.csv` | wRMSE del pronóstico ingenuo y estacional (lo que el modelo tiene que vencer) |
| `kpi2_baseline.csv` | Error % del costo del menú con "el precio de hoy" (práctica actual del fondero) |
| `reports/calidad_datos.md` | Completitud, unicidad y vigencia por fuente (los 5 datos críticos de la E2) |

## Estructura

```
config/insumos.csv        catálogo maestro: insumo ↔ producto SNIIM, rango de precio plausible
plantillas/               recetas/menús de ejemplo y template de tickets
src/insumos/
  ingest/                 sniim.py, banxico.py, manual.py
  build/                  semanal.py (gold), costeo.py (costos + baselines)
  features/calendario.py  temporadas mexicanas
  calidad.py              reporte de calidad
  sample.py               datos simulados para `demo`
data/                     (no se sube a git) bronze/ silver/ gold/ fondas/ external/
data_sample/              (no se sube a git) salida de `demo`, SIMULADA
```

## Siguientes pasos

1. **Modelo de pronóstico** (`src/insumos/model/`): modelo global de gradient boosting sobre todos los
   insumos (rezagos, medias móviles, volatilidad, macro con rezago de publicación, calendario),
   regresión por cuantiles para intervalos de confianza, backtesting con origen móvil y comparación
   contra ingenuo / ingenuo estacional / SARIMAX con el wRMSE del KPI 1.
2. **Recomendador**: el costeo lo hace el código; el LLM solo propone combinaciones y se descartan
   las que no caben en el presupuesto.
3. **Dashboard** (`app/`): HTML que lee los JSON exportados de gold y del modelo.

## Uso de IA generativa

Pipeline construido con apoyo de Claude (Anthropic). Todo análisis que se presente debe
verificarse por el equipo (reglas de operación del Capstone): declararlo en el anexo.

## INEGI (proteínas, granos, lácteos, tortilla)

Precio por kg reconstruido = `precio_base_kg × INPC_genérico(t) / INPC_genérico(fecha_base)`.
Llena `config/inegi_series.csv`:
- `indicador_id`: en el BIE del INEGI (Precios e inflación → INPC → por objeto del gasto → genérico,
  nacional, quincenal) abre la serie del genérico y copia su clave numérica.
- `precio_base_kg` y `fecha_base`: un precio por kg conocido y su fecha (ticket de la Central, Profeco).
Después: `python -m insumos inegi && python -m insumos gold`.
