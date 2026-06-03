# heuristics_precision_v6_chatgpt.py
# Identifica URLs candidatas a dataset con 3 heurísticas:
# H1 -> extensión directa de la URL o búsqueda de archivos descargables dentro de la página/JSON.
# H2 -> metadatos HTTP: Content-Type, Content-Disposition y extensión final.
# H3 -> lectura de .dataset.json para comprobar si aparece la URL o alguna URL de dataset.

import csv
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse, urljoin
from functools import lru_cache
from datetime import datetime

import requests


# ==============================
# RUTAS
# ==============================

INPUT_CSV = "outputs/all_links_normalized.csv"
OUTPUT_CSV = "outputs/heuristics_results.csv"
OUTPUT_JSON = "outputs/heuristics_results.json"

# Carpeta donde están los PDF y los .dataset.json
GAP_KGE_JSON_DIR = "pdfs"


# ==============================
# CONFIGURACIÓN
# ==============================

REQUEST_TIMEOUT = 10
MAX_PAGE_BYTES = 1_000_000  # máximo 1 MB para inspeccionar HTML/JSON

# Archivos de datos directos que SÍ aceptamos como dataset.
# IMPORTANTE: no incluimos .zip, .gz, .tar, .tgz, .7z, etc.
DATA_EXTENSIONS = {
    ".csv", ".tsv", ".json", ".xml", ".rdf",
    ".xlsx", ".xls", ".parquet", ".h5", ".hdf5",
    ".pkl", ".pickle", ".npy", ".npz",
    ".db", ".sqlite", ".sqlite3",
    ".dat", ".data", ".arff", ".mat"
}

# Archivos comprimidos que NO queremos marcar como dataset.
COMPRESSED_EXTENSIONS = {
    ".zip", ".gz", ".tar", ".tgz", ".7z", ".rar", ".bz2", ".xz"
}

DATA_CONTENT_TYPES = {
    "text/csv",
    "application/csv",
    "text/tab-separated-values",
    "application/json",
    "application/ld+json",
    "application/xml",
    "text/xml",
    "application/rdf+xml",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/parquet",
    "application/x-parquet",
    "application/x-hdf5",
    "application/x-sqlite3",
    "application/octet-stream"
}

COMPRESSED_CONTENT_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "application/gzip",
    "application/x-gzip",
    "application/x-tar",
    "application/x-7z-compressed",
    "application/x-rar-compressed",
    "application/x-bzip2",
    "application/x-xz"
}

DATASET_LINK_KEYWORDS = {
    "dataset", "datasets", "data", "download", "downloads",
    "train", "training", "test", "dev", "validation", "valid",
    "benchmark", "benchmarks", "corpus", "database", "annotations",
    "annotation", "labels", "features", "samples", "records",
    "instances", "metadata", "table", "tables", "export"
}

# Palabras suficientemente fuertes para aceptar extensiones ambiguas como .json/.xml/.pkl/.mat.
# No meto "file/files/resource" porque aparecen en casi todas las webs y dan falsos positivos.
STRONG_DATASET_CONTEXT_KEYWORDS = {
    "dataset", "datasets", "data", "train", "training", "test", "dev",
    "validation", "valid", "benchmark", "corpus", "database",
    "annotations", "annotation", "labels", "features", "samples",
    "records", "instances", "metadata"
}

NEGATIVE_LINK_KEYWORDS = {
    "paper", "article", "citation", "bibtex", "reference",
    "documentation", "docs", "wiki", "blog", "login", "signin",
    "contact", "about", "license", "terms", "privacy",
    "manifest", "opensearch", "sitemap", "rss", "feed", "robots",
    "favicon", "static", "assets", "bundle", "webpack", "serviceworker"
}

# Extensiones de datos fuertes: normalmente son ficheros de datos reales.
STRONG_DATA_EXTENSIONS = {
    ".csv", ".tsv", ".xlsx", ".xls", ".parquet",
    ".h5", ".hdf5", ".npy", ".npz", ".arff", ".mat",
    ".db", ".sqlite", ".sqlite3", ".dat", ".data"
}

# Extensiones débiles: pueden ser dataset, pero también metadatos técnicos de una web.
# Para estas exigimos keywords de dataset/data/train/test/etc.
WEAK_DATA_EXTENSIONS = {".json", ".xml", ".rdf", ".pkl", ".pickle"}

# Ficheros técnicos muy comunes que NO son datasets aunque acaben en .json/.xml.
TECHNICAL_FILENAMES = {
    "manifest.json", "site.webmanifest", "asset-manifest.json",
    "opensearch.xml", "sitemap.xml", "sitemap_index.xml",
    "feed.xml", "rss.xml", "atom.xml", "robots.txt",
    "browserconfig.xml", "crossdomain.xml", "clientaccesspolicy.xml",
    "tdmrep-policy.json", "security.txt", "package.json",
    "package-lock.json", "yarn.lock", "composer.json", "composer.lock",
    "package-lock.json", "pnpm-lock.yaml", "requirements.txt",
    "environment.yml", "metadata.json", "info.json", "config.json",
    "database-config-example.json", ".markdownlint.json", "osdd.xml",
    "wlwmanifest.xml", "os-grok.xml", "os-x.xml"
}

# Dominios donde el mismo dominio no basta para confirmar datasets desde .dataset.json.
# Ejemplo: si el JSON tiene un GitHub con un CSV, no queremos marcar cualquier github.com/... del paper.
GENERIC_HOSTING_DOMAINS = {
    "github.com", "raw.githubusercontent.com", "gitlab.com", "bitbucket.org",
    "doi.org", "dx.doi.org", "arxiv.org", "semanticscholar.org",
    "api.semanticscholar.org", "acm.org", "doi.acm.org", "usenix.org",
    "ieee.org", "ieeexplore.ieee.org", "springer.com", "link.springer.com"
}

# Dominios/repo conocidos donde una URL sin extensión puede seguir siendo dataset.
TRUSTED_DATASET_REPOSITORY_DOMAINS = {
    "zenodo.org", "figshare.com", "datadryad.org", "dryad.org",
    "dataverse.harvard.edu", "kaggle.com", "archive.ics.uci.edu",
    "openml.org", "physionet.org", "huggingface.co", "tensorflow.org",
    "paperswithcode.com", "registry.opendata.aws", "data.gov",
    "data.europa.eu", "data.world", "osf.io", "mendeley.com"
}

# Nombres demasiado genéricos/plataformas que NO deben confirmar una URL por nombre.
GENERIC_DATASET_NAMES = {
    "github", "gitlab", "bitbucket", "arxiv", "semantic scholar", "semanticscholar",
    "acl anthology", "acm", "ieee", "springer", "usenix", "doi", "zenodo",
    "figshare", "kaggle", "tensorflow", "tensorflow datasets", "qa", "dataset", "data",
    "annotations", "frames", "masks", "samples", "resnet", "github repository"
}

# Para máxima precisión: una página solo se marca por H1 si el archivo encontrado
# pertenece al mismo sitio/repo. Los enlaces externos se guardan como evidencia,
# pero no validan la URL actual.
ALLOW_EXTERNAL_DOWNLOAD_LINKS_IN_H1 = False

# H1 más estricta: para una página normal no basta encontrar un .csv/.json.
# La página/JSON también debe tener contexto textual o estructural de dataset.
REQUIRE_PAGE_DATASET_CONTEXT_IN_H1 = True

# Clasificador IA opcional. Por defecto está apagado para que el script funcione sin
# descargar modelos ni usar APIs. Si quieres usar Hugging Face local, ponlo a True
# e instala transformers + torch + un modelo local/cacheado.
USE_HF_ZERO_SHOT_FOR_H1 = False
HF_ZERO_SHOT_MODEL = "facebook/bart-large-mnli"

