"""consolida parquet intermedios y construye el modelo para power bi"""
import argparse
from importlib import import_module

import numpy as np
import pandas as pd
import requests

from config import INTERIM_DIR, PROCESSED_DIR, REFERENCE_DIR

REGION_ORDEN = import_module("02_clean").REGION_ORDEN

# ruta al archivo de variacion mensual del ipc
IPC_PATH = REFERENCE_DIR / "ipc_variacion_mensual.csv"
# url para consultar serie de inflacion desde la api publica
IPC_URL = "https://mindicador.cl/api/ipc/{year}"
# nombres abreviados de los meses del anio
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def cargar_ipc(desde: pd.Timestamp, hasta: pd.Timestamp, refresh: bool) -> pd.DataFrame:
    """carga y actualiza la serie de inflacion mensual"""
    cache = pd.read_csv(IPC_PATH, parse_dates=["fecha"]) if IPC_PATH.exists() else pd.DataFrame()
    falta = cache.empty or cache["fecha"].min() > desde or cache["fecha"].max() < hasta
    if refresh or falta:
        filas = []
        for y in range(desde.year, hasta.year + 1):
            r = requests.get(IPC_URL.format(year=y), timeout=30)
            r.raise_for_status()
            for p in r.json()["serie"]:
                filas.append({"fecha": pd.Timestamp(p["fecha"][:7] + "-01"), "variacion_pct": p["valor"]})
        cache = (pd.concat([cache, pd.DataFrame(filas)])
                 .drop_duplicates("fecha", keep="last").sort_values("fecha"))
        REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
        cache.to_csv(IPC_PATH, index=False, date_format="%Y-%m-%d")
    return cache


def factores_deflactor(meses: pd.DatetimeIndex, refresh: bool) -> pd.DataFrame:
    """calcula factores para ajustar montos a pesos de fecha base"""
    ipc = cargar_ipc(meses.min(), meses.max(), refresh).set_index("fecha")["variacion_pct"]
    ipc = ipc.reindex(pd.date_range(meses.min(), meses.max(), freq="MS"))
    if ipc.isna().any():
        # completa con cero si el ultimo mes no esta publicado
        print(f"AVISO: IPC no publicado para {list(ipc[ipc.isna()].index.strftime('%Y-%m'))}; se asume 0%")
        ipc = ipc.fillna(0)
    indice = (1 + ipc / 100).cumprod()
    out = pd.DataFrame({"fecha": indice.index, "indice_ipc": indice.values})
    out["factor_real"] = indice.iloc[-1] / out["indice_ipc"]
    return out


def marcar_outliers(oc: pd.DataFrame) -> pd.Series:
    """calcula z-score robusto para detectar montos atipicos por modalidad"""
    x = np.log10(oc["monto_clp"].clip(lower=1))
    g = x.groupby(oc["modalidad"])
    med = g.transform("median")
    mad = g.transform(lambda s: (s - s.median()).abs().median())
    z = 0.6745 * (x - med) / mad.replace(0, np.nan)
    return z.fillna(0)


def aplicar_correcciones_manuales(oc: pd.DataFrame, rubro: pd.DataFrame) -> pd.DataFrame:
    """aplica correcciones manuales desde archivo de referencia"""
    path = REFERENCE_DIR / "correcciones_manuales.csv"
    if not path.exists():
        return oc.assign(revisada_ok=False)
    corr = pd.read_csv(path)
    desconocidas = set(corr["accion"]) - {"monto_como_clp", "excluir", "mantener"}
    if desconocidas:
        raise ValueError(f"correcciones_manuales.csv: acciones no válidas {desconocidas}")
    oc = oc.set_index("codigo")
    corr = corr[corr["codigo"].isin(oc.index)]

    a_clp = corr.loc[corr["accion"] == "monto_como_clp", "codigo"]
    escala = oc.loc[a_clp, "monto_original"] / oc.loc[a_clp, "monto_clp"]
    oc.loc[a_clp, "monto_clp"] = oc.loc[a_clp, "monto_original"]
    oc.loc[a_clp, "flag_moneda_corregida"] = True
    # reescala monto de rubros en proporcion a la orden corregida
    m = rubro["codigo"].isin(escala.index)
    rubro.loc[m, "monto_clp"] *= rubro.loc[m, "codigo"].map(escala)

    oc.loc[corr.loc[corr["accion"] == "excluir", "codigo"], "flag_monto_imposible"] = True
    oc["revisada_ok"] = oc.index.isin(corr.loc[corr["accion"] == "mantener", "codigo"])
    print(f"Correcciones manuales aplicadas: {len(corr)}")
    return oc.reset_index()


