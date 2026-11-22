"""Descarga el cierre diario de los índices y mantiene el histórico.

- Primera ejecución: baja todo el histórico disponible.
- Siguientes ejecuciones: actualiza solo el último mes y lo une al histórico.
Genera:
  data/historico.csv  -> una fila por día, una columna por índice
  data/ultimo.json    -> último valor y variación diaria (para la web)
"""
import json
from pathlib import Path

import pandas as pd
import yfinance as yf

INDICES = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq",
    "^DJI": "Dow Jones",
    "^IBEX": "IBEX 35",
    "GC=F": "Oro",
    "BTC-USD": "Bitcoin",
    "^TNX": "Bono EE.UU. 10 años (%)",
}

DATA = Path("data")
DATA.mkdir(exist_ok=True)
CSV = DATA / "historico.csv"
JSON = DATA / "ultimo.json"


def descargar(periodo):
    df = yf.download(
        list(INDICES),
        period=periodo,
        interval="1d",
        auto_adjust=False,
        progress=False,
    )["Close"]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "fecha"
    return df.dropna(how="all")


def main():
    if CSV.exists():
        antiguo = pd.read_csv(CSV, index_col="fecha", parse_dates=True)
        nuevo = descargar("1mo")
        # Los datos nuevos mandan; el histórico rellena el resto
        datos = nuevo.combine_first(antiguo)
    else:
        datos = descargar("max")

    datos = datos.sort_index().round(4)
    datos.to_csv(CSV)

    resumen = []
    for simbolo, nombre in INDICES.items():
        if simbolo not in datos:
            continue
        serie = datos[simbolo].dropna()
        if len(serie) < 2:
            continue
        ultimo, anterior = serie.iloc[-1], serie.iloc[-2]
        resumen.append(
            {
                "simbolo": simbolo,
                "nombre": nombre,
                "fecha": serie.index[-1].strftime("%Y-%m-%d"),
                "valor": round(float(ultimo), 4),
                "variacion_pct": round(float((ultimo / anterior - 1) * 100), 2),
            }
        )
    JSON.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Histórico actualizado: {len(datos)} días, {len(resumen)} índices.")


if __name__ == "__main__":
    main()
