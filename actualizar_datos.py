"""Descarga el cierre diario de índices, bonos, Euríbor, activos y empresas
y mantiene el histórico.

Genera:
  data/historico.csv  -> una fila por día, una columna por indicador
  data/ultimo.json    -> último valor y variación diaria, agrupado por sección

PARA AÑADIR EMPRESAS: rellena el diccionario EMPRESAS con el código de Yahoo
Finance y el nombre, por ejemplo {"IBE.MC": "Iberdrola", "AAPL": "Apple"}.
"""
import io
import json
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

# ----------------------------- CONFIGURACIÓN -----------------------------
ACTIVOS = {"GC=F": "Oro", "BTC-USD": "Bitcoin", "BZ=F": "Petróleo Brent"}
CAMBIO = {"EURUSD=X": "Euro / Dólar"}  # dólares por 1 euro
CONVERTIBLES = ["GC=F", "BTC-USD", "BZ=F"]  # se pueden ver en euros o en dólares
BURSATILES = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq",
    "^DJI": "Dow Jones",
    "^IBEX": "IBEX 35",
}
EMPRESAS = {  # código de Yahoo Finance: nombre
    "SPCX": "SpaceX",
    "ANT": "Anthropic",
    "TEF.MC": "Telefónica",
    "NTGY.MC": "Naturgy",
    "CABK.MC": "CaixaBank",
    "ENG.MC": "Enagás",
    "RED.MC": "Redeia",
}
BONOS_YAHOO = {"^TNX": "Bono EE.UU. 10 años"}
BONOS_BDE = {  # identificador propio: (código de serie en el Banco de España, nombre)
    "ES10Y": ("DPUG0B1F0ZP", "Bono España 10 años"),
    "DE10Y": ("DPUDNBBN308", "Bono Alemania 10 años"),
}
BONOS_STOOQ = {  # identificador propio: (código en Stooq, nombre)
    "FR10Y": ("10yfry.b", "Bono Francia 10 años"),
}
EURIBOR_BCE = {"EURIBOR12M": ("EURIBOR1YD_", "Euríbor 12 meses")}

YAHOO = {**ACTIVOS, **CAMBIO, **BURSATILES, **EMPRESAS, **BONOS_YAHOO}
NOMBRES = dict(YAHOO)
NOMBRES.update({k: v[1] for k, v in BONOS_BDE.items()})
NOMBRES.update({k: v[1] for k, v in BONOS_STOOQ.items()})
NOMBRES.update({k: v[1] for k, v in EURIBOR_BCE.items()})

# Moneda en la que cotiza cada valor (los índices y tipos no llevan moneda)
MONEDA = {s: "USD" for s in CONVERTIBLES}
MONEDA.update({s: ("EUR" if s.endswith(".MC") else "USD") for s in EMPRESAS})

# (nombre de la sección, indicadores, ¿son tipos de interés?, ¿admite euros/dólares?)
SECCIONES = [
    ("Activos", [*ACTIVOS, *CAMBIO], False, True),
    ("Índices bursátiles", list(BURSATILES), False, False),
    ("Bonos y tipos", [*BONOS_YAHOO, "ES10Y", "FR10Y", "DE10Y", *EURIBOR_BCE], True, False),
    ("Empresas", list(EMPRESAS), False, False),
]
# --------------------------------------------------------------------------

DATA = Path("data")
DATA.mkdir(exist_ok=True)
CSV = DATA / "historico.csv"
JSON = DATA / "ultimo.json"
UA = {"User-Agent": "Mozilla/5.0"}


def limpiar(df):
    df = df.copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "fecha"
    return df.dropna(how="all")


def yahoo(simbolos, periodo):
    if not simbolos:
        return pd.DataFrame()
    df = yf.download(simbolos, period=periodo, interval="1d",
                     auto_adjust=False, progress=False)["Close"]
    if isinstance(df, pd.Series):
        df = df.to_frame(simbolos[0])
    return limpiar(df)


BDE_URL = "https://www.bde.es/webbe/es/estadisticas/compartido/datos/csv/uoi_sindi.csv"
MESES = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
         "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12}