def concentracion(rr: pd.DataFrame, dim_prov: pd.DataFrame, top_n: int = 10):
    """calcula indice de concentracion hhi y principales proveedores"""
    g = rr.groupby(["anio", "rubro", "rut_proveedor"], as_index=False)["monto_clp_real"].sum()
    g = g[g["monto_clp_real"] > 0]
    g["cuota"] = g["monto_clp_real"] / g.groupby(["anio", "rubro"])["monto_clp_real"].transform("sum")
    g = g.sort_values(["anio", "rubro", "cuota"], ascending=[True, True, False])
    g["ranking"] = g.groupby(["anio", "rubro"]).cumcount() + 1
    conc = (g.groupby(["anio", "rubro"])
            .agg(monto_clp_real=("monto_clp_real", "sum"), n_proveedores=("rut_proveedor", "size"),
                 hhi=("cuota", lambda s: (s.pow(2) * 10_000).sum()), cuota_top1=("cuota", "max"))
            .reset_index())
    conc["cuota_top10"] = (g[g["ranking"] <= 10].groupby(["anio", "rubro"])["cuota"].sum()
                           .reindex(pd.MultiIndex.from_frame(conc[["anio", "rubro"]])).values)
    top = g[g["ranking"] <= top_n].merge(dim_prov[["rut_proveedor", "proveedor"]], on="rut_proveedor")
    return conc, top[["anio", "rubro", "ranking", "rut_proveedor", "proveedor", "monto_clp_real", "cuota"]]