# ==============================
# H4: ChatGPT / OpenAI opcional
# ==============================
# Por defecto está apagado para que el script funcione sin API key.
# Para activarlo en Windows PowerShell:
#   $env:OPENAI_API_KEY="tu_api_key"
#   $env:USE_OPENAI_LLM_HEURISTIC="true"
#   python heuristics_precision_v6_chatgpt.py
#
# Recomendación: úsalo como verificador de positivos dudosos, no para todas las URLs.
USE_OPENAI_LLM_HEURISTIC = os.getenv("USE_OPENAI_LLM_HEURISTIC", "false").lower() == "true"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_REVIEW_ALL_URLS = os.getenv("OPENAI_REVIEW_ALL_URLS", "false").lower() == "true"
OPENAI_REVIEW_RULE_POSITIVES = os.getenv("OPENAI_REVIEW_RULE_POSITIVES", "true").lower() == "true"
OPENAI_ALLOW_POSITIVE_OVERRIDE = os.getenv("OPENAI_ALLOW_POSITIVE_OVERRIDE", "false").lower() == "true"
OPENAI_REJECTION_CONFIDENCE = float(os.getenv("OPENAI_REJECTION_CONFIDENCE", "0.65"))
OPENAI_POSITIVE_CONFIDENCE = float(os.getenv("OPENAI_POSITIVE_CONFIDENCE", "0.85"))
OPENAI_PAGE_TEXT_CHARS = int(os.getenv("OPENAI_PAGE_TEXT_CHARS", "3500"))

PAGE_DATASET_TERMS = {
    "dataset", "datasets", "data set", "data sets", "corpus", "benchmark",
    "database", "annotations", "annotation", "labels", "features",
    "samples", "records", "instances", "training data", "test data",
    "validation data", "evaluation data"
}

PAGE_DOWNLOAD_TERMS = {
    "download", "downloads", "downloadable", "available", "access",
    "get the data", "data available", "available at", "download the data",
    "download dataset", "download data"
}

JSON_DATASET_SCHEMA_KEYS = {
    "@type", "type", "dataset", "datasets", "name", "title", "description",
    "distribution", "downloadurl", "download_url", "contenturl", "content_url",
    "encoding", "associatedmedia", "variablemeasured", "includedindatacatalog"
}

JSON_DOWNLOAD_KEYS = {
    "download", "downloads", "downloadurl", "download_url", "contenturl",
    "content_url", "url", "href", "file", "files", "data", "dataset",
    "distribution", "resources", "resource"
}

DATASET_NAME_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with",
    "dataset", "datasets", "data", "corpus", "benchmark", "database",
    "repository", "collection", "challenge", "paper", "available", "at",
    "http", "https", "www", "com", "org", "net", "edu", "gov"
}

URL_REGEX = re.compile(r'https?://[^\s"\'<>\)\]]+', re.IGNORECASE)
HREF_REGEX = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
SRC_REGEX = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)


# ==============================
# UTILIDADES GENERALES
# ==============================

def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def get_path(url: str) -> str:
    try:
        return urlparse(url).path.lower()
    except Exception:
        return ""


def get_extension(url: str) -> str:
    try:
        path = get_path(url)
        match = re.search(r"(\.[a-z0-9]+)$", path)
        return match.group(1).lower() if match else ""
    except Exception:
        return ""


def normalize_loose(url: str) -> str:
    if not url:
        return ""
    url = str(url).strip().lower()
    url = re.sub(r"#.*$", "", url)
    url = url.rstrip("/.,;:!?)]}>'\"")
    return url


def tokenize_url(url: str) -> set:
    try:
        parsed = urlparse(url)
        raw = f"{parsed.netloc}{parsed.path}{parsed.query}".lower()
        return {t for t in re.split(r"[/\\\-_.?=&:#]+", raw) if t}
    except Exception:
        return set()


def is_data_file_url(url: str) -> bool:
    return get_extension(url) in DATA_EXTENSIONS


