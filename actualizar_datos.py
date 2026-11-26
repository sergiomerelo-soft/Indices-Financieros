"""Descarga el cierre diario de índices, bonos, Euríbor, activos y empresas
y mantiene el histórico.

Las listas de lo que se sigue (activos, índices, bonos y empresas, y su orden) se
leen de empresas.json, que se edita desde la app con el botón "Editar".

Genera:
  data/historico.csv  -> una fila por día, una columna por indicador
  data/ultimo.json    -> último valor y variación diaria, agrupado por sección
  data/avisos.json    -> indicadores de la lista de los que no se han podido obtener datos
"""
import io
import json
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

# ----------------------------- CONFIGURACIÓN -----------------------------
CONFIG_JSON = Path("empresas.json")
CAMBIO = {"EURUSD=X": "Euro / Dólar"}  # dólares por 1 euro (fijo: sirve para convertir a euros)

# Lo que se sigue si no existe empresas.json
DEFECTO = {
    "activos": [
        {"ticker": "GC=F", "nombre": "Oro"},
        {"ticker": "BTC-USD", "nombre": "Bitcoin"},
        {"ticker": "BZ=F", "nombre": "Petróleo Brent"},
    ],
    "bursatiles": [
        {"ticker": "^GSPC", "nombre": "S&P 500"},
        {"ticker": "^IXIC", "nombre": "Nasdaq"},
        {"ticker": "^DJI", "nombre": "Dow Jones"},
        {"ticker": "^IBEX", "nombre": "IBEX 35"},
    ],
    "bonos": [
        {"ticker": "^TNX", "nombre": "Bono EE.UU. 10 años"},
        {"ticker": "ES10Y", "nombre": "Bono España 10 años"},
        {"ticker": "FR10Y", "nombre": "Bono Francia 10 años"},
        {"ticker": "DE10Y", "nombre": "Bono Alemania 10 años"},
        {"ticker": "EURIBOR12M", "nombre": "Euríbor 12 meses"},
    ],
    "empresas": [
        {"ticker": "CABK.MC", "nombre": "CaixaBank", "grupo": "Bancos"},
        {"ticker": "NTGY.MC", "nombre": "Naturgy", "grupo": "Utilities"},
        {"ticker": "ENG.MC", "nombre": "Enagás", "grupo": "Utilities"},
        {"ticker": "RED.MC", "nombre": "Redeia", "grupo": "Utilities"},
        {"ticker": "TEF.MC", "nombre": "Telefónica", "grupo": "Tecnológicas"},
        {"ticker": "SPCX", "nombre": "SpaceX", "grupo": "Tecnológicas"},
        {"ticker": "ANT", "nombre": "Anthropic", "grupo": "Tecnológicas"},
    ],
}

# Bonos y tipos que no vienen de Yahoo: id -> (fuente, código, nombre)
CATALOGO = {
    "ES10Y": ("bde", "DPUG0B1F0ZP", "Bono España 10 años"),
    "DE10Y": ("bde", "DPUDNBBN308", "Bono Alemania 10 años"),
    "FR10Y": ("stooq", "10yfry.b", "Bono Francia 10 años"),
    "EURIBOR1M": ("bce", "EURIBOR1MD_", "Euríbor 1 mes"),
    "EURIBOR3M": ("bce", "EURIBOR3MD_", "Euríbor 3 meses"),
    "EURIBOR6M": ("bce", "EURIBOR6MD_", "Euríbor 6 meses"),
    "EURIBOR12M": ("bce", "EURIBOR1YD_", "Euríbor 12 meses"),
}

# Moneda según el sufijo del ticker (si no hay sufijo: dólares)
SUFIJOS_MONEDA = {
    "MC": "EUR", "LS": "EUR", "MI": "EUR", "PA": "EUR", "DE": "EUR", "AS": "EUR", "BR": "EUR",
    "HE": "EUR", "L": "GBp", "SW": "CHF", "CO": "DKK", "ST": "SEK", "OL": "NOK", "SR": "SAR",
    "KS": "KRW", "T": "JPY", "HK": "HKD", "MX": "MXN",
}


def moneda_de(ticker):
    return SUFIJOS_MONEDA.get(ticker.rsplit(".", 1)[1], "USD") if "." in ticker else "USD"


def leer_config():
    try:
        c = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
    except Exception:
        c = None
    if isinstance(c, list):  # formato antiguo: solo la lista de empresas
        c = {"empresas": c}
    if not isinstance(c, dict):
        c = {}
    cfg = {}
    for clave, defecto in DEFECTO.items():
        lista = c.get(clave)
        cfg[clave] = ([e for e in lista if isinstance(e, dict) and e.get("ticker")]
                      if isinstance(lista, list) else defecto)
    return cfg


