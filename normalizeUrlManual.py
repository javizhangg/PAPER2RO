# normalizeUrlManual.py
# Normaliza las URLs del Excel manual UrlManual.xlsx
# NO elimina ninguna URL.
# Entrada: Benchmark/UrlManual.xlsx
# Salida:  Benchmark/UrlManual_normalized.xlsx

import re
import pandas as pd
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, unquote


# ==============================
# RUTAS
# ==============================

BASE_DIR = Path(__file__).resolve().parent

INPUT_EXCEL = BASE_DIR / "Benchmark" / "UrlManual.xlsx"
OUTPUT_EXCEL = BASE_DIR / "Benchmark" / "UrlManual_normalized.xlsx"


# ==============================
# CONFIGURACIÓN
# ==============================

DATA_EXTENSIONS = {
    ".csv", ".tsv", ".json", ".xml", ".rdf",
    ".xlsx", ".xls", ".parquet", ".h5", ".hdf5",
    ".zip", ".gz", ".tar", ".tgz", ".7z",
    ".pkl", ".pickle", ".npy", ".npz",
    ".db", ".sqlite", ".sqlite3"
}

TRACKING_PARAMS_PREFIXES = ("utm_",)

TRACKING_PARAMS_EXACT = {
    "fbclid", "gclid", "dclid", "mc_cid", "mc_eid",
    "igshid", "ref", "ref_src", "source", "spm",
    "campaign", "medium", "term", "content",
    "sessionid", "phpsessid"
}

DOI_REGEX = re.compile(
    r'(?:https?://(?:dx\.)?doi\.org/|doi:)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)',
    re.IGNORECASE
)


# ==============================
# LIMPIEZA BÁSICA
# ==============================

def clean_url(url: str) -> str:
    if pd.isna(url):
        return ""

    url = str(url).strip()

    if not url:
        return ""

    url = url.replace("\u200b", "")
    url = url.replace("\ufeff", "")

    url = url.replace("&gt;", "")
    url = url.replace("&lt;", "")
    url = url.replace("&amp;", "&")

    url = re.sub(r"\s+", "", url)

    url = url.strip("<>()[]{}\"'")
    url = url.rstrip(".,;:!?)]}>\"'•·")

    return url


def remove_trailing_garbage(url: str) -> str:
    if not url:
        return ""

    old = None

    while old != url:
        old = url

        url = url.strip()
        url = url.strip("<>()[]{}\"'")
        url = url.rstrip(".,;:!?)]}>\"'•·")

        url = re.sub(r'/\.[A-Za-z].*$', '', url)

        url = re.sub(
            r'\.(Second|First|Third|The|This|These|Figure|Table|Section|Appendix|Related|Introducing|Rethinking|Towards|Exploring|Using|From|With|And|For).*$',
            '',
            url,
            flags=re.IGNORECASE
        )

        url = re.sub(r'\.Ac-?cessed:?.*$', '', url, flags=re.IGNORECASE)

        url = re.sub(
            r'(Accessed|Retrieved|Available\s*at|Available\s*from).*$',
            '',
            url,
            flags=re.IGNORECASE
        )

        url = re.sub(r'\.[A-Z][A-Za-z-]{8,}.*$', '', url)

        url = re.sub(r'(?<=\d)\.[A-Z][A-Za-z]+.*$', '', url)

        url = re.sub(r',\d{4}.*$', '', url)

    return url


# ==============================
# NORMALIZADORES ESPECIALES
# ==============================

def normalize_doi(url: str) -> str:
    if not url:
        return ""

    match = DOI_REGEX.search(url)

    if not match:
        return url

    doi = match.group(1)
    doi = doi.strip()
    doi = doi.rstrip(".,;:!?)]}>\"'•·")

    return f"https://doi.org/{doi}"


def normalize_arxiv(parsed):
    path = parsed.path.strip("/")

    m = re.match(
        r"(abs|pdf)/([0-9]{4}\.[0-9]{4,5})(v\d+)?(?:\.pdf)?",
        path,
        re.IGNORECASE
    )

    if not m:
        return None

    arxiv_id = m.group(2)
    version = m.group(3) or ""

    return f"https://arxiv.org/abs/{arxiv_id}{version}"


def normalize_github(parsed):
    path = parsed.path.rstrip("/")

    return urlunparse((
        "https",
        "github.com",
        path,
        "",
        "",
        ""
    ))


# ==============================
# VALIDACIÓN Y NORMALIZACIÓN
# ==============================

def is_valid_domain(netloc: str) -> bool:
    if not netloc:
        return False

    netloc = netloc.lower().strip()

    if "@" in netloc:
        netloc = netloc.split("@")[-1]

    if ":" in netloc:
        netloc = netloc.split(":")[0]

    if "." not in netloc:
        return False

    if netloc.startswith(".") or netloc.endswith("."):
        return False

    return True