def safe_json_dumps(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def get_filename_from_url(url: str) -> str:
    try:
        path = urlparse(url).path
        return Path(path).name.lower()
    except Exception:
        return ""


def is_technical_link(url: str) -> bool:
    """Ignora JSON/XML típicos de la infraestructura de una web."""
    filename = get_filename_from_url(url)
    path = get_path(url)

    if filename in TECHNICAL_FILENAMES:
        return True

    technical_path_tokens = (
        "/static/", "/assets/", "/_next/", "/webpack/",
        "/favicon", "/icons/", "/apple-touch-icon"
    )
    if any(tok in path for tok in technical_path_tokens):
        return True

    if filename.endswith((".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2")):
        return True

    return False


def has_dataset_context(url: str) -> bool:
    """Comprueba si la URL contiene palabras que sugieren dataset real."""
    tokens = tokenize_url(url)
    return bool(tokens.intersection(DATASET_LINK_KEYWORDS))


def has_strong_dataset_context(url: str) -> bool:
    """Contexto fuerte de dataset en dominio/path/query."""
    tokens = tokenize_url(url)
    return bool(tokens.intersection(STRONG_DATASET_CONTEXT_KEYWORDS))


def data_file_match_level(url: str) -> str:
    """
    Devuelve el tipo de coincidencia de archivo de datos.
    - strong: extensión tabular/datos muy fiable.
    - ambiguous_with_context: extensión ambigua con contexto fuerte de dataset.
    - weak_with_context: JSON/XML/RDF con contexto fuerte de dataset.
    - compressed/technical/none: no se acepta.
    """
    ext = get_extension(url)

    if ext in COMPRESSED_EXTENSIONS:
        return "compressed"

    if is_technical_link(url):
        return "technical"

    # Extensiones muy fiables para enlaces directos de datos.
    reliable_exts = {".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".h5", ".hdf5", ".arff", ".db", ".sqlite", ".sqlite3"}
    if ext in reliable_exts:
        return "strong"

    # Extensiones ambiguas: pueden ser dataset, pero también modelos, código o configuración.
    ambiguous_exts = {".json", ".xml", ".rdf", ".pkl", ".pickle", ".npy", ".npz", ".mat", ".dat", ".data"}
    if ext in ambiguous_exts and has_strong_dataset_context(url):
        if ext in {".json", ".xml", ".rdf"}:
            return "weak_with_context"
        return "ambiguous_with_context"

    return "none"

def root_domain(domain: str) -> str:
    """Aproximación simple para comparar dominios sin depender de librerías externas."""
    domain = (domain or "").lower().strip()
    if domain.startswith("www."):
        domain = domain[4:]
    parts = [p for p in domain.split(".") if p]
    if len(parts) <= 2:
        return domain
    return ".".join(parts[-2:])


def is_trusted_dataset_repository_domain(domain: str) -> bool:
    domain = (domain or "").lower().strip()
    rd = root_domain(domain)
    return domain in TRUSTED_DATASET_REPOSITORY_DOMAINS or rd in TRUSTED_DATASET_REPOSITORY_DOMAINS


def github_owner_repo(url: str) -> str:
    """Devuelve owner/repo para URLs de GitHub, si existe."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain not in {"github.com", "www.github.com"}:
            return ""
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(parts) >= 2:
            return f"{parts[0].lower()}/{parts[1].lower()}"
    except Exception:
        pass
    return ""


def is_candidate_link_relevant_to_input(input_url: str, candidate_link: str) -> bool:
    """
    Evita que una página sea dataset solo porque enlaza a un dataset externo.
    - mismo root domain => aceptable;
    - GitHub => exige mismo owner/repo;
    - si ALLOW_EXTERNAL_DOWNLOAD_LINKS_IN_H1=True, permite externos.
    """
    if ALLOW_EXTERNAL_DOWNLOAD_LINKS_IN_H1:
        return True

    input_domain = get_domain(input_url)
    candidate_domain = get_domain(candidate_link)
    if not input_domain or not candidate_domain:
        return False

    input_gh = github_owner_repo(input_url)
    cand_gh = github_owner_repo(candidate_link)
    if input_gh or cand_gh:
        return bool(input_gh and cand_gh and input_gh == cand_gh)

    return root_domain(input_domain) == root_domain(candidate_domain)


def clean_dataset_name_text(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"\s+", " ", name)
    name = name.strip(" -–—:;,.()[]{}\"'")
    return name


def is_good_dataset_name(name: str) -> bool:
    """
    Filtra nombres extraídos del .dataset.json que son demasiado genéricos
    o claramente no son nombres de dataset.
    """
    clean = clean_dataset_name_text(name)
    low = clean.lower()

    if not clean or len(clean) < 4 or len(clean) > 120:
        return False
    if low in GENERIC_DATASET_NAMES:
        return False
    if low.startswith(("http://", "https://", "www.")):
        return False
    if re.search(r"\bet\s+al\.?\b", low):
        return False
    if re.search(r"\(.*\d{4}.*\)", low) or re.search(r",\s*\d{4}", low):
        return False

    tokens = normalize_name_tokens(clean)
    distinctive = {t for t in tokens if len(t) >= 5 and t not in DATASET_NAME_STOPWORDS}

    if not distinctive:
        return False

    # Si solo tiene un token distintivo, que no sea una plataforma/genérico.
    if len(distinctive) == 1:
        tok = next(iter(distinctive))
        if tok in GENERIC_DATASET_NAMES or len(tok) < 6:
            return False

    return True


def normalize_name_tokens(text: str) -> set:
    text = (text or "").lower()
    tokens = re.split(r"[^a-z0-9]+", text)
    return {t for t in tokens if len(t) >= 3 and t not in DATASET_NAME_STOPWORDS}


def url_text_for_name_matching(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.netloc} {parsed.path}".lower()


def url_matches_dataset_name(url: str, dataset_names: list) -> tuple[bool, str]:
    """
    Marca True si el nombre del dataset mencionado en el .dataset.json
    aparece de forma distintiva en el dominio o path de la URL actual.

    Más estricto que antes:
    - evita que una palabra genérica como 'data' o 'dataset' valide la URL;
    - exige 2 tokens distintivos, o 1 token largo/compacto muy claro.
    """
    target_text = url_text_for_name_matching(url)
    target_tokens = tokenize_url(url)
    compact_target = re.sub(r"[^a-z0-9]", "", target_text)

    for name in dataset_names:
        if not is_good_dataset_name(name):
            continue
        name_tokens = normalize_name_tokens(name)
        # Tokens realmente distintivos. Evitamos tokens cortos/genéricos.
        distinctive = {t for t in name_tokens if len(t) >= 4 and t not in DATASET_NAME_STOPWORDS}
        if not distinctive:
            continue

        overlap = distinctive.intersection(target_tokens)

        # Caso sólido: dos o más tokens del nombre aparecen en la URL.
        if len(overlap) >= 2:
            return True, name

        # Caso de dataset de nombre corto pero distintivo: cifar, mnist, wikiart, imagenet...
        if len(overlap) == 1:
            tok = next(iter(overlap))
            if len(tok) >= 6:
                return True, name

        # Nombre compacto, pero solo si es suficientemente largo.
        # Ej: 'assemblage dataset' -> assemblage; 'wikiart dataset' -> wikiart.
        compact_name = "".join(sorted(distinctive))
        if len(compact_name) >= 8 and compact_name in compact_target:
            return True, name

        # También probamos cada token distintivo largo dentro del texto compacto.
        for tok in distinctive:
            if len(tok) >= 8 and tok in compact_target:
                return True, name

    return False, ""



# ==============================
# H1: contexto de página / JSON
# ==============================

def safe_parse_json_text(text: str):
    """Intenta parsear una respuesta como JSON. Si no se puede, devuelve None."""
    if not text:
        return None
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        return json.loads(stripped)
    except Exception:
        return None


def iter_json_key_values(obj, parent_key: str = ""):
    """Recorre un JSON y devuelve pares clave/valor de forma recursiva."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).strip().lower().replace("-", "_")
            compact = key.replace("_", "")
            yield compact, v
            yield from iter_json_key_values(v, compact)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_json_key_values(item, parent_key)


def json_has_dataset_structure(obj) -> dict:
    """
    Detecta si el JSON tiene estructura típica de dataset:
    schema.org Dataset, distribution/downloadURL/contentURL, recursos, etc.
    """
    if obj is None:
        return {"matched": False, "score": 0, "signals": []}

    score = 0
    signals = []

    for key, value in iter_json_key_values(obj):
        if key in {"@type", "type"}:
            value_txt = str(value).lower()
            if "dataset" in value_txt or "datacatalog" in value_txt:
                score += 4
                signals.append("json_schema_type_dataset")

        if key in {"downloadurl", "download_url", "contenturl", "content_url"}:
            score += 3
            signals.append(f"json_download_key:{key}")

        if key in {"distribution", "resources", "resource", "files", "file"}:
            score += 1
            signals.append(f"json_resource_key:{key}")

        if isinstance(value, str):
            low = value.lower()
            if any(term in low for term in PAGE_DATASET_TERMS):
                score += 1
                signals.append(f"json_text_dataset_context:{key}")

    # Evitar sumar infinito si hay muchas claves repetidas.
    score = min(score, 8)
    return {"matched": score >= 3, "score": score, "signals": sorted(set(signals))[:20]}


def extract_candidate_links_from_json_obj(obj, base_url: str = "") -> list:
    """
    Extrae enlaces de un JSON, priorizando campos que suelen contener descargas.
    También usa urljoin para enlaces relativos.
    """
    found = set()
    if obj is None:
        return []

    for key, value in iter_json_key_values(obj):
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, list):
            values = [x for x in value if isinstance(x, str)]
        else:
            values = []

        if key in JSON_DOWNLOAD_KEYS or values:
            for txt in values:
                for match in URL_REGEX.findall(txt):
                    found.add(match.rstrip(".,;:!?)]}>'\""))
                # enlace relativo que parece archivo de datos
                if re.search(r"\.(csv|tsv|json|xml|rdf|xlsx|xls|parquet|h5|hdf5|npy|npz|arff|mat|db|sqlite|dat|data)(?:$|[?#])", txt, re.I):
                    found.add(urljoin(base_url, txt.strip()))

    return sorted(found)


def normalize_page_text(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.lower().strip()


def candidate_link_context_windows(text: str, candidate_links: list, window: int = 350) -> list:
    """
    Saca trozos de texto alrededor de URLs/nombres de archivo encontrados.
    Sirve para comprobar que cerca del link pone dataset/download/data.
    """
    if not text or not candidate_links:
        return []

    windows = []
    lower = text.lower()

    for item in candidate_links[:20]:
        link = item.get("link", item) if isinstance(item, dict) else item
        candidates = [link.lower(), get_filename_from_url(link).lower()]
        for needle in candidates:
            if not needle:
                continue
            idx = lower.find(needle)
            if idx == -1:
                continue
            start = max(0, idx - window)
            end = min(len(text), idx + len(needle) + window)
            snippet = normalize_page_text(text[start:end])
            if snippet:
                windows.append(snippet)
            break

    return windows[:20]


def text_has_dataset_download_context(text: str, candidate_links: list | None = None) -> dict:
    """
    Exige contexto de dataset en la página.
    No basta con encontrar un archivo: debe aparecer texto tipo dataset/data/download/train/test/etc.
    """
    clean = normalize_page_text(text or "")
    if not clean:
        return {"matched": False, "score": 0, "signals": [], "matched_terms": []}

    score = 0
    signals = []
    matched_terms = []

    dataset_terms = sorted([t for t in PAGE_DATASET_TERMS if t in clean])
    download_terms = sorted([t for t in PAGE_DOWNLOAD_TERMS if t in clean])

    if dataset_terms:
        score += min(3, len(dataset_terms))
        signals.append("page_has_dataset_terms")
        matched_terms.extend(dataset_terms[:10])

    if download_terms:
        score += min(2, len(download_terms))
        signals.append("page_has_download_terms")
        matched_terms.extend(download_terms[:10])

    # Contexto cerca del enlace descargable: más fuerte que contexto global.
    windows = candidate_link_context_windows(text, candidate_links or [])
    local_hits = []
    for snippet in windows:
        has_data = any(t in snippet for t in PAGE_DATASET_TERMS)
        has_download = any(t in snippet for t in PAGE_DOWNLOAD_TERMS)
        if has_data and has_download:
            local_hits.append(snippet[:300])

    if local_hits:
        score += 4
        signals.append("dataset_download_context_near_candidate_link")

    # Para que sea True debe haber evidencia de dataset y de descarga/acceso,
    # o contexto local fuerte cerca del enlace.
    matched = bool(local_hits) or (bool(dataset_terms) and bool(download_terms) and score >= 4)

    return {
        "matched": matched,
        "score": score,
        "signals": sorted(set(signals)),
        "matched_terms": sorted(set(matched_terms))[:20],
        "local_context_samples": local_hits[:3]
    }


def optional_hf_dataset_classifier(text: str) -> dict:
    """
    Clasificador IA opcional con Hugging Face local.
    Está apagado por defecto. No es necesario para que funcione el script.
    Úsalo solo como señal adicional, no como verdad absoluta.
    """
    if not USE_HF_ZERO_SHOT_FOR_H1:
        return {"used": False, "matched": False, "label": "", "score": 0.0, "reason": "disabled"}

    try:
        from transformers import pipeline
        classifier = pipeline("zero-shot-classification", model=HF_ZERO_SHOT_MODEL)
        sample = normalize_page_text(text or "")[:2500]
        labels = [
            "dataset download page",
            "software or code repository",
            "scientific paper or citation page",
            "general web page"
        ]
        out = classifier(sample, candidate_labels=labels)
        best_label = out["labels"][0]
        best_score = float(out["scores"][0])
        return {
            "used": True,
            "matched": best_label == "dataset download page" and best_score >= 0.70,
            "label": best_label,
            "score": best_score,
            "reason": "hf_zero_shot"
        }
    except Exception as e:
        return {"used": True, "matched": False, "label": "", "score": 0.0, "reason": f"hf_error:{e}"}

# ==============================
# H1: EXTENSIÓN O INSPECCIÓN DE PÁGINA/JSON
# ==============================

def fetch_page_text(url: str, timeout: int = REQUEST_TIMEOUT, max_bytes: int = MAX_PAGE_BYTES) -> dict:
    """
    Descarga una página o JSON como texto, sin guardar archivo.
    Sirve para buscar dentro enlaces a .csv, .xlsx, .zip, etc.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 dataset-url-detector/1.0",
        "Accept": "text/html,application/json,application/ld+json,text/plain,*/*;q=0.8"
    }

    try:
        with requests.get(url, headers=headers, allow_redirects=True, timeout=timeout, stream=True) as response:
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
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

            raw = b"".join(chunks)

            text = ""
            for enc in ("utf-8", "utf-8-sig", "latin-1"):
                try:
                    text = raw.decode(enc, errors="replace")
                    break
                except Exception:
                    pass

            return {
                "ok": True,
                "status_code": response.status_code,
                "content_type": content_type,
                "final_url": final_url,
                "bytes_read": len(raw),
                "text": text
            }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "text": ""
        }


def extract_candidate_links_from_text(text: str, base_url: str = "") -> list:
    """
    Extrae URLs absolutas desde HTML, JSON o texto plano.
    También convierte enlaces relativos usando urljoin.
    """
    found = set()

    if not text:
        return []

    # URLs completas en cualquier texto/JSON
    for match in URL_REGEX.findall(text):
        found.add(match.rstrip(".,;:!?)]}>'\""))

    # href="..."
    for match in HREF_REGEX.findall(text):
        href = match.strip()
        if href and not href.startswith(("mailto:", "javascript:", "#")):
            found.add(urljoin(base_url, href))

    # src="..."
    for match in SRC_REGEX.findall(text):
        src = match.strip()
        if src and not src.startswith(("mailto:", "javascript:", "#")):
            found.add(urljoin(base_url, src))

    return sorted(found)


def score_candidate_download_link(link: str) -> dict:
    """
    Decide si un enlace encontrado dentro de una página parece archivo de dataset.

    Versión más estricta:
    - ignora comprimidos (.zip, .gz, .tar, etc.)
    - ignora ficheros técnicos (manifest.json, opensearch.xml, feed.xml, etc.)
    - acepta .csv/.tsv/.xlsx/.parquet/etc. directamente
    - acepta .json/.xml solo si el enlace tiene contexto de dataset/data/train/test/etc.
    """
    ext = get_extension(link)
    tokens = tokenize_url(link)
    positive_tokens = sorted(tokens.intersection(DATASET_LINK_KEYWORDS))
    negative_tokens = sorted(tokens.intersection(NEGATIVE_LINK_KEYWORDS))

    match_level = data_file_match_level(link)
    score = 0
    signals = []

    if match_level == "compressed":
        return {
            "link": link,
            "extension": ext,
            "score": 0,
            "signals": [f"compressed_file_ignored:{ext}"],
            "matched": False
        }

    if match_level == "technical":
        return {
            "link": link,
            "extension": ext,
            "score": 0,
            "signals": ["technical_web_file_ignored"],
            "matched": False
        }

    if match_level == "strong":
        score += 5
        signals.append(f"strong_data_extension:{ext}")

    elif match_level == "ambiguous_with_context":
        score += 5
        signals.append(f"ambiguous_data_extension_with_dataset_context:{ext}")

    elif match_level == "weak_with_context":
        score += 4
        signals.append(f"weak_data_extension_with_dataset_context:{ext}")

    if positive_tokens:
        score += min(2, len(positive_tokens))
        signals.append("dataset_keywords:" + "|".join(positive_tokens[:5]))

    if negative_tokens:
        score -= min(3, len(negative_tokens))
        signals.append("negative_keywords:" + "|".join(negative_tokens[:5]))

    return {
        "link": link,
        "extension": ext,
        "score": max(score, 0),
        "signals": signals,
        "matched": score >= 5
    }

def heuristic_1_database_by_extension_or_page(url: str) -> dict:
    """
    H1, versión confirmatoria:

    - Si la URL es un archivo de datos directo fuerte (.csv, .tsv, .xlsx, etc.), True.
    - Si la URL es una página/API/JSON, NO basta con encontrar un archivo.
      Tiene que cumplir dos condiciones a la vez:
        1) existe un descargable de datos directo y no comprimido;
        2) la página/JSON contiene contexto claro de dataset o descarga de datos.
    - Si no se cumplen ambas, es negativo.
    """
    direct_ext = get_extension(url)
    direct_match_level = data_file_match_level(url)

    # Caso directo: la URL actual ya es un archivo de datos real.
    if direct_match_level == "strong":
        return {
            "heuristic": "h1_confirmed_dataset_download_in_page",
            "matched": True,
            "score": 7,
            "reason": "url_is_direct_strong_data_file",
            "value": {
                "direct_extension": direct_ext,
                "direct_match_level": direct_match_level,
                "page_downloaded": False,
                "page_dataset_context": {},
                "candidate_dataset_links": [],
                "external_candidate_dataset_links_not_used": [],
                "signals": [f"direct_strong_data_file:{direct_ext}"]
            }
        }

    # JSON/XML directos solo se aceptan si la propia URL tiene contexto fuerte.
    if direct_match_level in {"ambiguous_with_context", "weak_with_context"}:
        return {
            "heuristic": "h1_confirmed_dataset_download_in_page",
            "matched": True,
            "score": 6,
            "reason": "url_is_direct_ambiguous_data_file_with_dataset_context",
            "value": {
                "direct_extension": direct_ext,
                "direct_match_level": direct_match_level,
                "page_downloaded": False,
                "page_dataset_context": {},
                "candidate_dataset_links": [],
                "external_candidate_dataset_links_not_used": [],
                "signals": [f"direct_{direct_match_level}:{direct_ext}"]
            }
        }

    if direct_match_level in {"compressed", "technical"}:
        return {
            "heuristic": "h1_confirmed_dataset_download_in_page",
            "matched": False,
            "score": 0,
            "reason": f"direct_{direct_match_level}_file_ignored",
            "value": {
                "direct_extension": direct_ext,
                "direct_match_level": direct_match_level,
                "page_downloaded": False,
                "page_dataset_context": {},
                "candidate_dataset_links": [],
                "external_candidate_dataset_links_not_used": [],
                "signals": [f"direct_{direct_match_level}_file_ignored:{direct_ext}"]
            }
        }

    # Caso página/API: descargamos texto/JSON y buscamos evidencia doble.
    page = fetch_page_text(url)

    if not page["ok"]:
        return {
            "heuristic": "h1_confirmed_dataset_download_in_page",
            "matched": False,
            "score": 0,
            "reason": "page_download_error",
            "value": {
                "direct_extension": direct_ext,
                "error": page.get("error", ""),
                "page_dataset_context": {},
                "candidate_dataset_links": [],
                "external_candidate_dataset_links_not_used": [],
                "signals": []
            }
        }

    text = page.get("text", "")
    final_url = page.get("final_url", url)
    content_type = page.get("content_type", "")

    parsed_json = safe_parse_json_text(text)

    # 1) Extraer descargables desde texto/HTML y, si es JSON, desde estructura JSON.
    candidate_links = set(extract_candidate_links_from_text(text, base_url=final_url))
    if parsed_json is not None:
        candidate_links.update(extract_candidate_links_from_json_obj(parsed_json, base_url=final_url))

    scored_links = []
    for link in sorted(candidate_links):
        scored = score_candidate_download_link(link)
        if scored["score"] > 0:
            scored_links.append(scored)

    scored_links.sort(key=lambda x: x["score"], reverse=True)

    # 2) Quedarnos solo con descargables que pertenecen a la misma página/sitio/repo.
    matched_links = []
    external_matched_links = []
    for x in scored_links:
        if not x["matched"]:
            continue
        if is_candidate_link_relevant_to_input(url, x["link"]):
            matched_links.append(x)
        else:
            x = dict(x)
            x["signals"] = list(x.get("signals", [])) + ["external_download_link_not_used_for_label"]
            external_matched_links.append(x)

    # 3) Comprobar que la página/JSON realmente habla de dataset/datos descargables.
    text_context = text_has_dataset_download_context(text, matched_links)
    json_context = json_has_dataset_structure(parsed_json)
    ai_context = optional_hf_dataset_classifier(text)

    page_context_score = text_context.get("score", 0) + json_context.get("score", 0)
    page_context_matched = (
        text_context.get("matched", False)
        or json_context.get("matched", False)
        or ai_context.get("matched", False)
    )

    # Regla principal: para páginas/APIs debe haber descargable + contexto.
    has_confirmed_download = len(matched_links) > 0
    matched = has_confirmed_download and page_context_matched
    score = 0
    if matched:
        score = min(10, matched_links[0]["score"] + page_context_score)

    if matched:
        reason = "dataset_download_link_found_and_page_has_dataset_context"
    elif not has_confirmed_download:
        reason = "no_relevant_dataset_download_link_found_in_page"
    else:
        reason = "download_link_found_but_page_has_no_dataset_context"

    signals = []
    if has_confirmed_download:
        signals.append("relevant_dataset_download_link_found")
    if page_context_matched:
        signals.append("page_or_json_has_dataset_context")

    return {
        "heuristic": "h1_confirmed_dataset_download_in_page",
        "matched": matched,
        "score": score,
        "reason": reason,
        "value": {
            "direct_extension": direct_ext,
            "direct_match_level": direct_match_level,
            "page_downloaded": True,
            "status_code": page.get("status_code", ""),
            "content_type": content_type,
            "final_url": final_url,
            "bytes_read": page.get("bytes_read", 0),
            "is_json_response": parsed_json is not None,
            "candidate_dataset_links": matched_links[:20],
            "external_candidate_dataset_links_not_used": external_matched_links[:20],
            "all_scored_candidate_links_sample": scored_links[:20],
            "page_dataset_context": {
                "matched": page_context_matched,
                "score": page_context_score,
                "text_context": text_context,
                "json_context": json_context,
                "ai_context": ai_context
            },
            "signals": signals
        }
    }


# ==============================
# H2: HTTP CONTENT-TYPE / CONTENT-DISPOSITION
# ==============================

def heuristic_2_http_metadata(url: str, timeout: int = REQUEST_TIMEOUT) -> dict:
    """
    Segunda heurística:
    Hace HEAD y si no sirve hace GET stream.
    Mira Content-Type, Content-Disposition y extensión de la URL final.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 dataset-url-detector/1.0",
        "Accept": "*/*"
    }

    response = None

    try:
        try:
            response = requests.head(url, headers=headers, allow_redirects=True, timeout=timeout)
        except Exception:
            response = None

        if response is None or response.status_code >= 400 or not response.headers.get("Content-Type"):
            response = requests.get(url, headers=headers, allow_redirects=True, timeout=timeout, stream=True)

        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        content_length = response.headers.get("Content-Length", "")
        content_disposition = response.headers.get("Content-Disposition", "").lower()
        final_url = response.url
        final_ext = get_extension(final_url)

        if final_ext in COMPRESSED_EXTENSIONS or content_type in COMPRESSED_CONTENT_TYPES:
            return {
                "heuristic": "h2_http_metadata",
                "matched": False,
                "score": 0,
                "reason": "compressed_file_ignored",
                "value": {
                    "status_code": response.status_code,
                    "content_type": content_type,
                    "content_length": content_length,
                    "content_disposition": content_disposition,
                    "filename_from_content_disposition": "",
                    "filename_extension": final_ext,
                    "final_url": final_url,
                    "final_extension": final_ext,
                    "signals": [f"compressed_file_ignored:{final_ext or content_type}"]
                }
            }

        signals = []
        score = 0

        final_match_level = data_file_match_level(final_url)

        # Content-Type por sí solo solo vale para formatos muy específicos.
        # application/octet-stream/json/xml no bastan si no hay extensión/nombre de dataset.
        strong_content_types = {
            "text/csv", "application/csv", "text/tab-separated-values",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/parquet", "application/x-parquet", "application/x-hdf5",
            "application/x-sqlite3"
        }

        if content_type in strong_content_types:
            score += 4
            signals.append(f"strong_data_content_type:{content_type}")

        if final_match_level in {"strong", "ambiguous_with_context", "weak_with_context"}:
            score += 3
            signals.append(f"final_data_file_match:{final_match_level}:{final_ext}")

        if "attachment" in content_disposition:
            score += 2
            signals.append("content_disposition_attachment")

        filename_match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)', content_disposition)
        filename = filename_match.group(1) if filename_match else ""
        filename_ext = get_extension("https://example.com/" + filename) if filename else ""

        if filename_ext in COMPRESSED_EXTENSIONS:
            return {
                "heuristic": "h2_http_metadata",
                "matched": False,
                "score": 0,
                "reason": "compressed_file_ignored",
                "value": {
                    "status_code": response.status_code,
                    "content_type": content_type,
                    "content_length": content_length,
                    "content_disposition": content_disposition,
                    "filename_from_content_disposition": filename,
                    "filename_extension": filename_ext,
                    "final_url": final_url,
                    "final_extension": final_ext,
                    "signals": [f"compressed_file_ignored:{filename_ext}"]
                }
            }

        filename_match_level = data_file_match_level("https://example.com/" + filename) if filename else "none"
        if filename_match_level in {"strong", "ambiguous_with_context", "weak_with_context"}:
            score += 3
            signals.append(f"content_disposition_filename_data_file:{filename_match_level}:{filename_ext}")

        if content_length:
            signals.append("has_content_length")

        matched = score > 0

        return {
            "heuristic": "h2_http_metadata",
            "matched": matched,
            "score": score,
            "reason": "http_metadata_dataset_signal" if matched else "no_http_metadata_dataset_signal",
            "value": {
                "status_code": response.status_code,
                "content_type": content_type,
                "content_length": content_length,
                "content_disposition": content_disposition,
                "filename_from_content_disposition": filename,
                "filename_extension": filename_ext,
                "final_url": final_url,
                "final_extension": final_ext,
                "signals": signals
            }
        }

    except Exception as e:
        return {
            "heuristic": "h2_http_metadata",
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


# ==============================
# H3: .dataset.json
# ==============================

def safe_read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def iter_json_nodes(obj):
    yield obj
    if isinstance(obj, dict):
        for value in obj.values():
            yield from iter_json_nodes(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_json_nodes(item)


def extract_urls_from_json_obj(obj) -> list:
    found = set()
    for node in iter_json_nodes(obj):
        if isinstance(node, str):
            for match in URL_REGEX.findall(node):
                found.add(normalize_loose(match))
    return sorted(found)


def extract_dataset_like_urls_from_json_obj(obj) -> list:
    """
    Extrae solo URLs del .dataset.json que parecen datasets de forma razonable.
    No acepta comprimidos y no acepta JSON/XML técnicos.
    """
    urls = extract_urls_from_json_obj(obj)
    dataset_like = []

    for u in urls:
        level = data_file_match_level(u)
        tokens = tokenize_url(u)
        has_dataset_keyword = bool(tokens.intersection(DATASET_LINK_KEYWORDS))

        if level in {"strong", "weak_with_context", "ambiguous_with_context"}:
            dataset_like.append(u)
        elif has_dataset_keyword and level not in {"compressed", "technical"}:
            # URL sin extensión, pero con contexto claro de dataset.
            # Para dominios genéricos exigimos un repositorio/plataforma conocida de datasets.
            domain = get_domain(u)
            if is_trusted_dataset_repository_domain(domain) or "dataset" in tokens or "datasets" in tokens:
                dataset_like.append(u)

    return sorted(set(dataset_like))


def extract_dataset_names_from_json_obj(obj) -> list:
    """
    Extrae posibles nombres de dataset del .dataset.json.
    Sirve para validar casos donde el JSON dice que el dataset se llama X
    y la URL actual es x.com o dominio.com/x.
    """
    name_keys = {
        "rawform", "raw_form", "normalizedform", "normalized_form",
        "mention", "name", "dataset", "dataset_name",
        "title", "label"
    }
    names = set()

    for node in iter_json_nodes(obj):
        if isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    continue
                key = k.strip().lower().replace("-", "_")
                key_compact = key.replace("_", "")
                if key in name_keys or key_compact in name_keys:
                    value = clean_dataset_name_text(v)
                    # Evitamos meter párrafos enormes, citas, plataformas genéricas o URLs puras como nombre.
                    if is_good_dataset_name(value):
                        names.add(value)

        # No usamos cualquier string suelto como nombre: en los JSON hay mucho texto contextual
        # que provoca coincidencias falsas. Preferimos claves explícitas como rawForm/name/title.

    return sorted(names)

def get_dataset_json_path(paper_name: str, base_dir: str = GAP_KGE_JSON_DIR) -> str:
    paper_name = (paper_name or "").strip()
    if not paper_name:
        return ""

    if paper_name.lower().endswith(".pdf"):
        stem = paper_name[:-4]
    else:
        stem = paper_name

    return str(Path(base_dir) / f"{stem}.dataset.json")


@lru_cache(maxsize=2048)
def summarize_dataset_json(json_path: str) -> dict:
    data = safe_read_json(json_path)

    if data is None:
        return {
            "exists": False,
            "error": "json_not_readable",
            "urls": [],
            "dataset_like_urls": [],
            "dataset_names": []
        }

    urls = extract_urls_from_json_obj(data)
    dataset_like_urls = extract_dataset_like_urls_from_json_obj(data)
    dataset_names = extract_dataset_names_from_json_obj(data)

    return {
        "exists": True,
        "error": "",
        "urls": urls,
        "dataset_like_urls": dataset_like_urls,
        "dataset_names": dataset_names,
        "url_count": len(urls),
        "dataset_like_url_count": len(dataset_like_urls),
        "dataset_name_count": len(dataset_names)
    }


def heuristic_3_dataset_json(url: str, paper: str, base_dir: str = GAP_KGE_JSON_DIR) -> dict:
    """
    Tercera heurística, versión estricta:

    Usa el .dataset.json como evidencia para confirmar la URL actual.
    NO marca True solo porque el paper tenga algún dataset.

    Da True si:
    1) la URL actual aparece exactamente en el .dataset.json;
    2) la URL actual comparte dominio con una URL dataset-like del JSON y además
       la URL actual tiene pinta de dataset;
    3) el JSON menciona un nombre de dataset X y la URL actual contiene ese nombre
       en el dominio/path, por ejemplo X.com o dominio.com/X.
    """
    json_path = get_dataset_json_path(paper, base_dir=base_dir)

    empty_value = {
        "json_path": json_path,
        "matched_exact_url": False,
        "matched_same_domain_dataset_url": False,
        "matched_dataset_name_in_url": False,
        "matched_dataset_name": "",
        "input_domain": get_domain(url),
        "json_url_count": 0,
        "dataset_like_url_count": 0,
        "dataset_name_count": 0,
        "sample_urls": [],
        "sample_dataset_like_urls": [],
        "sample_dataset_names": [],
        "signals": []
    }

    if not json_path or not Path(json_path).exists():
        return {
            "heuristic": "h3_dataset_json",
            "matched": False,
            "score": 0,
            "reason": "dataset_json_not_found",
            "value": empty_value
        }

    summary = summarize_dataset_json(json_path)

    if not summary["exists"]:
        return {
            "heuristic": "h3_dataset_json",
            "matched": False,
            "score": 0,
            "reason": summary.get("error", "dataset_json_unreadable"),
            "value": empty_value
        }

    input_norm = normalize_loose(url)
    input_domain = get_domain(url)
    input_root_domain = root_domain(input_domain)
    input_tokens = tokenize_url(url)

    json_urls = summary["urls"]
    dataset_like_urls = summary["dataset_like_urls"]
    dataset_names = summary.get("dataset_names", [])

    dataset_like_domains = {get_domain(u) for u in dataset_like_urls if u}
    dataset_like_root_domains = {root_domain(d) for d in dataset_like_domains if d}

    # Coincidencia exacta: ahora solo se acepta directamente si la URL exacta
    # está dentro de las URLs dataset-like. Si solo aparece en json_urls pero no
    # parece dataset, no valida por sí sola.
    matched_exact_url = input_norm in dataset_like_urls

    matched_exact_raw_url_with_dataset_signal = (
        input_norm in json_urls
        and (data_file_match_level(url) in {"strong", "weak_with_context", "ambiguous_with_context"}
             or has_strong_dataset_context(url)
             or is_trusted_dataset_repository_domain(input_domain))
    )

    # Misma web que una URL dataset-like del JSON, pero solo vale si la URL actual
    # también parece relacionada con dataset. Así evitamos marcar todos los DOIs o todas
    # las páginas del paper.
    input_match_level = data_file_match_level(url)
    input_has_dataset_signal = (
        input_match_level in {"strong", "ambiguous_with_context", "weak_with_context"}
        or has_strong_dataset_context(url)
    )

    # Para dominios genéricos tipo github.com, arxiv.org, doi.org, etc.,
    # no basta compartir dominio: son demasiado amplios.
    is_generic_domain = input_root_domain in GENERIC_HOSTING_DOMAINS or input_domain in GENERIC_HOSTING_DOMAINS

    matched_same_domain_dataset_url = (
        input_root_domain in dataset_like_root_domains
        and input_has_dataset_signal
        and not is_generic_domain
    ) if input_root_domain else False

    matched_dataset_name_in_url, matched_dataset_name = url_matches_dataset_name(url, dataset_names)

    score = 0
    signals = []

    if matched_exact_url:
        score += 6
        signals.append("exact_dataset_like_url_appears_in_dataset_json")

    if matched_exact_raw_url_with_dataset_signal:
        score += 4
        signals.append("exact_raw_url_appears_in_dataset_json_and_input_has_dataset_signal")

    if matched_same_domain_dataset_url:
        score += 4
        signals.append("same_domain_as_dataset_like_url_and_input_has_dataset_signal")

    if matched_dataset_name_in_url:
        score += 4
        signals.append("dataset_name_from_json_appears_in_input_url")

    matched = score > 0

    return {
        "heuristic": "h3_dataset_json",
        "matched": matched,
        "score": score,
        "reason": "dataset_json_confirms_current_url" if matched else "dataset_json_does_not_confirm_current_url",
        "value": {
            "json_path": json_path,
            "matched_exact_url": matched_exact_url,
            "matched_exact_raw_url_with_dataset_signal": matched_exact_raw_url_with_dataset_signal,
            "matched_same_domain_dataset_url": matched_same_domain_dataset_url,
            "matched_dataset_name_in_url": matched_dataset_name_in_url,
            "matched_dataset_name": matched_dataset_name,
            "input_domain": input_domain,
            "input_root_domain": input_root_domain,
            "input_match_level": input_match_level,
            "is_generic_domain": is_generic_domain,
            "json_url_count": summary["url_count"],
            "dataset_like_url_count": summary["dataset_like_url_count"],
            "dataset_name_count": summary.get("dataset_name_count", 0),
            "sample_urls": json_urls[:20],
            "sample_dataset_like_urls": dataset_like_urls[:20],
            "sample_dataset_names": dataset_names[:20],
            "signals": signals
        }
    }


# ==============================
# H4: ChatGPT / OpenAI semantic verifier
# ==============================

def extract_html_title(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
    if not m:
        return ""
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    return title[:300]


def compact_for_llm(value, max_chars: int = 1200) -> str:
    try:
        txt = json.dumps(value, ensure_ascii=False)
    except Exception:
        txt = str(value)
    txt = re.sub(r"\s+", " ", txt).strip()
    if len(txt) > max_chars:
        return txt[:max_chars] + "..."
    return txt


def build_openai_page_context(url: str) -> dict:
    """Descarga una muestra textual para que ChatGPT pueda razonar con contexto."""
    page = fetch_page_text(url, max_bytes=min(MAX_PAGE_BYTES, 500_000))
    if not page.get("ok"):
        return {
            "ok": False,
            "error": page.get("error", ""),
            "title": "",
            "content_type": "",
            "final_url": "",
            "text_excerpt": ""
        }

    raw_text = page.get("text", "") or ""
    clean_text = normalize_page_text(raw_text)
    return {
        "ok": True,
        "title": extract_html_title(raw_text),
        "content_type": page.get("content_type", ""),
        "final_url": page.get("final_url", url),
        "text_excerpt": clean_text[:OPENAI_PAGE_TEXT_CHARS]
    }


def should_run_openai_h4(url: str, h1: dict, h2: dict, h3: dict) -> bool:
    if not USE_OPENAI_LLM_HEURISTIC:
        return False

    if OPENAI_REVIEW_ALL_URLS:
        return True

    # Modo recomendado: revisar solo positivos de reglas, porque el objetivo principal
    # es reducir falsos positivos.
    if OPENAI_REVIEW_RULE_POSITIVES and (h1.get("matched") or h2.get("matched") or h3.get("matched")):
        return True

    return False


def openai_dataset_url_classifier(url: str, paper: str, h1: dict, h2: dict, h3: dict) -> dict:
    """
    H4: usa ChatGPT/OpenAI como verificador semántico.

    Devuelve una decisión estructurada. No sustituye a H1/H2/H3 por defecto: se usa
    sobre todo para rechazar positivos dudosos.
    """
    if not should_run_openai_h4(url, h1, h2, h3):
        return {
            "heuristic": "h4_openai_chatgpt",
            "used": False,
            "matched": False,
            "confidence": 0.0,
            "category": "not_used",
            "reason": "disabled_or_not_needed",
            "value": {"signals": []}
        }

    if not os.getenv("OPENAI_API_KEY"):
        return {
            "heuristic": "h4_openai_chatgpt",
            "used": False,
            "matched": False,
            "confidence": 0.0,
            "category": "not_used",
            "reason": "missing_openai_api_key",
            "value": {"signals": ["OPENAI_API_KEY_not_set"]}
        }

    page_context = build_openai_page_context(url)

    h1_value = h1.get("value", {})
    h2_value = h2.get("value", {})
    h3_value = h3.get("value", {})

    evidence = {
        "url": url,
        "paper": paper,
        "page": page_context,
        "h1": {
            "matched": h1.get("matched"),
            "score": h1.get("score"),
            "reason": h1.get("reason"),
            "candidate_dataset_links": h1_value.get("candidate_dataset_links", [])[:5],
            "external_candidate_dataset_links_not_used": h1_value.get("external_candidate_dataset_links_not_used", [])[:5],
            "page_dataset_context": h1_value.get("page_dataset_context", {})
        },
        "h2": {
            "matched": h2.get("matched"),
            "score": h2.get("score"),
            "reason": h2.get("reason"),
            "content_type": h2_value.get("content_type", ""),
            "content_disposition": h2_value.get("content_disposition", ""),
            "final_url": h2_value.get("final_url", ""),
            "final_extension": h2_value.get("final_extension", ""),
            "signals": h2_value.get("signals", [])
        },
        "h3": {
            "matched": h3.get("matched"),
            "score": h3.get("score"),
            "reason": h3.get("reason"),
            "signals": h3_value.get("signals", []),
            "matched_dataset_name": h3_value.get("matched_dataset_name", ""),
            "sample_dataset_like_urls": h3_value.get("sample_dataset_like_urls", [])[:10],
            "sample_dataset_names": h3_value.get("sample_dataset_names", [])[:10]
        }
    }

    system_prompt = (
        "You are a strict dataset URL classifier. Classify whether the URL itself is "
        "a dataset, a direct non-compressed data file, or a dataset landing/catalog page. "
        "Be conservative: if the URL is mainly a code repository, paper page, citation, "
        "blog, documentation, general website, login page, or merely mentions/links an "
        "external dataset, classify it as not_dataset. Do not count compressed files such "
        "as zip/tar/gz/7z as datasets. Technical files like manifest.json, opensearch.xml, "
        "sitemap.xml, package.json are not datasets. Return only valid JSON."
    )

    user_prompt = (
        "Classify this URL using the evidence below.\n\n"
        "Positive examples: direct CSV/TSV/XLSX/Parquet/HDF5/SQLite/ARFF file; "
        "a dataset landing page whose main purpose is to provide dataset access; "
        "a data repository record/catalog page for a concrete dataset.\n"
        "Negative examples: GitHub software/code repo unless the repo itself is clearly a dataset repository; "
        "scientific paper page; DOI/arXiv/citation; documentation; general website; "
        "page that only links to an external dataset.\n\n"
        f"Evidence JSON:\n{compact_for_llm(evidence, 9000)}"
    )

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "is_dataset": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "category": {
                "type": "string",
                "enum": [
                    "direct_data_file",
                    "dataset_landing_page",
                    "data_repository_record",
                    "software_or_code_repository",
                    "paper_or_citation_page",
                    "documentation_or_general_page",
                    "technical_web_file",
                    "external_dataset_mentioned_only",
                    "uncertain"
                ]
            },
            "reason": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "risk_of_false_positive": {"type": "string", "enum": ["low", "medium", "high"]}
        },
        "required": ["is_dataset", "confidence", "category", "reason", "evidence", "risk_of_false_positive"]
    }

    try:
        from openai import OpenAI
        client = OpenAI()

        # Chat Completions con Structured Outputs. Si tu versión de la librería no lo soporta,
        # actualiza con: pip install --upgrade openai
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "dataset_url_classification",
                    "strict": True,
                    "schema": schema
                }
            }
        )

        content = response.choices[0].message.content
        parsed = json.loads(content)

        return {
            "heuristic": "h4_openai_chatgpt",
            "used": True,
            "matched": bool(parsed.get("is_dataset")),
            "confidence": float(parsed.get("confidence", 0.0)),
            "category": parsed.get("category", "uncertain"),
            "reason": parsed.get("reason", ""),
            "value": {
                "model": OPENAI_MODEL,
                "evidence": parsed.get("evidence", []),
                "risk_of_false_positive": parsed.get("risk_of_false_positive", "medium"),
                "signals": ["openai_structured_verdict"]
            }
        }

    except Exception as e:
        return {
            "heuristic": "h4_openai_chatgpt",
            "used": True,
            "matched": False,
            "confidence": 0.0,
            "category": "error",
            "reason": "openai_error",
            "value": {"error": str(e), "signals": ["openai_error"]}
        }


