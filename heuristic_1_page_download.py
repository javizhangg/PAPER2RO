import csv
import json
import re
import io
import zipfile
from pathlib import Path
from urllib.parse import urlparse, urljoin, unquote
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


# ==============================
# RUTAS Y CONFIGURACIÓN
# ==============================

INPUT_CSV = "outputs/all_links_normalized.csv"
OUTPUT_CSV = "outputs/heuristic_1_results.csv"
OUTPUT_JSON = "outputs/heuristic_1_results.json"

REQUEST_TIMEOUT = 10
MAX_SAMPLE_BYTES = 1_000_000
MAX_ZIP_DOWNLOAD_BYTES = 100_000_000  # 100 MB máximo para inspeccionar ZIPs
MAX_WORKERS = 16

# Si está en False, solo acepta descargables:
# - del mismo dominio raíz
# - o de repositorios confiables de datasets
ALLOW_EXTERNAL_DATASET_LINKS = False


# ==============================
# REGEX
# ==============================

URL_REGEX = re.compile(r'https?://[^\s"\'<>\)\]]+', re.IGNORECASE)
HREF_REGEX = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
SRC_REGEX = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)
CONTENT_DISPOSITION_FILENAME_REGEX = re.compile(
    r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)',
    re.IGNORECASE
)


# ==============================
# EXTENSIONES DE DATASET
# ==============================

DATASET_DOWNLOAD_EXTENSIONS = {
    ".csv",
    ".tsv",
    ".tab",

    ".parquet",
    ".feather",
    ".arff",

    ".h5",
    ".hdf5",

    ".sqlite",
    ".sqlite3",
    ".db",

    ".xls",
    ".xlsx",
    ".ods",

    ".sav",
    ".dta",
    ".sas7bdat",

    ".npy",
    ".npz",
    ".mat"
}

# Estos no se aceptan siempre.
# Se aceptan si:
# 1) vienen combinados con una extensión clara de dataset: .csv.gz, .tsv.gz, etc.
# 2) o si el nombre/ruta tiene contexto de dataset.
COMPRESSED_EXTENSIONS = {
    ".zip",
    ".gz",
    ".tar",
    ".tgz",
    ".bz2",
    ".xz"
}

# ZIP se trata aparte: no se marca como dataset solo por acabar en .zip.
# Se descarga y se abre para comprobar si dentro hay CSV.
ZIP_EXTENSIONS = {".zip"}
ZIP_DATASET_EXTENSIONS_TO_REPORT = DATASET_DOWNLOAD_EXTENSIONS

# Extensiones que NO queremos aceptar como dataset directo
# porque dan muchos falsos positivos.
EXCLUDED_EXTENSIONS = {
    ".json",
    ".xml",
    ".rdf",
    ".ttl",
    ".nt",
    ".owl",
    ".html",
    ".htm",
    ".php",
    ".asp",
    ".aspx",
    ".pdf",
    ".txt"
}

DATASET_NAME_KEYWORDS = {
    "dataset",
    "datasets",
    "data",
    "datos",
    "train",
    "training",
    "test",
    "valid",
    "validation",
    "dev",
    "labels",
    "label",
    "annotations",
    "annotation",
    "features",
    "samples",
    "records",
    "corpus",
    "benchmark",
    "database",
    "db",
    "tables",
    "table",
    "supplementary",
    "supplemental",
    "supporting",
    "raw",
    "processed"
}

# Content-Type que pueden indicar archivo descargable de dataset.
# OJO: application/octet-stream por sí solo no basta.
# Se acepta si además hay filename o contexto de dataset.
DATASET_CONTENT_TYPES = {
    "text/csv",
    "text/tab-separated-values",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/x-parquet",
    "application/parquet",
    "application/vnd.apache.parquet",
    "application/zip",
    "application/gzip",
    "application/x-gzip",
    "application/x-hdf5",
    "application/x-sqlite3",
    "application/octet-stream"
}

HTML_OR_TEXT_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain"
}

# Repositorios externos habituales donde sí es razonable aceptar descargables
# aunque no estén en el mismo dominio que la URL original.
TRUSTED_DATA_REPOSITORY_DOMAINS = {
    "zenodo.org",
    "figshare.com",
    "osf.io",
    "datadryad.org",
    "dryad.figshare.com",
    "github.com",
    "raw.githubusercontent.com",
    "huggingface.co",
    "kaggle.com",
    "mendeley.com",
    "data.mendeley.com",
    "dataverse.harvard.edu",
    "openaire.eu",
    "ebi.ac.uk",
    "ncbi.nlm.nih.gov",
    "ftp.ncbi.nlm.nih.gov",
    "www.ebi.ac.uk"
}


# ==============================
# UTILIDADES DE URL
# ==============================

def clean_url(url: str) -> str:
    if not url:
        return ""

    return str(url).strip().rstrip(".,;:!?)]}>'\"")


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

    # Regla simple. Para máxima precisión con dominios tipo .co.uk
    # se podría usar tldextract, pero así evitamos dependencias externas.
    return ".".join(parts[-2:])


def get_path(url: str) -> str:
    try:
        return urlparse(url).path.lower()
    except Exception:
        return ""


def get_filename_from_url(url: str) -> str:
    try:
        return Path(urlparse(url).path).name.lower()
    except Exception:
        return ""


def get_extension(url: str) -> str:
    try:
        return Path(get_filename_from_url(url)).suffix.lower()
    except Exception:
        return ""


def get_all_extensions(url: str) -> list:
    try:
        return [s.lower() for s in Path(get_filename_from_url(url)).suffixes]
    except Exception:
        return []


def filename_has_dataset_context(url: str) -> bool:
    """
    Comprueba si el nombre/ruta/query de la URL parece de dataset.
    Esto sirve sobre todo para aceptar comprimidos genéricos como .zip.
    """

    try:
        parsed = urlparse(url)
        text = f"{parsed.netloc} {parsed.path} {parsed.query}".lower()

        tokens = {
            t for t in re.split(r"[^a-zA-Z0-9]+", text)
            if t
        }

        return bool(tokens.intersection(DATASET_NAME_KEYWORDS))

    except Exception:
        return False


def is_kaggle_url(url: str) -> bool:
    """
    Devuelve True si la URL pertenece a Kaggle.
    No exige que el path tenga /datasets/.
    """
    try:
        parsed = urlparse(clean_url(url))
        domain = parsed.netloc.lower().strip()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain == "kaggle.com" or domain.endswith(".kaggle.com")

    except Exception:
        return False

def is_kaggle_dataset_url(url: str) -> bool:
    """
    Devuelve True si la URL pertenece a Kaggle y en el path aparece
    el segmento datasets/dataset.

    Ejemplos positivos:
      https://www.kaggle.com/datasets/usuario/nombre-dataset
      https://kaggle.com/datasets/usuario/nombre-dataset/data

    No necesita descargar la página: solo usa urlparse().
    """

    try:
        parsed = urlparse(clean_url(url))
        domain = parsed.netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        if domain != "kaggle.com":
            return False

        path = unquote(parsed.path or "").lower()
        path = re.sub(r"/{2,}", "/", path).strip("/")
        parts = [p for p in path.split("/") if p]

        # Kaggle usa normalmente /datasets/<usuario>/<dataset>.
        # Acepto también "dataset" por si aparece en alguna ruta parseada.
        return "datasets" in parts or "dataset" in parts

    except Exception:
        return False


