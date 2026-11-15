<p align="center">
  <img src="assets/branding/logo.png" alt="VortesInsight" width="420">
</p>

# VortesInsight — ¿Qué tan competitivas son las compras del Estado de Chile?

Pipeline de datos en Python + dashboard en Power BI sobre **las órdenes de compra
del Estado de Chile 2019–2025** (datos abiertos de ChileCompra / Mercado Público):
12,8 millones de órdenes y ~60 GB de CSV crudo, convertidos en un modelo limpio,
deflactado y auditable.

> Guía completa de modelado, medidas DAX y visualización disponible en [dashboard/POWERBI.md](dashboard/POWERBI.md).

## La pregunta de negocio

> **¿Cuánto compra el Estado sin competencia (trato directo) frente a licitaciones y
> convenios marco, dónde se concentra, cómo ha evolucionado, y qué tan concentrado
> está el gasto en pocos proveedores?**

La ley de compras públicas (Ley 19.886) establece la licitación como regla y el
trato directo como excepción justificada. Medir cuánto se usa la excepción, en qué
regiones y con qué causales es una pregunta relevante para auditoría, control de
gestión y para cualquier proveedor que quiera venderle al Estado.

## Insights

Montos en pesos reales de diciembre 2025. Base: 12,8 millones de órdenes de compra 2019–2025.

### 1. La pandemia duplicó el trato directo, y recién en 2025 volvió bajo el nivel previo

| | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|
| % del monto por trato directo | 16,9% | **28,3%** | 25,6% | 23,1% | 27,5% | 20,3% | **11,5%** |
| Trato directo por "emergencia" (miles de MM $) | 375 | **1.852** | 1.486 | 1.061 | 1.216 | 918 | 689 |

En 2020 las compras sin licitación por la causal *emergencia, urgencia o imprevisto*
se multiplicaron por **4,9**, y el trato directo se mantuvo sobre 23% del gasto por
cuatro años. "Emergencia" es la causal más usada del período: **38% de todo el monto
en trato directo**. La caída a 11,5% en 2025 coincide con la entrada en vigencia de
la Ley 21.634, que modernizó la ley de compras (diciembre 2024).

### 2. La Compra Ágil cambió *cómo* compra el Estado, no *cuánto*

La Compra Ágil (compras pequeñas con cotización simplificada, desde 2020) pasó de
0% a **42% de las órdenes de compra** en 2025, pero representa solo el **6,2% del
monto**. Al mismo tiempo, el convenio marco cayó de **22,6% a 4,8%** del monto: las
compras chicas migraron desde los catálogos de convenio marco a la Compra Ágil.
De paso, el "apuro de diciembre" se diluyó: diciembre concentraba el 12,7% del
gasto anual en 2019 y el 7,5% en 2025 (un mes neutro pesaría 8,3%).

### 3. Combustibles: un mercado que se concentró en un solo proveedor

En el rubro combustibles la cuota del mayor proveedor (Copec) subió de **28% en 2019
a 75% en 2023**, y el índice de concentración HHI pasó de 1.535 a **5.638**: muy por
sobre el umbral de "alta concentración" (2.500) de las guías de libre competencia.
En 2025 sigue en 2.158. Contraste: medicamentos, el rubro de mayor gasto
($10,4 billones), se mantiene competitivo todo el período (HHI ≈ 200–275, ningún
proveedor sobre 7%).

> Ojo con los rubros: la clasificación viene del comprador y tiene ruido. Por
> ejemplo, las tarjetas de alimentación de JUNAEB (Sodexo, Edenred) aparecen como
> "artículos de electrónica" y "productos de papel". Por eso el insight 3 se apoya
> en un rubro bien definido y en su evolución, no en el ranking de HHI.

Las cifras completas, regeneradas por el pipeline, están en
[reports/insights.md](reports/insights.md).

## Datos

