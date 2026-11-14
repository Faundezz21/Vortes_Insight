"""limpia y procesa archivos mensuales generando datos intermedios"""
import argparse
import re
import unicodedata
import zipfile

import numpy as np
import pandas as pd

from config import CSV_OPTS, INTERIM_DIR, PROCESSED_DIR, RAW_COLUMNS, RAW_DIR

# estados de orden que representan compras efectivas
ESTADOS_VALIDOS = {"Aceptada", "Recepcion Conforme"}

# tope legal maximo para modalidad de compra agil
TOPE_COMPRA_AGIL_CLP = 7_000_000

# monto minimo para considerar una orden valida
MONTO_MINIMO_CLP = 1_000

# monto limite para filtrar errores evidentes de digitacion
TOPE_MONTO_PLAUSIBLE_CLP = 1e12

# lista de regiones ordenadas de norte a sur
REGIONES = [
    ("arica", "Arica y Parinacota"), ("tarapaca", "Tarapacá"),
    ("antofagasta", "Antofagasta"), ("atacama", "Atacama"), ("coquimbo", "Coquimbo"),
    ("valparaiso", "Valparaíso"), ("metropolitana", "Metropolitana"),
    ("higgins", "O'Higgins"), ("maule", "Maule"), ("nuble", "Ñuble"),
    ("bio", "Biobío"), ("araucania", "Araucanía"), ("los rios", "Los Ríos"),
    ("los lagos", "Los Lagos"), ("aysen", "Aysén"), ("magallanes", "Magallanes"),
]
# mapa de posicion geografica para ordenar regiones
REGION_ORDEN = {nombre: i + 1 for i, (_, nombre) in enumerate(REGIONES)}


def sin_tildes(s: str) -> str:
    """elimina tildes y convierte el texto a minusculas"""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def normalizar_region(valor) -> str:
    """normaliza el nombre de la region segun el catalogo"""
    if not isinstance(valor, str):
        return "Sin región"
    v = sin_tildes(valor)
    for clave, nombre in REGIONES:
        if clave in v:
            return nombre
    return "Sin región"


def limpiar_texto(s: pd.Series) -> pd.Series:
    """elimina espacios dobles y estandariza texto"""
    return s.astype("string").str.strip().str.upper().str.replace(r"\s+", " ", regex=True)


def normalizar_rut(s: pd.Series) -> pd.Series:
    """elimina puntos y guion dejando formato limpio de rut"""
    return s.astype("string").str.upper().str.replace(r"[^0-9K-]", "", regex=True)


def clasificar_modalidad(df: pd.DataFrame) -> pd.Series:
    """clasifica la modalidad de contratacion de la orden"""
    proc = df["ProcedenciaOC"].fillna("").map(sin_tildes)
    agil = df["EsCompraAgil"].eq("Si")
    trato = df["EsTratoDirecto"].eq("Si")
    return pd.Series(
        np.select(
            [agil,
             proc.str.contains("convenio marco"),
             proc.str.contains("licitacion publica") & ~trato,
             proc.str.contains("licitacion privada") & ~trato,
             trato],
            ["Compra Ágil", "Convenio Marco", "Licitación pública", "Licitación privada", "Trato directo"],
            default="Otro",
        ),
        index=df.index,
    )


def leer_zip(path) -> pd.DataFrame:
    """lee y valida las columnas necesarias del csv comprimido"""
    with zipfile.ZipFile(path) as z:
        csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(csvs) != 1:
            raise ValueError(f"{path.name}: se esperaba 1 CSV en el zip, hay {len(csvs)}")
        with z.open(csvs[0]) as f:
            header = pd.read_csv(f, nrows=0, **CSV_OPTS).columns
        faltan = set(RAW_COLUMNS) - set(header)
        if faltan:
            raise ValueError(f"{path.name}: faltan columnas {sorted(faltan)}")
        with z.open(csvs[0]) as f:
            return pd.read_csv(f, usecols=RAW_COLUMNS, **CSV_OPTS)