def kaggle_dataset_result(url: str) -> dict:
    """
    Resultado positivo estándar para páginas de dataset en Kaggle.
    """

    return {
        "matched": True,
        "reason": "kaggle_dataset_path",
        "value": {
            "url": url,
            "es_dataset_directo": False,
            "tipo_dataset_descargable": "kaggle_dataset_page",
            "pagina_con_descargables": True,
            "dataset_descargable": url,
            "dataset_descargables_encontrados": [url],
            "json_descargado": False,
            "html_descargado": False,
            "header_checked": False,
            "zip_descargado": False,
            "zip_contiene_csv": False,
            "zip_csv_encontrados": [],
            "zip_total_archivos": 0,
            "zip_archivos_sample": [],
            "zip_error": "",
            "header_content_type": "",
            "header_content_disposition": "",
            "total_descargables_json": 0,
            "total_descargables_html": 0,
            "json_dataset_descargables_encontrados": [],
            "html_dataset_descargables_encontrados": []
        }
    }



def kaggle_html_dataset_result(url: str, html_value: dict | None = None) -> dict:
    """
    Resultado positivo estándar cuando una página de Kaggle contiene
    la palabra 'dataset' en el HTML descargado.
    """

    html_value = html_value or {}

    return {
        "matched": True,
        "reason": "kaggle_html_contains_dataset_word",
        "value": {
            "url": url,
            "es_dataset_directo": False,
            "tipo_dataset_descargable": "kaggle_dataset_page_html",
            "pagina_con_descargables": True,
            "dataset_descargable": url,
            "dataset_descargables_encontrados": [url],
            "json_descargado": False,
            "html_descargado": bool(html_value.get("html_descargado", True)),
            "header_checked": False,
            "zip_descargado": False,
            "zip_contiene_csv": False,
            "zip_csv_encontrados": [],
            "zip_total_archivos": 0,
            "zip_archivos_sample": [],
            "zip_error": "",
            "header_content_type": "",
            "header_content_disposition": "",
            "total_descargables_json": 0,
            "total_descargables_html": 0,
            "json_dataset_descargables_encontrados": [],
            "html_dataset_descargables_encontrados": [url],
            "html_content_type": html_value.get("html_content_type", ""),
            "html_final_url": html_value.get("html_final_url", "")
        }
    }


def check_kaggle_html_contains_dataset(url: str, session=None) -> dict:
    """
    Regla extra para Kaggle:
    - Si la URL es de Kaggle,
    - descarga el HTML,
    - y si aparece la palabra 'dataset' o 'datasets',
      la heurística devuelve dataset=True.

    Esto sirve para URLs de Kaggle que no tienen extensión .csv/.zip
    y que tampoco siempre exponen descargables directos en el HTML.
    """

    if not is_kaggle_url(url):
        return {
            "matched": False,
            "reason": "not_kaggle_url",
            "value": {
                "html_descargado": False,
                "html_error_reason": "",
                "html_content_type": "",
                "html_final_url": ""
            }
        }

    html_response = fetch_html_sample(url, session=session)

    if not html_response.get("ok"):
        return {
            "matched": False,
            "reason": "kaggle_html_could_not_be_downloaded",
            "value": {
                "html_descargado": False,
                "html_error_reason": html_response.get("reason", ""),
                "html_error": html_response.get("error", ""),
                "html_content_type": html_response.get("content_type", ""),
                "html_final_url": html_response.get("final_url", "")
            }
        }

    html = html_response.get("text", "") or ""

    # Búsqueda por palabra completa para evitar coincidencias raras dentro de otras palabras.
    # Acepta dataset y datasets.
    if re.search(r"\bdatasets?\b", html, flags=re.IGNORECASE):
        return {
            "matched": True,
            "reason": "kaggle_html_contains_dataset_word",
            "value": {
                "html_descargado": True,
                "html_content_type": html_response.get("content_type", ""),
                "html_final_url": html_response.get("final_url", "")
            }
        }

    return {
        "matched": False,
        "reason": "kaggle_html_downloaded_but_dataset_word_not_found",
        "value": {
            "html_descargado": True,
            "html_content_type": html_response.get("content_type", ""),
            "html_final_url": html_response.get("final_url", "")
        }
    }

def url_ends_with_dataset_file(url: str) -> bool:
    """
    Devuelve True si la URL acaba en un formato típico de dataset.

    Importante:
    - No acepta JSON/XML/RDF/HTML/PDF/TXT como dataset directo.
    - Acepta .csv.gz, .tsv.gz, .parquet.gz, etc.
    - NO acepta .zip directamente.
      Los ZIP se descargan y se inspeccionan con check_zip_for_csv().
    """

    url = clean_url(url)

    ext = get_extension(url)
    all_ext = get_all_extensions(url)

    if ext in EXCLUDED_EXTENSIONS:
        return False

    # Un ZIP no se acepta directamente. Hay que abrirlo y mirar dentro.
    if ext in ZIP_EXTENSIONS:
        return False

    # Formatos directos de dataset
    if ext in DATASET_DOWNLOAD_EXTENSIONS:
        return True

    has_dataset_ext = any(e in DATASET_DOWNLOAD_EXTENSIONS for e in all_ext)
    has_compressed_ext = any(e in COMPRESSED_EXTENSIONS for e in all_ext)

    # Ejemplo: .csv.gz, .tsv.gz, .parquet.gz
    if has_dataset_ext and has_compressed_ext:
        return True

    # Comprimidos no ZIP tipo training_data.tar.gz.
    # OJO: no se abre el TAR aquí; solo se acepta si el nombre da contexto.
    if has_compressed_ext and ext not in ZIP_EXTENSIONS:
        return filename_has_dataset_context(url)

    return False

def get_dataset_file_type(url: str) -> str:
    """
    Devuelve la extensión principal detectada.

    Para .csv.gz devuelve .csv,
    porque lo importante es el tipo de dato real.
    """

    all_ext = get_all_extensions(url)

    for ext in reversed(all_ext):
        if ext in DATASET_DOWNLOAD_EXTENSIONS:
            return ext

    for ext in reversed(all_ext):
        if ext in COMPRESSED_EXTENSIONS:
            return ext

    return get_extension(url)


def is_same_root_domain(url1: str, url2: str) -> bool:
    d1 = root_domain(get_domain(url1))
    d2 = root_domain(get_domain(url2))

    return bool(d1 and d2 and d1 == d2)


def is_trusted_data_repository(url: str) -> bool:
    domain = get_domain(url)

    if not domain:
        return False

    domain = domain.lower()

    if domain.startswith("www."):
        domain_no_www = domain[4:]
    else:
        domain_no_www = domain

    if domain_no_www in TRUSTED_DATA_REPOSITORY_DOMAINS:
        return True

    # Permite subdominios de repositorios confiables.
    for trusted in TRUSTED_DATA_REPOSITORY_DOMAINS:
        if domain_no_www.endswith("." + trusted):
            return True

    return False