| | |
|---|---|
| **Fuente** | [Datos abiertos ChileCompra](https://datos-abiertos.chilecompra.cl/) — descarga masiva de Órdenes de Compra |
| **URL de descarga** | `https://transparenciachc.blob.core.windows.net/oc-da/{año}-{mes}.zip` |
| **Período** | enero 2019 – diciembre 2025 (84 archivos mensuales) |
| **Volumen** | ~100 MB comprimido / ~700 MB CSV por mes, 80 columnas, una fila por **ítem** |
| **Actualización** | mensual (el rango es configurable en `scripts/config.py`) |
| **Dato auxiliar** | IPC mensual del INE (vía API de [mindicador.cl](https://mindicador.cl)), cacheado en `data/reference/` |

## Cómo correr el pipeline

Requisitos: Python 3.11+ y ~10 GB libres en disco.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt

python scripts/01_download.py     # descarga los zips mensuales (idempotente, en paralelo)
python scripts/02_clean.py        # limpia mes a mes -> data/interim/
python scripts/03_build_model.py  # une, deflacta y arma el modelo -> data/processed/
python scripts/04_insights.py     # cifras de los insights -> reports/insights.md
```

Para probar rápido con pocos meses: `python scripts/01_download.py --desde 2024-11 --hasta 2025-01`.

### Qué hace cada paso

```
data/raw/oc/*.zip ──02_clean──▶ data/interim/oc_YYYY-MM.parquet ──03_build_model──▶ data/processed/
 (80 columnas,                    (1 fila por OC, tipos,             fact_oc.parquet          (12,8 M OC, detalle)
  1 fila por ítem)                 modalidad, región, flags)         pbi_hechos_mensual.parquet (683 mil filas, p/ Power BI)
                                                                     dim_*.csv, agg_*.csv, outliers_revision.csv
```

## Decisiones de limpieza

Cada decisión está documentada en el código (`# DECISIÓN:`) y su impacto mes a mes
queda en [data/processed/cleaning_log.csv](data/processed/cleaning_log.csv).

| Problema en el crudo | Decisión | Por qué |
|---|---|---|
| **Una fila por ítem**, con el monto total de la OC repetido en cada fila | Separar cabecera (1 fila/OC) e ítems | Sumar fila a fila **duplica con creces** el gasto (mar-2024: $2,7 vs $1,2 billones) |
| Modalidad repartida en 3 columnas que se solapan | Una sola columna `modalidad`; Compra Ágil se evalúa primero | Toda Compra Ágil viene marcada también como trato directo: sin esto el % de trato directo pasa de ~14% a ~51% |
| Estados "Enviada", "En proceso", "Cancelación solicitada" | Se descartan | Aún pueden anularse; no son gasto efectivo |
| Montos en UF, USD, UTM, EUR (~1-5% de las OC) | Se usa el monto en pesos que entrega ChileCompra | Se verificó que el tipo de cambio implícito es coherente |
| **Moneda mal codificada**: $233 MM marcados como UTM → $15,7 **billones** | Regla automática: OC no-CLP > $1 billón se reinterpreta en pesos | Ninguna OC real supera $1 billón (el Estado gasta ~$1,3 billones *al mes*) |
| **Moneda dudosa**: OC en UF/UTM/USD que además son outlier estadístico (153 OC, $5,4 billones) | Se excluyen del análisis | La mayoría son errores evidentes: un municipio de ~10 mil habitantes con una OC de $189 mil MM, OC a personas naturales por $80 mil MM |
| Casos ambiguos (p.ej. concesión de basura a 72 meses en UTM, contrato de IDEMIA en USD) | Revisión manual registrada en [correcciones_manuales.csv](data/reference/correcciones_manuales.csv) con acción `mantener`, `excluir` o `monto_como_clp` | Una regla no distingue un contrato real grande de un error de moneda; la descripción de la OC sí |
| Montos < $1.000 y Compras Ágiles sobre el tope legal | Se marcan y quedan fuera del análisis | Son OC de prueba o errores de registro |
| OC muy grandes (CENABAST, JUNAEB, hospitales) | Se **marcan** como outlier estadístico (z robusto > 5 sobre log-monto, por modalidad) pero **se mantienen** | Son reales; borrarlas escondería justamente el gasto más relevante |
| Nulos en región (~1%), sector (~0,6%), rubro (~16% de ítems) | Categoría explícita "Sin región / Sin sector / Sin clasificar" | Descartarlas quita gasto real; imputarlas inventa datos |
| Nombres de proveedor inconsistentes | Llave = RUT normalizado | La razón social cambia entre OC; el RUT no |
| Inflación (IPC acumulado 2019–2025 > 40%) | Montos deflactados a pesos del último mes | Sin esto, toda tendencia muestra "más gasto" que en parte es solo inflación |
| OC con varios rubros | El monto de la OC se reparte por el peso neto de cada línea | La suma por rubro cuadra exactamente con el total de la OC |

## Dashboard

Power BI Desktop. El modelo, las medidas DAX y el layout están especificados en
[dashboard/POWERBI.md](dashboard/POWERBI.md); el tema de colores en
[dashboard/vortesinsight-theme.json](dashboard/vortesinsight-theme.json).

- **KPIs**: monto real, N° de OC, % trato directo, ticket promedio.
- **Tendencia**: gasto mensual por modalidad; % de trato directo mensual.
- **Comparación**: % de trato directo por región (norte → sur) contra el promedio nacional.
- **Filtros**: año, región, modalidad, sector.
- **Página 2**: concentración de proveedores por rubro (índice HHI) y top 10 proveedores.

> Estructura visual de dos páginas documentada en detalle en [dashboard/POWERBI.md](dashboard/POWERBI.md).

## Limitaciones

- **Contratos plurianuales**: una OC registra el monto completo de un contrato en el
  mes en que se emite (p.ej. una concesión de basura a 72 meses). Los picos mensuales
  reflejan *adjudicación*, no ejecución de caja.
- **Región = región de la unidad compradora**, no donde se ejecuta el gasto. Los
  servicios centrales con sede en Santiago inflan la Región Metropolitana.
- **Compra Ágil** existe recién desde 2020; las comparaciones de modalidad antes y
  después de esa fecha deben leerse con eso en mente.
- La revisión manual de outliers cubre los casos más grandes, no todos. La exclusión
  por "moneda dudosa" es conservadora: puede dejar fuera algún contrato real en UF/USD.
- El crecimiento del gasto total en 2024–2025 incluye contratos plurianuales grandes
  (p.ej. el Programa de Alimentación Escolar 2025–2028), no solo más gasto anual.

## Estructura

```
├── assets/branding/      logo y favicon
├── scripts/              pipeline (01 → 04) + config.py
├── data/
│   ├── reference/        IPC cacheado, correcciones manuales (versionado)
│   ├── processed/        modelo final (CSV versionados; parquet regenerables)
│   ├── raw/ interim/     generados por el pipeline (no versionados)
├── dashboard/            .pbix, tema, guía de armado, capturas
└── reports/insights.md   cifras de los insights
```

---
Autor: [Faundezz21](https://github.com/Faundezz21) · Repositorio: [VortesInsight](https://github.com/Faundezz21/VortesInsight) · Datos: ChileCompra (licencia de datos abiertos del Estado de Chile)

## Licencia

Distribuido bajo licencia **MIT**. Ver [`LICENSE`](LICENSE).
