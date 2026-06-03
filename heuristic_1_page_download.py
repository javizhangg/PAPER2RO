import csv
import json
import re
import tempfile
import os
from pathlib import Path
from urllib.parse import urlparse, urljoin
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.message import Message

import requests


# ==============================
# RUTAS Y CONFIGURACIÓN
# ==============================

INPUT_CSV = "outputs/all_links_normalized.csv"
OUTPUT_CSV = "outputs/heuristic_1_results.csv"
OUTPUT_JSON = "outputs/heuristic_1_results.json"

REQUEST_TIMEOUT = 10
MAX_PAGE_BYTES = 1_000_000
MAX_DOWNLOAD_BYTES = 80_000_000

MAX_WORKERS = 16

ALLOW_EXTERNAL_DOWNLOAD_LINKS_IN_H1 = False


# ==============================
# EXTENSIONES
# ==============================

VERY_STRONG_DATA_EXTENSIONS = {
    ".csv", ".tsv", ".parquet", ".h5", ".hdf5",
    ".sqlite", ".sqlite3", ".db", ".arff"
}

MEDIUM_DATA_EXTENSIONS = {
    ".npy", ".npz", ".mat", ".pkl", ".pickle", ".dat", ".data"
}

WEAK_DATA_EXTENSIONS = {
    ".json", ".xml", ".rdf", ".jsonl", ".ndjson", ".ttl", ".nt", ".owl"
}

COMPRESSED_EXTENSIONS = {
    ".zip", ".gz", ".tar", ".tgz", ".7z", ".rar", ".bz2", ".xz"
}

NON_DATA_EXTENSIONS = {
    ".html", ".htm", ".php", ".asp", ".aspx",
    ".js", ".css",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".doc", ".docx", ".ppt", ".pptx",
    ".bib", ".ris", ".md", ".rst",
    ".py", ".java", ".c", ".cpp", ".h", ".hpp",
    ".ipynb", ".yml", ".yaml", ".toml", ".ini", ".lock",
    ".exe", ".dll", ".so"
}

TECHNICAL_FILENAMES = {
    "manifest.json", "site.webmanifest", "asset-manifest.json",
    "opensearch.xml", "sitemap.xml", "sitemap_index.xml",
    "feed.xml", "rss.xml", "atom.xml", "robots.txt",
    "browserconfig.xml", "crossdomain.xml",
    "clientaccesspolicy.xml", "tdmrep-policy.json",
    "security.txt", "package.json", "package-lock.json",
    "yarn.lock", "pnpm-lock.yaml",
    "composer.json", "composer.lock",
    "requirements.txt", "environment.yml",
    "metadata.json", "info.json", "config.json",
    "database-config-example.json"
}

KGE_STRICT_TXT_FILES = {
    "train.txt", "test.txt", "valid.txt", "dev.txt",
    "entities.txt", "relations.txt", "triples.txt"
}

KGE_FAMOUS_DATASETS = {
    "wn18", "wn18rr", "fb15k", "fb15k237",
    "yago", "yago3", "dbpedia", "wikidata",
    "nell", "nell995", "umls", "kinship", "codex"
}


# ==============================
# CONTENT-TYPE
# ==============================

DATASET_CONTENT_TYPES = {
    "text/csv",
    "text/tab-separated-values",
    "text/plain",
    "application/csv",
    "application/json",
    "application/ld+json",
    "application/x-ndjson",
    "application/jsonlines",
    "application/parquet",
    "application/vnd.apache.parquet",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/x-sqlite3",
    "application/sqlite3",
    "application/octet-stream",
    "application/zip",
    "application/x-zip-compressed",
    "application/gzip",
    "application/x-gzip",
    "application/x-tar",
    "application/x-7z-compressed",
    "application/x-bzip2",
    "application/x-xz"
}

HTML_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml"
}


# ==============================
# PALABRAS CLAVE
# ==============================

DATASET_LINK_KEYWORDS = {
    "dataset", "datasets", "data", "download", "downloads",
    "train", "training", "test", "dev", "validation", "valid",
    "benchmark", "benchmarks", "corpus", "database",
    "annotations", "annotation", "labels", "features",
    "samples", "records", "instances", "metadata",
    "table", "tables", "export"
}