def is_super_famous_dataset_domain_url(url: str) -> bool:
    """
    Marca directamente como dataset URLs de repositorios muy famosos.

    OJO:
    - Kaggle se acepta directamente si es Kaggle.
    - Zenodo solo si es /record/ o /records/.
    - HuggingFace solo si es /datasets/.
    - GitHub NO se acepta directamente porque puede ser código, no dataset.
    """

    try:
        url = clean_url(url)
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        path = unquote(parsed.path or "").lower()

        # Kaggle: tú querías que Kaggle sea positivo directamente
        if domain == "kaggle.com" or domain.endswith(".kaggle.com"):
            return True

        # Zenodo datasets/records
        if domain == "zenodo.org":
            return path.startswith("/record/") or path.startswith("/records/")

        # Figshare
        if domain == "figshare.com" or domain.endswith(".figshare.com"):
            return "/articles/" in path or "/datasets/" in path

        # Dryad
        if domain in {"datadryad.org", "dryad.figshare.com"}:
            return True

        # Mendeley Data
        if domain == "data.mendeley.com":
            return "/datasets/" in path

        # Harvard Dataverse
        if domain == "dataverse.harvard.edu":
            return "dataset.xhtml" in path or "/dataset/" in path

        # OSF
        if domain == "osf.io":
            return True

        # HuggingFace datasets
        if domain == "huggingface.co":
            return path.startswith("/datasets/")

        return False

    except Exception:
        return False

def is_allowed_dataset_link(input_url: str, candidate_url: str) -> bool:
    """
    Controla si aceptamos un descargable encontrado.

    Por defecto:
    - mismo dominio raíz: sí
    - repositorio confiable: sí
    - externo desconocido: no, salvo que ALLOW_EXTERNAL_DATASET_LINKS=True
    """

    if ALLOW_EXTERNAL_DATASET_LINKS:
        return True

    if is_same_root_domain(input_url, candidate_url):
        return True

    if is_trusted_data_repository(candidate_url):
        return True

    return False


def filename_from_content_disposition(value: str) -> str:
    if not value:
        return ""

    match = CONTENT_DISPOSITION_FILENAME_REGEX.search(value)

    if match:
        return clean_url(match.group(1).strip())

    return ""


# ==============================
# INSPECCIÓN DE ZIP
# ==============================

def is_zip_by_url(url: str) -> bool:
    """
    Detecta si una URL parece apuntar a un .zip por su nombre/ruta.
    """
    return get_extension(url) in ZIP_EXTENSIONS


def is_zip_by_headers(content_type: str, content_disposition: str, final_url: str) -> bool:
    """
    Detecta ZIP por cabeceras HTTP, filename de Content-Disposition o URL final.
    """
    content_type = (content_type or "").lower().split(";")[0].strip()
    content_disposition = content_disposition or ""

    filename = filename_from_content_disposition(content_disposition)

    if content_type in {"application/zip", "application/x-zip-compressed", "multipart/x-zip"}:
        return True

    if filename and get_extension(filename) in ZIP_EXTENSIONS:
        return True

    if final_url and is_zip_by_url(final_url):
        return True

    return False


def inspect_zip_bytes(raw_zip: bytes) -> dict:
    """
    Abre un ZIP en memoria y revisa si contiene archivos típicos de dataset.
    No acepta JSON ni XML como dataset.
    """

    try:
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
            names = [
                name for name in zf.namelist()
                if name and not name.endswith("/")
            ]

            dataset_files = [
                name for name in names
                if Path(name).suffix.lower() in ZIP_DATASET_EXTENSIONS_TO_REPORT
            ]

            return {
                "ok": True,
                "zip_contiene_dataset": bool(dataset_files),
                "zip_contiene_csv": bool(dataset_files),  # mantengo el nombre para compatibilidad
                "zip_csv_encontrados": dataset_files,     # aquí ahora van CSV, TSV, XLSX, etc.
                "zip_total_archivos": len(names),
                "zip_archivos_sample": names[:50],
                "zip_error": ""
            }

    except zipfile.BadZipFile:
        return {
            "ok": False,
            "zip_contiene_dataset": False,
            "zip_contiene_csv": False,
            "zip_csv_encontrados": [],
            "zip_total_archivos": 0,
            "zip_archivos_sample": [],
            "zip_error": "bad_zip_file"
        }

    except Exception as e:
        return {
            "ok": False,
            "zip_contiene_dataset": False,
            "zip_contiene_csv": False,
            "zip_csv_encontrados": [],
            "zip_total_archivos": 0,
            "zip_archivos_sample": [],
            "zip_error": str(e)
        }


def check_zip_for_csv(url: str, session=None) -> dict:
    """
    Descarga un ZIP y comprueba si dentro contiene al menos un .csv.

    IMPORTANTE:
    - Si el ZIP supera MAX_ZIP_DOWNLOAD_BYTES, no se descarga entero.
    - Si contiene CSV, la URL se considera dataset.
    """

    url = clean_url(url)

    headers = {
        "User-Agent": "Mozilla/5.0 dataset-zip-detector/1.0",
        "Accept": "application/zip, application/octet-stream, */*"
    }

    client = session if session else requests

    try:
        with client.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            stream=True
        ) as response:

            status_code = response.status_code
            content_type = response.headers.get(
                "Content-Type", ""
            ).split(";")[0].strip().lower()
            content_disposition = response.headers.get("Content-Disposition", "")
            content_length_raw = response.headers.get("Content-Length", "")
            final_url = response.url

            if status_code >= 400:
                return {
                    "matched": False,
                    "reason": f"zip_http_status_{status_code}",
                    "dataset_url": final_url,
                    "tipo_dataset_descargable": ".zip",
                    "zip_descargado": False,
                    "zip_contiene_csv": False,
                    "zip_csv_encontrados": [],
                    "zip_total_archivos": 0,
                    "zip_archivos_sample": [],
                    "zip_error": "",
                    "zip_content_type": content_type,
                    "zip_content_length": content_length_raw
                }

            try:
                content_length = int(content_length_raw) if content_length_raw else 0
            except Exception:
                content_length = 0

            if content_length and content_length > MAX_ZIP_DOWNLOAD_BYTES:
                return {
                    "matched": False,
                    "reason": "zip_too_large_to_download",
                    "dataset_url": final_url,
                    "tipo_dataset_descargable": ".zip",
                    "zip_descargado": False,
                    "zip_contiene_csv": False,
                    "zip_csv_encontrados": [],
                    "zip_total_archivos": 0,
                    "zip_archivos_sample": [],
                    "zip_error": f"content_length_{content_length}_exceeds_limit_{MAX_ZIP_DOWNLOAD_BYTES}",
                    "zip_content_type": content_type,
                    "zip_content_length": content_length_raw
                }

            chunks = []
            total = 0

            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue

                chunks.append(chunk)
                total += len(chunk)

                if total > MAX_ZIP_DOWNLOAD_BYTES:
                    return {
                        "matched": False,
                        "reason": "zip_download_exceeded_limit",
                        "dataset_url": final_url,
                        "tipo_dataset_descargable": ".zip",
                        "zip_descargado": False,
                        "zip_contiene_csv": False,
                        "zip_csv_encontrados": [],
                        "zip_total_archivos": 0,
                        "zip_archivos_sample": [],
                        "zip_error": f"download_exceeded_limit_{MAX_ZIP_DOWNLOAD_BYTES}",
                        "zip_content_type": content_type,
                        "zip_content_length": content_length_raw
                    }

            raw_zip = b"".join(chunks)
            inspection = inspect_zip_bytes(raw_zip)

            matched = bool(inspection.get("ok") and inspection.get("zip_contiene_csv"))

            return {
                "matched": matched,
                "reason": "zip_contains_csv" if matched else "zip_downloaded_but_no_csv_found",
                "dataset_url": final_url,
                "tipo_dataset_descargable": ".zip",
                "zip_descargado": True,
                "zip_contiene_csv": bool(inspection.get("zip_contiene_csv", False)),
                "zip_csv_encontrados": inspection.get("zip_csv_encontrados", []),
                "zip_total_archivos": inspection.get("zip_total_archivos", 0),
                "zip_archivos_sample": inspection.get("zip_archivos_sample", []),
                "zip_error": inspection.get("zip_error", ""),
                "zip_content_type": content_type,
                "zip_content_length": content_length_raw,
                "content_disposition": content_disposition
            }

    except Exception as e:
        return {
            "matched": False,
            "reason": "zip_request_exception",
            "dataset_url": url,
            "tipo_dataset_descargable": ".zip",
            "zip_descargado": False,
            "zip_contiene_csv": False,
            "zip_csv_encontrados": [],
            "zip_total_archivos": 0,
            "zip_archivos_sample": [],
            "zip_error": str(e),
            "zip_content_type": "",
            "zip_content_length": ""
        }


