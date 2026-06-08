# normalizeUrlManual.py
# Normaliza las URLs del Excel manual UrlManual.xlsx
#
# Entrada:
#   Benchmark/UrlManual.xlsx
#
# Salidas:
#   Benchmark/UrlManual_normalized.xlsx
#   Benchmark/UrlManual_removed.xlsx
#
# Excel final:
#   pdf | url
#
# Hace:
# - limpia URLs
# - fuerza formato https://www.
# - normaliza Kaggle, DOI, arXiv, GitHub
# - elimina URLs inválidas
# - elimina ruido GROBID/XML
# - elimina duplicados exactos
# - elimina URLs parecidas

import re
import pandas as pd
from pathlib import Path
from urllib.parse import (
    urlparse,
    urlunparse,
    parse_qsl,
    urlencode,
    unquote,
)
from difflib import SequenceMatcher


# ==============================
# RUTAS
# ==============================

BASE_DIR = Path(__file__).resolve().parent

INPUT_EXCEL = BASE_DIR / "Benchmark" / "UrlManual.xlsx"

OUTPUT_EXCEL = BASE_DIR / "Benchmark" / "UrlManual_normalized.xlsx"
REMOVED_EXCEL = BASE_DIR / "Benchmark" / "UrlManual_removed.xlsx"


# ==============================
# CONFIGURACIÓN
# ==============================

REMOVE_RAW_TEI = True
REMOVE_STRUCTURED_DOI = False

REMOVE_SIMILAR_URLS = True

# 0.90 = equilibrado
# 0.85 = más agresivo eliminando parecidas
# 0.95 = más conservador
SIMILARITY_THRESHOLD = 0.90

# True = si la misma URL aparece en varios PDFs, se queda solo la primera vez.
# False = conserva una URL una vez por cada PDF.
GLOBAL_DEDUPLICATION = True


TRACKING_PARAMS_PREFIXES = ("utm_",)

TRACKING_PARAMS_EXACT = {
    "fbclid",
    "gclid",
    "dclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "ref",
    "ref_src",
    "source",
    "spm",
    "campaign",
    "medium",
    "term",
    "content",
    "sessionid",
    "phpsessid",
}


INTERNAL_GROBID_DOMAINS = {
    "www.tei-c.org",
    "tei-c.org",
    "www.w3.org",
    "w3.org",
}