STRONG_DATASET_CONTEXT_KEYWORDS = {
    "dataset", "datasets", "data", "train", "training", "test",
    "dev", "validation", "valid", "benchmark", "corpus",
    "database", "annotations", "annotation", "labels",
    "features", "samples", "records", "instances", "metadata"
}

NEGATIVE_LINK_KEYWORDS = {
    "paper", "article", "citation", "bibtex", "reference",
    "documentation", "docs", "wiki", "blog", "login", "signin",
    "contact", "about", "license", "terms", "privacy",
    "manifest", "opensearch", "sitemap", "rss", "feed", "robots",
    "favicon", "static", "assets", "bundle", "webpack",
    "serviceworker", "readme", "requirements", "environment",
    "package", "config"
}

PAGE_DATASET_TERMS = {
    "dataset", "datasets", "data set", "data sets", "corpus",
    "benchmark", "database", "annotations", "annotation",
    "labels", "features", "samples", "records", "instances",
    "training data", "test data", "validation data", "evaluation data"
}

PAGE_DOWNLOAD_TERMS = {
    "download", "downloads", "downloadable", "available", "access",
    "get the data", "data available", "available at",
    "download the data", "download dataset", "download data"
}

GOOD_FILENAME_KEYWORDS = {
    "dataset", "datasets", "data", "train", "training",
    "test", "dev", "valid", "validation", "labels", "label",
    "annotations", "annotation", "features", "samples",
    "records", "instances", "corpus", "benchmark",
    "database", "metadata"
}

BAD_FILENAME_KEYWORDS = {
    "manifest", "package", "config", "sitemap", "feed",
    "opensearch", "citation", "bibtex", "readme",
    "license", "requirements", "environment", "robots",
    "favicon", "bundle", "webpack"
}

TECHNICAL_PATH_TOKENS = (
    "/static/", "/assets/", "/_next/", "/webpack/",
    "/favicon", "/icons/", "/apple-touch-icon",
    "/css/", "/js/", "/scripts/", "/dist/", "/build/"
)


# ==============================
# REGEX
# ==============================

URL_REGEX = re.compile(r'https?://[^\s"\'<>\)\]]+', re.IGNORECASE)
HREF_REGEX = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
SRC_REGEX = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)


# ==============================
# UTILIDADES DE URL
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


def get_extensions(url: str) -> list:
    try:
        return [s.lower() for s in Path(get_filename_from_url(url)).suffixes]
    except Exception:
        return []


def tokenize_url(url: str) -> set:
    try:
        parsed = urlparse(url)
        raw = f"{parsed.netloc}{parsed.path}{parsed.query}".lower()
        return {t for t in re.split(r"[/\\\-_.?=&:#]+", raw) if t}
    except Exception:
        return set()


def root_domain(domain: str) -> str:
    domain = (domain or "").lower().strip()

    if domain.startswith("www."):
        domain = domain[4:]

    parts = [p for p in domain.split(".") if p]

    if len(parts) <= 2:
        return domain

    return ".".join(parts[-2:])