def bde(series):
    """Banco de España: indicadores diarios (bonos a 10 años de España y Alemania)."""
    r = requests.get(BDE_URL, headers=UA, timeout=120)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.content.decode("latin-1")))
    col_fecha = df.columns[0]
    # Las filas de datos empiezan por una fecha tipo "03 ENE 2000"
    df = df[df[col_fecha].astype(str).str.match(r"^\d{2} [A-Z]{3} \d{4}$")]
    texto = df[col_fecha].astype(str)
    fechas = pd.to_datetime(pd.DataFrame({
        "year": texto.str[7:].astype(int),
        "month": texto.str[3:6].map(MESES).astype(int),
        "day": texto.str[:2].astype(int),
    }))
    salida = pd.DataFrame(index=pd.DatetimeIndex(fechas))
    for simbolo, (codigo, _) in series.items():
        salida[simbolo] = pd.to_numeric(df[codigo], errors="coerce").values
    return limpiar(salida)


def stooq(codigo, simbolo):
    r = requests.get("https://stooq.com/q/d/l/", params={"s": codigo, "i": "d"},
                     headers=UA, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), parse_dates=["Date"], index_col="Date")
    return limpiar(df[["Close"]].rename(columns={"Close": simbolo}))


def bce(clave, simbolo):
    for freq in ("D", "M"):  # diario y, si no existe, mensual
        url = f"https://data-api.ecb.europa.eu/service/data/FM/{freq}.U2.EUR.RT.MM.{clave}.HSTA"
        r = requests.get(url, params={"format": "csvdata"}, headers=UA, timeout=30)
        if r.status_code != 200:
            continue
        df = pd.read_csv(io.StringIO(r.text))
        serie = pd.Series(df["OBS_VALUE"].values,
                          index=pd.to_datetime(df["TIME_PERIOD"]), name=simbolo)
        return limpiar(serie.to_frame())
    raise ValueError("sin datos en el BCE")


def intentar(nombre, funcion, *args):
    try:
        return funcion(*args)
    except Exception as e:  # un fallo en una fuente no debe parar el resto
        print(f"AVISO: no se pudo descargar {nombre}: {e}")
        return pd.DataFrame()


def resumen(datos):
    secciones = []
    for nombre_sec, simbolos, tasa, convertible in SECCIONES:
        items = []
        for s in simbolos:
            if s not in datos:
                continue
            serie = datos[s].dropna()
            if len(serie) < 2:
                continue
            ult, ant = float(serie.iloc[-1]), float(serie.iloc[-2])
            items.append({
                "simbolo": s,
                "nombre": NOMBRES[s],
                "fecha": serie.index[-1].strftime("%Y-%m-%d"),
                "valor": round(ult, 4),
                "tasa": tasa,
                "variacion": round((ult - ant) * 100, 1) if tasa
                else round((ult / ant - 1) * 100, 2),
                "unidad": "pb" if tasa else "%",
                "moneda": MONEDA.get(s),
                "convertible": s in CONVERTIBLES,
                "decimales": 4 if s in CAMBIO else 2,
            })
        secciones.append({"nombre": nombre_sec, "tasa": tasa,
                          "convertible": convertible, "items": items})
    return {"secciones": secciones}


def main():
    datos = (pd.read_csv(CSV, index_col="fecha", parse_dates=True)
             if CSV.exists() else pd.DataFrame())

    # Yahoo: último mes para lo que ya tenemos, histórico completo para lo nuevo
    ya = [s for s in YAHOO if s in datos.columns and datos[s].notna().any()]
    nuevos = [s for s in YAHOO if s not in ya]
    partes = [intentar("Yahoo (actualización)", yahoo, ya, "1mo"),
              intentar("Yahoo (histórico)", yahoo, nuevos, "max")]
    partes.append(intentar("Banco de España (bonos ES y DE)", bde, BONOS_BDE))
    for simbolo, (codigo, _) in BONOS_STOOQ.items():
        partes.append(intentar(simbolo, stooq, codigo, simbolo))
    for simbolo, (clave, _) in EURIBOR_BCE.items():
        partes.append(intentar(simbolo, bce, clave, simbolo))

    for p in partes:
        if not p.empty:
            datos = p if datos.empty else p.combine_first(datos)

    datos = datos.sort_index().round(4)
    datos.to_csv(CSV)
    JSON.write_text(json.dumps(resumen(datos), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"Histórico actualizado: {len(datos)} días, {datos.shape[1]} indicadores.")


if __name__ == "__main__":
    main()