def combine_rule_and_openai_decision(h1: dict, h2: dict, h3: dict, h4: dict) -> tuple[str, str]:
    rule_positive = h1.get("matched") or h2.get("matched") or h3.get("matched")

    matched_heuristics = []
    if h1.get("matched"):
        matched_heuristics.append("heuristica_1")
    if h2.get("matched"):
        matched_heuristics.append("heuristica_2")
    if h3.get("matched"):
        matched_heuristics.append("heuristica_3")

    # Sin OpenAI usado: comportamiento clásico.
    if not h4.get("used"):
        if rule_positive:
            return "dataset", "|".join(matched_heuristics)
        return "not_dataset", "no_heuristic_matched"

    h4_positive = h4.get("matched") and h4.get("confidence", 0.0) >= OPENAI_POSITIVE_CONFIDENCE
    h4_negative_confident = (not h4.get("matched")) and h4.get("confidence", 0.0) >= OPENAI_REJECTION_CONFIDENCE

    # Uso principal recomendado: si las reglas dicen dataset pero ChatGPT lo rechaza
    # con confianza suficiente, bajamos a not_dataset.
    if rule_positive and h4_negative_confident:
        return "not_dataset", "chatgpt_rejected_rule_positive:" + "|".join(matched_heuristics)

    # Si las reglas dicen dataset y ChatGPT también lo confirma, se queda dataset.
    if rule_positive and h4.get("matched"):
        return "dataset", "|".join(matched_heuristics + ["heuristica_4_chatgpt"])

    # Si las reglas dicen dataset pero ChatGPT está incierto, conservamos la decisión de reglas
    # para no perder recall. Puedes cambiarlo si quieres máxima precisión.
    if rule_positive:
        return "dataset", "|".join(matched_heuristics + ["chatgpt_uncertain_or_low_confidence"])

    # Opcional: permitir que ChatGPT rescate falsos negativos. Apagado por defecto.
    if (not rule_positive) and OPENAI_ALLOW_POSITIVE_OVERRIDE and h4_positive:
        return "dataset", "heuristica_4_chatgpt_positive_override"

    return "not_dataset", "no_heuristic_matched"