def safe_json_dumps(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


# ==============================
# CLASIFICACIÓN POR URL
# ==============================

def filename_has_good_context(url: str) -> bool:
    filename = get_filename_from_url(url)
    tokens = tokenize_url(url)
    filename_tokens = {
        t for t in re.split(r"[^a-z0-9]+", filename.lower()) if t
    }

    return bool(
        tokens.intersection(STRONG_DATASET_CONTEXT_KEYWORDS)
        or filename_tokens.intersection(GOOD_FILENAME_KEYWORDS)
    )


def filename_has_bad_context(url: str) -> bool:
    filename = get_filename_from_url(url)
    path = get_path(url)

    filename_tokens = {
        t for t in re.split(r"[^a-z0-9]+", filename.lower()) if t
    }

    if filename in TECHNICAL_FILENAMES:
        return True

    if filename_tokens.intersection(BAD_FILENAME_KEYWORDS):
        return True

    if any(tok in path for tok in TECHNICAL_PATH_TOKENS):
        return True

    return False


def is_technical_link(url: str) -> bool:
    filename = get_filename_from_url(url)
    path = get_path(url)

    if filename in TECHNICAL_FILENAMES:
        return True

    if any(tok in path for tok in TECHNICAL_PATH_TOKENS):
        return True

    if filename.endswith((
        ".js", ".css", ".png", ".jpg", ".jpeg", ".gif",
        ".svg", ".ico", ".woff", ".woff2", ".ttf", ".eot"
    )):
        return True

    return False


def data_file_match_level(url: str) -> str:
    filename = get_filename_from_url(url)
    ext = get_extension(url)
    extensions = get_extensions(url)
    tokens = tokenize_url(url)

    if filename in KGE_STRICT_TXT_FILES:
        return "strong"

    if any(e in COMPRESSED_EXTENSIONS for e in extensions):
        if (
            tokens.intersection(KGE_FAMOUS_DATASETS)
            or "dataset" in tokens
            or "data" in tokens
        ):
            return "strong_compressed_dataset"
        return "compressed_generic"

    if is_technical_link(url) or filename_has_bad_context(url):
        return "technical"

    if ext in NON_DATA_EXTENSIONS:
        return "not_data_extension"

    if ext in VERY_STRONG_DATA_EXTENSIONS:
        return "strong"

    if ext in MEDIUM_DATA_EXTENSIONS:
        if filename_has_good_context(url):
            return "medium_with_context"
        return "medium_without_context"

    if ext in WEAK_DATA_EXTENSIONS:
        if filename_has_good_context(url):
            return "weak_with_context"
        return "weak_without_context"

    return "none"


# ==============================
# DESCARGA TEMPORAL
# ==============================

def filename_from_content_disposition(header_value: str) -> str:
    if not header_value:
        return ""

    try:
        msg = Message()
        msg["content-disposition"] = header_value
        filename = msg.get_param("filename", header="content-disposition")

        if filename:
            return Path(str(filename)).name.lower()

        return ""

    except Exception:
        return ""


def guess_level_from_filename(filename: str, original_url: str) -> str:
    if not filename:
        return "none"

    fake_url = original_url.rstrip("/") + "/" + filename
    return data_file_match_level(fake_url)


def looks_like_downloadable_response(
    content_type: str,
    content_disposition: str,
    final_url: str
) -> bool:
    ct = (content_type or "").split(";")[0].strip().lower()
    cd = (content_disposition or "").lower()

    if "attachment" in cd:
        return True

    if ct in HTML_CONTENT_TYPES:
        return False

    if ct in DATASET_CONTENT_TYPES:
        return True

    final_level = data_file_match_level(final_url)

    if final_level in {
        "strong",
        "strong_compressed_dataset",
        "medium_with_context",
        "weak_with_context"
    }:
        return True

    return False


def inspect_downloaded_file(
    temp_path: str,
    filename_hint: str,
    final_url: str,
    content_type: str
) -> dict:
    path = Path(temp_path)
    size = path.stat().st_size if path.exists() else 0

    filename = (
        filename_hint
        or get_filename_from_url(final_url)
        or path.name
    ).lower()

    ext = Path(filename).suffix.lower()
    suffixes = [s.lower() for s in Path(filename).suffixes]

    level = guess_level_from_filename(filename, final_url)

    score = 0
    signals = []

    if level in {"strong", "strong_compressed_dataset"}:
        score += 8
        signals.append(f"downloaded_file_extension_confirms_dataset:{ext}")

    elif level in {"medium_with_context", "weak_with_context"}:
        score += 6
        signals.append(f"downloaded_file_extension_with_context:{ext}")

    elif ext in VERY_STRONG_DATA_EXTENSIONS:
        score += 8
        signals.append(f"downloaded_file_strong_extension:{ext}")

    elif ext in MEDIUM_DATA_EXTENSIONS:
        score += 5
        signals.append(f"downloaded_file_medium_extension:{ext}")

    elif ext in WEAK_DATA_EXTENSIONS:
        score += 4
        signals.append(f"downloaded_file_weak_extension:{ext}")

    elif any(e in COMPRESSED_EXTENSIONS for e in suffixes):
        score += 5
        signals.append("downloaded_file_compressed_archive")

    ct = (content_type or "").split(";")[0].strip().lower()

    if ct in DATASET_CONTENT_TYPES:
        score += 2
        signals.append(f"dataset_like_content_type:{ct}")

    if ct in HTML_CONTENT_TYPES:
        score -= 6
        signals.append(f"html_content_type_not_direct_download:{ct}")

    first = b""

    try:
        with open(temp_path, "rb") as f:
            first = f.read(8192)
    except Exception:
        pass

    first_strip = first.lstrip()

    if first.startswith(b"PK\x03\x04"):
        score += 2
        signals.append("zip_magic_bytes")

    elif first.startswith(b"\x1f\x8b"):
        score += 2
        signals.append("gzip_magic_bytes")

    elif first_strip.startswith((b"{", b"[")) and ext in {
        ".json", ".jsonl", ".ndjson", ""
    }:
        score += 2
        signals.append("json_like_first_bytes")

    elif b"," in first and b"\n" in first and ext in {
        ".csv", ".txt", ""
    }:
        score += 2
        signals.append("csv_like_first_bytes")

    elif b"\t" in first and b"\n" in first and ext in {
        ".tsv", ".txt", ""
    }:
        score += 2
        signals.append("tsv_like_first_bytes")

    if size == 0:
        score = 0
        signals.append("empty_downloaded_file")

    score = max(0, min(10, score))

    return {
        "downloaded_filename": filename,
        "downloaded_extension": ext,
        "downloaded_extensions": suffixes,
        "downloaded_size_bytes": size,
        "downloaded_match_level": level,
        "downloaded_content_type": ct,
        "score": score,
        "signals": signals,
        "matched": score >= 6
    }


def try_temporary_download_and_inspect(
    url: str,
    timeout: int = REQUEST_TIMEOUT,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    session=None
) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 dataset-url-detector/1.0",
        "Accept": "application/octet-stream,text/csv,application/json,application/zip,*/*;q=0.8"
    }

    temp_path = ""

    try:
        client = session if session else requests

        with client.get(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=timeout,
            stream=True
        ) as response:

            status_code = response.status_code
            content_type = response.headers.get(
                "Content-Type", ""
            ).split(";")[0].strip().lower()

            content_disposition = response.headers.get("Content-Disposition", "")
            final_url = response.url

            filename_hint = (
                filename_from_content_disposition(content_disposition)
                or get_filename_from_url(final_url)
            )

            if status_code >= 400:
                return {
                    "ok": False,
                    "download_attempted": False,
                    "reason": f"http_status_{status_code}",
                    "status_code": status_code
                }

            if not looks_like_downloadable_response(
                content_type,
                content_disposition,
                final_url
            ):
                return {
                    "ok": True,
                    "download_attempted": False,
                    "reason": "response_does_not_look_like_direct_download",
                    "status_code": status_code,
                    "content_type": content_type,
                    "content_disposition": content_disposition,
                    "final_url": final_url,
                    "filename_hint": filename_hint
                }

            suffix = Path(filename_hint).suffix if filename_hint else ".tmp"

            fd, temp_path = tempfile.mkstemp(
                prefix="h1_download_",
                suffix=suffix
            )
            os.close(fd)

            total = 0

            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if not chunk:
                        continue

                    f.write(chunk)
                    total += len(chunk)

                    if total > max_bytes:
                        return {
                            "ok": False,
                            "download_attempted": True,
                            "reason": "file_too_large_for_temporary_check",
                            "status_code": status_code,
                            "content_type": content_type,
                            "content_disposition": content_disposition,
                            "final_url": final_url,
                            "filename_hint": filename_hint,
                            "downloaded_size_bytes": total
                        }

            inspected = inspect_downloaded_file(
                temp_path=temp_path,
                filename_hint=filename_hint,
                final_url=final_url,
                content_type=content_type
            )

            inspected.update({
                "ok": True,
                "download_attempted": True,
                "reason": "temporary_file_downloaded_and_deleted",
                "status_code": status_code,
                "content_disposition": content_disposition,
                "final_url": final_url,
                "filename_hint": filename_hint,
                "temp_file_deleted": True
            })

            return inspected

    except Exception as e:
        return {
            "ok": False,
            "download_attempted": False,
            "reason": "download_exception",
            "error": str(e)
        }

    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass


