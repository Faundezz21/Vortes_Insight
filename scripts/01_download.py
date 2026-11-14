"""descarga zips mensuales de ordenes de compra desde chilecompra"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from config import DEFAULT_END, DEFAULT_START, OC_URL, RAW_DIR, month_range

# tamano de bloque de descarga de un megabyte
CHUNK = 1 << 20


def download(year: int, month: int, session: requests.Session, retries: int = 5) -> str:
    """descarga un archivo mensual si no existe previamente"""
    url = OC_URL.format(year=year, month=month)
    # ruta de destino con formato de dos digitos para el mes
    dest = RAW_DIR / f"{year}-{month:02d}.zip"
    tmp = dest.with_suffix(".part")
    # reintentos para manejar cortes de conexion del servidor
    for attempt in range(1, retries + 1):
        try:
            head = session.head(url, timeout=30)
            if head.status_code == 404:
                return "no publicado"
            head.raise_for_status()
            remote_size = int(head.headers.get("Content-Length", 0))
            if dest.exists() and dest.stat().st_size == remote_size:
                return "ya existe"
            with session.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(CHUNK):
                        f.write(chunk)
            if tmp.stat().st_size != remote_size:
                raise IOError("descarga incompleta")
            tmp.replace(dest)
            return f"ok ({remote_size / 1e6:.0f} MB)"
        except (requests.RequestException, IOError) as e:
            if attempt == retries:
                tmp.unlink(missing_ok=True)
                return f"ERROR: {e}"
            time.sleep(10 * attempt)


def main():
    """ejecuta la descarga en paralelo segun los argumentos entregados"""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--desde", default=DEFAULT_START, help="YYYY-MM")
    p.add_argument("--hasta", default=DEFAULT_END, help="YYYY-MM")
    p.add_argument("--workers", type=int, default=4, help="descargas simultaneas")
    args = p.parse_args()

    # asegura existencia de la carpeta de destino
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # funcion interna de descarga para ejecucion con hilos
    def job(ym):
        with requests.Session() as s:
            return ym, download(*ym, s)

    errors = 0
    # procesa descargas en paralelo con un grupo de hilos
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for (y, m), status in pool.map(job, month_range(args.desde, args.hasta)):
            errors += status.startswith("ERROR")
            print(f"{y}-{m:02d}: {status}", flush=True)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