# ==============================
# REVISIÓN DE CABECERAS HTTP
# ==============================

def check_url_headers_for_dataset_file(url: str, session=None) -> dict:
    """
    Mira cabeceras HTTP para detectar si una URL que no termina en .csv/.xlsx/etc.
    realmente devuelve un archivo de dataset.

    Ejemplo:
    https://example.com/download?id=123
    Content-Disposition: attachment; filename="data.csv"
    """

    headers = {
        "User-Agent": "Mozilla/5.0 dataset-header-detector/1.0",
        "Accept": "*/*"
    }

    client = session if session else requests

    try:
        response = client.head(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        # Algunos servidores bloquean HEAD o lo implementan mal.
        # En ese caso hacemos GET con Range para no descargar todo.
        if response.status_code in (403, 405) or response.status_code >= 500:
            response = client.get(
                url,
                headers={**headers, "Range": "bytes=0-2048"},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
                stream=True
            )

        status_code = response.status_code
        content_type = response.headers.get(
            "Content-Type", ""
        ).split(";")[0].strip().lower()

        content_disposition = response.headers.get("Content-Disposition", "")
        content_length = response.headers.get("Content-Length", "")
        final_url = response.url

        if status_code >= 400:
            return {
                "matched": False,
                "reason": f"header_http_status_{status_code}",
                "status_code": status_code,
                "final_url": final_url,
                "content_type": content_type,
                "content_disposition": content_disposition,
                "content_length": content_length
            }

        filename = filename_from_content_disposition(content_disposition)

        candidates = [final_url]

        if filename:
            # Si Content-Disposition dice filename="data.csv"
            # lo revisamos como candidato.
            candidates.append(urljoin(final_url, filename))

        for candidate in candidates:
            if url_ends_with_dataset_file(candidate):
                return {
                    "matched": True,
                    "reason": "headers_indicate_dataset_file",
                    "status_code": status_code,
                    "final_url": final_url,
                    "content_type": content_type,
                    "content_disposition": content_disposition,
                    "content_length": content_length,
                    "filename": filename,
                    "dataset_url": final_url,
                    "tipo_dataset_descargable": get_dataset_file_type(candidate)
                }

        # Si las cabeceras o la URL final indican ZIP, se descarga y se inspecciona.
        # Solo será positivo si dentro hay al menos un .csv.
        if is_zip_by_headers(content_type, content_disposition, final_url):
            zip_check = check_zip_for_csv(final_url, session=session)

            if zip_check.get("matched"):
                zip_check.update({
                    "status_code": status_code,
                    "final_url": final_url,
                    "content_type": content_type,
                    "content_disposition": content_disposition,
                    "content_length": content_length,
                    "filename": filename
                })
                return zip_check

            return {
                "matched": False,
                "reason": zip_check.get("reason", "zip_checked_but_no_csv_found"),
                "status_code": status_code,
                "final_url": final_url,
                "content_type": content_type,
                "content_disposition": content_disposition,
                "content_length": content_length,
                "filename": filename,
                "zip_descargado": bool(zip_check.get("zip_descargado", False)),
                "zip_contiene_csv": bool(zip_check.get("zip_contiene_csv", False)),
                "zip_csv_encontrados": zip_check.get("zip_csv_encontrados", []),
                "zip_total_archivos": zip_check.get("zip_total_archivos", 0),
                "zip_archivos_sample": zip_check.get("zip_archivos_sample", []),
                "zip_error": zip_check.get("zip_error", "")
            }

        # Content-Type por sí solo puede ser ambiguo.
        # application/octet-stream solo se acepta si hay attachment/filename/contexto.
        if content_type in DATASET_CONTENT_TYPES:
            has_attachment = "attachment" in content_disposition.lower()
            has_filename = bool(filename)
            has_dataset_context = filename_has_dataset_context(final_url)

            if content_type != "application/octet-stream":
                if has_attachment or has_filename or has_dataset_context:
                    return {
                        "matched": True,
                        "reason": "content_type_indicates_dataset_file",
                        "status_code": status_code,
                        "final_url": final_url,
                        "content_type": content_type,
                        "content_disposition": content_disposition,
                        "content_length": content_length,
                        "filename": filename,
                        "dataset_url": final_url,
                        "tipo_dataset_descargable": get_dataset_file_type(filename or final_url)
                    }

            if content_type == "application/octet-stream":
                if has_attachment and (has_filename or has_dataset_context):
                    return {
                        "matched": True,
                        "reason": "octet_stream_with_dataset_context",
                        "status_code": status_code,
                        "final_url": final_url,
                        "content_type": content_type,
                        "content_disposition": content_disposition,
                        "content_length": content_length,
                        "filename": filename,
                        "dataset_url": final_url,
                        "tipo_dataset_descargable": get_dataset_file_type(filename or final_url)
                    }

        return {
            "matched": False,
            "reason": "headers_do_not_indicate_dataset_file",
            "status_code": status_code,
            "final_url": final_url,
            "content_type": content_type,
            "content_disposition": content_disposition,
            "content_length": content_length,
            "filename": filename
        }

    except Exception as e:
        return {
            "matched": False,
            "reason": "header_check_exception",
            "error": str(e)
        }


def validate_candidate_headers(candidate_url: str, session=None) -> dict:
    """
    Valida un candidato encontrado en HTML/JSON.

    Para máxima precisión:
    - primero se acepta por extensión clara;
    - si no hay extensión clara, se mira Content-Disposition / Content-Type.
    """

    candidate_url = clean_url(candidate_url)

    if not candidate_url:
        return {
            "matched": False,
            "reason": "empty_candidate"
        }

    # Página de Kaggle con /datasets/... => dataset aunque no acabe en .csv/.zip.
    if is_kaggle_dataset_url(candidate_url):
        return {
            "matched": True,
            "reason": "kaggle_dataset_path",
            "dataset_url": candidate_url,
            "tipo_dataset_descargable": "kaggle_dataset_page",
            "zip_descargado": False,
            "zip_contiene_csv": False,
            "zip_csv_encontrados": [],
            "zip_total_archivos": 0,
            "zip_archivos_sample": []
        }

    # Si el candidato es ZIP, no se acepta por nombre:
    # se descarga y se abre para comprobar si contiene CSV.
    if is_zip_by_url(candidate_url):
        return check_zip_for_csv(candidate_url, session=session)

    if url_ends_with_dataset_file(candidate_url):
        return {
            "matched": True,
            "reason": "candidate_url_ends_with_dataset_file",
            "dataset_url": candidate_url,
            "tipo_dataset_descargable": get_dataset_file_type(candidate_url),
            "zip_descargado": False,
            "zip_contiene_csv": False,
            "zip_csv_encontrados": [],
            "zip_total_archivos": 0,
            "zip_archivos_sample": []
        }

    return check_url_headers_for_dataset_file(candidate_url, session=session)


# ==============================
# DESCARGA JSON
# ==============================

def fetch_json_sample(url: str, session=None) -> dict:
    """
    Intenta descargar una muestra de la URL y parsearla como JSON.

    Si la URL devuelve HTML, PDF u otro contenido no JSON,
    devolverá ok=False con reason='response_is_not_valid_json'.
    """

    headers = {
        "User-Agent": "Mozilla/5.0 dataset-json-detector/1.0",
        "Accept": "application/json, application/ld+json, text/json, */*;q=0.8"
    }

    try:
        client = session if session else requests

        with client.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            stream=True
        ) as response:

            status_code = response.status_code
            content_type = response.headers.get(
                "Content-Type", ""
            ).split(";")[0].strip().lower()

            final_url = response.url

            if status_code >= 400:
                return {
                    "ok": False,
                    "reason": f"http_status_{status_code}",
                    "status_code": status_code,
                    "content_type": content_type,
                    "final_url": final_url,
                    "json": None
                }

            chunks = []
            total = 0

            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue

                chunks.append(chunk)
                total += len(chunk)

                if total >= MAX_SAMPLE_BYTES:
                    break

            raw = b"".join(chunks)

            text = ""
            for encoding in ("utf-8", "utf-8-sig", "latin-1"):
                try:
                    text = raw.decode(encoding, errors="replace")
                    break
                except Exception:
                    pass

            text = text.strip()

            if not text:
                return {
                    "ok": False,
                    "reason": "empty_response",
                    "status_code": status_code,
                    "content_type": content_type,
                    "final_url": final_url,
                    "json": None
                }

            try:
                parsed_json = json.loads(text)
            except Exception as e:
                return {
                    "ok": False,
                    "reason": "response_is_not_valid_json",
                    "error": str(e),
                    "status_code": status_code,
                    "content_type": content_type,
                    "final_url": final_url,
                    "json": None,
                    "text_sample": text[:500]
                }

            return {
                "ok": True,
                "reason": "json_downloaded_and_parsed",
                "status_code": status_code,
                "content_type": content_type,
                "final_url": final_url,
                "json": parsed_json
            }

    except Exception as e:
        return {
            "ok": False,
            "reason": "json_request_exception",
            "error": str(e),
            "json": None
        }