# ==============================
# ANÁLISIS DE HTML / JSON
# ==============================

def fetch_page_text(
    url: str,
    timeout: int = REQUEST_TIMEOUT,
    max_bytes: int = MAX_PAGE_BYTES,
    session=None
) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 dataset-url-detector/1.0",
        "Accept": "text/html,application/json,application/ld+json,text/plain,*/*;q=0.8"
    }

    try:
        client = session if session else requests

        with client.get(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=timeout,
            stream=True
        ) as response:

            content_type = response.headers.get(
                "Content-Type", ""
            ).split(";")[0].strip().lower()

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

            for encoding in ("utf-8", "utf-8-sig", "latin-1"):
                try:
                    text = raw.decode(encoding, errors="replace")
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


def safe_parse_json_text(text: str):
    if not text:
        return None

    stripped = text.strip()

    if not stripped or stripped[0] not in "[{":
        return None

    try:
        return json.loads(stripped)
    except Exception:
        return None


def iter_json_key_values(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k).strip().lower().replace("-", "_").replace("_", ""), v
            yield from iter_json_key_values(v)

    elif isinstance(obj, list):
        for item in obj:
            yield from iter_json_key_values(item)


def extract_candidate_links_from_json_obj(obj, base_url: str = "") -> list:
    found = set()

    if obj is None:
        return []

    data_ext_pattern = (
        r"\.(csv|tsv|json|jsonl|ndjson|xml|rdf|ttl|nt|owl|xlsx|xls|"
        r"parquet|h5|hdf5|npy|npz|arff|mat|db|sqlite|sqlite3|dat|"
        r"data|pkl|pickle)(?:$|[?#])"
    )

    for key, value in iter_json_key_values(obj):
        values = []

        if isinstance(value, str):
            values = [value]

        elif isinstance(value, list):
            values = [x for x in value if isinstance(x, str)]

        for txt in values:
            for match in URL_REGEX.findall(txt):
                found.add(match.rstrip(".,;:!?)]}>'\""))

            if re.search(data_ext_pattern, txt, re.I):
                found.add(urljoin(base_url, txt.strip()))

    return sorted(found)


