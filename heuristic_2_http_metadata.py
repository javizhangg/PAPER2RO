import csv
import json
import re
from pathlib import Path
from urllib.parse import urlparse, urljoin, unquote
from datetime import datetime

import requests


INPUT_CSV = "outputs/all_links_normalized.csv"
OUTPUT_CSV = "outputs/heuristic_2_results.csv"
OUTPUT_JSON = "outputs/heuristic_2_results.json"

REQUEST_TIMEOUT = 12
MAX_RESPONSE_BYTES = 2_000_000


# --------------------------------------------------------------------
# FORMATOS PERMITIDOS COMO DATASET
# IMPORTANTE:
# No incluyo .json, .xml ni .rdf porque has dicho que NO quieres aceptarlos.
# --------------------------------------------------------------------

ALLOWED_DATASET_EXTENSIONS = {
    ".csv",
    ".tsv",
    ".xlsx",
    ".xls",
    ".parquet",
    ".h5",
    ".hdf5",
    ".arff",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".sav",
    ".dta",
    ".rds",
    ".feather",
    ".orc",
    ".mat",
    ".npy",
    ".npz"
}

# Archivos comprimidos: se aceptan solo si tienen contexto de dataset/download.
# Muchos datasets reales vienen como .zip.
ALLOWED_ARCHIVE_EXTENSIONS = {
    ".zip",
    ".gz",
    ".tar",
    ".tgz",
    ".bz2",
    ".xz"
}

# No acepto .rar ni .7z por ser menos estándar y más difícil de inspeccionar.
# Si quieres, luego los añadimos.


DATASET_TERMS = {
    "dataset",
    "datasets",
    "data set",
    "data sets",
    "corpus",
    "benchmark",
    "database",
    "training data",
    "test data",
    "validation data",
    "evaluation data",
    "annotations",
    "annotation",
    "labels",
    "features",
    "samples",
    "records",
    "instances",
    "supplementary data",
    "supplemental data",
    "data repository",
    "data archive"
}

DOWNLOAD_TERMS = {
    "download",
    "downloads",
    "downloadable",
    "file",
    "files",
    "csv",
    "tsv",
    "xlsx",
    "xls",
    "parquet",
    "hdf5",
    "sqlite",
    "export",
    "raw data",
    "supplementary file",
    "supplemental file"
}

NEGATIVE_TERMS = {
    "paper",
    "article",
    "citation",
    "bibtex",
    "reference",
    "references",
    "documentation",
    "docs",
    "wiki",
    "blog",
    "login",
    "signin",
    "contact",
    "about",
    "license",
    "terms",
    "privacy",
    "manifest",
    "opensearch",
    "sitemap",
    "rss",
    "feed",
    "robots",
    "favicon",
    "static",
    "assets",
    "bundle",
    "webpack",
    "serviceworker",
    "mailto",
    "javascript"
}

TECHNICAL_FILENAMES = {
    "manifest.json",
    "site.webmanifest",
    "asset-manifest.json",
    "opensearch.xml",
    "sitemap.xml",
    "sitemap_index.xml",
    "feed.xml",
    "rss.xml",
    "atom.xml",
    "robots.txt",
    "browserconfig.xml",
    "crossdomain.xml",
    "clientaccesspolicy.xml",
    "tdmrep-policy.json",
    "security.txt",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "composer.json",
    "composer.lock",
    "pnpm-lock.yaml",
    "requirements.txt",
    "environment.yml",
    "metadata.json",
    "info.json",
    "config.json",
    "database-config-example.json"
}

DATASET_DOMAINS = {
    "zenodo.org",
    "figshare.com",
    "kaggle.com",
    "huggingface.co",
    "data.mendeley.com",
    "datadryad.org",
    "dryad.org",
    "dataverse.harvard.edu",
    "osf.io",
    "openml.org",
    "archive.ics.uci.edu",
    "data.europa.eu",
    "data.gov",
    "datos.gob.es",
    "raw.githubusercontent.com"
}

JSON_CONTENT_TYPES = {
    "application/json",
    "application/ld+json",
    "application/vnd.api+json",
    "text/json"
}

