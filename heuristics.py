# heuristics.py
# Procesos para identificar URLs candidatas a dataset
# H1 -> extensiones de datos
# H2 -> metadatos HTTP / content negotiation
# H3 -> señales reutilizables tipo GAP-KGE
# H4 -> señales basadas en la propia URL
# H5 -> señales extraídas automáticamente de los .dataset.json generados por GAP-KGE / DataStet
# H6 -> inspección temporal del contenido descargado
# H1, H4 Y H6 son los de la heuristica 1 que se basa en ver si la extension de la url o del archivo descargado es un .csv, .json, .xml, etc.
# H2 es la heuristica 2 que se basa en hacer una petición HTTP HEAD o GET para obtener el content type, content disposition, etc. y ver si indican que es un archivo de datos.
# H3 es la heuristica 3 basada en GAP-KGE. H5 se ignora para el benchmark.

import csv
import json
import re
import io
import zipfile
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from functools import lru_cache
import requests
import xml.etree.ElementTree as ET

INPUT_CSV = "outputs/all_links_normalized.csv"
OUTPUT_CSV = "outputs/heuristics_results.csv"
OUTPUT_JSON = "outputs/heuristics_results.json"

# Carpeta donde están los PDFs y los .dataset.json
GAP_KGE_JSON_DIR = "pdfs"

# Carpeta temporal donde se descargan ficheros para inspección
TEMP_DOWNLOAD_DIR = "temp_downloads"

# ==================================
# H1: extensiones de ficheros de datos
# ==================================
DATA_EXTENSIONS = {
    ".csv", ".tsv", ".json", ".xml", ".rdf",
    ".xlsx", ".xls", ".parquet", ".h5", ".hdf5"
}

# Por ahora descartamos ZIP como extensión directa,
# pero H6 sí puede abrir ZIP temporalmente e inspeccionarlo.
EXCLUDED_EXTENSIONS = {
    ".zip"
}

# ==================================
# H3: señales tipo GAP-KGE
# ==================================
KNOWN_DATASET_DOMAINS = {
    "zenodo.org",
    "figshare.com",
    "datadryad.org",
    "dryad.org",
    "dataverse.org",
    "kaggle.com",
    "data.gov",
    "archive.ics.uci.edu",
    "openml.org",
    "physionet.org",
    "commoncrawl.org",
    "imagenet.org",
    "image-net.org",
    "grouplens.org",
    "registry.opendata.aws",
    "grand-challenge.org",
    "voxforge.org",
    "movielens.org"
}

URL_DATASET_KEYWORDS = {
    "dataset", "datasets", "data", "corpus", "benchmark",
    "benchmarks", "download", "downloads", "challenge",
    "repository", "archive", "collection", "eval", "evaluation",
    "leaderboard", "train", "test", "dev", "split"
}

URL_NEGATIVE_KEYWORDS = {
    "github", "code", "software", "repo", "implementation",
    "paper", "pdf", "docs", "documentation", "wiki", "blog",
    "slides", "tutorial", "readme"
}

# ==================================
# H2: metadatos HTTP / content negotiation
# ==================================
DATA_CONTENT_TYPES = {
    "text/csv",
    "application/csv",
    "application/json",
    "application/xml",
    "text/xml",
    "application/rdf+xml",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
    "text/tab-separated-values"
}

# ==================================
# H6: configuración de descarga temporal
# ==================================
DOWNLOAD_SAMPLE_BYTES = 65536   # 64 KB
DOWNLOAD_MAX_BYTES = 262144     # 256 KB máximo a descargar para inspección
DOWNLOAD_TIMEOUT = 10

ZIP_DATA_EXTENSIONS = {
    ".csv", ".tsv", ".json", ".xml", ".rdf",
    ".xlsx", ".xls", ".parquet", ".h5", ".hdf5"
}

# Tokens sospechosos de páginas de validación, anti-bot o challenge
SUSPICIOUS_HTML_TOKENS = {
    "validate", "captcha", "challenge", "bot", "verify",
    "perfdrive", "radware", "cloudflare", "akamai"
}

# ==================================
# Utilidades generales
# ==================================

## Funciones para extraer el dominio de una URL. Ejemplo: "https://example.com/path" -> "example.com"
def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


## Funciones que saca la ruta de una URL. Ejemplo: "https://example.com/path/to/file.csv?query=1" -> "/path/to/file.csv"
def get_path(url: str) -> str:
    try:
        return urlparse(url).path.lower()
    except Exception:
        return ""


## Funciones para extraer la extensión de una URL. Ejemplo: "https://example.com/path/to/file.csv?query=1" -> ".csv"
def get_extension(url: str) -> str:
    try:
        path = get_path(url)
        match = re.search(r"(\.[a-z0-9]+)$", path)
        if match:
            return match.group(1)
    except Exception:
        pass
    return ""


## Funciones que divide la URL en tokens útiles para buscar señales léxicas. Ejemplo: "https://example.com/dataset/123?version=1" -> {"example", "com", "dataset", "123", "version", "1"}
def tokenize_url(url: str) -> set:
    """
    Divide la URL en tokens útiles para buscar señales léxicas.
    """
    tokens = set()
    try:
        parsed = urlparse(url)
        raw = f"{parsed.netloc}{parsed.path}{parsed.query}".lower()
        split_tokens = re.split(r"[/\\\-_.?=&:#]+", raw)
        tokens = {t for t in split_tokens if t}
    except Exception:
        pass
    return tokens


