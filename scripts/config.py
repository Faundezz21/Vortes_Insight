"""configuracion de rutas, columnas y fechas del pipeline"""
from pathlib import Path

# rutas principales del proyecto
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW_DIR = DATA / "raw" / "oc"
INTERIM_DIR = DATA / "interim"
PROCESSED_DIR = DATA / "processed"
REFERENCE_DIR = DATA / "reference"

# url base para descargar ordenes de compra mensuales
OC_URL = "https://transparenciachc.blob.core.windows.net/oc-da/{year}-{month}.zip"

# rango de fechas por defecto para el analisis
DEFAULT_START = "2019-01"
DEFAULT_END = "2025-12"

# columnas seleccionadas para optimizar uso de memoria
RAW_COLUMNS = [
    "Codigo", "FechaEnvio", "fechaUltimaModificacion",
    "ProcedenciaOC", "EsTratoDirecto", "EsCompraAgil", "Estado",
    "MontoTotalOC", "TipoMonedaOC", "MontoTotalOC_PesosChilenos",
    "CodigoOrganismoPublico", "OrganismoPublico", "sector", "RegionUnidadCompra",
    "RutSucursal", "NombreProveedor", "RegionProveedor",
    "IDItem", "RubroN1", "totalLineaNeto",
]

# opciones para lectura rapida de csv
CSV_OPTS = dict(sep=";", encoding="latin-1", decimal=",", low_memory=False)


def month_range(start: str, end: str):
    """genera tuplas de anio y mes en un rango dado"""
    y, m = map(int, start.split("-"))
    ye, me = map(int, end.split("-"))
    while (y, m) <= (ye, me):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1