# ==============================
# DESCARGA HTML
# ==============================

def fetch_html_sample(url: str, session=None) -> dict:
    """
    Descarga HTML/texto de la URL para revisar si dentro hay enlaces
    a archivos descargables de dataset.

    Ahora evita parsear binarios/PDF/ZIP como si fueran HTML.
    """

    headers = {
        "User-Agent": "Mozilla/5.0 dataset-html-detector/1.0",
        "Accept": "text/html,application/xhtml+xml,text/plain,*/*;q=0.8"
    }

    try:
        client = session if session else requests

        with client.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            stream=True
        ) as response:

            status_code = response.status_code
            content_type = response.headers.get(
                "Content-Type", ""
            ).split(";")[0].strip().lower()

            final_url = response.url

            if status_code >= 400:
                return {
                    "ok": False,
                    "reason": f"http_status_{status_code}",
                    "status_code": status_code,
                    "content_type": content_type,
                    "final_url": final_url,
                    "text": ""
                }

            if content_type and content_type not in HTML_OR_TEXT_CONTENT_TYPES:
                return {
                    "ok": False,
                    "reason": "response_is_not_html_or_text",
                    "status_code": status_code,
                    "content_type": content_type,
                    "final_url": final_url,
                    "text": ""
                }

            chunks = []
            total = 0

            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue

                chunks.append(chunk)
                total += len(chunk)

                if total >= MAX_SAMPLE_BYTES:
                    break

            raw = b"".join(chunks)

            text = ""
            for encoding in ("utf-8", "utf-8-sig", "latin-1"):
                try:
                    text = raw.decode(encoding, errors="replace")
                    break
                except Exception:
                    pass

            return {
                "ok": True,
                "reason": "html_downloaded",
                "status_code": status_code,
                "content_type": content_type,
                "final_url": final_url,
                "text": text
            }

    except Exception as e:
        return {
            "ok": False,
            "reason": "html_request_exception",
            "error": str(e),
            "text": ""
        }


# ==============================
# EXTRACCIÓN DESDE JSON
# ==============================

DOWNLOAD_KEYS = {
    "downloadurl",
    "download_url",
    "contenturl",
    "content_url",
    "url",
    "file",
    "files",
    "href",
    "link",
    "links",
    "distribution",
    "distributions",
    "resources",
    "resource"
}


def normalize_key(key: str) -> str:
    return str(key).lower().replace("-", "_").replace(" ", "_")


def iter_json_items(obj):
    """
    Recorre recursivamente todo el JSON.
    Devuelve pares key, value.
    """

    if isinstance(obj, dict):
        for k, v in obj.items():
            yield normalize_key(k), v
            yield from iter_json_items(v)

    elif isinstance(obj, list):
        for item in obj:
            yield "", item
            yield from iter_json_items(item)


def extract_strings_from_json(obj) -> list:
    """
    Extrae todos los strings que aparecen dentro del JSON.
    """

    strings = []

    if isinstance(obj, dict):
        for value in obj.values():
            strings.extend(extract_strings_from_json(value))

    elif isinstance(obj, list):
        for item in obj:
            strings.extend(extract_strings_from_json(item))

    elif isinstance(obj, str):
        value = obj.strip()
        if value:
            strings.append(value)

    return strings


def extract_downloadable_candidates_from_json(obj, base_url: str) -> list:
    """
    Busca posibles archivos descargables dentro de un JSON.

    Detecta:
    - URLs completas dentro de strings.
    - Rutas relativas que contengan una extensión de dataset.
    - Campos típicos como downloadURL, contentUrl, url, files, resources, etc.
    """

    found = set()

    if obj is None:
        return []

    # 1) Revisar claves típicas de descargables
    for key, value in iter_json_items(obj):
        key_clean = key.replace("_", "")

        if key_clean in DOWNLOAD_KEYS or key in DOWNLOAD_KEYS:

            if isinstance(value, str):
                candidate = value.strip()

                if candidate:
                    found.add(clean_url(urljoin(base_url, candidate)))

            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        candidate = item.strip()

                        if candidate:
                            found.add(clean_url(urljoin(base_url, candidate)))

                    elif isinstance(item, dict):
                        for s in extract_strings_from_json(item):
                            found.add(clean_url(urljoin(base_url, s)))

            elif isinstance(value, dict):
                for s in extract_strings_from_json(value):
                    found.add(clean_url(urljoin(base_url, s)))

    # 2) Buscar URLs completas en cualquier string del JSON
    all_strings = extract_strings_from_json(obj)

    possible_extensions = DATASET_DOWNLOAD_EXTENSIONS.union(COMPRESSED_EXTENSIONS)

    for text in all_strings:
        text = text.strip()

        for match in URL_REGEX.findall(text):
            found.add(clean_url(match))

        # 3) Si hay una extensión de dataset dentro del texto,
        # puede ser una ruta relativa tipo /files/train.tsv
        lower_text = text.lower()

        if any(ext in lower_text for ext in possible_extensions):
            found.add(clean_url(urljoin(base_url, text)))

    return sorted(found)