HTML_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml"
}

DOWNLOAD_CONTENT_TYPES = {
    "text/csv",
    "application/csv",
    "text/tab-separated-values",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/parquet",
    "application/x-parquet",
    "application/x-hdf5",
    "application/x-sqlite3",
    "application/x-stata",
    "application/octet-stream"
}

ARCHIVE_CONTENT_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "application/gzip",
    "application/x-gzip",
    "application/x-tar",
    "application/x-bzip2",
    "application/x-xz"
}


HREF_A_REGEX = re.compile(
    r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL
)

HREF_REGEX = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)

URL_REGEX = re.compile(
    r'https?://[^\s"\'<>\)\]\}]+',
    re.IGNORECASE
)


# --------------------------------------------------------------------
# FUNCIONES BÁSICAS
# --------------------------------------------------------------------

def normalize_loose(url: str) -> str:
    if not url:
        return ""

    url = str(url).strip()
    url = re.sub(r"#.*$", "", url)
    url = url.rstrip("/.,;:!?)]}>'\"")
    return url


def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def root_domain(domain: str) -> str:
    domain = (domain or "").lower().strip()

    if domain.startswith("www."):
        domain = domain[4:]

    parts = [p for p in domain.split(".") if p]

    if len(parts) <= 2:
        return domain

    if parts[-2] in {"co", "com", "org", "ac", "edu", "gov"} and len(parts) >= 3:
        return ".".join(parts[-3:])

    return ".".join(parts[-2:])


def is_dataset_domain(url: str) -> bool:
    domain = get_domain(url)
    rd = root_domain(domain)

    return domain in DATASET_DOMAINS or rd in DATASET_DOMAINS


def get_extension(url: str) -> str:
    try:
        path = unquote(urlparse(url).path.lower())
        match = re.search(r"(\.[a-z0-9]+)$", path)
        return match.group(1).lower() if match else ""
    except Exception:
        return ""


def get_filename_from_url(url: str) -> str:
    try:
        return Path(unquote(urlparse(url).path)).name.lower()
    except Exception:
        return ""


def tokenize_text(text: str) -> set:
    text = str(text or "").lower()
    return {t for t in re.split(r"[/\\\-_.?=&:#%+,\s]+", text) if t}


def text_contains_any(text: str, terms: set) -> bool:
    text = str(text or "").lower()
    return any(term in text for term in terms)


def url_has_dataset_context(url: str) -> bool:
    url_text = unquote(str(url or "")).lower()
    return text_contains_any(url_text, DATASET_TERMS)


def url_has_download_context(url: str) -> bool:
    url_text = unquote(str(url or "")).lower()
    return text_contains_any(url_text, DOWNLOAD_TERMS)


def is_technical_link(url: str) -> bool:
    filename = get_filename_from_url(url)
    path = urlparse(url).path.lower()

    if filename in TECHNICAL_FILENAMES:
        return True

    technical_paths = (
        "/static/",
        "/assets/",
        "/_next/",
        "/webpack/",
        "/favicon",
        "/icons/",
        "/apple-touch-icon",
        "/css/",
        "/js/",
        "/fonts/"
    )

    if any(x in path for x in technical_paths):
        return True

    if filename.endswith((
        ".js",
        ".css",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".map",
        ".webp"
    )):
        return True

    return False