def extract_candidate_links_from_text(text: str, base_url: str = "") -> list:
    found = set()

    if not text:
        return []

    for match in URL_REGEX.findall(text):
        found.add(match.rstrip(".,;:!?)]}>'\""))

    for regex in (HREF_REGEX, SRC_REGEX):
        for match in regex.findall(text):
            item = match.strip()

            if item and not item.startswith(("mailto:", "javascript:", "#")):
                found.add(urljoin(base_url, item))

    return sorted(found)


def normalize_page_text(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).lower().strip()


def candidate_link_context_windows(
    text: str,
    candidate_links: list,
    window: int = 350
) -> list:
    windows = []
    lower = (text or "").lower()

    for item in candidate_links[:20]:
        link = item.get("link", item) if isinstance(item, dict) else item

        needles = [
            link.lower(),
            get_filename_from_url(link).lower()
        ]

        for needle in needles:
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


def text_has_dataset_download_context(
    text: str,
    candidate_links: list | None = None
) -> dict:
    clean = normalize_page_text(text or "")

    if not clean:
        return {
            "matched": False,
            "score": 0,
            "signals": [],
            "matched_terms": []
        }

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

    local_hits = []

    for snippet in candidate_link_context_windows(text, candidate_links or []):
        if (
            any(t in snippet for t in PAGE_DATASET_TERMS)
            and any(t in snippet for t in PAGE_DOWNLOAD_TERMS)
        ):
            local_hits.append(snippet[:300])

    if local_hits:
        score += 4
        signals.append("dataset_download_context_near_candidate_link")

    matched = bool(local_hits) or (
        bool(dataset_terms)
        and bool(download_terms)
        and score >= 4
    )

    return {
        "matched": matched,
        "score": score,
        "signals": sorted(set(signals)),
        "matched_terms": sorted(set(matched_terms))[:20],
        "local_context_samples": local_hits[:3]
    }


def json_has_dataset_structure(obj) -> dict:
    if obj is None:
        return {
            "matched": False,
            "score": 0,
            "signals": []
        }

    score = 0
    signals = []

    for key, value in iter_json_key_values(obj):
        value_text = str(value).lower()

        if key in {"@type", "type"} and (
            "dataset" in value_text or "datacatalog" in value_text
        ):
            score += 4
            signals.append("json_schema_type_dataset")

        if key in {"downloadurl", "download_url", "contenturl", "content_url"}:
            score += 3
            signals.append(f"json_download_key:{key}")

        if key in {"distribution", "resources", "resource", "files", "file"}:
            score += 1
            signals.append(f"json_resource_key:{key}")

        if isinstance(value, str) and any(
            term in value.lower() for term in PAGE_DATASET_TERMS
        ):
            score += 1
            signals.append(f"json_text_dataset_context:{key}")

    return {
        "matched": score >= 3,
        "score": min(score, 8),
        "signals": sorted(set(signals))[:20]
    }