DOI_REGEX = re.compile(
    r"(?:https?://(?:dx\.)?doi\.org/|doi:)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)",
    re.IGNORECASE,
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
    url = url.replace("\u00ad", "")

    url = url.replace("&gt;", "")
    url = url.replace("&lt;", "")
    url = url.replace("&amp;", "&")

    url = re.sub(r"\s+", "", url)

    url = url.strip("<>()[]{}\"'")
    url = url.rstrip(".,;:!?)]}>\"'•·")

    return url


def remove_trailing_garbage(url: str) -> str:
    """
    Quita basura típica pegada al final por extracción de PDF/GROBID.
    """
    if not url:
        return ""

    old = None

    while old != url:
        old = url

        url = url.strip()
        url = url.strip("<>()[]{}\"'")
        url = url.rstrip(".,;:!?)]}>\"'•·")

        url = re.sub(r"/\.[A-Za-z].*$", "", url)

        url = re.sub(
            r"\.(Second|First|Third|The|This|These|Figure|Table|Section|Appendix|Related|Introducing|Rethinking|Towards|Exploring|Using|From|With|And|For).*$",
            "",
            url,
            flags=re.IGNORECASE,
        )

        url = re.sub(r"\.Ac-?cessed:?.*$", "", url, flags=re.IGNORECASE)

        url = re.sub(
            r"(Accessed|Retrieved|Available\s*at|Available\s*from).*$",
            "",
            url,
            flags=re.IGNORECASE,
        )

        url = re.sub(r"\.[A-Z][A-Za-z-]{8,}.*$", "", url)

        url = re.sub(r"(?<=\d)\.[A-Z][A-Za-z]+.*$", "", url)

        url = re.sub(r",\d{4}.*$", "", url)

    return url


# ==============================
# NORMALIZACIÓN ESPECIAL
# ==============================

def add_www_to_domain(netloc: str) -> str:
    """
    Fuerza que el dominio tenga www.
    Ejemplo:
      kaggle.com -> www.kaggle.com
      github.com -> www.github.com
      doi.org -> www.doi.org
      zenodo.org -> www.zenodo.org
    """
    netloc = netloc.lower().strip()

    if netloc.startswith("www."):
        return netloc

    return "www." + netloc


def remove_www_from_domain(netloc: str) -> str:
    """
    Quita www. para validar y comparar internamente.
    """
    netloc = netloc.lower().strip()

    if netloc.startswith("www."):
        return netloc[4:]

    return netloc


def normalize_doi(url: str) -> str:
    if not url:
        return ""

    match = DOI_REGEX.search(url)

    if not match:
        return ""

    doi = match.group(1)
    doi = doi.strip()
    doi = doi.rstrip(".,;:!?)]}>\"'•·")

    return f"https://www.doi.org/{doi}"


def normalize_arxiv(parsed) -> str | None:
    path = parsed.path.strip("/")

    m = re.match(
        r"(abs|pdf)/([0-9]{4}\.[0-9]{4,5})(v\d+)?(?:\.pdf)?",
        path,
        re.IGNORECASE,
    )

    if not m:
        return None

    arxiv_id = m.group(2)
    version = m.group(3) or ""

    return f"https://www.arxiv.org/abs/{arxiv_id}{version}"


def normalize_github(parsed) -> str:
    path = parsed.path.rstrip("/")

    return urlunparse((
        "https",
        "www.github.com",
        path,
        "",
        "",
        "",
    ))


def normalize_kaggle(parsed) -> str:
    """
    Normaliza Kaggle al formato:
    https://www.kaggle.com/datasets/usuario/dataset
    """
    path = unquote(parsed.path or "")
    path = re.sub(r"/{2,}", "/", path)

    if path != "/":
        path = path.rstrip("/")

    # Quitar sufijos que suelen apuntar al mismo dataset
    path = re.sub(r"/code$", "", path, flags=re.IGNORECASE)
    path = re.sub(r"/data$", "", path, flags=re.IGNORECASE)
    path = re.sub(r"/metadata$", "", path, flags=re.IGNORECASE)
    path = re.sub(r"/download$", "", path, flags=re.IGNORECASE)

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

    return urlunparse((
        "https",
        "www.kaggle.com",
        path,
        "",
        query,
        "",
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


def is_internal_grobid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        return domain in INTERNAL_GROBID_DOMAINS
    except Exception:
        return False


def normalize_url(url: str) -> str:
    url = clean_url(url)
    url = remove_trailing_garbage(url)

    if not url:
        return ""

    low = url.lower()

    # DOI puro o DOI con doi.org
    if (
        low.startswith("10.")
        or low.startswith("doi:")
        or "doi.org/" in low
        or "dx.doi.org/" in low
    ):
        return normalize_doi(url)

    # www.ejemplo.com -> https://www.ejemplo.com
    if low.startswith("www."):
        url = "https://" + url

    parsed = urlparse(url)

    # ejemplo.com/path -> https://ejemplo.com/path
    if not parsed.scheme:
        parsed = urlparse("https://" + url)

    scheme = parsed.scheme.lower()

    if scheme not in {"http", "https", "ftp"}:
        return ""

    netloc = parsed.netloc.lower()

    if not netloc:
        return ""

    # Quitar puertos típicos
    if netloc.endswith(":80"):
        netloc = netloc[:-3]

    if netloc.endswith(":443"):
        netloc = netloc[:-4]

    # Dominio base sin www para validar y detectar especiales
    base_netloc = remove_www_from_domain(netloc)

    if not is_valid_domain(base_netloc):
        return ""

    # Forzar siempre https y www
    final_netloc = add_www_to_domain(base_netloc)

    path = unquote(parsed.path or "")
    path = re.sub(r"/{2,}", "/", path)

    if path != "/":
        path = path.rstrip("/")

    fake_parsed = parsed._replace(
        scheme="https",
        netloc=final_netloc,
        path=path,
    )

    # Kaggle
    if base_netloc == "kaggle.com":
        return normalize_kaggle(fake_parsed)

    # arXiv
    if base_netloc == "arxiv.org":
        arxiv_norm = normalize_arxiv(fake_parsed)
        if arxiv_norm:
            return arxiv_norm

    # GitHub
    if base_netloc == "github.com":
        return normalize_github(fake_parsed)

    # Limpiar parámetros basura
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
        "https",
        final_netloc,
        path,
        "",
        query,
        "",
    ))

    return normalized


# ==============================
# DUPLICADOS Y SIMILITUD
# ==============================

def dedup_key(url: str, pdf: str = "") -> str:
    if not url:
        return ""

    parsed = urlparse(url)

    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/").lower()
    query = parsed.query.lower()

    base_key = f"{netloc}{path}?{query}"

    if GLOBAL_DEDUPLICATION:
        return base_key

    return f"{pdf.lower()}::{base_key}"


def simplify_path_for_similarity(path: str) -> str:
    if not path:
        return ""

    path = unquote(path.lower().strip())
    path = re.sub(r"/{2,}", "/", path)

    if path != "/":
        path = path.rstrip("/")

    path = re.sub(r"/index\.(html|htm|php|asp|aspx)$", "", path)

    path = re.sub(r"\.(html|htm|php|asp|aspx|pdf)$", "", path)

    path = re.sub(
        r"/(download|downloads|view|viewer|preview|file|files|full|abstract|record|records|data|metadata|code)$",
        "",
        path,
    )

    path = re.sub(r"[-_]+", "-", path)

    return path.strip("/")


def are_urls_similar(url1: str, url2: str) -> bool:
    p1 = urlparse(url1)
    p2 = urlparse(url2)

    domain1 = p1.netloc.lower()
    domain2 = p2.netloc.lower()

    if domain1 != domain2:
        return False

    path1 = simplify_path_for_similarity(p1.path)
    path2 = simplify_path_for_similarity(p2.path)

    if not path1 or not path2:
        return False

    if path1 == path2:
        return True

    if path1 in path2 or path2 in path1:
        return True

    similarity = SequenceMatcher(None, path1, path2).ratio()

    return similarity >= SIMILARITY_THRESHOLD


def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def find_similar_url(norm: str, kept_urls_by_domain: dict) -> str:
    domain = get_domain(norm)

    for existing_url in kept_urls_by_domain.get(domain, []):
        if are_urls_similar(norm, existing_url):
            return existing_url

    return ""


# ==============================
# DETECTAR COLUMNAS
# ==============================

def detect_url_column(df):
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
        "Original URL",
        "normalized_url",
        "link_normalized",
    ]

    for col in possible_columns:
        if col in df.columns:
            return col

    raise ValueError(
        "No se ha encontrado una columna de URLs. "
        "La columna debería llamarse 'URL detectada', 'url' o 'link'."
    )


