# Guía de armado del dashboard en Power BI

El `.pbix` se arma a mano en Power BI Desktop (gratis) a partir de las tablas que deja
el pipeline en `data/processed/`. Esta guía deja fijo el modelo, las medidas y el layout
para que el resultado sea reproducible.

> **Por qué tablas agregadas y no las 13 millones de OC:** el detalle por OC haría que el
> `.pbix` pese cientos de MB (GitHub rechaza archivos > 100 MB). El hecho agregado
> (mes × organismo × región × modalidad × causal) responde todas las preguntas del
> dashboard. El detalle sigue disponible en `fact_oc.parquet` para análisis ad-hoc.

## 1. Cargar datos

**Inicio → Obtener datos**:

| Tabla | Archivo | Conector |
|---|---|---|
| `hechos` | `data/processed/pbi_hechos_mensual.parquet` | Parquet |
| `dim_organismo` | `data/processed/dim_organismo.csv` | Texto/CSV (UTF-8) |
| `dim_region` | `data/processed/dim_region.csv` | Texto/CSV |
| `dim_fecha` | `data/processed/dim_fecha.csv` | Texto/CSV |
| `concentracion` | `data/processed/pbi_concentracion_rubro.csv` | Texto/CSV |
| `top_proveedores` | `data/processed/pbi_top_proveedores_rubro.csv` | Texto/CSV |

En Power Query revisa los tipos: `fecha` = Fecha, montos = Número decimal,
`es_trato_directo` = Verdadero/Falso, `anio` = Número entero.
Para que el `.pbix` no dependa de tu ruta local, crea un **parámetro** `RutaDatos`
y úsalo en el origen de cada consulta.

## 2. Relaciones (vista de modelo)

| Desde (muchos) | Hacia (uno) | Dirección |
|---|---|---|
| `hechos[fecha]` | `dim_fecha[fecha]` | simple |
| `hechos[organismo_id]` | `dim_organismo[organismo_id]` | simple |
| `hechos[region]` | `dim_region[region]` | simple |

`concentracion` y `top_proveedores` quedan **sin relación** (tienen su propio `anio` y
`rubro`); se filtran con segmentadores propios en la página 2.

Ajustes:
- `dim_region[region]` → **Ordenar por columna** → `orden_norte_sur` (los gráficos quedan de norte a sur).
- `dim_fecha[mes_nombre]` → **Ordenar por columna** → `mes`.
- Oculta las columnas llave en la vista de informe.

## 3. Medidas (DAX)

Crea una tabla vacía `_Medidas` y agrega:

```dax
Monto real = SUM ( hechos[monto_clp_real] )

Monto real (billones) = DIVIDE ( [Monto real], 1e12 )

N° OC = SUM ( hechos[n_oc] )

Ticket promedio = DIVIDE ( [Monto real], [N° OC] )

Monto trato directo = CALCULATE ( [Monto real], hechos[es_trato_directo] = TRUE () )

% trato directo = DIVIDE ( [Monto trato directo], [Monto real] )

% trato directo nacional =
    CALCULATE ( [% trato directo], REMOVEFILTERS ( dim_region ) )

% OC Compra Ágil =
    DIVIDE ( CALCULATE ( [N° OC], hechos[modalidad] = "Compra Ágil" ), [N° OC] )

Organismos compradores = DISTINCTCOUNT ( hechos[organismo_id] )

Monto real año anterior =
    VAR anio_sel = MAX ( dim_fecha[anio] )
    RETURN CALCULATE ( [Monto real], REMOVEFILTERS ( dim_fecha ), dim_fecha[anio] = anio_sel - 1 )

Var. % vs año anterior = DIVIDE ( [Monto real] - [Monto real año anterior], [Monto real año anterior] )
```

Formato: `Monto real (billones)` → decimal con 2 decimales y sufijo "bill."; `%` → porcentaje 1 decimal;
`Ticket promedio` → moneda $ sin decimales en millones (`#,0,,"MM"`).

## 4. Tema

**Vista → Temas → Examinar temas** → `dashboard/vortesinsight-theme.json`.
Los colores de las series siguen un orden fijo validado para daltonismo; asígnalos así
en cada gráfico por modalidad para que **cada modalidad tenga siempre el mismo color**:

| Modalidad | Color |
|---|---|
| Licitación pública | `#2a78d6` azul |
| Trato directo | `#eb6834` naranja |
| Compra Ágil | `#1baf7a` aqua |
| Convenio Marco | `#eda100` amarillo |
| Licitación privada | `#e87ba4` magenta |
| Otro | `#008300` verde |

Varios de esos colores tienen bajo contraste sobre blanco: por eso los gráficos de
barras llevan **etiquetas de datos visibles** y la página 2 incluye una tabla.

## 5. Página 1 — "¿Qué tan competitivas son las compras del Estado?"

Lienzo 16:9 (1280 × 720). De arriba a abajo:

1. **Encabezado**: `assets/branding/logo.png` a la izquierda + título de la página.
2. **Fila de filtros** (segmentadores, una sola fila): `dim_fecha[anio]` (entre),
   `dim_region[region]` (lista desplegable), `hechos[modalidad]` (lista desplegable),
   `dim_organismo[sector]` (lista desplegable).
3. **Fila de KPIs** (4 tarjetas): `Monto real (billones)` con `Var. % vs año anterior`
   debajo · `N° OC` · `% trato directo` · `Ticket promedio`.
4. **Tendencia** (ancho completo): gráfico de **columnas apiladas**, eje X `dim_fecha[fecha]`
   (jerarquía año → mes, ver por mes), valores `Monto real`, leyenda `hechos[modalidad]`.
   Sin eje secundario: el % de trato directo va en su propio visual.
5. **Abajo izquierda — comparación por región**: barras horizontales,
   eje `dim_region[region]`, valor `% trato directo`, **línea de referencia** constante
   = `% trato directo nacional` (panel Analítica), etiquetas de datos activadas.
6. **Abajo derecha**: línea `% trato directo` por `dim_fecha[fecha]` (un solo color, sin leyenda).

## 6. Página 2 — "¿Quién se lleva el gasto?"

1. Segmentadores: `concentracion[anio]`, `concentracion[rubro]` (sincroniza `top_proveedores`
   con los mismos campos usando "Editar interacciones" o segmentadores separados).
2. **Tabla** de rubros: `rubro`, `monto_clp_real`, `n_proveedores`, `hhi`, `cuota_top1`, `cuota_top10`,
   con formato condicional de **una sola tonalidad** (escala secuencial) en `hhi`.
3. **Barras horizontales**: top 10 proveedores (`top_proveedores[proveedor]`, `cuota`) del rubro elegido.
4. Cuadro de texto con la lectura del HHI: < 1.500 competitivo · 1.500–2.500 moderado · > 2.500 concentrado.

## 7. Capturas para el README

Con el filtro "todos los años": **Archivo → Exportar → PDF** o captura de la ventana
completa a 1280 × 720, y guárdalas en `dashboard/screenshots/` como
`01_resumen.png` y `02_concentracion.png`.