def normalize_url(url: str) -> str:
    url = clean_url(url)
    url = remove_trailing_garbage(url)

    if not url:
        return ""

    low = url.lower()

    # DOI
    if (
        low.startswith("10.")
        or low.startswith("doi:")
        or "doi.org/" in low
        or "dx.doi.org/" in low
    ):
        return normalize_doi(url)

    # Si empieza por www., añadir esquema
    if low.startswith("www."):
        url = "https://" + url

    parsed = urlparse(url)

    # Si no tiene esquema, añadir https por defecto
    if not parsed.scheme:
        parsed = urlparse("https://" + url)

    scheme = parsed.scheme.lower()

    if scheme not in {"http", "https", "ftp"}:
        return ""

    netloc = parsed.netloc.lower()

    if not netloc:
        return ""

    # Quitar www.
    if netloc.startswith("www."):
        netloc = netloc[4:]

    # Quitar puertos típicos
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]

    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]

    if not is_valid_domain(netloc):
        return ""

    # Normalizar path
    path = unquote(parsed.path or "")
    path = re.sub(r"/{2,}", "/", path)

    if path != "/":
        path = path.rstrip("/")

    fake_parsed = parsed._replace(
        scheme=scheme,
        netloc=netloc,
        path=path
    )

    # arXiv: /pdf/xxxx.pdf -> /abs/xxxx
    if netloc == "arxiv.org":
        arxiv_norm = normalize_arxiv(fake_parsed)
        if arxiv_norm:
            return arxiv_norm

    # GitHub: siempre https://github.com/...
    if netloc == "github.com":
        return normalize_github(fake_parsed)

    # Limpiar parámetros basura, pero mantener parámetros útiles
    query_params = []

    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        klow = k.lower()

        if klow in TRACKING_PARAMS_EXACT:
            continue

        if any(klow.startswith(prefix) for prefix in TRACKING_PARAMS_PREFIXES):
            continue

        query_params.append((k, v))

    query_params.sort()
    query = urlencode(query_params, doseq=True)

    normalized = urlunparse((
        scheme,
        netloc,
        path,
        "",
        query,
        ""
    ))

    return normalized


# ==============================
# FUNCIONES AUXILIARES
# ==============================

def dedup_key(url: str) -> str:
    """
    Solo crea una clave para SABER si dos URLs normalizadas son iguales.
    NO se usa para eliminar filas.
    """
    if not url:
        return ""

    parsed = urlparse(url)

    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/").lower()
    query = parsed.query.lower()

    return f"{netloc}{path}?{query}"


def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def get_extension(url: str) -> str:
    try:
        path = urlparse(url).path.lower()
        m = re.search(r"(\.[a-z0-9]+)$", path)

        if m:
            return m.group(1)

    except Exception:
        pass

    return ""


def is_data_extension(ext: str) -> bool:
    return ext in DATA_EXTENSIONS


def detect_url_column(df):
    """
    Detecta automáticamente la columna donde están las URLs.
    En tu Excel concreto debería ser: 'URL detectada'
    """

    possible_columns = [
        "URL detectada",
        "Url detectada",
        "url detectada",
        "URL Detectada",
        "URL DETECTADA",

        "url",
        "URL",
        "Url",
        "link",
        "Link",
        "enlace",
        "Enlace",
        "original_url",
        "Original URL"
    ]

    for col in possible_columns:
        if col in df.columns:
            return col

    raise ValueError(
        "No se ha encontrado una columna de URLs. "
        "En tu Excel la columna debería llamarse 'URL detectada'."
    )


# ==============================
# MAIN
# ==============================

def main():
    if not INPUT_EXCEL.exists():
        print(f"No existe el archivo: {INPUT_EXCEL}")
        return

    print(f"Leyendo: {INPUT_EXCEL}")

    df = pd.read_excel(INPUT_EXCEL)

    print("Columnas encontradas:")
    print(df.columns.tolist())

    url_column = detect_url_column(df)

    print(f"Columna de URLs detectada: {url_column}")

    # Guardamos URL original
    df["original_url"] = df[url_column]

    # Normalizamos, pero NO eliminamos ninguna fila
    df["normalized_url"] = df[url_column].apply(normalize_url)

    # Información auxiliar
    df["domain"] = df["normalized_url"].apply(get_domain)
    df["extension"] = df["normalized_url"].apply(get_extension)
    df["is_data_extension"] = df["extension"].apply(is_data_extension)

    # Clave de comparación exacta, solo informativa
    df["dedup_key"] = df["normalized_url"].apply(dedup_key)

    # Marcamos duplicados exactos, pero NO los eliminamos
    df["is_duplicate_exact"] = df.duplicated(
        subset=["dedup_key"],
        keep="first"
    )

    # Estado de normalización
    df["normalization_status"] = df["normalized_url"].apply(
        lambda x: "invalid_or_empty" if not x else "ok"
    )

    # Guardar Excel manteniendo todas las filas
    df.to_excel(OUTPUT_EXCEL, index=False)

    total = len(df)
    valid = len(df[df["normalization_status"] == "ok"])
    invalid = len(df[df["normalization_status"] == "invalid_or_empty"])
    duplicates = len(df[df["is_duplicate_exact"] == True])

    print("\nNormalización terminada.")
    print(f"URLs totales: {total}")
    print(f"URLs válidas: {valid}")
    print(f"URLs inválidas o vacías: {invalid}")
    print(f"URLs duplicadas exactas detectadas, NO eliminadas: {duplicates}")

    print("\nArchivo guardado en:")
    print(OUTPUT_EXCEL)


if __name__ == "__main__":
    main()