def detect_pdf_column(df):
    possible_columns = [
        "pdf",
        "PDF",
        "paper",
        "Paper",
        "filename",
        "Filename",
        "file",
        "File",
        "archivo",
        "Archivo",
        "document",
        "Document",
    ]

    for col in possible_columns:
        if col in df.columns:
            return col

    return None


def get_optional_column(row, possible_names, default=""):
    for name in possible_names:
        if name in row.index:
            value = row.get(name, default)

            if pd.isna(value):
                return default

            return str(value).strip()

    return default


# ==============================
# PROCESAMIENTO PRINCIPAL
# ==============================

def process_dataframe(df: pd.DataFrame, url_column: str, pdf_column: str | None):
    seen = {}
    kept_rows = []
    removed_rows = []

    kept_urls_by_domain = {}

    for index, row in df.iterrows():
        raw_url = row.get(url_column, "")

        if pd.isna(raw_url):
            raw_url = ""

        raw_url = str(raw_url).strip()

        section = get_optional_column(
            row,
            ["section", "Section", "seccion", "Seccion", "sección", "Sección"],
            "",
        ).lower()

        if pdf_column:
            pdf_name = row.get(pdf_column, "")

            if pd.isna(pdf_name):
                pdf_name = ""

            pdf_name = str(pdf_name).strip()
        else:
            pdf_name = ""

        # Mantener datos originales solo para el Excel de eliminadas
        original_data = row.to_dict()

        # 1. Eliminar raw_tei si existe columna section
        if REMOVE_RAW_TEI and section == "raw_tei":
            removed_row = dict(original_data)
            removed_row["pdf"] = pdf_name
            removed_row["original_url"] = raw_url
            removed_row["normalized_url"] = ""
            removed_row["duplicate_of"] = ""
            removed_row["removal_reason"] = "raw_tei_fallback_noise"
            removed_rows.append(removed_row)
            continue

        # 2. Eliminar DOI estructurado si se activa
        if REMOVE_STRUCTURED_DOI and section == "doi":
            removed_row = dict(original_data)
            removed_row["pdf"] = pdf_name
            removed_row["original_url"] = raw_url
            removed_row["normalized_url"] = ""
            removed_row["duplicate_of"] = ""
            removed_row["removal_reason"] = "grobid_structured_doi_not_manual_url"
            removed_rows.append(removed_row)
            continue

        # 3. Normalizar
        norm = normalize_url(raw_url)

        # 4. Eliminar inválidas
        if not norm:
            removed_row = dict(original_data)
            removed_row["pdf"] = pdf_name
            removed_row["original_url"] = raw_url
            removed_row["normalized_url"] = ""
            removed_row["duplicate_of"] = ""
            removed_row["removal_reason"] = "empty_or_invalid_after_normalization"
            removed_rows.append(removed_row)
            continue

        # 5. Eliminar URLs internas XML/GROBID
        if is_internal_grobid_url(norm):
            removed_row = dict(original_data)
            removed_row["pdf"] = pdf_name
            removed_row["original_url"] = raw_url
            removed_row["normalized_url"] = norm
            removed_row["duplicate_of"] = ""
            removed_row["removal_reason"] = "internal_grobid_or_xml_url"
            removed_rows.append(removed_row)
            continue

        # 6. Duplicado exacto
        key = dedup_key(norm, pdf_name)

        if key in seen:
            removed_row = dict(original_data)
            removed_row["pdf"] = pdf_name
            removed_row["original_url"] = raw_url
            removed_row["normalized_url"] = norm
            removed_row["duplicate_of"] = seen[key]
            removed_row["removal_reason"] = "duplicate_exact"
            removed_rows.append(removed_row)
            continue

        # 7. Duplicado parecido
        if REMOVE_SIMILAR_URLS:
            similar_to = find_similar_url(norm, kept_urls_by_domain)

            if similar_to:
                removed_row = dict(original_data)
                removed_row["pdf"] = pdf_name
                removed_row["original_url"] = raw_url
                removed_row["normalized_url"] = norm
                removed_row["duplicate_of"] = similar_to
                removed_row["removal_reason"] = "duplicate_similar"
                removed_rows.append(removed_row)
                continue

        # 8. Conservar solo columnas simples
        seen[key] = norm

        domain = get_domain(norm)
        kept_urls_by_domain.setdefault(domain, []).append(norm)

        kept_rows.append({
            "pdf": pdf_name,
            "url": norm,
        })

    kept_df = pd.DataFrame(kept_rows, columns=["pdf", "url"])
    removed_df = pd.DataFrame(removed_rows)

    return kept_df, removed_df


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
    pdf_column = detect_pdf_column(df)

    print(f"Columna de URLs detectada: {url_column}")

    if pdf_column:
        print(f"Columna de PDF detectada: {pdf_column}")
    else:
        print("No se ha detectado columna de PDF. La columna pdf saldrá vacía.")

    total_original = len(df)

    kept_df, removed_df = process_dataframe(df, url_column, pdf_column)

    kept_df.to_excel(OUTPUT_EXCEL, index=False)
    removed_df.to_excel(REMOVED_EXCEL, index=False)

    total_kept = len(kept_df)
    total_removed = len(removed_df)

    print("\nNormalización terminada.")
    print(f"URLs originales: {total_original}")
    print(f"URLs conservadas: {total_kept}")
    print(f"URLs eliminadas: {total_removed}")

    if total_removed > 0 and "removal_reason" in removed_df.columns:
        print("\nResumen de eliminadas:")
        print(removed_df["removal_reason"].value_counts())

    print("\nArchivos guardados:")
    print(f"- {OUTPUT_EXCEL}")
    print(f"- {REMOVED_EXCEL}")


if __name__ == "__main__":
    main()