def limpiar_mes(path):
    """limpia y estructura las ordenes y rubros de un mes"""
    periodo = path.stem
    raw = leer_zip(path)
    log = {"periodo": periodo, "filas_crudas": len(raw)}

    # elimina registros duplicados por item
    dup_exactos = raw.duplicated().sum()
    raw = raw.drop_duplicates()
    raw = raw.drop_duplicates(subset="IDItem", keep="last")
    log["filas_duplicadas"] = int(dup_exactos)

    # convierte columnas a formatos de fecha y numerico
    for c in ["FechaEnvio", "fechaUltimaModificacion"]:
        raw[c] = pd.to_datetime(raw[c], errors="coerce")
    for c in ["MontoTotalOC", "MontoTotalOC_PesosChilenos", "totalLineaNeto"]:
        raw[c] = pd.to_numeric(raw[c], errors="coerce")

    # extrae cabecera unica por orden de compra
    cab_cols = [c for c in RAW_COLUMNS if c not in ("IDItem", "RubroN1", "totalLineaNeto")]
    oc = raw[cab_cols].drop_duplicates(subset="Codigo", keep="last").copy()
    log["oc_unicas"] = len(oc)

    # filtra por estados validos de recepcion y aceptacion
    estado_ok = oc["Estado"].isin(ESTADOS_VALIDOS)
    log["oc_descartadas_estado"] = int((~estado_ok).sum())
    log["estados_descartados"] = "; ".join(
        f"{k}={v}" for k, v in oc.loc[~estado_ok, "Estado"].value_counts().items())
    oc = oc[estado_ok]

    # descarta registros sin fecha o sin monto valido
    sin_datos = oc["FechaEnvio"].isna() | oc["MontoTotalOC_PesosChilenos"].isna()
    log["oc_descartadas_sin_fecha_o_monto"] = int(sin_datos.sum())
    oc = oc[~sin_datos]

    # estandariza moneda a pesos chilenos
    oc["monto_clp"] = oc["MontoTotalOC_PesosChilenos"]
    oc["moneda_original"] = oc["TipoMonedaOC"].astype("string")

    # reinterpreta montos desproporcionados en moneda extranjera
    mal_codificada = oc["moneda_original"].ne("CLP") & (oc["monto_clp"] > TOPE_MONTO_PLAUSIBLE_CLP)
    oc.loc[mal_codificada, "monto_clp"] = oc.loc[mal_codificada, "MontoTotalOC"]
    oc["flag_moneda_corregida"] = mal_codificada
    log["oc_moneda_corregida"] = int(mal_codificada.sum())

    # normaliza texto de regiones organismos y proveedores
    oc["modalidad"] = clasificar_modalidad(oc)
    oc["region"] = oc["RegionUnidadCompra"].map(normalizar_region)
    oc["sector"] = oc["sector"].fillna("Sin sector").astype("string")
    oc["organismo"] = limpiar_texto(oc["OrganismoPublico"])
    oc["proveedor"] = limpiar_texto(oc["NombreProveedor"])
    oc["rut_proveedor"] = normalizar_rut(oc["RutSucursal"])
    oc["region_proveedor"] = oc["RegionProveedor"].map(normalizar_region)

    # marca registros con montos que escapan a los criterios validos
    oc["flag_monto_minimo"] = oc["monto_clp"] < MONTO_MINIMO_CLP
    oc["flag_agil_sobre_tope"] = oc["modalidad"].eq("Compra Ágil") & (oc["monto_clp"] > TOPE_COMPRA_AGIL_CLP)
    oc["flag_monto_imposible"] = oc["monto_clp"] > TOPE_MONTO_PLAUSIBLE_CLP
    log["oc_flag_monto_imposible"] = int(oc["flag_monto_imposible"].sum())
    log["oc_flag_monto_minimo"] = int(oc["flag_monto_minimo"].sum())
    log["oc_flag_agil_sobre_tope"] = int(oc["flag_agil_sobre_tope"].sum())
    log["monto_flag_agil_sobre_tope_mm"] = round(oc.loc[oc["flag_agil_sobre_tope"], "monto_clp"].sum() / 1e6, 1)

    # genera tabla de ordenes limpia
    oc_out = oc.rename(columns={
        "MontoTotalOC": "monto_original", "Codigo": "codigo", "FechaEnvio": "fecha_envio", "fechaUltimaModificacion": "fecha_modificacion",
        "Estado": "estado", "ProcedenciaOC": "causal", "CodigoOrganismoPublico": "organismo_id",
    })[[
        "codigo", "fecha_envio", "fecha_modificacion", "estado", "modalidad", "causal",
        "monto_clp", "monto_original", "moneda_original", "organismo_id", "organismo", "sector", "region",
        "rut_proveedor", "proveedor", "region_proveedor", "flag_moneda_corregida",
        "flag_monto_imposible", "flag_monto_minimo", "flag_agil_sobre_tope",
    ]]
    oc_out["periodo_archivo"] = periodo

    # prorratea el monto de la orden segun el peso de cada rubro
    it = raw.loc[raw["Codigo"].isin(oc_out["codigo"]), ["Codigo", "RubroN1", "totalLineaNeto"]].copy()
    it["RubroN1"] = it["RubroN1"].fillna("Sin clasificar").astype("string").str.strip()
    it["totalLineaNeto"] = it["totalLineaNeto"].clip(lower=0).fillna(0)
    it = it.groupby(["Codigo", "RubroN1"], as_index=False)["totalLineaNeto"].sum()
    tot = it.groupby("Codigo")["totalLineaNeto"].transform("sum")
    n = it.groupby("Codigo")["totalLineaNeto"].transform("size")
    it["peso"] = np.where(tot > 0, it["totalLineaNeto"] / tot.where(tot > 0, 1), 1 / n)
    it = it.merge(oc_out[["codigo", "monto_clp"]], left_on="Codigo", right_on="codigo")
    it["monto_clp"] = it["monto_clp"] * it["peso"]
    rubro_out = it[["codigo", "RubroN1", "monto_clp"]].rename(columns={"RubroN1": "rubro"})
    rubro_out["periodo_archivo"] = periodo

    log["oc_finales"] = len(oc_out)
    log["monto_total_mm"] = round(oc_out["monto_clp"].sum() / 1e6, 1)
    # retorna tablas limpias de ordenes rubros y registro de control
    return oc_out, rubro_out, log


