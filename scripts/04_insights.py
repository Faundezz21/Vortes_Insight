"""calcula metricas clave y genera reporte de insights en markdown"""
import pandas as pd

from config import PROCESSED_DIR, ROOT

# ruta de salida para el reporte de metricas
OUT = ROOT / "reports" / "insights.md"
# constantes para formateo de montos en billones y millones
BILLON = 1e12
MM = 1e6


def pct(x):
    """formatea un valor numerico a porcentaje de un decimal"""
    return f"{x * 100:.1f}%"


def tabla(df: pd.DataFrame) -> str:
    """convierte un dataframe a tabla markdown o texto"""
    return df.to_markdown(index=False) if hasattr(df, "to_markdown") else df.to_string(index=False)


def main():
    """ejecuta el calculo de metricas y escribe el reporte final"""
    # lee tabla de hechos y filtra solo registros validos para analisis
    f = pd.read_parquet(PROCESSED_DIR / "fact_oc.parquet")
    f = f[f["en_analisis"]].copy()
    f["anio"] = f["fecha"].dt.year
    f["mes"] = f["fecha"].dt.month
    for c in ["modalidad", "region", "causal"]:
        f[c] = f[c].astype(str)
    org = pd.read_csv(PROCESSED_DIR / "dim_organismo.csv")
    prov = pd.read_parquet(PROCESSED_DIR / "dim_proveedor.parquet")
    rub = pd.read_parquet(PROCESSED_DIR / "fact_oc_rubro.parquet")
    out = [f"# Insights — Compras públicas de Chile {f['anio'].min()}–{f['anio'].max()}\n",
           "_Generado por `scripts/04_insights.py`. Montos en pesos reales del último mes de la serie._\n"]

    # calcula evolucion anual de gasto y modalidades
    anual = (f.groupby("anio")
             .agg(n_oc=("codigo", "size"), monto_billones=("monto_clp_real", "sum"))
             .assign(monto_billones=lambda d: (d.monto_billones / BILLON).round(2)))
    td = f[f["es_trato_directo"]].groupby("anio")["monto_clp_real"].sum() / f.groupby("anio")["monto_clp_real"].sum()
    ag = f[f["modalidad"].eq("Compra Ágil")].groupby("anio")["codigo"].size() / f.groupby("anio")["codigo"].size()
    anual["pct_monto_trato_directo"] = td.map(pct)
    anual["pct_oc_compra_agil"] = ag.reindex(anual.index).fillna(0).map(pct)
    out += ["## 1. Panorama anual\n", tabla(anual.reset_index()), ""]

    # analiza estacionalidad y gasto del mes de diciembre
    completos = f.groupby("anio")["mes"].nunique().loc[lambda s: s == 12].index
    if len(completos) == 0:
        raise SystemExit("Se necesita al menos un año completo para el análisis de estacionalidad.")
    f12 = f[f["anio"].isin(completos)]
    m = f12.groupby(["anio", "mes"])["monto_clp_real"].sum().unstack()
    share = m.div(m.sum(axis=1), axis=0)
    dic = share[12]
    td_m = f12[f12["es_trato_directo"]].groupby(["anio", "mes"])["monto_clp_real"].sum().unstack()
    td_dic = td_m[12] / td_m.sum(axis=1)
    n_m = f12.groupby(["anio", "mes"])["codigo"].size().unstack()
    out += ["## 2. Efecto fin de año\n",
             "Peso de diciembre en el gasto anual (un mes \"neutro\" pesaría 8,3%):\n",
             tabla(pd.DataFrame({"anio": dic.index, "pct_gasto_diciembre": dic.map(pct).values,
                                 "pct_trato_directo_en_diciembre": td_dic.reindex(dic.index).map(pct).values,
                                 "oc_dic_vs_promedio_ene_nov": (n_m[12] / n_m.loc[:, 1:11].mean(axis=1)).round(2).values})),
             "", "Mes con mayor gasto promedio (share medio sobre todos los años):\n",
             tabla(share.mean().rename("share_promedio").map(pct).reset_index().rename(columns={"mes": "mes"})), ""]

    # analiza proporcion de trato directo por region
    reg = f[f["region"] != "Sin región"]
    r = reg.groupby("region").agg(monto=("monto_clp_real", "sum"),
                                  monto_td=("monto_clp_real", lambda s: s[reg.loc[s.index, "es_trato_directo"]].sum()))
    r["pct_trato_directo"] = r["monto_td"] / r["monto"]
    r = r.sort_values("pct_trato_directo", ascending=False)
    nacional = f["monto_clp_real"][f["es_trato_directo"]].sum() / f["monto_clp_real"].sum()
    out += ["## 3. Trato directo por región (todo el período, % del monto)\n",
            f"Promedio nacional: {pct(nacional)}\n",
            tabla(r.assign(monto_billones=(r.monto / BILLON).round(2), pct_trato_directo=r.pct_trato_directo.map(pct))
                  .reset_index()[["region", "monto_billones", "pct_trato_directo"]]), ""]

    # identifica causales mas frecuentes de trato directo
    c = (f[f["es_trato_directo"]].groupby("causal")["monto_clp_real"].sum().sort_values(ascending=False))
    out += ["### Causales de trato directo (por monto)\n",
            tabla((c / c.sum()).head(8).map(pct).rename("pct").reset_index()), ""]

    # analiza gasto por causales de emergencia a lo largo del tiempo
    em = (f[f["causal"].str.contains("Emergencia", case=False, na=False)]
          .groupby("anio")["monto_clp_real"].sum() / MM / 1e3).round(1)
    out += ["### Trato directo por \"Emergencia, urgencia o imprevisto\" (miles de MM $)\n",
            tabla(em.rename("miles_mm").reset_index()), ""]

    # calcula concentracion de proveedores por rubro segun indice hhi
    rr = rub.merge(f[["codigo", "rut_proveedor"]], on="codigo")
    g = rr.groupby(["rubro", "rut_proveedor"])["monto_clp_real"].sum()
    tot = g.groupby(level=0).sum()
    cuota = g / tot.reindex(g.index.get_level_values(0)).values
    hhi = (cuota.pow(2) * 10_000).groupby(level=0).sum()
    top1 = cuota.groupby(level=0).max()
    n_prov = g.groupby(level=0).size()
    conc = pd.DataFrame({"monto_billones": (tot / BILLON).round(2), "hhi": hhi.round(0),
                         "cuota_mayor_proveedor": top1, "n_proveedores": n_prov})
    relevantes = conc[conc["monto_billones"] >= 0.5].sort_values("hhi", ascending=False)
    lider = (cuota.rename("cuota").reset_index().sort_values("cuota").drop_duplicates("rubro", keep="last")
             .merge(prov, on="rut_proveedor")[["rubro", "proveedor"]].set_index("rubro"))
    relevantes = relevantes.join(lider)
    relevantes["cuota_mayor_proveedor"] = relevantes["cuota_mayor_proveedor"].map(pct)
    out += ["## 4. Concentración de proveedores por rubro (rubros ≥ $0,5 billones)\n",
            tabla(relevantes.reset_index()), ""]

    # genera ranking de los diez principales organismos compradores
    top_org = (f.groupby("organismo_id")["monto_clp_real"].sum().nlargest(10).reset_index()
               .merge(org, on="organismo_id"))
    top_org["pct_total"] = (top_org["monto_clp_real"] / f["monto_clp_real"].sum()).map(pct)
    top_org["monto_billones"] = (top_org["monto_clp_real"] / BILLON).round(2)
    out += ["## 5. Top 10 organismos compradores\n",
            tabla(top_org[["organismo", "sector", "monto_billones", "pct_total"]]), ""]

    # guarda el reporte resultante en formato markdown
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