# ==============================
# SCORING DE ENLACES INTERNOS
# ==============================

def score_candidate_download_link(link: str) -> dict:
    ext = get_extension(link)
    extensions = get_extensions(link)
    tokens = tokenize_url(link)

    positive_tokens = sorted(tokens.intersection(DATASET_LINK_KEYWORDS))
    negative_tokens = sorted(tokens.intersection(NEGATIVE_LINK_KEYWORDS))

    level = data_file_match_level(link)

    score = 0
    signals = []

    if level in {
        "technical",
        "not_data_extension",
        "compressed_generic",
        "medium_without_context"
    }:
        return {
            "link": link,
            "extension": ext,
            "extensions": extensions,
            "match_level": level,
            "score": 0,
            "signals": [f"{level}_ignored"],
            "matched": False
        }

    if level == "strong_compressed_dataset":
        score += 7
        signals.append("kge_compressed_dataset_archive")

    elif level == "strong":
        score += 6
        signals.append("strong_data_file_or_kge_txt")

    elif level == "medium_with_context":
        score += 5
        signals.append(f"medium_extension_with_context:{ext}")

    elif level == "weak_with_context":
        score += 4
        signals.append(f"weak_extension_with_context:{ext}")

    if positive_tokens:
        score += min(2, len(positive_tokens))
        signals.append("dataset_keywords:" + "|".join(positive_tokens[:3]))

    kge_hits = tokens.intersection(KGE_FAMOUS_DATASETS)

    if kge_hits:
        score += 3
        signals.append("specific_kge_dataset_target:" + "|".join(kge_hits))

    if negative_tokens:
        score -= min(3, len(negative_tokens))
        signals.append("negative_keywords:" + "|".join(negative_tokens[:3]))

    score = max(score, 0)

    return {
        "link": link,
        "extension": ext,
        "extensions": extensions,
        "match_level": level,
        "score": score,
        "signals": signals,
        "matched": score >= 5
    }


def is_candidate_link_relevant_to_input(input_url: str, candidate_link: str) -> bool:
    if ALLOW_EXTERNAL_DOWNLOAD_LINKS_IN_H1:
        return True

    input_root = root_domain(get_domain(input_url))
    candidate_root = root_domain(get_domain(candidate_link))

    return bool(input_root and candidate_root and input_root == candidate_root)


# ==============================
# HEURÍSTICA 1
# ==============================

