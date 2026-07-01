import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, unquote
from difflib import SequenceMatcher

import pandas as pd


# ==============================
# RUTAS
# ==============================

BASE_DIR = Path(__file__).resolve().parent

INPUT_EXCEL = BASE_DIR / "Benchmark" / "UrlManual.xlsx"
if not INPUT_EXCEL.exists():
    INPUT_EXCEL = BASE_DIR / "UrlManual.xlsx"

OUTPUT_EXCEL = BASE_DIR / "Benchmark" / "UrlManual_normalized.xlsx"
REMOVED_EXCEL = BASE_DIR / "Benchmark" / "UrlManual_removed.xlsx"


# ==============================
# CONFIGURACION
# ==============================

REMOVE_RAW_TEI = True
REMOVE_STRUCTURED_DOI = False
REMOVE_SIMILAR_URLS = True
SIMILARITY_THRESHOLD = 0.90

# True = si la misma URL aparece en varios PDFs, se queda solo la primera vez.
# False = conserva una URL por cada PDF.
GLOBAL_DEDUPLICATION = False

TRACKING_PARAMS_PREFIXES = ("utm_",)

TRACKING_PARAMS_EXACT = {
    "fbclid", "gclid", "dclid", "mc_cid", "mc_eid", "igshid",
    "ref", "ref_src", "source", "spm", "campaign", "medium",
    "term", "content", "sessionid", "phpsessid",
}

INTERNAL_GROBID_DOMAINS = {
    "www.tei-c.org", "tei-c.org", "www.w3.org", "w3.org",
}

DOI_REGEX = re.compile(
    r"(?:https?://(?:dx\.)?doi\.org/|doi:)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)",
    re.IGNORECASE,
)


# ==============================
# LIMPIEZA BASICA
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
# NORMALIZACION SUAVE
# ==============================

def normalize_doi(url: str) -> str:
    match = DOI_REGEX.search(url)

    if not match:
        return ""

    doi = match.group(1).strip().rstrip(".,;:!?)]}>\"'•·")
    return f"https://doi.org/{doi}"


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
    """
    Normalizacion suave:
    - si no hay esquema, añade https:// porque si no urlparse no detecta dominio;
    - si ya tiene http o https, respeta el esquema original;
    - no fuerza www;
    - limpia path, barra final y parametros basura.
    """
    url = clean_url(url)
    url = remove_trailing_garbage(url)

    if not url:
        return ""

    low = url.lower()

    if low.startswith("10.") or low.startswith("doi:") or "doi.org/" in low or "dx.doi.org/" in low:
        return normalize_doi(url)

    if low.startswith("www."):
        url = "https://" + url

    parsed = urlparse(url)

    if not parsed.scheme:
        parsed = urlparse("https://" + url)

    scheme = parsed.scheme.lower()

    if scheme not in {"http", "https", "ftp"}:
        return ""

    netloc = parsed.netloc.lower().strip()

    if netloc.endswith(":80"):
        netloc = netloc[:-3]

    if netloc.endswith(":443"):
        netloc = netloc[:-4]

    if not is_valid_domain(netloc):
        return ""

    path = unquote(parsed.path or "")
    path = re.sub(r"/{2,}", "/", path)

    if path != "/":
        path = path.rstrip("/")

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

    return urlunparse((scheme, netloc, path, "", query, ""))


# ==============================
# DATASET LABEL
# ==============================

def normalize_text_for_label(value: str) -> str:
    if value is None or pd.isna(value):
        return ""

    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text


def parse_dataset_value(value) -> bool:
    text = normalize_text_for_label(value)

    if not text:
        return False

    true_values = {
        "si", "sí", "yes", "true", "1", "dataset", "data", "datos",
        "positive", "positivo", "parece dataset",
    }

    false_values = {
        "no", "false", "0", "not_dataset", "not dataset", "negative",
        "negativo", "codigo", "código", "web", "doi", "paper", "referencia",
    }

    if text in true_values:
        return True

    if text in false_values:
        return False

    if text.startswith("si"):
        return True

    if text.startswith("no"):
        return False

    return False


# ==============================
# DEDUPLICACION Y SIMILITUD
# ==============================

def is_internal_grobid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        return domain in INTERNAL_GROBID_DOMAINS
    except Exception:
        return False


def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def dedup_key(url: str, pdf: str = "") -> str:
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

    domain1 = p1.netloc.lower().replace("www.", "")
    domain2 = p2.netloc.lower().replace("www.", "")

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


def simplicity_score(url: str) -> tuple:
    parsed = urlparse(url)
    query_count = len(parse_qsl(parsed.query, keep_blank_values=True))
    path_len = len(parsed.path.strip("/").split("/")) if parsed.path.strip("/") else 0
    return (query_count, path_len, len(url))


def choose_simpler_url(url_a: str, url_b: str) -> str:
    return url_a if simplicity_score(url_a) <= simplicity_score(url_b) else url_b


# ==============================
# COLUMNAS
# ==============================

def detect_url_column(df):
    possible_columns = [
        "URL detectada", "Url detectada", "url detectada", "URL Detectada",
        "URL DETECTADA", "url", "URL", "Url", "link", "Link",
        "enlace", "Enlace", "original_url", "Original URL",
        "normalized_url", "link_normalized",
    ]

    for col in possible_columns:
        if col in df.columns:
            return col

    raise ValueError("No se ha encontrado una columna de URLs. Usa 'url', 'URL', 'link' o similar.")


def detect_pdf_column(df):
    possible_columns = [
        "pdf", "PDF", "paper", "Paper", "filename", "Filename",
        "file", "File", "archivo", "Archivo", "document", "Document",
    ]

    for col in possible_columns:
        if col in df.columns:
            return col

    return None