CFG = leer_config()
NOMBRES, YAHOO = {}, {}
BONOS_BDE, BONOS_STOOQ, EURIBOR_BCE = {}, {}, {}
DESTINO = {"bde": BONOS_BDE, "stooq": BONOS_STOOQ, "bce": EURIBOR_BCE}
for _clave, _lista in CFG.items():
    for _e in _lista:
        _t = _e["ticker"]
        if _clave == "bonos" and _t in CATALOGO:
            _fuente, _codigo, _nom = CATALOGO[_t]
            NOMBRES[_t] = _e.get("nombre") or _nom
            DESTINO[_fuente][_t] = (_codigo, NOMBRES[_t])
        else:
            NOMBRES[_t] = _e.get("nombre") or _t
            YAHOO[_t] = NOMBRES[_t]
YAHOO.update(CAMBIO)
NOMBRES.update(CAMBIO)

EMPRESAS = {e["ticker"]: NOMBRES[e["ticker"]] for e in CFG["empresas"]}
GRUPO_DE = {e["ticker"]: e.get("grupo") or "Otras" for e in CFG["empresas"]}
ORDEN_EMPRESAS = [t for g in dict.fromkeys(GRUPO_DE.values()) for t in EMPRESAS if GRUPO_DE[t] == g]
CONVERTIBLES = [e["ticker"] for e in CFG["activos"]]  # se pueden ver en euros o en dólares
MONEDA = {t: "USD" for t in CONVERTIBLES}
MONEDA.update({t: moneda_de(t) for t in EMPRESAS})

# (nombre de la sección, indicadores, ¿son tipos de interés?, ¿admite euros/dólares?)
SECCIONES = [
    ("Activos", [*CONVERTIBLES, *CAMBIO], False, True),
    ("Índices bursátiles", [e["ticker"] for e in CFG["bursatiles"]], False, False),
    ("Bonos y tipos", [e["ticker"] for e in CFG["bonos"]], True, False),
    ("Empresas", ORDEN_EMPRESAS, False, False),
]
# --------------------------------------------------------------------------

DATA = Path("data")
DATA.mkdir(exist_ok=True)
CSV = DATA / "historico.csv"
JSON = DATA / "ultimo.json"
AVISOS = DATA / "avisos.json"
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


TIPOS_SUGERENCIA = {"activos": ("FUTURE", "CRYPTOCURRENCY"), "bursatiles": ("INDEX",),
                    "empresas": ("EQUITY", "ETF")}


def sugerencias(nombre, clave):
    """Posibles tickers para un nombre (búsqueda de Yahoo Finance)."""
    tipos = TIPOS_SUGERENCIA.get(clave)
    if not tipos:
        return []
    try:
        res = yf.Search(nombre, max_results=8, news_count=0).quotes
        return [{"symbol": q["symbol"],
                 "nombre": q.get("shortname") or q.get("longname") or q["symbol"],
                 "bolsa": q.get("exchDisp", "")}
                for q in res if q.get("symbol") and q.get("quoteType") in tipos][:4]
    except Exception:
        return []


def avisos(datos):
    """Indicadores de la lista de los que no se han podido obtener datos."""
    sin = {}
    for clave, lista in CFG.items():
        for e in lista:
            t = e["ticker"]
            if t in datos and datos[t].dropna().shape[0] >= 2:
                continue
            propio = clave == "bonos" and t in CATALOGO
            sin[t] = {"nombre": NOMBRES.get(t, t), "seccion": clave,
                      "sugerencias": [] if propio else sugerencias(NOMBRES.get(t, t), clave)}
            if clave == "empresas":
                sin[t]["grupo"] = GRUPO_DE.get(t, "Otras")
    return sin


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
                "grupo": GRUPO_DE.get(s, "Otras"),
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
    if BONOS_BDE:
        partes.append(intentar("Banco de España (bonos ES y DE)", bde, BONOS_BDE))
    for simbolo, (codigo, _) in BONOS_STOOQ.items():
        partes.append(intentar(simbolo, stooq, codigo, simbolo))
    for simbolo, (clave, _) in EURIBOR_BCE.items():
        partes.append(intentar(simbolo, bce, clave, simbolo))

    for p in partes:
        if not p.empty:
            datos = p if datos.empty else p.combine_first(datos)

    # Quita las columnas de lo que ya no está en la lista
    datos = datos[[c for c in datos.columns if c in NOMBRES]]
    datos = datos.sort_index().round(4)
    datos.to_csv(CSV)
    JSON.write_text(json.dumps(resumen(datos), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    AVISOS.write_text(json.dumps({"sin_datos": avisos(datos)}, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(f"Histórico actualizado: {len(datos)} días, {datos.shape[1]} indicadores.")


if __name__ == "__main__":
    main()