def heuristic_1(url: str, session=None) -> dict:
    direct_ext = get_extension(url)
    direct_extensions = get_extensions(url)
    direct_level = data_file_match_level(url)

    # 1) Primero mira la URL por terminación.
    if direct_level in {"strong", "strong_compressed_dataset"}:
        return {
            "matched": True,
            "score": 8,
            "reason": f"url_extension_confirms_{direct_level}",
            "value": {
                "direct_extension": direct_ext,
                "direct_extensions": direct_extensions,
                "direct_match_level": direct_level,
                "signals": [f"direct_valid_target:{direct_ext}"]
            }
        }

    if direct_level in {"medium_with_context", "weak_with_context"}:
        return {
            "matched": True,
            "score": 6,
            "reason": "url_extension_confirms_data_file_with_context",
            "value": {
                "direct_extension": direct_ext,
                "direct_extensions": direct_extensions,
                "direct_match_level": direct_level,
                "signals": [f"direct_{direct_level}:{direct_ext}"]
            }
        }

    # Si la URL termina claramente en algo negativo, descarta.
    if direct_level in {
        "technical",
        "not_data_extension",
        "medium_without_context",
        "weak_without_context"
    }:
        return {
            "matched": False,
            "score": 0,
            "reason": f"url_extension_rejects_{direct_level}",
            "value": {
                "direct_extension": direct_ext,
                "direct_extensions": direct_extensions,
                "direct_match_level": direct_level,
                "signals": [f"direct_{direct_level}_ignored:{direct_ext}"]
            }
        }

    # 2) Si la URL no permite decidir, intenta descarga temporal.
    download_check = try_temporary_download_and_inspect(url, session=session)

    if download_check.get("matched"):
        return {
            "matched": True,
            "score": download_check.get("score", 7),
            "reason": "url_downloaded_temporarily_and_file_type_confirms_dataset",
            "value": {
                "direct_extension": direct_ext,
                "direct_extensions": direct_extensions,
                "direct_match_level": direct_level,
                "download_check": download_check,
                "signals": download_check.get("signals", []) + [
                    "temporary_download_deleted_after_check"
                ]
            }
        }

    # 3) Si no es descargable directo, analiza HTML/JSON.
    page = fetch_page_text(url, session=session)

    if not page["ok"]:
        return {
            "matched": False,
            "score": 0,
            "reason": "not_direct_download_and_page_download_error",
            "value": {
                "direct_extension": direct_ext,
                "direct_extensions": direct_extensions,
                "direct_match_level": direct_level,
                "download_check": download_check,
                "error": page.get("error", "")
            }
        }

    text = page.get("text", "")
    final_url = page.get("final_url", url)

    parsed_json = safe_parse_json_text(text)

    candidate_links = set(extract_candidate_links_from_text(text, final_url))

    if parsed_json is not None:
        candidate_links.update(
            extract_candidate_links_from_json_obj(parsed_json, final_url)
        )

    scored_links = sorted(
        [score_candidate_download_link(link) for link in candidate_links],
        key=lambda x: x["score"],
        reverse=True
    )

    matched_links = []

    for item in scored_links:
        if not item["matched"]:
            continue

        if is_candidate_link_relevant_to_input(url, item["link"]):
            matched_links.append(item)

    text_context = text_has_dataset_download_context(text, matched_links)
    json_context = json_has_dataset_structure(parsed_json)

    page_context_score = text_context["score"] + json_context["score"]
    page_context_matched = text_context["matched"] or json_context["matched"]

    matched = bool(matched_links) and page_context_matched

    score = (
        min(10, matched_links[0]["score"] + page_context_score)
        if matched
        else 0
    )

    reason = (
        "dataset_download_link_found_and_page_has_dataset_context"
        if matched
        else "not_direct_dataset_download_and_no_valid_page_dataset_link"
    )

    return {
        "matched": matched,
        "score": score,
        "reason": reason,
        "value": {
            "direct_extension": direct_ext,
            "direct_extensions": direct_extensions,
            "direct_match_level": direct_level,
            "download_check": download_check,
            "status_code": page.get("status_code", ""),
            "content_type": page.get("content_type", ""),
            "final_url": final_url,
            "is_json_response": parsed_json is not None,
            "candidate_dataset_links": matched_links[:20],
            "all_scored_candidate_links_sample": scored_links[:20],
            "page_dataset_context": {
                "matched": page_context_matched,
                "score": page_context_score,
                "text_context": text_context,
                "json_context": json_context
            }
        }
    }


# ==============================
# CARGA Y GUARDADO
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

            rows.append({
                "pdf": row.get("paper", "").strip(),
                "url": url_to_process
            })

    return rows


def save_csv_simple(rows: list, path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["pdf", "url", "heuristica"]

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
    print(f"-> Iniciando análisis con {MAX_WORKERS} hilos...")

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
                    "score": 0,
                    "reason": f"thread_catastrophic_error: {str(e)}",
                    "value": {}
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
                "score": 0,
                "reason": "not_processed",
                "value": {}
            }
        )

        # CSV SIMPLE: SOLO PDF, URL Y TRUE/FALSE
        csv_rows.append({
            "pdf": pdf,
            "url": url,
            "heuristica": bool(h.get("matched", False))
        })

        # JSON COMPLETO: para depurar si algo sale mal
        json_rows.append({
            "pdf": pdf,
            "url": url,
            "heuristic_1": h
        })

    saved_csv = save_csv_simple(csv_rows, OUTPUT_CSV)
    saved_json = save_json_full(json_rows, OUTPUT_JSON)

    print("\n================ RESUMEN DE EJECUCIÓN ================")
    print(f" Filas finales procesadas: {len(csv_rows)}")
    print(f" Confirmados como DATASET: {sum(1 for r in csv_rows if r['heuristica'] is True)}")
    print(f" Descartados como NOT DATASET: {sum(1 for r in csv_rows if r['heuristica'] is False)}")
    print(f" CSV guardado en: {saved_csv}")
    print(f" JSON guardado en: {saved_json}")
    print("======================================================")


if __name__ == "__main__":
    main()