def detect_dataset_column(df):
    possible_columns = [
        "es_dataset", "Es dataset", "dataset", "Dataset", "¿Parece dataset?",
        "Parece dataset", "parece dataset", "is_dataset", "Is dataset",
        "heuristica", "Heuristica",
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
# PROCESAMIENTO
# ==============================

def process_dataframe(df: pd.DataFrame, url_column: str, pdf_column: str | None, dataset_column: str | None):
    kept_rows = []
    removed_rows = []
    seen = {}

    for _, row in df.iterrows():
        raw_url = row.get(url_column, "")
        raw_url = "" if pd.isna(raw_url) else str(raw_url).strip()

        section = get_optional_column(
            row,
            ["section", "Section", "seccion", "Seccion", "sección", "Sección"],
            "",
        ).lower()

        if pdf_column:
            pdf_name = row.get(pdf_column, "")
            pdf_name = "" if pd.isna(pdf_name) else str(pdf_name).strip()
        else:
            pdf_name = ""

        if dataset_column:
            dataset_value = row.get(dataset_column, "")
        else:
            dataset_value = ""

        original_data = row.to_dict()

        if REMOVE_RAW_TEI and section == "raw_tei":
            removed_row = dict(original_data)
            removed_row.update({"pdf": pdf_name, "original_url": raw_url, "normalized_url": "", "duplicate_of": "", "removal_reason": "raw_tei_fallback_noise"})
            removed_rows.append(removed_row)
            continue

        if REMOVE_STRUCTURED_DOI and section == "doi":
            removed_row = dict(original_data)
            removed_row.update({"pdf": pdf_name, "original_url": raw_url, "normalized_url": "", "duplicate_of": "", "removal_reason": "grobid_structured_doi_not_manual_url"})
            removed_rows.append(removed_row)
            continue

        norm = normalize_url(raw_url)

        if not norm:
            removed_row = dict(original_data)
            removed_row.update({"pdf": pdf_name, "original_url": raw_url, "normalized_url": "", "duplicate_of": "", "removal_reason": "empty_or_invalid_after_normalization"})
            removed_rows.append(removed_row)
            continue

        if is_internal_grobid_url(norm):
            removed_row = dict(original_data)
            removed_row.update({"pdf": pdf_name, "original_url": raw_url, "normalized_url": norm, "duplicate_of": "", "removal_reason": "internal_grobid_or_xml_url"})
            removed_rows.append(removed_row)
            continue

        key = dedup_key(norm, pdf_name)

        if key in seen:
            removed_row = dict(original_data)
            removed_row.update({"pdf": pdf_name, "original_url": raw_url, "normalized_url": norm, "duplicate_of": seen[key], "removal_reason": "duplicate_exact"})
            removed_rows.append(removed_row)
            continue

        similar_index = None
        similar_url = ""

        if REMOVE_SIMILAR_URLS:
            for idx, kept_row in enumerate(kept_rows):
                existing_url = kept_row["url"]

                if are_urls_similar(norm, existing_url):
                    similar_index = idx
                    similar_url = existing_url
                    break

        if similar_index is not None:
            simpler = choose_simpler_url(norm, similar_url)

            if simpler == norm:
                old = kept_rows[similar_index]
                kept_rows[similar_index] = {"pdf": pdf_name, "url": norm, "es_dataset": parse_dataset_value(dataset_value)}
                removed_row = dict(original_data)
                removed_row.update({"pdf": old.get("pdf", ""), "original_url": old.get("url", ""), "normalized_url": old.get("url", ""), "duplicate_of": norm, "removal_reason": "duplicate_similar_replaced_by_simpler"})
                removed_rows.append(removed_row)
            else:
                removed_row = dict(original_data)
                removed_row.update({"pdf": pdf_name, "original_url": raw_url, "normalized_url": norm, "duplicate_of": similar_url, "removal_reason": "duplicate_similar"})
                removed_rows.append(removed_row)

            continue

        seen[key] = norm
        kept_rows.append({"pdf": pdf_name, "url": norm, "es_dataset": parse_dataset_value(dataset_value)})

    kept_df = pd.DataFrame(kept_rows, columns=["pdf", "url", "es_dataset"])
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
    dataset_column = detect_dataset_column(df)

    print(f"Columna de URLs detectada: {url_column}")
    print(f"Columna de PDF detectada: {pdf_column if pdf_column else 'NO detectada'}")
    print(f"Columna dataset detectada: {dataset_column if dataset_column else 'NO detectada'}")

    total_original = len(df)
    kept_df, removed_df = process_dataframe(df, url_column, pdf_column, dataset_column)

    OUTPUT_EXCEL.parent.mkdir(parents=True, exist_ok=True)
    REMOVED_EXCEL.parent.mkdir(parents=True, exist_ok=True)

    kept_df.to_excel(OUTPUT_EXCEL, index=False)
    removed_df.to_excel(REMOVED_EXCEL, index=False)

    print("\nNormalizacion terminada.")
    print(f"URLs originales: {total_original}")
    print(f"URLs conservadas: {len(kept_df)}")
    print(f"URLs eliminadas: {len(removed_df)}")

    if len(removed_df) > 0 and "removal_reason" in removed_df.columns:
        print("\nResumen de eliminadas:")
        print(removed_df["removal_reason"].value_counts())

    print("\nArchivos guardados:")
    print(f"- {OUTPUT_EXCEL}")
    print(f"- {REMOVED_EXCEL}")


if __name__ == "__main__":
    main()