# ==============================
# EXTRACCIÓN DESDE HTML
# ==============================

def extract_downloadable_candidates_from_html(html: str, base_url: str) -> list:
    """
    Extrae posibles descargables desde HTML.

    Busca:
    - href="..."
    - src="..."
    - URLs completas escritas dentro del HTML
    """

    found = set()

    if not html:
        return []

    # URLs completas escritas directamente
    for match in URL_REGEX.findall(html):
        found.add(clean_url(match))

    # href=""
    for match in HREF_REGEX.findall(html):
        item = match.strip()

        if not item:
            continue

        if item.startswith(("mailto:", "javascript:", "#")):
            continue

        absolute_url = urljoin(base_url, item)
        found.add(clean_url(absolute_url))

    # src=""
    for match in SRC_REGEX.findall(html):
        item = match.strip()

        if not item:
            continue

        if item.startswith(("mailto:", "javascript:", "#")):
            continue

        absolute_url = urljoin(base_url, item)
        found.add(clean_url(absolute_url))

    return sorted(found)


# ==============================
# FILTRADO DE DESCARGABLES DATASET
# ==============================

def find_dataset_downloadables(input_url: str, candidates: list, session=None) -> list:
    """
    De todos los candidatos encontrados, devuelve solo descargables
    con formato típico de dataset.

    Para máxima precisión:
    - primero exige dominio permitido;
    - después valida por extensión;
    - si no hay extensión clara, revisa cabeceras.
    """

    dataset_links = []

    for link in candidates:
        link = clean_url(link)

        if not link:
            continue

        if not link.startswith(("http://", "https://")):
            continue

        if not is_allowed_dataset_link(input_url, link):
            continue

        validation = validate_candidate_headers(link, session=session)

        if not validation.get("matched"):
            continue

        dataset_url = validation.get("dataset_url", link)
        dataset_links.append(clean_url(dataset_url))

    return sorted(set(dataset_links))




def collect_zip_metadata_from_dataset_links(dataset_links: list, session=None) -> dict:
    """
    Si entre los enlaces positivos hay ZIPs, vuelve a inspeccionarlos
    para guardar en CSV/JSON qué CSV internos se encontraron.
    """

    for link in dataset_links:
        if is_zip_by_url(link):
            return check_zip_for_csv(link, session=session)

    return {
        "zip_descargado": False,
        "zip_contiene_csv": False,
        "zip_csv_encontrados": [],
        "zip_total_archivos": 0,
        "zip_archivos_sample": [],
        "zip_error": "",
        "reason": ""
    }

# ==============================
# CHECK JSON
# ==============================

def check_json_for_dataset_downloadables(url: str, session=None) -> dict:
    """
    Revisa la URL como JSON.

    Si puede parsear JSON, busca dentro descargables tipo dataset.
    """

    json_response = fetch_json_sample(url, session=session)

    if not json_response.get("ok"):
        return {
            "matched": False,
            "reason": "json_could_not_be_downloaded_or_parsed",
            "value": {
                "json_descargado": False,
                "json_error_reason": json_response.get("reason", ""),
                "json_error": json_response.get("error", ""),
                "json_content_type": json_response.get("content_type", ""),
                "json_final_url": json_response.get("final_url", ""),
                "json_dataset_descargable": "",
                "json_dataset_descargables_encontrados": [],
                "total_descargables_json": 0,
                "descargables_json_sample": []
            }
        }

    parsed_json = json_response.get("json")
    final_url = json_response.get("final_url", url)

    downloadable_candidates = extract_downloadable_candidates_from_json(
        parsed_json,
        final_url
    )

    dataset_downloadables = find_dataset_downloadables(
        url,
        downloadable_candidates,
        session=session
    )

    if dataset_downloadables:
        return {
            "matched": True,
            "reason": "json_contains_dataset_downloadable",
            "value": {
                "json_descargado": True,
                "json_final_url": final_url,
                "json_content_type": json_response.get("content_type", ""),
                "json_dataset_descargable": dataset_downloadables[0],
                "json_dataset_descargables_encontrados": dataset_downloadables,
                "total_descargables_json": len(downloadable_candidates),
                "descargables_json_sample": downloadable_candidates[:20]
            }
        }

    return {
        "matched": False,
        "reason": "json_downloaded_but_no_dataset_downloadable_found",
        "value": {
            "json_descargado": True,
            "json_final_url": final_url,
            "json_content_type": json_response.get("content_type", ""),
            "json_dataset_descargable": "",
            "json_dataset_descargables_encontrados": [],
            "total_descargables_json": len(downloadable_candidates),
            "descargables_json_sample": downloadable_candidates[:20]
        }
    }


# ==============================
# CHECK HTML
# ==============================

def check_html_for_dataset_downloadables(url: str, session=None) -> dict:
    """
    Descarga el HTML de la URL y busca si dentro hay archivos descargables
    con formato típico de dataset.
    """

    html_response = fetch_html_sample(url, session=session)

    if not html_response.get("ok"):
        return {
            "matched": False,
            "reason": "html_could_not_be_downloaded",
            "value": {
                "html_descargado": False,
                "html_error_reason": html_response.get("reason", ""),
                "html_error": html_response.get("error", ""),
                "html_content_type": html_response.get("content_type", ""),
                "html_final_url": html_response.get("final_url", ""),
                "html_dataset_descargable": "",
                "html_dataset_descargables_encontrados": [],
                "total_descargables_html": 0,
                "descargables_html_sample": []
            }
        }

    final_url = html_response.get("final_url", url)
    html = html_response.get("text", "")

    html_candidates = extract_downloadable_candidates_from_html(
        html,
        final_url
    )

    dataset_downloadables = find_dataset_downloadables(
        url,
        html_candidates,
        session=session
    )

    if dataset_downloadables:
        return {
            "matched": True,
            "reason": "html_contains_dataset_downloadable",
            "value": {
                "html_descargado": True,
                "html_final_url": final_url,
                "html_content_type": html_response.get("content_type", ""),
                "html_dataset_descargable": dataset_downloadables[0],
                "html_dataset_descargables_encontrados": dataset_downloadables,
                "total_descargables_html": len(html_candidates),
                "descargables_html_sample": html_candidates[:20]
            }
        }

    return {
        "matched": False,
        "reason": "html_downloaded_but_no_dataset_downloadable_found",
        "value": {
            "html_descargado": True,
            "html_final_url": final_url,
            "html_content_type": html_response.get("content_type", ""),
            "html_dataset_descargable": "",
            "html_dataset_descargables_encontrados": [],
            "total_descargables_html": len(html_candidates),
            "descargables_html_sample": html_candidates[:20]
        }
    }


# ==============================
# HEURÍSTICA 1
# ==============================