def clean_html_text(text: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.lower().strip()


# --------------------------------------------------------------------
# DETECCIÓN DE ARCHIVO DESCARGABLE VÁLIDO
# --------------------------------------------------------------------

def is_allowed_dataset_file(url: str) -> bool:
    """
    Devuelve True solo si la URL apunta a un formato permitido.
    NO acepta .json ni .xml.
    """

    if not url:
        return False

    if is_technical_link(url):
        return False

    ext = get_extension(url)

    if ext in ALLOWED_DATASET_EXTENSIONS:
        return True

    return False


def is_allowed_archive_file(url: str) -> bool:
    """
    Aceptamos .zip/.gz/.tar solo si hay contexto de dataset o descarga.
    """

    if not url:
        return False

    if is_technical_link(url):
        return False

    ext = get_extension(url)

    if ext not in ALLOWED_ARCHIVE_EXTENSIONS:
        return False

    if url_has_dataset_context(url) or url_has_download_context(url) or is_dataset_domain(url):
        return True

    return False


def score_downloadable_candidate(candidate_url: str, anchor_text: str = "") -> tuple[int, list]:
    """
    Puntúa un enlace descargable.
    La condición fuerte es encontrar un formato permitido.
    """

    score = 0
    signals = []

    if not candidate_url:
        return 0, signals

    if is_technical_link(candidate_url):
        return 0, ["technical_link_ignored"]

    ext = get_extension(candidate_url)

    combined_text = f"{candidate_url} {anchor_text}".lower()

    if is_allowed_dataset_file(candidate_url):
        score += 6
        signals.append(f"allowed_dataset_file:{ext}")

    elif is_allowed_archive_file(candidate_url):
        score += 5
        signals.append(f"allowed_archive_with_dataset_context:{ext}")

    else:
        return 0, ["not_allowed_dataset_format"]

    if text_contains_any(combined_text, DATASET_TERMS):
        score += 2
        signals.append("candidate_has_dataset_terms")

    if text_contains_any(combined_text, DOWNLOAD_TERMS):
        score += 2
        signals.append("candidate_has_download_terms")

    if is_dataset_domain(candidate_url):
        score += 1
        signals.append("candidate_known_dataset_domain")

    if text_contains_any(combined_text, NEGATIVE_TERMS):
        score -= 2
        signals.append("candidate_has_negative_terms")

    return max(score, 0), signals


# --------------------------------------------------------------------
# DESCARGA HTTP
# --------------------------------------------------------------------

def request_url(url: str, timeout: int = REQUEST_TIMEOUT):
    headers = {
        "User-Agent": "Mozilla/5.0 dataset-detector/heuristic-2",
        "Accept": "application/json,text/html,text/csv,application/octet-stream,*/*"
    }

    response = None

    try:
        response = requests.get(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=timeout,
            stream=True
        )

        return response

    except Exception:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

        raise


def read_limited_response(response, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    content = b""

    for chunk in response.iter_content(chunk_size=8192):
        if not chunk:
            continue

        content += chunk

        if len(content) >= max_bytes:
            content = content[:max_bytes]
            break

    return content


def decode_response_bytes(content: bytes, response) -> str:
    encoding = response.encoding or "utf-8"

    try:
        return content.decode(encoding, errors="replace")
    except Exception:
        return content.decode("utf-8", errors="replace")


def get_response_metadata(response) -> dict:
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    content_length = response.headers.get("Content-Length", "")
    content_disposition = response.headers.get("Content-Disposition", "").lower()

    final_url = response.url
    final_ext = get_extension(final_url)

    filename_match = re.search(
        r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)',
        content_disposition
    )

    filename = filename_match.group(1) if filename_match else ""
    filename = unquote(filename).strip()

    filename_ext = get_extension("https://example.com/" + filename) if filename else ""

    return {
        "status_code": response.status_code,
        "content_type": content_type,
        "content_length": content_length,
        "content_disposition": content_disposition,
        "filename_from_content_disposition": filename,
        "filename_extension": filename_ext,
        "final_url": final_url,
        "final_extension": final_ext
    }


# --------------------------------------------------------------------
# EXTRAER LINKS DE HTML
# --------------------------------------------------------------------

def extract_links_from_html(html: str, base_url: str) -> list:
    links = []

    for match in HREF_A_REGEX.finditer(html):
        href = match.group(1).strip()
        anchor_text = clean_html_text(match.group(2) or "")

        absolute = urljoin(base_url, href)
        absolute = normalize_loose(absolute)

        if absolute.startswith("http://") or absolute.startswith("https://"):
            links.append({
                "url": absolute,
                "anchor_text": anchor_text[:300],
                "source": "html_a_tag"
            })

    seen = {x["url"] for x in links}

    for match in HREF_REGEX.finditer(html):
        href = match.group(1).strip()

        absolute = urljoin(base_url, href)
        absolute = normalize_loose(absolute)

        if absolute.startswith("http://") or absolute.startswith("https://"):
            if absolute not in seen:
                seen.add(absolute)
                links.append({
                    "url": absolute,
                    "anchor_text": "",
                    "source": "html_href"
                })

    return links


# --------------------------------------------------------------------
# EXTRAER LINKS DESDE JSON
# --------------------------------------------------------------------

def flatten_json_values(obj, max_items: int = 5000) -> list:
    """
    Recorre un JSON y extrae strings útiles.
    Sirve para encontrar URLs, nombres de archivo, títulos, descripciones, etc.
    """

    values = []
    stack = [obj]

    while stack and len(values) < max_items:
        current = stack.pop()

        if isinstance(current, dict):
            for key, value in current.items():
                values.append(str(key))

                if isinstance(value, (dict, list)):
                    stack.append(value)
                else:
                    values.append(str(value))

        elif isinstance(current, list):
            for item in current:
                if isinstance(item, (dict, list)):
                    stack.append(item)
                else:
                    values.append(str(item))

        else:
            values.append(str(current))

    return values


def extract_candidate_links_from_json(obj, base_url: str) -> list:
    """
    Busca URLs y nombres de archivos dentro del JSON.
    """

    candidates = []
    values = flatten_json_values(obj)

    for value in values:
        value = str(value or "").strip()

        if not value:
            continue

        # Caso 1: el valor contiene URLs completas.
        found_urls = URL_REGEX.findall(value)

        for found in found_urls:
            found = normalize_loose(found)
            candidates.append({
                "url": found,
                "anchor_text": value[:300],
                "source": "json_url"
            })

        # Caso 2: el valor parece ser un archivo relativo.
        # Ejemplo: files/dataset.csv o download/data.xlsx
        possible_file_match = re.search(
            r'([A-Za-z0-9_\-./%]+(' +
            "|".join(re.escape(ext) for ext in ALLOWED_DATASET_EXTENSIONS.union(ALLOWED_ARCHIVE_EXTENSIONS)) +
            r'))(\?|$)',
            value,
            re.IGNORECASE
        )

        if possible_file_match:
            relative = possible_file_match.group(1)
            absolute = urljoin(base_url, relative)
            absolute = normalize_loose(absolute)

            candidates.append({
                "url": absolute,
                "anchor_text": value[:300],
                "source": "json_relative_file"
            })

    # Quitar duplicados
    unique = []
    seen = set()

    for c in candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)

    return unique