def main():
    """consolida los datos del periodo y exporta el modelo estrella"""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--refresh-ipc", action="store_true")
    args = p.parse_args()

    oc_files = sorted(INTERIM_DIR.glob("oc_*.parquet"))
    if not oc_files:
        raise SystemExit("No hay datos en data/interim/. Corre primero scripts/02_clean.py")
    # carga y consolida los archivos intermedios de ordenes y rubros
    oc = pd.concat([pd.read_parquet(f) for f in oc_files], ignore_index=True)
    rubro = pd.concat([pd.read_parquet(f) for f in INTERIM_DIR.glob("rubro_*.parquet")], ignore_index=True)

    # conserva la version mas reciente de ordenes modificadas
    n0 = len(oc)
    oc = oc.sort_values(["fecha_modificacion", "periodo_archivo"]).drop_duplicates("codigo", keep="last")
    print(f"OC repetidas entre meses descartadas: {n0 - len(oc):,}")
    rubro = rubro.merge(oc[["codigo", "periodo_archivo"]], on=["codigo", "periodo_archivo"])

    oc = aplicar_correcciones_manuales(oc, rubro)

    # asigna fecha mensual segun fecha de envio de la orden
    oc["fecha"] = oc["fecha_envio"].dt.to_period("M").dt.to_timestamp()

    # deflacta montos a pesos reales de la fecha base
    defl = factores_deflactor(pd.DatetimeIndex(oc["fecha"].unique()), args.refresh_ipc)
    oc = oc.merge(defl[["fecha", "factor_real"]], on="fecha", how="left")
    oc["monto_clp_real"] = oc["monto_clp"] * oc["factor_real"]
    base = defl["fecha"].max().strftime("%Y-%m")

    # marca registros atipicos y excluye monedas dudosas
    oc["z_monto"] = marcar_outliers(oc)
    oc["flag_outlier_estadistico"] = oc["z_monto"] > 5
    oc["flag_moneda_dudosa"] = (oc["moneda_original"].ne("CLP") & oc["flag_outlier_estadistico"]
                                & ~oc["flag_moneda_corregida"] & ~oc["revisada_ok"])
    oc["en_analisis"] = ~(oc["flag_monto_minimo"] | oc["flag_agil_sobre_tope"] | oc["flag_monto_imposible"]
                          | oc["flag_moneda_dudosa"])
    print(f"OC con moneda dudosa excluidas: {oc['flag_moneda_dudosa'].sum():,} "
          f"(${oc.loc[oc['flag_moneda_dudosa'], 'monto_clp_real'].sum() / 1e12:,.2f} billones)")
    oc["es_trato_directo"] = oc["modalidad"].eq("Trato directo")

    # genera tablas de dimensiones para el modelo
    ult = oc.sort_values("fecha_envio")
    dim_org = ult.drop_duplicates("organismo_id", keep="last")[["organismo_id", "organismo", "sector"]]
    dim_prov = ult.drop_duplicates("rut_proveedor", keep="last")[["rut_proveedor", "proveedor", "region_proveedor"]]
    dim_region = pd.DataFrame({"region": list(REGION_ORDEN) + ["Sin región"],
                               "orden_norte_sur": list(REGION_ORDEN.values()) + [99]})
    fechas = pd.date_range(oc["fecha"].min(), oc["fecha"].max(), freq="MS")
    dim_fecha = pd.DataFrame({"fecha": fechas, "anio": fechas.year, "mes": fechas.month,
                              "mes_nombre": [MESES[m - 1] for m in fechas.month],
                              "trimestre": "T" + fechas.quarter.astype(str)})

    # genera tabla de hechos principal con variables categoricas
    fact = oc[["codigo", "fecha", "fecha_envio", "modalidad", "es_trato_directo", "causal", "estado",
               "monto_clp", "monto_clp_real", "moneda_original", "organismo_id", "region",
               "rut_proveedor", "flag_moneda_corregida", "flag_monto_imposible", "flag_monto_minimo",
               "flag_agil_sobre_tope", "flag_outlier_estadistico", "flag_moneda_dudosa", "en_analisis"]].copy()
    for c in ["modalidad", "causal", "estado", "moneda_original", "region"]:
        fact[c] = fact[c].astype("category")
    rubro = rubro.merge(oc[["codigo", "factor_real"]], on="codigo")
    rubro["monto_clp_real"] = rubro["monto_clp"] * rubro.pop("factor_real")
    rubro = rubro[["codigo", "rubro", "monto_clp", "monto_clp_real"]]

    # exporta tablas base del modelo procesado
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    fact.to_parquet(PROCESSED_DIR / "fact_oc.parquet", index=False)
    rubro.to_parquet(PROCESSED_DIR / "fact_oc_rubro.parquet", index=False)
    dim_prov.to_parquet(PROCESSED_DIR / "dim_proveedor.parquet", index=False)
    dim_org.to_csv(PROCESSED_DIR / "dim_organismo.csv", index=False)
    dim_region.to_csv(PROCESSED_DIR / "dim_region.csv", index=False)
    dim_fecha.to_csv(PROCESSED_DIR / "dim_fecha.csv", index=False, date_format="%Y-%m-%d")

    # genera y exporta agregados livianos en csv
    a = oc[oc["en_analisis"]]
    (a.groupby(["fecha", "modalidad"], as_index=False)
      .agg(n_oc=("codigo", "size"), monto_clp=("monto_clp", "sum"), monto_clp_real=("monto_clp_real", "sum"))
      .to_csv(PROCESSED_DIR / "agg_mensual_modalidad.csv", index=False, date_format="%Y-%m-%d"))
    (a.assign(anio=a["fecha"].dt.year)
      .groupby(["anio", "region", "modalidad"], as_index=False)
      .agg(n_oc=("codigo", "size"), monto_clp_real=("monto_clp_real", "sum"))
      .to_csv(PROCESSED_DIR / "agg_anual_region_modalidad.csv", index=False))
    (oc[oc["flag_outlier_estadistico"] | oc["flag_moneda_corregida"]]
      .nlargest(300, "monto_clp")[["codigo", "fecha", "modalidad", "monto_clp", "moneda_original",
                                   "z_monto", "flag_moneda_dudosa", "en_analisis", "organismo", "proveedor"]]
      .to_csv(PROCESSED_DIR / "outliers_revision.csv", index=False, date_format="%Y-%m-%d"))

    # genera tabla de hechos agregada para optimizar el reporte
    pbi = (a.assign(causal=np.where(a["es_trato_directo"], a["causal"], "(no aplica)"))
           .groupby(["fecha", "organismo_id", "region", "modalidad", "es_trato_directo", "causal"],
                    as_index=False, observed=True)
           .agg(n_oc=("codigo", "size"), monto_clp=("monto_clp", "sum"), monto_clp_real=("monto_clp_real", "sum")))
    pbi.to_parquet(PROCESSED_DIR / "pbi_hechos_mensual.parquet", index=False)

    rr = (rubro.merge(a[["codigo", "rut_proveedor", "fecha"]], on="codigo")
          .assign(anio=lambda d: d["fecha"].dt.year))
    conc, top = concentracion(rr, dim_prov)
    conc.to_csv(PROCESSED_DIR / "pbi_concentracion_rubro.csv", index=False)
    top.to_csv(PROCESSED_DIR / "pbi_top_proveedores_rubro.csv", index=False)
    print(f"Hecho agregado para Power BI: {len(pbi):,} filas (vs {len(fact):,} OC)")

    print(f"OC en el modelo: {len(fact):,} | en análisis: {fact['en_analisis'].sum():,} | "
          f"outliers estadísticos marcados: {fact['flag_outlier_estadistico'].sum():,}")
    print(f"Montos reales expresados en pesos de {base}. Salida: {PROCESSED_DIR}")


if __name__ == "__main__":
    main()
