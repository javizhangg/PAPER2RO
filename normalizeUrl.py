# normalize_urls.py
# Normaliza URLs extraídas y elimina duplicados exactos + parecidos.
#
# Entrada:
#   outputs/all_links.csv
#
# Salidas:
#   outputs/all_links_normalized.csv
#   outputs/removed_urls.csv
#
# CSV final sencillo:
#   pdf,url
#
# Normalización:
#   kaggle.com/datasets/xxx -> https://www.kaggle.com/datasets/xxx
#   https://kaggle.com/datasets/xxx -> https://www.kaggle.com/datasets/xxx
#   http://github.com/user/repo -> https://www.github.com/user/repo
#   10.xxxx/yyyy -> https://www.doi.org/10.xxxx/yyyy

import csv
import re
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

INPUT_CSV = "outputs/all_links.csv"
OUTPUT_CSV = "outputs/all_links_normalized.csv"
REMOVED_CSV = "outputs/removed_urls.csv"


# ==============================
# CONFIGURACIÓN
# ==============================

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

        # Ruido típico cuando se pega texto después de una URL
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
    Ejemplos:
      kaggle.com -> www.kaggle.com
      zenodo.org -> www.zenodo.org
      github.com -> www.github.com
      doi.org -> www.doi.org
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

    # Quitar sufijos que normalmente apuntan al mismo dataset
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
# NORMALIZACIÓN GENERAL
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

    # DOI puro o doi.org
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

    # Dominio sin www para validar y detectar dominios especiales
    base_netloc = remove_www_from_domain(netloc)

    if not is_valid_domain(base_netloc):
        return ""

    # Forzar https y www para todas las URLs
    scheme = "https"
    final_netloc = add_www_to_domain(base_netloc)

    path = unquote(parsed.path or "")
    path = re.sub(r"/{2,}", "/", path)

    if path != "/":
        path = path.rstrip("/")

    fake_parsed = parsed._replace(
        scheme=scheme,
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

    # Quitar parámetros basura
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
# DUPLICADOS Y SIMILARES
# ==============================

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
# CSV
# ==============================

def load_csv(path: str) -> list[dict]:
    rows = []

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            pdf = (
                row.get("pdf", "")
                or row.get("paper", "")
                or row.get("PDF", "")
                or row.get("Paper", "")
            ).strip()

            url = (
                row.get("url", "")
                or row.get("link", "")
                or row.get("URL", "")
                or row.get("Link", "")
            ).strip()

            section = (
                row.get("section", "")
                or row.get("Section", "")
            ).strip()

            if url:
                rows.append({
                    "pdf": pdf,
                    "url": url,
                    "section": section,
                })

    return rows


def save_csv(rows: list[dict], path: str, fields: list[str]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


# ==============================
# PROCESAMIENTO
# ==============================

def process_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    seen = {}
    kept = []
    removed = []

    kept_urls_by_domain = {}

    for row in rows:
        pdf = row["pdf"]
        raw_url = row["url"]

        norm = normalize_url(raw_url)

        if not norm:
            removed.append({
                "pdf": pdf,
                "original_url": raw_url,
                "normalized_url": "",
                "duplicate_of": "",
                "reason": "invalid_after_normalization",
            })
            continue

        if is_internal_grobid_url(norm):
            removed.append({
                "pdf": pdf,
                "original_url": raw_url,
                "normalized_url": norm,
                "duplicate_of": "",
                "reason": "internal_grobid_or_xml_url",
            })
            continue

        # 1. Duplicado exacto
        key = dedup_key(norm, pdf)

        if key in seen:
            removed.append({
                "pdf": pdf,
                "original_url": raw_url,
                "normalized_url": norm,
                "duplicate_of": seen[key],
                "reason": "duplicate_exact",
            })
            continue

        # 2. Duplicado parecido
        if REMOVE_SIMILAR_URLS:
            similar_to = find_similar_url(norm, kept_urls_by_domain)

            if similar_to:
                removed.append({
                    "pdf": pdf,
                    "original_url": raw_url,
                    "normalized_url": norm,
                    "duplicate_of": similar_to,
                    "reason": "duplicate_similar",
                })
                continue

        # 3. Conservar
        seen[key] = norm

        domain = get_domain(norm)
        kept_urls_by_domain.setdefault(domain, []).append(norm)

        kept.append({
            "pdf": pdf,
            "url": norm,
        })

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

    save_csv(
        rows=kept,
        path=OUTPUT_CSV,
        fields=["pdf", "url"],
    )

    save_csv(
        rows=removed,
        path=REMOVED_CSV,
        fields=[
            "pdf",
            "original_url",
            "normalized_url",
            "duplicate_of",
            "reason",
        ],
    )

    print("\nGuardados:")
    print(f"- {OUTPUT_CSV}")
    print(f"- {REMOVED_CSV}")

    if removed:
        print("\nResumen de eliminadas:")

        counts = {}

        for r in removed:
            reason = r.get("reason", "")
            counts[reason] = counts.get(reason, 0) + 1

        for reason, count in counts.items():
            print(f"- {reason}: {count}")


if __name__ == "__main__":
    main()