def json_looks_like_dataset(obj) -> tuple[bool, list]:
    """
    Mira si el JSON parece describir un dataset.
    No basta con que sea JSON: buscamos palabras de dataset en keys/values.
    """

    signals = []
    values = flatten_json_values(obj)
    text = " ".join(values).lower()

    if text_contains_any(text, DATASET_TERMS):
        signals.append("json_has_dataset_terms")

    if text_contains_any(text, DOWNLOAD_TERMS):
        signals.append("json_has_download_terms")

    # Señales típicas de metadatos de dataset.
    metadata_keys = {
        "dataset",
        "datasets",
        "files",
        "file",
        "download",
        "downloads",
        "distribution",
        "distributions",
        "contenturl",
        "contenturl",
        "encodingformat",
        "mediaType".lower(),
        "name",
        "description",
        "title"
    }

    json_tokens = tokenize_text(text)
    if json_tokens.intersection(metadata_keys):
        signals.append("json_has_dataset_metadata_keys")

    looks_like = len(signals) >= 1

    return looks_like, signals


# --------------------------------------------------------------------
# HEURÍSTICA 2 PRINCIPAL
# --------------------------------------------------------------------

def heuristic_2(url: str, timeout: int = REQUEST_TIMEOUT) -> dict:
    """
    Nueva lógica:

    1. Descargar la URL.
    2. Si es un archivo permitido directo: dataset.
    3. Si es JSON: parsearlo, mirar si parece dataset y buscar descargables.
    4. Si es HTML: mirar si parece página de dataset y buscar descargables.
    5. Solo positivo si hay archivo descargable válido.
    6. NO se aceptan .json ni .xml como formato dataset final.
    """

    response = None

    try:
        url = normalize_loose(url)

        response = request_url(url, timeout=timeout)
        meta = get_response_metadata(response)

        content_type = meta["content_type"]
        final_url = meta["final_url"]
        final_ext = meta["final_extension"]
        filename = meta["filename_from_content_disposition"]
        filename_ext = meta["filename_extension"]
        content_disposition = meta["content_disposition"]

        score = 0
        signals = []
        candidates = []
        best_download_url = ""
        best_download_score = 0
        best_download_signals = []
        mode = "unknown"

        # --------------------------------------------------------------
        # 1. Caso: la propia URL ya es un archivo de dataset permitido.
        # --------------------------------------------------------------

        if is_allowed_dataset_file(final_url):
            score += 8
            signals.append(f"direct_allowed_dataset_file:{final_ext}")
            best_download_url = final_url
            best_download_score = 8
            best_download_signals = [f"direct_allowed_dataset_file:{final_ext}"]

        elif is_allowed_archive_file(final_url):
            score += 7
            signals.append(f"direct_allowed_archive_file:{final_ext}")
            best_download_url = final_url
            best_download_score = 7
            best_download_signals = [f"direct_allowed_archive_file:{final_ext}"]

        # --------------------------------------------------------------
        # 2. Caso: Content-Disposition indica archivo descargable.
        # --------------------------------------------------------------

        if filename:
            fake_filename_url = "https://example.com/" + filename

            if is_allowed_dataset_file(fake_filename_url):
                score += 8
                signals.append(f"content_disposition_allowed_dataset_file:{filename_ext}")

                if not best_download_url:
                    best_download_url = final_url
                    best_download_score = 8
                    best_download_signals = [
                        f"content_disposition_allowed_dataset_file:{filename_ext}"
                    ]

            elif filename_ext in ALLOWED_ARCHIVE_EXTENSIONS:
                if url_has_dataset_context(filename) or url_has_dataset_context(final_url):
                    score += 7
                    signals.append(f"content_disposition_allowed_archive_with_context:{filename_ext}")

                    if not best_download_url:
                        best_download_url = final_url
                        best_download_score = 7
                        best_download_signals = [
                            f"content_disposition_allowed_archive_with_context:{filename_ext}"
                        ]

        if "attachment" in content_disposition:
            signals.append("content_disposition_attachment")

        # --------------------------------------------------------------
        # 3. Si ya es archivo directo válido, no hace falta leer entero.
        # --------------------------------------------------------------

        if best_download_url:
            matched = score >= 7

            return {
                "matched": matched,
                "score": score,
                "reason": "direct_downloadable_dataset_file" if matched else "no_valid_dataset_download",
                "value": {
                    **meta,
                    "mode": "direct_file",
                    "signals": signals,
                    "json_dataset_signals": [],
                    "page_dataset_signals": [],
                    "best_download_url": best_download_url,
                    "best_download_score": best_download_score,
                    "best_download_signals": best_download_signals,
                    "download_candidates": []
                }
            }

        # --------------------------------------------------------------
        # 4. Leer contenido limitado.
        # --------------------------------------------------------------

        raw_content = read_limited_response(response, MAX_RESPONSE_BYTES)
        text_content = decode_response_bytes(raw_content, response)

        # --------------------------------------------------------------
        # 5. Caso JSON.
        # --------------------------------------------------------------

        is_json = (
            content_type in JSON_CONTENT_TYPES
            or final_ext == ".json"
            or text_content.strip().startswith("{")
            or text_content.strip().startswith("[")
        )

        json_dataset_signals = []

        if is_json:
            mode = "json"

            try:
                obj = json.loads(text_content)
                json_looks_dataset, json_dataset_signals = json_looks_like_dataset(obj)

                if json_looks_dataset:
                    score += 3
                    signals.extend(json_dataset_signals)

                candidates = extract_candidate_links_from_json(obj, final_url)

                for candidate in candidates:
                    candidate_url = candidate["url"]
                    anchor_text = candidate.get("anchor_text", "")

                    candidate_score, candidate_signals = score_downloadable_candidate(
                        candidate_url,
                        anchor_text
                    )

                    candidate["score"] = candidate_score
                    candidate["signals"] = candidate_signals

                    if candidate_score > best_download_score:
                        best_download_score = candidate_score
                        best_download_url = candidate_url
                        best_download_signals = candidate_signals

                candidates = sorted(
                    [c for c in candidates if c.get("score", 0) > 0],
                    key=lambda x: x["score"],
                    reverse=True
                )[:20]

                if best_download_score >= 5:
                    score += best_download_score
                    signals.append("json_contains_allowed_downloadable_file")
                    signals.extend([f"best_download_{s}" for s in best_download_signals])

                # Condición estricta:
                # JSON parece dataset + hay descargable permitido.
                matched = json_looks_dataset and best_download_score >= 5

                return {
                    "matched": matched,
                    "score": score,
                    "reason": "json_dataset_with_allowed_download" if matched else "json_without_valid_dataset_download",
                    "value": {
                        **meta,
                        "mode": mode,
                        "signals": signals,
                        "json_dataset_signals": json_dataset_signals,
                        "page_dataset_signals": [],
                        "best_download_url": best_download_url,
                        "best_download_score": best_download_score,
                        "best_download_signals": best_download_signals,
                        "download_candidates": candidates
                    }
                }

            except Exception as json_error:
                signals.append(f"json_parse_error:{json_error}")

        # --------------------------------------------------------------
        # 6. Caso HTML o texto.
        # --------------------------------------------------------------

        mode = "html_or_text"

        page_text = clean_html_text(text_content)
        page_dataset_signals = []

        page_looks_dataset = False

        if text_contains_any(page_text, DATASET_TERMS):
            page_looks_dataset = True
            page_dataset_signals.append("page_has_dataset_terms")
            score += 3

        if text_contains_any(page_text, DOWNLOAD_TERMS):
            page_dataset_signals.append("page_has_download_terms")
            score += 1

        if is_dataset_domain(final_url):
            page_looks_dataset = True
            page_dataset_signals.append("known_dataset_domain")
            score += 2

        signals.extend(page_dataset_signals)

        # Links HTML
        if content_type in HTML_CONTENT_TYPES or "<html" in text_content.lower():
            candidates = extract_links_from_html(text_content, final_url)
        else:
            # Texto plano: buscar URLs sueltas
            candidates = []
            for found in URL_REGEX.findall(text_content):
                candidates.append({
                    "url": normalize_loose(found),
                    "anchor_text": "",
                    "source": "plain_text_url"
                })

        for candidate in candidates:
            candidate_url = candidate["url"]
            anchor_text = candidate.get("anchor_text", "")

            candidate_score, candidate_signals = score_downloadable_candidate(
                candidate_url,
                anchor_text
            )

            candidate["score"] = candidate_score
            candidate["signals"] = candidate_signals

            if candidate_score > best_download_score:
                best_download_score = candidate_score
                best_download_url = candidate_url
                best_download_signals = candidate_signals

        candidates = sorted(
            [c for c in candidates if c.get("score", 0) > 0],
            key=lambda x: x["score"],
            reverse=True
        )[:20]

        if best_download_score >= 5:
            score += best_download_score
            signals.append("page_contains_allowed_downloadable_file")
            signals.extend([f"best_download_{s}" for s in best_download_signals])

        # Condición final estricta:
        # tiene que parecer dataset + tener descargable permitido.
        matched = page_looks_dataset and best_download_score >= 5

        return {
            "matched": matched,
            "score": score,
            "reason": "page_dataset_with_allowed_download" if matched else "no_valid_dataset_download",
            "value": {
                **meta,
                "mode": mode,
                "signals": signals,
                "json_dataset_signals": json_dataset_signals,
                "page_dataset_signals": page_dataset_signals,
                "best_download_url": best_download_url,
                "best_download_score": best_download_score,
                "best_download_signals": best_download_signals,
                "download_candidates": candidates
            }
        }

    except Exception as e:
        return {
            "matched": False,
            "score": 0,
            "reason": "http_error",
            "value": {
                "error": str(e),
                "signals": []
            }
        }

    finally:
        try:
            if response is not None:
                response.close()
        except Exception:
            pass