def main():
    """procesa todos los zips pendientes en la carpeta de datos crudos"""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true", help="reprocesar meses ya limpios")
    args = p.parse_args()

    # prepara carpetas de almacenamiento intermedio y procesado
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    log_path = PROCESSED_DIR / "cleaning_log.csv"
    # lee historial previo de limpieza si existe
    logs = pd.read_csv(log_path) if log_path.exists() and not args.force else pd.DataFrame()

    # busca archivos zips disponibles para procesar
    zips = sorted(RAW_DIR.glob("*.zip"))
    if not zips:
        raise SystemExit("No hay zips en data/raw/oc/. Corre primero scripts/01_download.py")
    for z in zips:
        out_oc = INTERIM_DIR / f"oc_{z.stem}.parquet"
        # omite meses ya procesados si no se fuerza la ejecucion
        if out_oc.exists() and not args.force:
            continue
        oc, rubro, log = limpiar_mes(z)
        oc.to_parquet(out_oc, index=False)
        rubro.to_parquet(INTERIM_DIR / f"rubro_{z.stem}.parquet", index=False)
        if not logs.empty:
            logs = logs[logs["periodo"] != log["periodo"]]
        logs = pd.concat([logs, pd.DataFrame([log])], ignore_index=True)
        # guarda datos intermedios y actualiza el registro de auditoria
        logs.sort_values("periodo").to_csv(log_path, index=False)
        print(f"{z.stem}: {log['filas_crudas']:,} filas -> {log['oc_finales']:,} OC "
              f"(estado descartado {log['oc_descartadas_estado']:,}, "
              f"ágil sobre tope {log['oc_flag_agil_sobre_tope']:,}) "
              f"${log['monto_total_mm']:,.0f} MM", flush=True)


if __name__ == "__main__":
    main()