def heuristic_1(url: str, session=None) -> dict:
    """
    Orden correcto de la heurística 1:

    1. Si la URL pertenece a un dominio famoso de datasets, dataset=True.
    2. Si la URL acaba en .csv, .tsv, .xlsx, .parquet, etc., dataset=True.
       No acepta .json ni .xml.
    3. Si la URL es .zip, descarga el ZIP y mira dentro.
    4. Descarga el HTML y busca enlaces descargables.
       Si encuentra .csv, .tsv, .xlsx, .parquet, .zip, etc., dataset=True.
       Si encuentra ZIP, abre el ZIP y mira dentro.
    5. Como apoyo final, revisa cabeceras HTTP.
    6. Si nada funciona, dataset=False.
    """

    url = clean_url(url)

    # ==============================
    # PASO 0: dominio famoso de dataset
    # ==============================
    if is_super_famous_dataset_domain_url(url):
        return {
            "matched": True,
            "reason": "super_famous_dataset_domain",
            "value": {
                "url": url,
                "es_dataset_directo": True,
                "tipo_dataset_descargable": "dataset_repository_page",
                "pagina_con_descargables": True,
                "dataset_descargable": url,
                "dataset_descargables_encontrados": [url],
                "json_descargado": False,
                "html_descargado": False,
                "header_checked": False,
                "zip_descargado": False,
                "zip_contiene_csv": False,
                "zip_csv_encontrados": [],
                "zip_total_archivos": 0,
                "zip_archivos_sample": [],
                "zip_error": "",
                "header_content_type": "",
                "header_content_disposition": "",
                "total_descargables_json": 0,
                "total_descargables_html": 0,
                "json_dataset_descargables_encontrados": [],
                "html_dataset_descargables_encontrados": []
            }
        }

    # ==============================
    # PASO 1: URL directa con extensión dataset
    # ==============================
    if url_ends_with_dataset_file(url):
        return {
            "matched": True,
            "reason": "url_ends_with_dataset_file",
            "value": {
                "url": url,
                "es_dataset_directo": True,
                "tipo_dataset_descargable": get_dataset_file_type(url),
                "pagina_con_descargables": False,
                "dataset_descargable": url,
                "dataset_descargables_encontrados": [url],
                "json_descargado": False,
                "html_descargado": False,
                "header_checked": False,
                "zip_descargado": False,
                "zip_contiene_csv": False,
                "zip_csv_encontrados": [],
                "zip_total_archivos": 0,
                "zip_archivos_sample": [],
                "zip_error": "",
                "header_content_type": "",
                "header_content_disposition": "",
                "total_descargables_json": 0,
                "total_descargables_html": 0,
                "json_dataset_descargables_encontrados": [],
                "html_dataset_descargables_encontrados": []
            }
        }

    # ==============================
    # PASO 2: si la propia URL es ZIP, abrirlo
    # ==============================
    if is_zip_by_url(url):
        zip_check = check_zip_for_csv(url, session=session)

        if zip_check.get("matched"):
            return {
                "matched": True,
                "reason": "zip_contains_dataset_file",
                "value": {
                    "url": url,
                    "es_dataset_directo": True,
                    "tipo_dataset_descargable": ".zip",
                    "pagina_con_descargables": False,
                    "dataset_descargable": zip_check.get("dataset_url", url),
                    "dataset_descargables_encontrados": [zip_check.get("dataset_url", url)],
                    "json_descargado": False,
                    "html_descargado": False,
                    "header_checked": False,
                    "zip_descargado": bool(zip_check.get("zip_descargado", False)),
                    "zip_contiene_csv": bool(zip_check.get("zip_contiene_csv", False)),
                    "zip_csv_encontrados": zip_check.get("zip_csv_encontrados", []),
                    "zip_total_archivos": zip_check.get("zip_total_archivos", 0),
                    "zip_archivos_sample": zip_check.get("zip_archivos_sample", []),
                    "zip_error": zip_check.get("zip_error", ""),
                    "header_content_type": "",
                    "header_content_disposition": "",
                    "total_descargables_json": 0,
                    "total_descargables_html": 0,
                    "json_dataset_descargables_encontrados": [],
                    "html_dataset_descargables_encontrados": []
                }
            }

    # ==============================
    # PASO 3: HTML
    # ==============================
    html_check = check_html_for_dataset_downloadables(url, session=session)
    html_value = html_check.get("value", {})

    if html_check.get("matched"):
        dataset_url = html_value.get("html_dataset_descargable", "")

        zip_metadata = collect_zip_metadata_from_dataset_links(
            html_value.get("html_dataset_descargables_encontrados", []),
            session=session
        )

        return {
            "matched": True,
            "reason": "html_contains_dataset_downloadable",
            "value": {
                "url": url,
                "es_dataset_directo": False,
                "tipo_dataset_descargable": get_dataset_file_type(dataset_url),
                "pagina_con_descargables": True,
                "dataset_descargable": dataset_url,
                "dataset_descargables_encontrados": html_value.get(
                    "html_dataset_descargables_encontrados",
                    []
                ),
                "json_descargado": False,
                "html_descargado": bool(html_value.get("html_descargado", False)),
                "header_checked": False,
                "header_content_type": "",
                "header_content_disposition": "",
                "zip_descargado": bool(zip_metadata.get("zip_descargado", False)),
                "zip_contiene_csv": bool(zip_metadata.get("zip_contiene_csv", False)),
                "zip_csv_encontrados": zip_metadata.get("zip_csv_encontrados", []),
                "zip_total_archivos": zip_metadata.get("zip_total_archivos", 0),
                "zip_archivos_sample": zip_metadata.get("zip_archivos_sample", []),
                "zip_error": zip_metadata.get("zip_error", ""),
                "total_descargables_json": 0,
                "total_descargables_html": html_value.get("total_descargables_html", 0),
                "descargables_json_sample": [],
                "descargables_html_sample": html_value.get("descargables_html_sample", []),
                "json_content_type": "",
                "html_content_type": html_value.get("html_content_type", ""),
                "json_dataset_descargables_encontrados": [],
                "html_dataset_descargables_encontrados": html_value.get(
                    "html_dataset_descargables_encontrados",
                    []
                )
            }
        }

    # ==============================
    # PASO 4: cabeceras HTTP como apoyo final
    # ==============================
    header_check = check_url_headers_for_dataset_file(url, session=session)

    if header_check.get("matched"):
        dataset_url = header_check.get("dataset_url", url)

        return {
            "matched": True,
            "reason": header_check.get("reason", "headers_indicate_dataset_file"),
            "value": {
                "url": url,
                "es_dataset_directo": True,
                "tipo_dataset_descargable": header_check.get("tipo_dataset_descargable", ""),
                "pagina_con_descargables": False,
                "dataset_descargable": dataset_url,
                "dataset_descargables_encontrados": [dataset_url],
                "json_descargado": False,
                "html_descargado": bool(html_value.get("html_descargado", False)),
                "header_checked": True,
                "header_content_type": header_check.get("content_type", ""),
                "header_content_disposition": header_check.get("content_disposition", ""),
                "header_final_url": header_check.get("final_url", ""),
                "zip_descargado": bool(header_check.get("zip_descargado", False)),
                "zip_contiene_csv": bool(header_check.get("zip_contiene_csv", False)),
                "zip_csv_encontrados": header_check.get("zip_csv_encontrados", []),
                "zip_total_archivos": header_check.get("zip_total_archivos", 0),
                "zip_archivos_sample": header_check.get("zip_archivos_sample", []),
                "zip_error": header_check.get("zip_error", ""),
                "total_descargables_json": 0,
                "total_descargables_html": html_value.get("total_descargables_html", 0),
                "json_dataset_descargables_encontrados": [],
                "html_dataset_descargables_encontrados": html_value.get(
                    "html_dataset_descargables_encontrados",
                    []
                )
            }
        }

    # ==============================
    # PASO 5: no encontrado
    # ==============================
    return {
        "matched": False,
        "reason": "no_dataset_downloadable_found_in_url_html_zip_or_headers",
        "value": {
            "url": url,
            "es_dataset_directo": False,
            "tipo_dataset_descargable": "",
            "pagina_con_descargables": bool(html_value.get("total_descargables_html", 0)),
            "dataset_descargable": "",
            "dataset_descargables_encontrados": [],
            "json_descargado": False,
            "html_descargado": bool(html_value.get("html_descargado", False)),
            "header_checked": True,
            "header_content_type": header_check.get("content_type", ""),
            "header_content_disposition": header_check.get("content_disposition", ""),
            "header_error_reason": header_check.get("reason", ""),
            "zip_descargado": bool(header_check.get("zip_descargado", False)),
            "zip_contiene_csv": bool(header_check.get("zip_contiene_csv", False)),
            "zip_csv_encontrados": header_check.get("zip_csv_encontrados", []),
            "zip_total_archivos": header_check.get("zip_total_archivos", 0),
            "zip_archivos_sample": header_check.get("zip_archivos_sample", []),
            "zip_error": header_check.get("zip_error", ""),
            "total_descargables_json": 0,
            "total_descargables_html": html_value.get("total_descargables_html", 0),
            "descargables_json_sample": [],
            "descargables_html_sample": html_value.get("descargables_html_sample", []),
            "json_content_type": "",
            "html_content_type": html_value.get("html_content_type", ""),
            "json_dataset_descargables_encontrados": [],
            "html_dataset_descargables_encontrados": html_value.get(
                "html_dataset_descargables_encontrados",
                []
            ),
            "html_error_reason": html_value.get("html_error_reason", "")
        }
    }