## Función para detectar si la URL es un DOI. Ejemplo: "https://doi.org/10.1234/abcd" -> True
def is_doi_url(url: str) -> bool:
    return get_domain(url) == "doi.org"


# ==================================
# Utilidades GAP-KGE / DataStet JSON
# ==================================

## Expresión regular para extraer URLs de un texto, usada en la extracción de URLs desde los .dataset.json.
URL_REGEX = re.compile(r'https?://[^\s"\'<>]+', re.IGNORECASE)

## Conjunto de nombres de claves que podrían contener.
TEXT_KEYS_HINT = {
    "rawform", "raw_form", "mention", "text", "name", "dataset",
    "dataset_name", "normalizedform", "normalized_form"
}

SCORE_KEYS_HINT = {
    "score", "confidence", "prob", "probability", "conf"
}


def normalize_url_loose(url: str) -> str:
    if not url:
        return ""
    u = url.strip()
    u = re.sub(r"#.*$", "", u)
    u = u.rstrip("/")
    return u.lower()


def safe_read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def iter_json_nodes(obj):
    yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from iter_json_nodes(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_json_nodes(item)


def extract_urls_from_json(obj) -> list:
    found = set()

    for node in iter_json_nodes(obj):
        if isinstance(node, str):
            for match in URL_REGEX.findall(node):
                found.add(normalize_url_loose(match))

    return sorted(found)


def extract_text_mentions_from_json(obj) -> list:
    mentions = set()

    for node in iter_json_nodes(obj):
        if isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(k, str):
                    continue

                key_norm = k.strip().lower().replace("-", "_")
                key_norm_compact = key_norm.replace("_", "")

                if key_norm_compact in TEXT_KEYS_HINT and isinstance(v, str):
                    txt = v.strip()
                    if txt and len(txt) <= 300:
                        mentions.add(txt)

    return sorted(mentions)


def extract_scores_from_json(obj) -> list:
    scores = []

    for node in iter_json_nodes(obj):
        if isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(k, str):
                    continue

                key_norm = k.strip().lower().replace("-", "_")
                key_norm_compact = key_norm.replace("_", "")

                if key_norm_compact in SCORE_KEYS_HINT:
                    try:
                        scores.append(float(v))
                    except Exception:
                        pass

    return scores


@lru_cache(maxsize=2048)
def summarize_gap_kge_json(json_path: str) -> dict:
    data = safe_read_json(json_path)

    if data is None:
        return {
            "exists": False,
            "error": "json_not_readable",
            "urls": [],
            "mentions": [],
            "scores": []
        }

    urls = extract_urls_from_json(data)
    mentions = extract_text_mentions_from_json(data)
    scores = extract_scores_from_json(data)
    positive_scores = [s for s in scores if s > 0]

    return {
        "exists": True,
        "error": "",
        "urls": urls,
        "mentions": mentions,
        "scores": scores,
        "max_score": max(scores) if scores else None,
        "positive_score_count": len(positive_scores),
        "url_count": len(urls),
        "mention_count": len(mentions)
    }


def get_gap_kge_json_path(paper_name: str, base_dir: str = GAP_KGE_JSON_DIR) -> str:
    paper_name = (paper_name or "").strip()
    if not paper_name:
        return ""

    if paper_name.lower().endswith(".pdf"):
        stem = paper_name[:-4]
    else:
        stem = paper_name

    return str(Path(base_dir) / f"{stem}.dataset.json")


# ==================================
# Utilidades H6: inspección de contenido
# ==================================
def looks_like_text(data: bytes) -> bool:
    if not data:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        try:
            data.decode("latin-1")
            return True
        except Exception:
            return False


def decode_sample(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(enc)
        except Exception:
            pass
    return ""


def detect_delimited_table(text: str) -> dict:
    """
    Intenta detectar si el texto parece una tabla CSV o TSV.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    lines = lines[:10]

    if len(lines) < 2:
        return {"matched": False, "kind": "", "columns": 0, "reason": "not_enough_lines"}

    for delimiter, kind in [(",", "csv"), ("\t", "tsv"), (";", "csv_semicolon")]:
        counts = []
        for line in lines[:5]:
            count = line.count(delimiter)
            counts.append(count)

        # necesitamos al menos 2 líneas con separadores
        positive_counts = [c for c in counts if c > 0]
        if len(positive_counts) < 2:
            continue

        # comprobamos consistencia aproximada
        if max(positive_counts) - min(positive_counts) <= 2:
            avg_cols = int(sum(c + 1 for c in positive_counts) / len(positive_counts))
            if avg_cols >= 2:
                return {
                    "matched": True,
                    "kind": kind,
                    "columns": avg_cols,
                    "reason": "consistent_delimited_rows"
                }

    return {"matched": False, "kind": "", "columns": 0, "reason": "no_consistent_delimited_pattern"}


def detect_json_content(data: bytes) -> dict:
    try:
        obj = json.loads(decode_sample(data))
        if isinstance(obj, list):
            return {"matched": True, "kind": "json_array", "reason": "valid_json_array"}
        if isinstance(obj, dict):
            return {"matched": True, "kind": "json_object", "reason": "valid_json_object"}
        return {"matched": True, "kind": "json_scalar", "reason": "valid_json_scalar"}
    except Exception:
        return {"matched": False, "kind": "", "reason": "invalid_json"}


def detect_xml_content(data: bytes) -> dict:
    try:
        text = decode_sample(data).lstrip()
        if not text.startswith("<"):
            return {"matched": False, "kind": "", "reason": "not_xml_like"}
        ET.fromstring(text[:DOWNLOAD_SAMPLE_BYTES])
        return {"matched": True, "kind": "xml", "reason": "valid_xml_prefix"}
    except Exception:
        return {"matched": False, "kind": "", "reason": "invalid_xml"}


def detect_excel_signature(data: bytes) -> dict:
    # XLSX / ZIP based
    if data.startswith(b"PK"):
        return {"matched": True, "kind": "zip_or_xlsx", "reason": "pk_signature"}
    # XLS antiguo (OLE)
    if data.startswith(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"):
        return {"matched": True, "kind": "xls_ole", "reason": "ole_signature"}
    return {"matched": False, "kind": "", "reason": "no_excel_signature"}


def inspect_zip_bytes(data: bytes) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            lower_names = [n.lower() for n in names]

            data_files = []
            for name in lower_names:
                ext = Path(name).suffix.lower()
                if ext in ZIP_DATA_EXTENSIONS:
                    data_files.append(name)

            return {
                "matched": len(data_files) > 0,
                "reason": "zip_contains_data_files" if data_files else "zip_without_data_files",
                "file_count": len(names),
                "data_files": data_files[:20],
                "sample_names": lower_names[:20]
            }
    except Exception as e:
        return {
            "matched": False,
            "reason": "zip_not_readable",
            "file_count": 0,
            "data_files": [],
            "sample_names": [],
            "error": str(e)
        }


## Función para crear la carpeta temporal si no existe
def ensure_temp_dir(path: str = TEMP_DOWNLOAD_DIR):
    Path(path).mkdir(parents=True, exist_ok=True)


## Función para eliminar un archivo temporal
def cleanup_file(path: str):
    try:
        if path and Path(path).exists():
            Path(path).unlink()
    except Exception:
        pass


## Función para eliminar la carpeta temporal si se queda vacía
def cleanup_dir_if_empty(path: str):
    try:
        p = Path(path)
        if p.exists() and p.is_dir() and not any(p.iterdir()):
            p.rmdir()
    except Exception:
        pass


## Función para detectar si el texto descargado parece HTML
def looks_like_html(text: str) -> bool:
    if not text:
        return False

    text_low = text.lower()
    html_markers = [
        "<html", "<!doctype html", "<head", "<body", "<script",
        "<meta", "<title", "</html>"
    ]
    return any(marker in text_low for marker in html_markers)


## Función para detectar si la URL final parece una redirección de validación o anti-bot
def suspicious_redirect_or_validation(final_url: str) -> bool:
    if not final_url:
        return False

    final_low = final_url.lower()
    return any(token in final_low for token in SUSPICIOUS_HTML_TOKENS)


## Función para descargar el recurso a un fichero temporal real
def save_download_to_temp_file(
    url: str,
    timeout: int = DOWNLOAD_TIMEOUT,
    max_bytes: int = DOWNLOAD_MAX_BYTES,
    temp_dir: str = TEMP_DOWNLOAD_DIR
) -> dict:
    """
    Descarga el recurso a un fichero temporal real y devuelve metadatos.
    """
    ensure_temp_dir(temp_dir)

    tmp_path = ""
    try:
        with requests.get(url, allow_redirects=True, timeout=timeout, stream=True) as response:
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            content_length = response.headers.get("Content-Length", "")
            final_url = response.url

            # Intentamos sacar extensión de la URL final
            final_ext = get_extension(final_url)

            with tempfile.NamedTemporaryFile(delete=False, dir=temp_dir, suffix=final_ext or ".tmp") as tmp:
                tmp_path = tmp.name
                total = 0

                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    tmp.write(chunk)
                    total += len(chunk)
                    if total >= max_bytes:
                        break

            return {
                "ok": True,
                "status_code": response.status_code,
                "content_type": content_type,
                "content_length": content_length,
                "final_url": final_url,
                "final_extension": final_ext,
                "bytes_downloaded": total,
                "temp_path": tmp_path
            }

    except Exception as e:
        cleanup_file(tmp_path)
        return {
            "ok": False,
            "error": str(e),
            "temp_path": ""
        }


## Función para leer solo el prefijo del fichero descargado
def read_file_prefix(path: str, max_bytes: int = DOWNLOAD_SAMPLE_BYTES) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(max_bytes)
    except Exception:
        return b""


## Función para inspeccionar un ZIP real guardado en disco
def inspect_zip_file(path: str) -> dict:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            lower_names = [n.lower() for n in names]

            data_files = []
            for name in lower_names:
                ext = Path(name).suffix.lower()
                if ext in ZIP_DATA_EXTENSIONS:
                    data_files.append(name)

            # Detectar XLSX real: ZIP con estructura Office
            office_markers = {"[content_types].xml", "xl/workbook.xml"}
            lower_set = set(lower_names)
            is_xlsx = all(marker in lower_set for marker in office_markers)

            return {
                "matched": len(data_files) > 0 or is_xlsx,
                "reason": (
                    "xlsx_office_structure" if is_xlsx
                    else "zip_contains_data_files" if data_files
                    else "zip_without_data_files"
                ),
                "file_count": len(names),
                "data_files": data_files[:20],
                "sample_names": lower_names[:20],
                "is_xlsx": is_xlsx
            }
    except Exception as e:
        return {
            "matched": False,
            "reason": "zip_not_readable",
            "file_count": 0,
            "data_files": [],
            "sample_names": [],
            "is_xlsx": False,
            "error": str(e)
        }


## Función para inspeccionar el fichero descargado ya guardado en disco
def inspect_saved_file(temp_path: str, content_type: str = "", final_url: str = "", final_ext: str = "") -> dict:
    """
    Inspecciona el archivo descargado ya guardado en disco.
    """
    prefix = read_file_prefix(temp_path, DOWNLOAD_SAMPLE_BYTES)

    if not prefix:
        return {
            "matched": False,
            "score": 0,
            "detected_kind": "",
            "signals": ["empty_or_unreadable_file"]
        }

    score = 0
    signals = []
    detected_kind = ""

    text_prefix = decode_sample(prefix) if looks_like_text(prefix) else ""
    is_html = (
        content_type == "text/html"
        or looks_like_html(text_prefix)
        or suspicious_redirect_or_validation(final_url)
    )

    if is_html:
        signals.append("html_response_or_validation_page")
        if suspicious_redirect_or_validation(final_url):
            signals.append("suspicious_validation_redirect")

    # 1. JSON válido
    if not is_html:
        json_check = detect_json_content(prefix)
        if json_check["matched"]:
            detected_kind = json_check["kind"]
            score += 3
            signals.append(json_check["reason"])

    # 2. XML válido
    if not detected_kind and not is_html:
        xml_check = detect_xml_content(prefix)
        if xml_check["matched"]:
            detected_kind = xml_check["kind"]
            score += 2
            signals.append(xml_check["reason"])

    # 3. CSV / TSV si NO es HTML
    if not detected_kind and not is_html and text_prefix:
        table_check = detect_delimited_table(text_prefix)
        if table_check["matched"]:
            detected_kind = table_check["kind"]
            score += 3
            signals.append(table_check["reason"])
            signals.append(f"columns:{table_check['columns']}")

    # 4. Firmas Excel / ZIP
    excel_check = detect_excel_signature(prefix)
    if excel_check["matched"]:
        if excel_check["kind"] == "xls_ole":
            detected_kind = "xls"
            score += 3
            signals.append(excel_check["reason"])

        elif excel_check["kind"] == "zip_or_xlsx":
            zip_check = inspect_zip_file(temp_path)
            if zip_check["matched"]:
                if zip_check.get("is_xlsx"):
                    detected_kind = "xlsx"
                    score += 3
                    signals.append("xlsx_office_structure")
                else:
                    detected_kind = "zip_with_data_files"
                    score += 3
                    signals.append(zip_check["reason"])
                    if zip_check["data_files"]:
                        signals.append(f"zip_data_files:{len(zip_check['data_files'])}")
            else:
                if final_ext == ".zip":
                    detected_kind = "zip"
                    score += 1
                    signals.append("zip_signature_without_clear_data_files")

    # 5. Refuerzo por content type solo si no es HTML sospechoso
    if not is_html and content_type in DATA_CONTENT_TYPES:
        score += 1
        signals.append("content_type_supports_data")

    return {
        "matched": score > 0,
        "score": score,
        "detected_kind": detected_kind,
        "signals": signals
    }


def download_sample(url: str, timeout: int = DOWNLOAD_TIMEOUT, max_bytes: int = DOWNLOAD_MAX_BYTES) -> dict:
    """
    Descarga solo una muestra del recurso para inspección.
    """
    try:
        with requests.get(url, allow_redirects=True, timeout=timeout, stream=True) as response:
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            content_length = response.headers.get("Content-Length", "")
            final_url = response.url

            chunks = []
            total = 0

            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    break

            data = b"".join(chunks)

            return {
                "ok": True,
                "status_code": response.status_code,
                "content_type": content_type,
                "content_length": content_length,
                "final_url": final_url,
                "bytes_downloaded": len(data),
                "data": data
            }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "data": b""
        }


# ==================================
# H1: extensión de fichero
# ==================================
def heuristic_extension(url: str) -> dict:
    ext = get_extension(url)

    if ext in EXCLUDED_EXTENSIONS:
        return {
            "heuristic": "extension",
            "matched": False,
            "value": ext,
            "reason": "excluded_extension",
            "score": 0
        }

    matched = ext in DATA_EXTENSIONS

    return {
        "heuristic": "extension",
        "matched": matched,
        "value": ext,
        "reason": "data_extension" if matched else "no_data_extension",
        "score": 3 if matched else 0
    }


# ==================================
# H2: metadatos HTTP de la URL
# ==================================
def heuristic_http_metadata(url: str, timeout: int = 8) -> dict:
    try:
        response = requests.head(
            url,
            allow_redirects=True,
            timeout=timeout
        )

        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        content_length = response.headers.get("Content-Length", "")
        disposition = response.headers.get("Content-Disposition", "").lower()

        if response.status_code >= 400 or not content_type:
            response = requests.get(
                url,
                allow_redirects=True,
                timeout=timeout,
                stream=True
            )

            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            content_length = response.headers.get("Content-Length", "")
            disposition = response.headers.get("Content-Disposition", "").lower()

        final_url = response.url
        final_domain = get_domain(final_url)
        final_ext = get_extension(final_url)

        signals = []
        score = 0

        if content_type in DATA_CONTENT_TYPES:
            signals.append("data_content_type")
            score += 3

        if final_ext in DATA_EXTENSIONS:
            signals.append("final_data_extension")
            score += 2

        if "attachment" in disposition:
            signals.append("attachment_download")
            score += 1

        matched_domain = ""
        for known in KNOWN_DATASET_DOMAINS:
            if final_domain == known or final_domain.endswith("." + known):
                matched_domain = known
                signals.append("known_dataset_domain")
                score += 1
                break

        if content_length:
            signals.append("has_content_length")

        matched = score > 0

        return {
            "heuristic": "http_metadata",
            "matched": matched,
            "score": score,
            "reason": "metadata_signals_found" if matched else "no_metadata_signal",
            "value": {
                "status_code": response.status_code,
                "content_type": content_type,
                "content_length": content_length,
                "content_disposition": disposition,
                "final_url": final_url,
                "final_domain": final_domain,
                "final_extension": final_ext,
                "matched_domain": matched_domain,
                "is_doi_input": is_doi_url(url),
                "signals": signals
            }
        }

    except Exception as e:
        return {
            "heuristic": "http_metadata",
            "matched": False,
            "score": 0,
            "reason": "http_error",
            "value": {
                "error": str(e),
                "is_doi_input": is_doi_url(url)
            }
        }


# ==================================
# H3: reutilización estilo GAP-KGE
# ==================================
def heuristic_gap_kge_style(url: str) -> dict:
    domain = get_domain(url)
    tokens = tokenize_url(url)

    matched_domain = ""
    for known in KNOWN_DATASET_DOMAINS:
        if domain == known or domain.endswith("." + known):
            matched_domain = known
            break

    positive_tokens = sorted(tokens.intersection(URL_DATASET_KEYWORDS))
    negative_tokens = sorted(tokens.intersection(URL_NEGATIVE_KEYWORDS))

    score = 0
    if matched_domain:
        score += 2
    if positive_tokens:
        score += min(2, len(positive_tokens))
    if negative_tokens:
        score -= min(2, len(negative_tokens))

    matched = score > 0

    return {
        "heuristic": "gap_kge_style",
        "matched": matched,
        "value": {
            "domain": domain,
            "matched_domain": matched_domain,
            "positive_tokens": positive_tokens,
            "negative_tokens": negative_tokens
        },
        "reason": "gap_like_url_signals" if matched else "no_gap_like_signal",
        "score": max(score, 0)
    }


# ==================================
# H4: basada en patrones de URL
# ==================================
def heuristic_url_pattern(url: str) -> dict:
    path = get_path(url)
    tokens = tokenize_url(url)

    positive_patterns = [
        "/dataset",
        "/datasets",
        "/data",
        "/download",
        "/downloads",
        "/challenge",
        "/corpus",
        "/benchmark"
    ]

    negative_patterns = [
        "/paper",
        ".pdf",
        "/wiki",
        "/docs",
        "/blog",
        "/slides",
        "/software",
        "/github"
    ]

    found_positive = [p for p in positive_patterns if p in path]
    found_negative = [p for p in negative_patterns if p in path]

    if "dataset" in tokens or "datasets" in tokens:
        found_positive.append("token:dataset")
    if "paper" in tokens or "pdf" in tokens:
        found_negative.append("token:paper/pdf")

    score = 0
    if found_positive:
        score += min(2, len(found_positive))
    if found_negative:
        score -= min(2, len(found_negative))

    matched = score > 0

    return {
        "heuristic": "url_pattern",
        "matched": matched,
        "value": {
            "positive_patterns": found_positive,
            "negative_patterns": found_negative
        },
        "reason": "url_pattern_signal" if matched else "no_url_pattern_signal",
        "score": max(score, 0)
    }


# ==================================
# H5: señales desde JSON de GAP-KGE / DataStet
# ==================================
def heuristic_gap_kge_json(url: str, paper: str, base_dir: str = GAP_KGE_JSON_DIR) -> dict:
    json_path = get_gap_kge_json_path(paper, base_dir=base_dir)

    if not json_path or not Path(json_path).exists():
        return {
            "heuristic": "gap_kge_json",
            "matched": False,
            "score": 0,
            "reason": "json_not_found",
            "value": {
                "json_path": json_path,
                "matched_exact_url": False,
                "matched_same_domain": False,
                "json_url_count": 0,
                "json_mention_count": 0,
                "max_json_score": None,
                "sample_mentions": [],
                "sample_urls": [],
                "signals": []
            }
        }

    summary = summarize_gap_kge_json(json_path)

    if not summary["exists"]:
        return {
            "heuristic": "gap_kge_json",
            "matched": False,
            "score": 0,
            "reason": summary.get("error", "json_unreadable"),
            "value": {
                "json_path": json_path,
                "matched_exact_url": False,
                "matched_same_domain": False,
                "json_url_count": 0,
                "json_mention_count": 0,
                "max_json_score": None,
                "sample_mentions": [],
                "sample_urls": [],
                "signals": []
            }
        }

    input_url_norm = normalize_url_loose(url)
    input_domain = get_domain(url)

    json_urls = summary["urls"]
    json_domains = {get_domain(u) for u in json_urls if u}

    matched_exact_url = input_url_norm in json_urls
    matched_same_domain = input_domain in json_domains if input_domain else False

    score = 0
    signals = []

    if matched_exact_url:
        score += 4
        signals.append("exact_url_in_gap_kge_json")
    elif matched_same_domain:
        score += 2
        signals.append("same_domain_in_gap_kge_json")

    if summary["mention_count"] > 0:
        score += 1
        signals.append("dataset_mentions_in_json")

    max_json_score = summary.get("max_score")
    if max_json_score is not None and max_json_score > 0:
        score += 1
        signals.append("positive_confidence_score")

    matched = score > 0

    return {
        "heuristic": "gap_kge_json",
        "matched": matched,
        "score": score,
        "reason": "gap_kge_json_signal" if matched else "no_gap_kge_json_signal",
        "value": {
            "json_path": json_path,
            "matched_exact_url": matched_exact_url,
            "matched_same_domain": matched_same_domain,
            "json_url_count": summary["url_count"],
            "json_mention_count": summary["mention_count"],
            "max_json_score": max_json_score,
            "sample_mentions": summary["mentions"][:10],
            "sample_urls": summary["urls"][:10],
            "signals": signals
        }
    }


# ==================================
# H6: inspección temporal del contenido descargado
# ==================================
def heuristic_download_inspection(url: str, timeout: int = DOWNLOAD_TIMEOUT) -> dict:
    """
    Descarga una muestra del contenido a un fichero temporal real,
    comprueba si realmente parece un archivo de datos, aunque la URL
    no lo indique claramente, y después elimina ese fichero temporal.
    """
    download_info = save_download_to_temp_file(
        url,
        timeout=timeout,
        max_bytes=DOWNLOAD_MAX_BYTES,
        temp_dir=TEMP_DOWNLOAD_DIR
    )

    if not download_info["ok"]:
        return {
            "heuristic": "download_inspection",
            "matched": False,
            "score": 0,
            "reason": "download_error",
            "value": {
                "error": download_info.get("error", ""),
                "temp_path": "",
                "detected_kind": "",
                "signals": []
            }
        }

    temp_path = download_info.get("temp_path", "")

    try:
        inspection = inspect_saved_file(
            temp_path=temp_path,
            content_type=download_info.get("content_type", ""),
            final_url=download_info.get("final_url", ""),
            final_ext=download_info.get("final_extension", "")
        )

        return {
            "heuristic": "download_inspection",
            "matched": inspection["matched"],
            "score": inspection["score"],
            "reason": "download_content_signal" if inspection["matched"] else "no_download_content_signal",
            "value": {
                "status_code": download_info.get("status_code", ""),
                "content_type": download_info.get("content_type", ""),
                "content_length": download_info.get("content_length", ""),
                "final_url": download_info.get("final_url", ""),
                "final_extension": download_info.get("final_extension", ""),
                "bytes_downloaded": download_info.get("bytes_downloaded", 0),
                "temp_path": temp_path,
                "detected_kind": inspection.get("detected_kind", ""),
                "signals": inspection.get("signals", [])
            }
        }

    finally:
        cleanup_file(temp_path)
        cleanup_dir_if_empty(TEMP_DOWNLOAD_DIR)


# ==================================
# Aplicación global
# ==================================
def apply_heuristics(
    url: str,
    paper: str = "",
    use_http: bool = False,
    use_download_inspection: bool = False
) -> dict:
    """
    Aplica las señales internas, pero las agrupa en SOLO 3 heurísticas reales:

    - Heurística 1: extensión de la URL o del archivo descargado.
      Incluye internamente H1 + H4 + H6.

    - Heurística 2: metadatos HTTP.
      Incluye internamente H2.

    - Heurística 3: GAP-KGE.
      Solo usa internamente H3: gap_kge_style_matched y gap_kge_style_score.
      H5 / gap_kge_json se ignora para el benchmark.

    IMPORTANTE:
    Si la heurística 3 detecta dataset, se acepta directamente como dataset.
    """

    # =========================
    # Señales internas
    # =========================
    h1_extension = heuristic_extension(url)
    h4_url_pattern = heuristic_url_pattern(url)

    h6_download = None
    if use_download_inspection:
        h6_download = heuristic_download_inspection(url)

    h2_http = None
    if use_http:
        h2_http = heuristic_http_metadata(url)

    h3_gap_style = heuristic_gap_kge_style(url)

    # IMPORTANTE:
    # Antes aquí también se calculaba H5 con heuristic_gap_kge_json(url, paper).
    # Ahora la heurística GAP-KGE debe centrarse SOLO en estas dos variables:
    # - gap_kge_style_matched
    # - gap_kge_style_score
    # Por eso H5 se ignora completamente para la decisión y para el score.
    h5_gap_json = None

    # =========================
    # HEURÍSTICA 1
    # Extensión de URL o archivo descargado
    # H1 + H4 + H6
    # =========================
    heuristica_1_matched = (
        h1_extension["matched"]
        or h4_url_pattern["matched"]
        or (h6_download["matched"] if h6_download else False)
    )

    heuristica_1_score = (
        h1_extension["score"]
        + h4_url_pattern["score"]
        + (h6_download["score"] if h6_download else 0)
    )

    heuristica_1_signals = []
    if h1_extension["matched"]:
        heuristica_1_signals.append("extension_data_file")
    if h4_url_pattern["matched"]:
        heuristica_1_signals.append("url_pattern_data_file")
    if h6_download and h6_download["matched"]:
        heuristica_1_signals.append("downloaded_file_looks_like_data")

    # =========================
    # HEURÍSTICA 2
    # Metadatos HTTP
    # H2
    # =========================
    heuristica_2_matched = h2_http["matched"] if h2_http else False
    heuristica_2_score = h2_http["score"] if h2_http else 0

    heuristica_2_signals = []
    if h2_http and h2_http["matched"]:
        http_value = h2_http.get("value", {})
        heuristica_2_signals = http_value.get("signals", [])

    # =========================
    # HEURÍSTICA 3
    # GAP-KGE
    # SOLO H3: gap_kge_style_matched + gap_kge_style_score
    # H5 / gap_kge_json queda ignorado.
    # =========================
    heuristica_3_matched = h3_gap_style["matched"]
    heuristica_3_score = h3_gap_style["score"]

    heuristica_3_signals = []
    if h3_gap_style["matched"]:
        heuristica_3_signals.append("gap_kge_style_signal")

    # =========================
    # Decisión final
    # =========================
    # Prioridad absoluta: GAP-KGE.
    # Si GAP-KGE dice que es dataset, ya no necesitamos más señales.
    if heuristica_3_matched:
        label = "dataset"
        decision_reason = "gap_kge_detected_dataset"
    elif heuristica_1_matched:
        label = "dataset"
        decision_reason = "extension_or_downloaded_file_detected_dataset"
    elif heuristica_2_matched:
        label = "dataset"
        decision_reason = "http_metadata_detected_dataset"
    else:
        label = "not_dataset"
        decision_reason = "no_heuristic_matched"

    total_score = heuristica_1_score + heuristica_2_score + heuristica_3_score

    return {
        "url": url,
        "paper": paper,

        # Resultado final por heurísticas reales
        "heuristica_1": {
            "name": "extension_url_o_archivo",
            "matched": heuristica_1_matched,
            "score": heuristica_1_score,
            "signals": heuristica_1_signals,
            "internal_results": {
                "h1_extension": h1_extension,
                "h4_url_pattern": h4_url_pattern,
                "h6_download_inspection": h6_download
            }
        },

        "heuristica_2": {
            "name": "http_metadata",
            "matched": heuristica_2_matched,
            "score": heuristica_2_score,
            "signals": heuristica_2_signals,
            "internal_results": {
                "h2_http_metadata": h2_http
            }
        },

        "heuristica_3": {
            "name": "gap_kge",
            "matched": heuristica_3_matched,
            "score": heuristica_3_score,
            "signals": heuristica_3_signals,
            "internal_results": {
                "h3_gap_kge_style": h3_gap_style
            }
        },

        "total_score": total_score,
        "label": label,
        "decision_reason": decision_reason
    }

# ==================================
# Lectura CSV normalizado
# ==================================
def load_normalized_csv(path):
    rows = []

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "paper": row.get("paper", "").strip(),
                "section": row.get("section", "").strip(),
                "original_url": row.get("original_url", "").strip(),
                "normalized_url": row.get("normalized_url", "").strip(),
                "domain": row.get("domain", "").strip(),
                "extension": row.get("extension", "").strip(),
                "is_data_extension": row.get("is_data_extension", "").strip()
            })

    return rows


# ==================================
# Procesar filas
# ==================================
def process_rows(rows, use_http=False, use_download_inspection=False):
    """
    Procesa las URLs y guarda SOLO 3 heurísticas principales en el CSV final.

    Las señales internas siguen guardándose en el JSON para trazabilidad,
    pero el CSV queda preparado para que el benchmark no evalúe H1, H4, H6,
    H3 y H5 como heurísticas separadas.
    """
    results_csv = []
    results_json = []

    for row in rows:
        url = row["normalized_url"]

        result = apply_heuristics(
            url,
            paper=row["paper"],
            use_http=use_http,
            use_download_inspection=use_download_inspection
        )

        h1 = result["heuristica_1"]
        h2 = result["heuristica_2"]
        h3 = result["heuristica_3"]

        h1_internal = h1.get("internal_results", {})
        h2_internal = h2.get("internal_results", {})
        h3_internal = h3.get("internal_results", {})

        h1_extension = h1_internal.get("h1_extension") or {}
        h4_url_pattern = h1_internal.get("h4_url_pattern") or {}
        h6_download = h1_internal.get("h6_download_inspection") or {}

        h2_http = h2_internal.get("h2_http_metadata") or {}

        h3_gap_style = h3_internal.get("h3_gap_kge_style") or {}
        # H5 / gap_kge_json ya no se usa en la heurística GAP-KGE.
        # La heurística 3 se limita a gap_kge_style_matched y gap_kge_style_score.
        h5_gap_json = {}
        gap_json_value = {}
        http_value = h2_http.get("value", {}) if h2_http else {}
        download_value = h6_download.get("value", {}) if h6_download else {}

        row_result = {
            "paper": row["paper"],
            "section": row["section"],
            "original_url": row["original_url"],
            "normalized_url": row["normalized_url"],
            "domain": row["domain"],
            "extension": row["extension"],

            # =====================================================
            # SOLO 3 HEURÍSTICAS REALES PARA EL BENCHMARK
            # =====================================================
            "heuristica_1_extension_url_o_archivo_matched": h1["matched"],
            "heuristica_1_extension_url_o_archivo_score": h1["score"],
            "heuristica_1_extension_url_o_archivo_signals": "|".join(h1.get("signals", [])),

            "heuristica_2_http_metadata_matched": h2["matched"],
            "heuristica_2_http_metadata_score": h2["score"],
            "heuristica_2_http_metadata_signals": "|".join(h2.get("signals", [])),

            "heuristica_3_gap_kge_matched": h3["matched"],
            "heuristica_3_gap_kge_score": h3["score"],
            "heuristica_3_gap_kge_signals": "|".join(h3.get("signals", [])),

            "total_score": result["total_score"],
            "label": result["label"],
            "decision_reason": result["decision_reason"],

            # =====================================================
            # Columnas auxiliares: sirven para depurar, NO para benchmark
            # =====================================================
            "debug_h1_extension_matched": h1_extension.get("matched", ""),
            "debug_h1_extension_score": h1_extension.get("score", ""),
            "debug_h1_extension_reason": h1_extension.get("reason", ""),
            "debug_h1_extension_value": h1_extension.get("value", ""),

            "debug_h4_url_pattern_matched": h4_url_pattern.get("matched", ""),
            "debug_h4_url_pattern_score": h4_url_pattern.get("score", ""),
            "debug_h4_url_pattern_reason": h4_url_pattern.get("reason", ""),

            "debug_h6_download_matched": h6_download.get("matched", ""),
            "debug_h6_download_score": h6_download.get("score", ""),
            "debug_h6_download_detected_kind": download_value.get("detected_kind", ""),
            "debug_h6_download_signals": "|".join(download_value.get("signals", [])) if download_value else "",

            "debug_h2_http_status_code": http_value.get("status_code", ""),
            "debug_h2_http_content_type": http_value.get("content_type", ""),
            "debug_h2_http_content_disposition": http_value.get("content_disposition", ""),
            "debug_h2_http_final_url": http_value.get("final_url", ""),
            "debug_h2_http_final_extension": http_value.get("final_extension", ""),

            "gap_kge_style_matched": h3_gap_style.get("matched", ""),
            "gap_kge_style_score": h3_gap_style.get("score", "")
        }

        results_csv.append(row_result)

        # En JSON se conserva todo el detalle interno para poder justificar
        # de dónde sale cada decisión.
        results_json.append({
            "paper": row["paper"],
            "section": row["section"],
            "original_url": row["original_url"],
            "normalized_url": row["normalized_url"],
            "result": result
        })

    return results_csv, results_json


# ==================================
# Guardado
# ==================================
def save_csv(rows, path):
    if not rows:
        return

    fields = rows[0].keys()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def save_json(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)


# ==================================
# MAIN
# ==================================
def main():
    if not Path(INPUT_CSV).exists():
        print(f"No existe {INPUT_CSV}")
        return

    rows = load_normalized_csv(INPUT_CSV)
    print(f"URLs normalizadas leídas: {len(rows)}")

    # Activa estas opciones según quieras experimentar
    USE_HTTP = True
    USE_DOWNLOAD_INSPECTION = True

    results_csv, results_json = process_rows(
        rows,
        use_http=USE_HTTP,
        use_download_inspection=USE_DOWNLOAD_INSPECTION
    )

    save_csv(results_csv, OUTPUT_CSV)
    save_json(results_json, OUTPUT_JSON)

    print("Resultados guardados en:")
    print(f"- {OUTPUT_CSV}")
    print(f"- {OUTPUT_JSON}")

    dataset_count = sum(1 for r in results_csv if r["label"] == "dataset")
    maybe_count = sum(1 for r in results_csv if r["label"] == "maybe_dataset")
    not_count = sum(1 for r in results_csv if r["label"] == "not_dataset")

    print("\nResumen:")
    print(f"- dataset: {dataset_count}")
    print(f"- maybe_dataset: {maybe_count}")
    print(f"- not_dataset: {not_count}")


if __name__ == "__main__":
    main()