# ==============================
# APLICACIÓN GLOBAL
# ==============================

def apply_heuristics(url: str, paper: str = "") -> dict:
    h1 = heuristic_1_database_by_extension_or_page(url)
    h2 = heuristic_2_http_metadata(url)
    h3 = heuristic_3_dataset_json(url, paper)
    h4 = openai_dataset_url_classifier(url, paper, h1, h2, h3)

    total_score = h1["score"] + h2["score"] + h3["score"] + (1 if h4.get("matched") else 0)

    label, decision_reason = combine_rule_and_openai_decision(h1, h2, h3, h4)

    return {
        "url": url,
        "paper": paper,
        "heuristica_1": {
            "name": "extension_o_links_dataset_en_pagina",
            "matched": h1["matched"],
            "score": h1["score"],
            "reason": h1["reason"],
            "value": h1["value"]
        },
        "heuristica_2": {
            "name": "http_content_type_content_disposition",
            "matched": h2["matched"],
            "score": h2["score"],
            "reason": h2["reason"],
            "value": h2["value"]
        },
        "heuristica_3": {
            "name": "dataset_json_urls",
            "matched": h3["matched"],
            "score": h3["score"],
            "reason": h3["reason"],
            "value": h3["value"]
        },
        "heuristica_4": {
            "name": "chatgpt_semantic_verifier",
            "used": h4.get("used", False),
            "matched": h4.get("matched", False),
            "confidence": h4.get("confidence", 0.0),
            "category": h4.get("category", ""),
            "reason": h4.get("reason", ""),
            "value": h4.get("value", {})
        },
        "total_score": total_score,
        "label": label,
        "decision_reason": decision_reason
    }