# ==============================
# CARGA CSV
# ==============================

def load_normalized_csv(path: str) -> list:
    rows = []

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            url_to_process = (
                row.get("normalized_url", "").strip()
                or row.get("url", "").strip()
                or row.get("original_url", "").strip()
            )

            pdf_name = (
                row.get("paper", "").strip()
                or row.get("pdf", "").strip()
                or row.get("file", "").strip()
            )

            rows.append({
                "pdf": pdf_name,
                "url": url_to_process
            })

    return rows


# ==============================
# GUARDADO CSV / JSON
# ==============================

def save_csv_simple(rows: list, path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "pdf",
        "url",
        "heuristica",
        "es_dataset_directo",
        "json_descargado",
        "html_descargado",
        "header_checked",
        "pagina_con_descargables",
        "dataset_descargable",
        "tipo_dataset_descargable",
        "zip_descargado",
        "zip_contiene_csv",
        "zip_csv_encontrados",
        "zip_total_archivos",
        "zip_error",
        "total_descargables_json",
        "total_descargables_html",
        "header_content_type",
        "header_content_disposition",
        "json_content_type",
        "html_content_type",
        "motivo"
    ]

    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
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
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        return alt


def save_json_full(rows: list, path: str) -> str:
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


# ==============================
# MAIN
# ==============================

def main():
    if not Path(INPUT_CSV).exists():
        print(f"No existe {INPUT_CSV}")
        return

    input_rows = load_normalized_csv(INPUT_CSV)

    unique_urls = sorted(
        list({row["url"] for row in input_rows if row["url"]})
    )

    print(f"-> Filas leídas: {len(input_rows)}")
    print(f"-> URLs únicas a procesar: {len(unique_urls)}")
    print(f"-> Iniciando heurística 1 precisa con {MAX_WORKERS} hilos...")

    url_results = {}

    session = requests.Session()

    adapter = requests.adapters.HTTPAdapter(
        pool_connections=MAX_WORKERS,
        pool_maxsize=MAX_WORKERS * 2
    )

    session.mount("http://", adapter)
    session.mount("https://", adapter)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {
            executor.submit(heuristic_1, url, session): url
            for url in unique_urls
        }

        for i, future in enumerate(as_completed(future_to_url), 1):
            url = future_to_url[future]

            try:
                url_results[url] = future.result()

            except Exception as e:
                url_results[url] = {
                    "matched": False,
                    "reason": f"thread_error: {str(e)}",
                    "value": {
                        "url": url,
                        "es_dataset_directo": False,
                        "json_descargado": False,
                        "html_descargado": False,
                        "header_checked": False,
                        "pagina_con_descargables": False,
                        "dataset_descargable": "",
                        "tipo_dataset_descargable": "",
                        "zip_descargado": False,
                        "zip_contiene_csv": False,
                        "zip_csv_encontrados": [],
                        "zip_total_archivos": 0,
                        "zip_archivos_sample": [],
                        "zip_error": "",
                        "total_descargables_json": 0,
                        "total_descargables_html": 0,
                        "header_content_type": "",
                        "header_content_disposition": "",
                        "json_content_type": "",
                        "html_content_type": ""
                    }
                }

            if i % 20 == 0 or i == len(unique_urls):
                print(f" Progreso: [{i}/{len(unique_urls)}] URLs analizadas.")

    session.close()

    csv_rows = []
    json_rows = []

    for row in input_rows:
        pdf = row["pdf"]
        url = row["url"]

        if not url:
            continue

        h = url_results.get(
            url,
            {
                "matched": False,
                "reason": "not_processed",
                "value": {}
            }
        )

        value = h.get("value", {})

        csv_rows.append({
            "pdf": pdf,
            "url": url,
            "heuristica": bool(h.get("matched", False)),
            "es_dataset_directo": bool(value.get("es_dataset_directo", False)),
            "json_descargado": bool(value.get("json_descargado", False)),
            "html_descargado": bool(value.get("html_descargado", False)),
            "header_checked": bool(value.get("header_checked", False)),
            "pagina_con_descargables": bool(value.get("pagina_con_descargables", False)),
            "dataset_descargable": value.get("dataset_descargable", ""),
            "tipo_dataset_descargable": value.get("tipo_dataset_descargable", ""),
            "zip_descargado": bool(value.get("zip_descargado", False)),
            "zip_contiene_csv": bool(value.get("zip_contiene_csv", False)),
            "zip_csv_encontrados": " | ".join(value.get("zip_csv_encontrados", [])),
            "zip_total_archivos": value.get("zip_total_archivos", 0),
            "zip_error": value.get("zip_error", ""),
            "total_descargables_json": value.get("total_descargables_json", 0),
            "total_descargables_html": value.get("total_descargables_html", 0),
            "header_content_type": value.get("header_content_type", ""),
            "header_content_disposition": value.get("header_content_disposition", ""),
            "json_content_type": value.get("json_content_type", ""),
            "html_content_type": value.get("html_content_type", ""),
            "motivo": h.get("reason", "")
        })

        json_rows.append({
            "pdf": pdf,
            "url": url,
            "heuristic_1": h
        })

    saved_csv = save_csv_simple(csv_rows, OUTPUT_CSV)
    saved_json = save_json_full(json_rows, OUTPUT_JSON)

    total_true = sum(1 for r in csv_rows if r["heuristica"] is True)
    total_false = sum(1 for r in csv_rows if r["heuristica"] is False)

    print("\n================ RESUMEN HEURÍSTICA 1 ================")
    print(f" Filas finales procesadas: {len(csv_rows)}")
    print(f" Confirmados como DATASET: {total_true}")
    print(f" Descartados como NOT DATASET: {total_false}")
    print(f" CSV guardado en: {saved_csv}")
    print(f" JSON guardado en: {saved_json}")
    print("======================================================")


if __name__ == "__main__":
    main()