# --------------------------------------------------------------------
# CSV / JSON OUTPUT
# --------------------------------------------------------------------

def load_normalized_csv(path: str) -> list:
    rows = []

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            rows.append({
                "paper": row.get("paper", "").strip(),
                "section": row.get("section", "").strip(),
                "original_url": row.get("original_url", "").strip(),
                "normalized_url": row.get("normalized_url", "").strip() or row.get("url", "").strip(),
                "domain": row.get("domain", "").strip(),
                "extension": row.get("extension", "").strip()
            })

    return rows


def save_csv(rows: list, path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    fields = list(rows[0].keys()) if rows else []

    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)

        return path

    except PermissionError:
        p = Path(path)
        alt = str(
            p.with_name(
                f"{p.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{p.suffix}"
            )
        )

        with open(alt, "w", newline="", encoding="utf-8-sig") as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)

        return alt


def save_json(rows: list, path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)

        return path

    except PermissionError:
        p = Path(path)
        alt = str(
            p.with_name(
                f"{p.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{p.suffix}"
            )
        )

        with open(alt, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)

        return alt


def main():
    if not Path(INPUT_CSV).exists():
        print(f"No existe {INPUT_CSV}")
        return

    input_rows = load_normalized_csv(INPUT_CSV)

    csv_rows = []
    json_rows = []

    for i, row in enumerate(input_rows, start=1):
        url = row["normalized_url"]

        if not url:
            continue

        print(f"[{i}/{len(input_rows)}] Analizando: {url}")

        h = heuristic_2(url)
        label = "dataset" if h["matched"] else "not_dataset"
        v = h.get("value", {})

        csv_rows.append({
            **row,
            "matched": h.get("matched", False),
            "score": h.get("score", 0),
            "reason": h.get("reason", ""),
            "mode": v.get("mode", ""),
            "status_code": v.get("status_code", ""),
            "content_type": v.get("content_type", ""),
            "content_length": v.get("content_length", ""),
            "content_disposition": v.get("content_disposition", ""),
            "filename_from_content_disposition": v.get("filename_from_content_disposition", ""),
            "filename_extension": v.get("filename_extension", ""),
            "final_url": v.get("final_url", ""),
            "final_extension": v.get("final_extension", ""),
            "best_download_url": v.get("best_download_url", ""),
            "best_download_score": v.get("best_download_score", ""),
            "best_download_signals": "|".join(v.get("best_download_signals", [])),
            "signals": "|".join(v.get("signals", [])),
            "label": label
        })

        json_rows.append({
            "row": row,
            "heuristic_2": h,
            "label": label
        })

    saved_csv = save_csv(csv_rows, OUTPUT_CSV)
    saved_json = save_json(json_rows, OUTPUT_JSON)

    print()
    print("Resultados:")
    print(f"Leídas: {len(input_rows)}")
    print(f"dataset: {sum(1 for r in csv_rows if r['label'] == 'dataset')}")
    print(f"not_dataset: {sum(1 for r in csv_rows if r['label'] == 'not_dataset')}")
    print(f"CSV: {saved_csv}")
    print(f"JSON: {saved_json}")

    print()
    print("Top URLs detectadas como dataset:")

    datasets = [r for r in csv_rows if r["label"] == "dataset"]
    datasets = sorted(datasets, key=lambda x: int(x["score"]), reverse=True)

    for r in datasets[:20]:
        print(f"- score={r['score']} | {r['normalized_url']}")
        print(f"  best_download: {r.get('best_download_url', '')}")
        print(f"  signals: {r.get('signals', '')[:300]}")


if __name__ == "__main__":
    main()