# ==============================
# LECTURA / GUARDADO
# ==============================

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


def process_rows(rows: list) -> tuple[list, list]:
    results_csv = []
    results_json = []

    for row in rows:
        url = row["normalized_url"]

        if not url:
            continue

        result = apply_heuristics(url, paper=row.get("paper", ""))

        h1 = result["heuristica_1"]
        h2 = result["heuristica_2"]
        h3 = result["heuristica_3"]
        h4 = result.get("heuristica_4", {})

        h1_value = h1.get("value", {})
        h2_value = h2.get("value", {})
        h3_value = h3.get("value", {})
        h4_value = h4.get("value", {})

        results_csv.append({
            "paper": row.get("paper", ""),
            "section": row.get("section", ""),
            "original_url": row.get("original_url", ""),
            "normalized_url": url,
            "domain": row.get("domain", "") or get_domain(url),
            "extension": row.get("extension", "") or get_extension(url),

            "heuristica_1_matched": h1["matched"],
            "heuristica_1_score": h1["score"],
            "heuristica_1_reason": h1["reason"],
            "heuristica_1_direct_extension": h1_value.get("direct_extension", ""),
            "heuristica_1_candidate_dataset_links": safe_json_dumps(h1_value.get("candidate_dataset_links", [])),
            "heuristica_1_external_candidate_links_not_used": safe_json_dumps(h1_value.get("external_candidate_dataset_links_not_used", [])),
            "heuristica_1_page_context": safe_json_dumps(h1_value.get("page_dataset_context", {})),
            "heuristica_1_is_json_response": h1_value.get("is_json_response", ""),

            "heuristica_2_matched": h2["matched"],
            "heuristica_2_score": h2["score"],
            "heuristica_2_reason": h2["reason"],
            "heuristica_2_content_type": h2_value.get("content_type", ""),
            "heuristica_2_content_disposition": h2_value.get("content_disposition", ""),
            "heuristica_2_final_url": h2_value.get("final_url", ""),
            "heuristica_2_final_extension": h2_value.get("final_extension", ""),
            "heuristica_2_signals": "|".join(h2_value.get("signals", [])),

            "heuristica_3_matched": h3["matched"],
            "heuristica_3_score": h3["score"],
            "heuristica_3_reason": h3["reason"],
            "heuristica_3_json_path": h3_value.get("json_path", ""),
            "heuristica_3_dataset_like_url_count": h3_value.get("dataset_like_url_count", 0),
            "heuristica_3_sample_dataset_like_urls": safe_json_dumps(h3_value.get("sample_dataset_like_urls", [])),
            "heuristica_3_dataset_name_count": h3_value.get("dataset_name_count", 0),
            "heuristica_3_matched_dataset_name": h3_value.get("matched_dataset_name", ""),
            "heuristica_3_sample_dataset_names": safe_json_dumps(h3_value.get("sample_dataset_names", [])),
            "heuristica_3_signals": "|".join(h3_value.get("signals", [])),

            "heuristica_4_used": h4.get("used", False),
            "heuristica_4_matched": h4.get("matched", False),
            "heuristica_4_confidence": h4.get("confidence", 0.0),
            "heuristica_4_category": h4.get("category", ""),
            "heuristica_4_reason": h4.get("reason", ""),
            "heuristica_4_evidence": safe_json_dumps(h4_value.get("evidence", [])),
            "heuristica_4_risk_of_false_positive": h4_value.get("risk_of_false_positive", ""),
            "heuristica_4_signals": "|".join(h4_value.get("signals", [])),

            "total_score": result["total_score"],
            "label": result["label"],
            "decision_reason": result["decision_reason"]
        })

        results_json.append({
            "paper": row.get("paper", ""),
            "section": row.get("section", ""),
            "original_url": row.get("original_url", ""),
            "normalized_url": url,
            "result": result
        })

    return results_csv, results_json


