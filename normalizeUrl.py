# urlNormalized.py
# Normalizador SUAVE para URLs extraidas automaticamente.
#
# Entrada:
#   outputs/all_links.csv
#
# Salidas:
#   outputs/all_links_normalized.csv
#   outputs/removed_urls.csv
#
# Resultado final:
#   pdf,url
#
# Hace:
# - limpia basura de extraccion
# - NO fuerza www
# - NO cambia http a https si la URL ya venia con http
# - NO cambia dominios salvo casos especiales muy claros como DOI puro
# - elimina parametros de tracking
# - elimina duplicados exactos
# - elimina URLs parecidas
# - conserva la URL mas simple/corta entre URLs parecidas

import csv
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, unquote
from difflib import SequenceMatcher


# ==============================
# RUTAS
# ==============================

INPUT_CSV = "outputs/all_links.csv"
OUTPUT_CSV = "outputs/all_links_normalized.csv"
REMOVED_CSV = "outputs/removed_urls.csv"


# ==============================
# CONFIGURACION
# ==============================

REMOVE_SIMILAR_URLS = True
SIMILARITY_THRESHOLD = 0.90

# True = si la misma URL aparece en varios PDFs, se queda solo la primera vez.
# False = conserva una URL por cada PDF.
GLOBAL_DEDUPLICATION = True

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
    if not url:
        return ""

    url = str(url).strip()

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
    """
    Menor score = URL mas simple.
    Preferimos:
    - menos longitud
    - menos query params
    - path mas corto
    """
    parsed = urlparse(url)
    query_count = len(parse_qsl(parsed.query, keep_blank_values=True))
    path_len = len(parsed.path.strip("/").split("/")) if parsed.path.strip("/") else 0
    return (query_count, path_len, len(url))


def choose_simpler_url(url_a: str, url_b: str) -> str:
    return url_a if simplicity_score(url_a) <= simplicity_score(url_b) else url_b


# ==============================
# CSV
# ==============================

def load_csv(path: str) -> list[dict]:
    rows = []

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            pdf = (row.get("pdf", "") or row.get("paper", "") or row.get("PDF", "") or row.get("Paper", "")).strip()
            url = (row.get("url", "") or row.get("link", "") or row.get("URL", "") or row.get("Link", "")).strip()
            section = (row.get("section", "") or row.get("Section", "")).strip()

            if url:
                rows.append({"pdf": pdf, "url": url, "section": section})

    return rows


def save_csv(rows: list[dict], path: str, fields: list[str]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ==============================
# PROCESAMIENTO
# ==============================

def process_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    kept = []
    removed = []
    seen = {}

    for row in rows:
        pdf = row.get("pdf", "")
        raw_url = row.get("url", "")
        norm = normalize_url(raw_url)

        if not norm:
            removed.append({"pdf": pdf, "original_url": raw_url, "normalized_url": "", "duplicate_of": "", "reason": "invalid_after_normalization"})
            continue

        if is_internal_grobid_url(norm):
            removed.append({"pdf": pdf, "original_url": raw_url, "normalized_url": norm, "duplicate_of": "", "reason": "internal_grobid_or_xml_url"})
            continue

        key = dedup_key(norm, pdf)

        if key in seen:
            removed.append({"pdf": pdf, "original_url": raw_url, "normalized_url": norm, "duplicate_of": seen[key], "reason": "duplicate_exact"})
            continue

        similar_index = None
        similar_url = ""

        if REMOVE_SIMILAR_URLS:
            for idx, kept_row in enumerate(kept):
                existing_url = kept_row["url"]

                if are_urls_similar(norm, existing_url):
                    similar_index = idx
                    similar_url = existing_url
                    break

        if similar_index is not None:
            simpler = choose_simpler_url(norm, similar_url)

            if simpler == norm:
                old = kept[similar_index]
                kept[similar_index] = {"pdf": pdf, "url": norm}
                removed.append({"pdf": old.get("pdf", ""), "original_url": old.get("url", ""), "normalized_url": old.get("url", ""), "duplicate_of": norm, "reason": "duplicate_similar_replaced_by_simpler"})
            else:
                removed.append({"pdf": pdf, "original_url": raw_url, "normalized_url": norm, "duplicate_of": similar_url, "reason": "duplicate_similar"})

            continue

        seen[key] = norm
        kept.append({"pdf": pdf, "url": norm})

    return kept, removed


# ==============================
# MAIN
# ==============================

def main():
    if not Path(INPUT_CSV).exists():
        print(f"No existe el archivo de entrada: {INPUT_CSV}")
        return

    rows = load_csv(INPUT_CSV)

    print(f"URLs originales: {len(rows)}")
    kept, removed = process_rows(rows)
    print(f"URLs conservadas: {len(kept)}")
    print(f"URLs eliminadas: {len(removed)}")

    save_csv(kept, OUTPUT_CSV, ["pdf", "url"])
    save_csv(removed, REMOVED_CSV, ["pdf", "original_url", "normalized_url", "duplicate_of", "reason"])

    print("\nGuardados:")
    print(f"- {OUTPUT_CSV}")
    print(f"- {REMOVED_CSV}")

    if removed:
        counts = {}
        for r in removed:
            reason = r.get("reason", "")
            counts[reason] = counts.get(reason, 0) + 1

        print("\nResumen de eliminadas:")
        for reason, count in counts.items():
            print(f"- {reason}: {count}")


if __name__ == "__main__":
    main()