def make_unlocked_output_path(path: str) -> str:
    """
    Devuelve una ruta alternativa con timestamp.
    Se usa cuando Windows/Excel/OneDrive bloquea el CSV o JSON original.
    """
    p = Path(path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return str(p.with_name(f"{p.stem}_{timestamp}{p.suffix}"))


def save_csv(rows: list, path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        fields = []
    else:
        fields = list(rows[0].keys())

    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            if not rows:
                f.write("")
            else:
                writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
        return path

    except PermissionError:
        alt_path = make_unlocked_output_path(path)
        print(f"\nNo se pudo escribir en: {path}")
        print("Probablemente el archivo está abierto en Excel, bloqueado por OneDrive o sin permisos.")
        print(f"Voy a guardar una copia alternativa en: {alt_path}")

        with open(alt_path, "w", newline="", encoding="utf-8-sig") as f:
            if not rows:
                f.write("")
            else:
                writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
        return alt_path


def save_json(rows: list, path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)
        return path

    except PermissionError:
        alt_path = make_unlocked_output_path(path)
        print(f"\nNo se pudo escribir en: {path}")
        print("Probablemente el archivo está abierto en otro programa o bloqueado por OneDrive.")
        print(f"Voy a guardar una copia alternativa en: {alt_path}")

        with open(alt_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)
        return alt_path


# ==============================
# MAIN
# ==============================

def main():
    if not Path(INPUT_CSV).exists():
        print(f"No existe {INPUT_CSV}")
        return

    rows = load_normalized_csv(INPUT_CSV)
    print(f"URLs normalizadas leídas: {len(rows)}")

    results_csv, results_json = process_rows(rows)

    saved_csv = save_csv(results_csv, OUTPUT_CSV)
    saved_json = save_json(results_json, OUTPUT_JSON)

    dataset_count = sum(1 for r in results_csv if r["label"] == "dataset")
    not_dataset_count = sum(1 for r in results_csv if r["label"] == "not_dataset")

    print("\nResultados guardados en:")
    print(f"- {saved_csv}")
    print(f"- {saved_json}")

    print("\nResumen:")
    print(f"- dataset: {dataset_count}")
    print(f"- not_dataset: {not_dataset_count}")


if __name__ == "__main__":
    main()
