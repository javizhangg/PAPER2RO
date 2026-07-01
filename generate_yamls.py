import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests
import yaml
import sys
import unicodedata
from difflib import SequenceMatcher


# Evita errores UnicodeEncodeError en Windows al imprimir caracteres raros extraídos de PDFs.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


print("[DEBUG] generate_yamls.py iniciado")


# ============================================================
# CONFIGURACIÓN
# ============================================================

USE_GROBID = True
GROBID_URL = "http://localhost:8070"
GROBID_TIMEOUT = 90

# Enriquecimiento de autores para ya2ro.
# Primero intenta Crossref usando el DOI/título del paper. Si Crossref trae ORCID,
# se cruza con los autores extraídos del PDF. Como fallback, busca en ORCID y
# valida que el perfil tenga una obra con título parecido al paper.
ENABLE_AUTHOR_ORCID_ENRICHMENT = True
CROSSREF_API = "https://api.crossref.org/works"
ORCID_API = "https://pub.orcid.org/v3.0"
METADATA_TIMEOUT = 20
# Recomendado por Crossref: pon tu email si quieres ser más "polite" con la API.
# Ejemplo Windows: set CROSSREF_MAILTO=tu_correo@alumnos.upm.es
CROSSREF_MAILTO = ""
MIN_TITLE_SIMILARITY = 0.72
MIN_AUTHOR_NAME_SIMILARITY = 0.86


CLEAR_OLD_YAMLS = True

MAX_DATASETS_PER_PAPER = 8

DATASET_REPOSITORIES = [
    "kaggle.com/datasets",
    "zenodo.org/record",
    "zenodo.org/records",
    "zenodo.org/doi",
    "figshare.com",
    "data.mendeley.com",
    "datadryad.org",
    "dryad",
    "huggingface.co/datasets",
    "osf.io",
    "pangaea.de",
    "physionet.org",
    "openneuro.org",
    "archive.ics.uci.edu/dataset",
    "archive.ics.uci.edu/ml/datasets",
    "openml.org",
]

DATASET_DIRECT_EXTENSIONS = (
    ".csv", ".tsv", ".xlsx", ".xls", ".parquet",
    ".zip", ".tar.gz", ".gz", ".7z",
    ".jsonl", ".h5", ".hdf5", ".npy", ".npz",
    ".mat", ".sqlite", ".db", ".sav", ".arff",
)

BAD_DATASET_NAMES = {
    "kaggle",
    "github",
    "github repository",
    "images",
    "image",
    "wsis",
    "tissue samples",
    "samples",
    "software",
    "toolbox",
    "data",
    "dataset",
    "datasets",
    "taxonomy",
    "taxonomies",
    "table",
    "csv",
    "file",
    "files",
    "appendix",
    "supplementary",
}

BAD_TITLE_LINES = {
    "abstract",
    "introduction",
    "background",
    "summary",
    "methods",
    "references",
    "data records",
    "technical validation",
    "keywords",
}

BAD_URL_PARTS = [
    "platform.openai.com/docs",
    "neurips.cc/public",
    "neurips.cc/conferences",
    "epa.gov/air-quality",
    "tissuegnostics.com",
    "arxiv.org/abs",
    "doi.org/10.1038/",
    "doi.org/10.1007/",
    "doi.org/10.1016/",
    "doi.org/10.1109/",
    "doi.org/10.1177/",
    "doi.org/10.3390/",
    "doi.org/10.3389/",
    "doi.org/10.1091/",
    "doi.org/10.1142/",
    "doi.org/10.4103/",
]

IGNORE_DATASET_URLS = [
    "github.com/kermitt2/grobid",
    "github.com/oeg-upm/ya2ro",
]



# ============================================================
# LIMPIEZA Y UTILIDADES
# ============================================================

def clean_text(text):
    if text is None:
        return ""

    text = str(text)
    text = text.replace("\x00", " ")
    text = text.replace("\n", " ")
    text = text.replace("\r", " ")
    text = text.replace("\t", " ")
    text = text.replace("https: //", "https://")
    text = text.replace("http: //", "http://")

    text = re.sub(
        r"(https?://[^\s]+/)\s+([A-Za-z0-9_.~:/?#\[\]@!$&'()*+,;=%-]+)",
        r"\1\2",
        text,
    )

    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_url(url):
    url = clean_text(url)

    if not url:
        return ""

    url = url.replace("https://www.kaggle.com/ datasets", "https://www.kaggle.com/datasets")
    url = url.replace("https://www.kaggle.com/ datasets/", "https://www.kaggle.com/datasets/")
    url = url.rstrip(').,;]}>"\'')

    # Kaggle mal pegado:
    # https://www.kaggle.com/datasets/uciml/iris.Jannis -> .../iris
    if "kaggle.com/datasets/" in url.lower():
        url = re.sub(r"(\.)([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+)$", "", url)
        url = re.sub(r"(\.)([A-Z][A-Za-z]+)(/)?$", "", url)

    # HuggingFace mal pegado:
    # https://huggingface.co/datasets/Zihan1004/FNSPID.Only -> .../FNSPID
    if "huggingface.co/datasets/" in url.lower():
        url = re.sub(r"(\.)([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+)$", "", url)

    # DOI mal pegado:
    # https://doi.org/10.1016/j.media.2020.101786Med -> ...101786
    if "doi.org/" in url.lower():
        url = re.sub(
            r"(https://doi\.org/10\.\d{4,9}/[^\s]+?)([A-Z][a-zA-Z]{2,})$",
            r"\1",
            url,
        )
        url = url.rstrip(".,;) ]}")

    return url


def unique_list(items):
    seen = set()
    result = []

    for item in items:
        if not item:
            continue

        if isinstance(item, str):
            item = clean_text(item)

        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)

        if key not in seen:
            seen.add(key)
            result.append(item)

    return result


def extract_urls_from_text(text):
    text = clean_text(text)
    urls = re.findall(r"https?://[^\s,;)>\]\}]+", text)
    return unique_list([normalize_url(u) for u in urls])


def extract_doi_from_text(text):
    text = clean_text(text)

    m = re.search(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", text, re.I)

    if not m:
        return ""

    doi = m.group(1)
    doi = doi.rstrip(".,;) ]}")
    return doi


def doi_to_url(doi):
    doi = clean_text(doi)

    if not doi:
        return ""

    if doi.lower().startswith("http"):
        return normalize_url(doi)

    return f"https://doi.org/{doi}"


def row_is_true(value):
    if isinstance(value, bool):
        return value

    value = str(value).strip().lower()
    return value in {"true", "1", "yes", "y", "si", "sí", "positivo", "positive"}


def safe_read_csv(path):
    path = Path(path)

    if not path.exists():
        print(f"[WARN] No existe CSV: {path}")
        return pd.DataFrame()

    try:
        df = pd.read_csv(path).fillna("")
        print(f"[OK] CSV leído: {path} | filas: {len(df)}")
        return df
    except Exception as e:
        print(f"[ERROR] No se pudo leer CSV: {path}")
        print(f"[ERROR] Motivo: {e}")
        return pd.DataFrame()


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# CLASIFICACIÓN URLS
# ============================================================

def is_github_url(url):
    return "github.com" in normalize_url(url).lower()


def is_direct_dataset_file(url):
    u = normalize_url(url).lower()
    u_no_query = u.split("?")[0]
    return u_no_query.endswith(DATASET_DIRECT_EXTENSIONS)


def is_kaggle_dataset_url(url):
    url = normalize_url(url)
    ul = url.lower()

    if "kaggle.com" not in ul:
        return False

    if "/code" in ul or "/notebooks" in ul or "/competitions" in ul:
        return False

    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]

    # Formato nuevo: /datasets/owner/name
    if len(parts) >= 3 and parts[0].lower() == "datasets":
        return True

    # Formato antiguo: /owner/name
    if len(parts) >= 2 and parts[0].lower() != "datasets":
        return True

    return False


def is_dataset_repo_url(url):
    u = normalize_url(url).lower()

    if is_kaggle_dataset_url(url):
        return True

    return any(repo in u for repo in DATASET_REPOSITORIES)


def is_probably_dataset_doi(url_or_text):
    text = normalize_url(url_or_text).lower()

    if "10.5061/dryad" in text:
        return True

    if "10.5281/zenodo" in text:
        return True

    if "10.6084/m9.figshare" in text:
        return True

    if "10.17632/" in text:
        return True

    if "10.1594/pangaea" in text:
        return True

    if "10.7910/dvn" in text:
        return True

    if "zenodo.org/record" in text or "zenodo.org/records" in text or "zenodo.org/doi" in text:
        return True

    if "figshare.com" in text:
        return True

    if "data.mendeley.com" in text:
        return True

    if "datadryad.org" in text or "dryad" in text:
        return True

    if "pangaea.de" in text:
        return True

    if "physionet.org" in text:
        return True

    if "openneuro.org" in text:
        return True

    return False


def normalize_github_repo_url(url):
    url = normalize_url(url)

    if "github.com" not in url.lower():
        return ""

    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]

    if len(parts) < 2:
        return ""

    owner = parts[0]
    repo = parts[1]

    return f"https://github.com/{owner}/{repo}"


def github_blob_to_repo_and_file(url):
    url = normalize_url(url)

    if "github.com" not in url.lower():
        return "", ""

    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]

    if len(parts) < 2:
        return "", ""

    owner = parts[0]
    repo = parts[1]
    repo_url = f"https://github.com/{owner}/{repo}"
    file_name = parts[-1] if len(parts) > 2 else ""

    return repo_url, file_name


def is_valid_dataset_candidate_url(url):
    url = normalize_url(url)
    ul = url.lower()

    if not url.startswith("http"):
        return False

    if any(x in ul for x in IGNORE_DATASET_URLS):
        return False

    if any(x in ul for x in BAD_URL_PARTS):
        return False

    bad_patterns = [
        r"\.[A-Z][A-Za-z]{2,}$",
        r"\(2020$",
        r"\(2019$",
        r"\(2021$",
        r"\(2022$",
        r"\(2023$",
        r"\(2024$",
        r"Med$",
        r"Answer=$",
        r"\.Heart$",
        r"\.Iris$",
        r"\.Jannis$",
        r"\.Only$",
        r"\.Articles$",
    ]

    for p in bad_patterns:
        if re.search(p, url):
            return False

    if is_kaggle_dataset_url(url):
        return True

    if extract_doi_from_text(url):
        return is_probably_dataset_doi(url)

    if is_dataset_repo_url(url):
        return True

    if is_direct_dataset_file(url):
        return True

    if is_github_url(url) and is_direct_dataset_file(url):
        return True

    return False


def score_dataset_url(url):
    url = normalize_url(url)
    ul = url.lower()

    score = 0

    if is_probably_dataset_doi(url):
        score += 100

    if "zenodo.org/records" in ul or "zenodo.org/record" in ul:
        score += 95

    if "datadryad.org" in ul or "dryad" in ul:
        score += 95

    if "figshare.com" in ul:
        score += 90

    if "data.mendeley.com" in ul:
        score += 90

    if is_kaggle_dataset_url(url):
        score += 85

    if "huggingface.co/datasets" in ul:
        score += 80

    if "physionet.org" in ul:
        score += 80

    if "openneuro.org" in ul:
        score += 80

    if "archive.ics.uci.edu" in ul:
        score += 70

    if "openml.org" in ul:
        score += 70

    if is_direct_dataset_file(url):
        score += 40

    if is_github_url(url) and is_direct_dataset_file(url):
        score += 45

    if any(x in ul for x in BAD_URL_PARTS):
        score -= 100

    return score


def keep_best_dataset_urls(urls):
    urls = unique_list([normalize_url(u) for u in urls if u])
    urls = [u for u in urls if is_valid_dataset_candidate_url(u)]

    if not urls:
        return []

    # Si hay DOI dataset, quitar GitHub como dataset.
    has_dataset_doi = any(extract_doi_from_text(u) and is_probably_dataset_doi(u) for u in urls)

    if has_dataset_doi:
        urls = [u for u in urls if not is_github_url(u)]

    # Si hay Kaggle/Zenodo/Dryad/etc., quitar archivos sueltos de GitHub.
    has_strong_repo = any(is_dataset_repo_url(u) and not is_github_url(u) for u in urls)

    if has_strong_repo:
        urls = [
            u for u in urls
            if not (is_github_url(u) and is_direct_dataset_file(u))
        ]

    ranked = sorted(urls, key=score_dataset_url, reverse=True)
    return unique_list(ranked)[:MAX_DATASETS_PER_PAPER]


def parse_possible_url_list(value):
    if value is None:
        return []

    if isinstance(value, list):
        return [normalize_url(x) for x in value if x]

    if isinstance(value, dict):
        urls = []
        for v in value.values():
            urls.extend(parse_possible_url_list(v))
        return urls

    value = clean_text(value)

    if not value:
        return []

    # Si viene como string JSON/lista Python
    try:
        parsed = json.loads(value)
        return parse_possible_url_list(parsed)
    except Exception:
        pass

    return extract_urls_from_text(value)


# ============================================================
# HEURÍSTICAS
# ============================================================

def flatten_heuristic_json(path, heuristic_key):
    path = Path(path)

    if not path.exists():
        print(f"[WARN] No existe JSON: {path}")
        return []

    try:
        data = load_json(path)
    except Exception as e:
        print(f"[WARN] No se pudo leer JSON: {path}")
        print(f"[WARN] Motivo: {e}")
        return []

    if isinstance(data, dict):
        data = data.get("results") or data.get("data") or [data]

    rows = []

    for item in data:
        if not isinstance(item, dict):
            continue

        pdf = clean_text(item.get("pdf", ""))
        url = normalize_url(item.get("url", ""))

        h = item.get(heuristic_key, {})
        if not isinstance(h, dict):
            h = {}

        value = h.get("value", {})
        if not isinstance(value, dict):
            value = {}

        row = {
            "pdf": pdf,
            "url": url,
            "matched": h.get("matched", False),
            "reason": clean_text(h.get("reason", "")),
        }

        for k, v in value.items():
            if isinstance(v, (dict, list)):
                row[k] = v
            else:
                row[k] = clean_text(v)

        rows.append(row)

    print(f"[OK] JSON leído: {path} | filas: {len(rows)}")
    return rows


def rows_to_dataframe(rows):
    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).fillna("")


def load_heuristics(base):
    outputs = base / "outputs"

    h1_csv = outputs / "heuristic_1_results.csv"
    h2_csv = outputs / "heuristic_2_results.csv"
    h1_json = outputs / "heuristic_1_results.json"
    h2_json = outputs / "heuristic_2_results.json"

    h1_df = safe_read_csv(h1_csv)
    h2_df = safe_read_csv(h2_csv)

    h1_json_df = rows_to_dataframe(flatten_heuristic_json(h1_json, "heuristic_1"))
    h2_json_df = rows_to_dataframe(flatten_heuristic_json(h2_json, "heuristic_2"))

    if not h1_json_df.empty:
        h1_df = pd.concat([h1_df, h1_json_df], ignore_index=True).fillna("")

    if not h2_json_df.empty:
        h2_df = pd.concat([h2_df, h2_json_df], ignore_index=True).fillna("")

    print(f"[INFO] Total filas H1: {len(h1_df)}")
    print(f"[INFO] Total filas H2: {len(h2_df)}")

    return h1_df, h2_df


def row_matches_current_paper(row, pdf_name, urls_norm):
    row_pdf = clean_text(row.get("pdf", "")) if hasattr(row, "get") else ""

    if row_pdf:
        row_pdf_name = Path(row_pdf).name

        if row_pdf_name == pdf_name:
            return True

        if pdf_name in row_pdf:
            return True

        return False

    row_url = normalize_url(row.get("url", "")) if hasattr(row, "get") else ""
    return row_url in urls_norm


def h1_row_is_positive(row):
    reason = str(row.get("reason", "")).lower()

    return (
        row_is_true(row.get("heuristica", ""))
        or row_is_true(row.get("matched", ""))
        or row_is_true(row.get("es_dataset_directo", ""))
        or row_is_true(row.get("pagina_con_descargables", ""))
        or row_is_true(row.get("is_dataset", ""))
        or normalize_url(row.get("dataset_descargable", "")) != ""
        or "super_famous_dataset_domain" in reason
        or ("dataset" in reason and "negative" not in reason)
    )


def h2_row_is_positive(row):
    status_code = str(row.get("status_code", "")).strip()
    error = str(row.get("error", "")).lower()
    reason = str(row.get("reason", "")).lower()
    motivo = str(row.get("motivo", "")).lower()

    has_bad_status = status_code in {"400", "401", "403", "404", "500", "502", "503"}
    has_error = any(x in error for x in ["timeout", "ssl", "notparsable", "error"])

    is_positive = (
        row_is_true(row.get("heuristica", ""))
        or row_is_true(row.get("matched", ""))
        or row_is_true(row.get("is_dataset", ""))
        or motivo == "metadata_says_dataset"
        or reason == "metadata_says_dataset"
        or str(row.get("dataset_metadata_value", "")).lower() == "dataset"
        or str(row.get("dataset_metadata_field", "")).strip() != ""
        or str(row.get("dataset_metadata_evidence", "")).strip() != ""
    )

    if has_bad_status or has_error:
        return False

    return is_positive


def heuristic_positive_urls(h1_df, h2_df, urls_in_this_paper, pdf_name, audit_rows):
    urls_norm = {normalize_url(u) for u in urls_in_this_paper if u}

    positives_h1 = []
    positives_h2 = []
    positives_fallback = []

    # ---------------- H1 ----------------
    if not h1_df.empty and "url" in h1_df.columns:
        for _, r in h1_df.iterrows():
            if not row_matches_current_paper(r, pdf_name, urls_norm):
                continue

            if not h1_row_is_positive(r):
                continue

            candidate_urls = []

            main_url = (
                normalize_url(r.get("dataset_descargable", ""))
                or normalize_url(r.get("url", ""))
            )

            if main_url:
                candidate_urls.append(main_url)

            for col in [
                "dataset_descargables_encontrados",
                "downloadable_files",
                "files",
                "urls",
                "found_urls",
            ]:
                if col in r:
                    candidate_urls.extend(parse_possible_url_list(r.get(col, "")))

            for u in unique_list(candidate_urls):
                decision = "accepted_h1" if is_valid_dataset_candidate_url(u) else "rejected_h1_invalid"

                audit_rows.append({
                    "paper": pdf_name,
                    "url": u,
                    "source": "H1",
                    "decision": decision,
                    "score": score_dataset_url(u) if is_valid_dataset_candidate_url(u) else 0,
                })

                if is_valid_dataset_candidate_url(u):
                    positives_h1.append(u)

    # ---------------- H2 ----------------
    if not h2_df.empty and "url" in h2_df.columns:
        for _, r in h2_df.iterrows():
            if not row_matches_current_paper(r, pdf_name, urls_norm):
                continue

            url = normalize_url(r.get("url", ""))

            if not url:
                continue

            if not h2_row_is_positive(r):
                audit_rows.append({
                    "paper": pdf_name,
                    "url": url,
                    "source": "H2",
                    "decision": "rejected_h2_not_positive",
                    "score": 0,
                })
                continue

            decision = "accepted_h2" if is_valid_dataset_candidate_url(url) else "rejected_h2_invalid"

            audit_rows.append({
                "paper": pdf_name,
                "url": url,
                "source": "H2",
                "decision": decision,
                "score": score_dataset_url(url) if is_valid_dataset_candidate_url(url) else 0,
            })

            if is_valid_dataset_candidate_url(url):
                positives_h2.append(url)

    # ---------------- Fallback desde paper / datastet ----------------
    for u in urls_in_this_paper:
        u = normalize_url(u)

        if not u:
            continue

        if is_valid_dataset_candidate_url(u):
            positives_fallback.append(u)
            audit_rows.append({
                "paper": pdf_name,
                "url": u,
                "source": "fallback",
                "decision": "accepted_fallback",
                "score": score_dataset_url(u),
            })

    positives_h1 = unique_list(positives_h1)
    positives_h2 = unique_list(positives_h2)
    positives_fallback = unique_list(positives_fallback)

    print(f"[INFO] Positivos H1 válidos: {len(positives_h1)}")
    for u in positives_h1:
        print(f"       [H1] {u}")

    print(f"[INFO] Positivos H2 válidos: {len(positives_h2)}")
    for u in positives_h2:
        print(f"       [H2] {u}")

    print(f"[INFO] Positivos fallback válidos: {len(positives_fallback)}")
    for u in positives_fallback:
        print(f"       [FALLBACK] {u}")

    union = unique_list(positives_h1 + positives_h2 + positives_fallback)
    final_urls = keep_best_dataset_urls(union)

    print(f"[INFO] Union final limpia H1 + H2 + fallback: {len(final_urls)}")
    for u in final_urls:
        print(f"       [DATASET FINAL] {u}")

    return final_urls


# ============================================================
# GAP-KGE / DATASTET
# ============================================================

def score_mention(m):
    name = clean_text(m.get("normalizedForm") or m.get("rawForm"))

    if not name or name.lower() in BAD_DATASET_NAMES:
        return -999

    score = 0

    if m.get("type") == "dataset-name":
        score += 10
    elif m.get("type") == "dataset-implicit":
        score += 2

    ctx = clean_text(m.get("context"))
    ctx_l = ctx.lower()

    if "dataset" in ctx_l:
        score += 3

    if "publicly available" in ctx_l or "freely available" in ctx_l:
        score += 4

    if "available at" in ctx_l or "available on" in ctx_l:
        score += 3

    if "doi.org" in ctx_l:
        score += 3

    if "data from" in ctx_l:
        score += 2

    attrs = m.get("documentContextAttributes") or m.get("mentionContextAttributes") or {}

    for role, weight in [("created", 5), ("shared", 4), ("used", 2)]:
        v = attrs.get(role, {})

        if isinstance(v, dict):
            if v.get("value") is True:
                score += weight

            try:
                score += float(v.get("score") or 0)
            except Exception:
                pass

    lname = name.lower()

    if "kaggle" in lname or "github" in lname:
        score -= 5

    return score


def extract_main_dataset_info(dataset_json):
    mentions = dataset_json.get("mentions", []) or []

    urls = []
    names = []
    contexts = []
    data_types = []

    for m in mentions:
        name = clean_text(m.get("normalizedForm") or m.get("rawForm"))
        ctx = clean_text(m.get("context"))

        if name:
            names.append(name)

        if ctx:
            contexts.append(ctx)

        urls.extend(extract_urls_from_text(ctx))

        implicit = m.get("dataset-implicit", {})
        if isinstance(implicit, dict):
            dt = clean_text(implicit.get("bestDataType", ""))
            if dt:
                data_types.append(dt)

    urls = unique_list(urls)
    names = unique_list(names)
    contexts = unique_list(contexts)
    data_types = unique_list(data_types)

    if not mentions:
        return {
            "name": "",
            "description": "",
            "urls": urls,
            "role": "",
            "all_names": names,
            "all_contexts": contexts,
            "data_types": data_types,
        }

    best = sorted(mentions, key=score_mention, reverse=True)[0]

    attrs = best.get("documentContextAttributes") or best.get("mentionContextAttributes") or {}
    role_scores = {}

    for role in ["created", "shared", "used"]:
        v = attrs.get(role, {})

        if isinstance(v, dict):
            try:
                role_scores[role] = float(v.get("score") or 0)
            except Exception:
                role_scores[role] = 0

    role = max(role_scores, key=role_scores.get) if role_scores else ""

    return {
        "name": clean_text(best.get("normalizedForm") or best.get("rawForm")),
        "description": clean_text(best.get("context")),
        "urls": urls,
        "role": role,
        "all_names": names,
        "all_contexts": contexts,
        "data_types": data_types,
    }


# ============================================================
# GROBID: XML TEI O BIBTEX
# ============================================================

def is_grobid_alive():
    if not USE_GROBID:
        print("[INFO] USE_GROBID=False")
        return False

    url = f"{GROBID_URL}/api/isalive"

    try:
        r = requests.get(url, timeout=10)
        return r.status_code == 200 and "true" in r.text.lower()
    except Exception as e:
        print(f"[WARN] No se pudo conectar con GROBID: {e}")
        return False


def tei_text(element):
    if element is None:
        return ""

    return clean_text(" ".join(element.itertext()))


def extract_person_name_from_author(author_node, ns):
    if author_node is None:
        return ""

    pers = author_node.find(".//tei:persName", ns)

    if pers is not None:
        forenames = []
        surnames = []

        for fn in pers.findall(".//tei:forename", ns):
            value = tei_text(fn)
            if value:
                forenames.append(value)

        for sn in pers.findall(".//tei:surname", ns):
            value = tei_text(sn)
            if value:
                surnames.append(value)

        name = clean_text(" ".join(forenames + surnames))

        if name:
            return name

    return tei_text(author_node)


def extract_bibtex_field(text, field):
    """
    Extrae campos BibTeX con llaves balanceadas:
    title = {...}
    author = {...}
    abstract = {...}
    """
    pattern = re.compile(rf"{field}\s*=\s*\{{", re.I)
    m = pattern.search(text)

    if not m:
        return ""

    start = m.end()
    depth = 1
    i = start

    while i < len(text):
        ch = text[i]

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1

            if depth == 0:
                return clean_text(text[start:i])

        i += 1

    return ""


def parse_bibtex_authors(author_text):
    author_text = clean_text(author_text)

    if not author_text:
        return []

    raw_authors = re.split(r"\s+and\s+", author_text)
    authors = []

    for raw in raw_authors:
        raw = clean_text(raw)
        raw = raw.replace("✉", "")
        raw = raw.replace("*", "")
        raw = raw.replace("†", "")
        raw = re.sub(r"\d+", "", raw)
        raw = clean_text(raw)

        if not raw:
            continue

        # BibTeX: Apellido, Nombre
        if "," in raw:
            parts = [clean_text(p) for p in raw.split(",") if clean_text(p)]

            if len(parts) >= 2:
                surname = parts[0]
                forename = " ".join(parts[1:])
                name = clean_text(f"{forename} {surname}")
            else:
                name = raw
        else:
            name = raw

        if name:
            authors.append(name)

    return unique_list(authors)


def parse_grobid_bibtex(bibtex_text):
    result = {
        "title": "",
        "summary": "",
        "authors": [],
        "doi": "",
    }

    result["title"] = extract_bibtex_field(bibtex_text, "title")
    result["summary"] = extract_bibtex_field(bibtex_text, "abstract")
    result["doi"] = extract_bibtex_field(bibtex_text, "doi")

    author_text = extract_bibtex_field(bibtex_text, "author")
    result["authors"] = parse_bibtex_authors(author_text)

    return result


def parse_grobid_response(text):
    """
    GROBID puede devolver:
    - XML TEI
    - BibTeX
    Este parser soporta ambos.
    """
    result = {
        "title": "",
        "summary": "",
        "authors": [],
        "doi": "",
    }

    if not text:
        return result

    raw_start = text[:300].strip()

    if raw_start.startswith("@"):
        print("[INFO] GROBID devolvió BibTeX. Se parsea como BibTeX.")
        return parse_grobid_bibtex(text)

    if not raw_start.startswith("<"):
        print("[WARN] GROBID no devolvió XML ni BibTeX válido.")
        print(f"[WARN] Inicio respuesta GROBID: {raw_start[:300]}")
        return result

    try:
        root = ET.fromstring(text)
    except Exception as e:
        print(f"[WARN] No se pudo parsear XML TEI de GROBID: {e}")
        print(f"[WARN] Inicio respuesta GROBID: {raw_start[:300]}")
        return result

    ns = {"tei": "http://www.tei-c.org/ns/1.0"}

    title_node = root.find(".//tei:titleStmt/tei:title", ns)
    if title_node is None:
        title_node = root.find(".//tei:analytic/tei:title", ns)

    result["title"] = tei_text(title_node)

    abstract_node = root.find(".//tei:profileDesc/tei:abstract", ns)
    if abstract_node is None:
        abstract_node = root.find(".//tei:abstract", ns)

    result["summary"] = tei_text(abstract_node)

    authors = []

    author_nodes = root.findall(".//tei:sourceDesc//tei:biblStruct//tei:analytic//tei:author", ns)
    if not author_nodes:
        author_nodes = root.findall(".//tei:titleStmt//tei:author", ns)

    for author_node in author_nodes:
        name = extract_person_name_from_author(author_node, ns)
        if name:
            authors.append(name)

    result["authors"] = unique_list(authors)

    doi_nodes = root.findall(".//tei:idno", ns)

    for node in doi_nodes:
        node_type = clean_text(node.attrib.get("type", "")).lower()
        value = tei_text(node)

        if node_type == "doi" and value:
            result["doi"] = value
            break

    return result


def extract_grobid_header_metadata(pdf_path):
    pdf_path = Path(pdf_path)

    empty = {
        "title": "",
        "summary": "",
        "authors": [],
        "doi": "",
        "source": "grobid_header",
    }

    if not USE_GROBID:
        return empty

    if not pdf_path.exists():
        return empty

    if not is_grobid_alive():
        print("[WARN] GROBID no está activo. Se usará PyMuPDF.")
        return empty

    try:
        print(f"[INFO] Enviando a GROBID header: {pdf_path.name}")

        with open(pdf_path, "rb") as f:
            files = {
                "input": (pdf_path.name, f, "application/pdf")
            }

            data = {
                "consolidateHeader": "0",
                "includeRawAffiliations": "1",
            }

            headers = {
                "Accept": "application/xml"
            }

            r = requests.post(
                f"{GROBID_URL}/api/processHeaderDocument",
                files=files,
                data=data,
                headers=headers,
                timeout=GROBID_TIMEOUT,
            )

        print(f"[DEBUG] GROBID header status: {r.status_code}")

        if r.status_code != 200:
            print(f"[WARN] GROBID header falló: status {r.status_code}")
            print(f"[WARN] Respuesta: {r.text[:300]}")
            return empty

        meta = parse_grobid_response(r.text)
        meta["source"] = "grobid_header"

        print(f"[OK] GROBID title: {meta.get('title')}")
        print(f"[OK] GROBID authors: {meta.get('authors')[:5]}")
        print(f"[OK] GROBID abstract length: {len(meta.get('summary', ''))}")

        return meta

    except Exception as e:
        print(f"[WARN] Error usando GROBID header en {pdf_path.name}: {e}")
        return empty


def extract_grobid_fulltext_metadata(pdf_path):
    pdf_path = Path(pdf_path)

    empty = {
        "title": "",
        "summary": "",
        "authors": [],
        "doi": "",
        "source": "grobid_fulltext",
    }

    if not USE_GROBID:
        return empty

    if not pdf_path.exists():
        return empty

    if not is_grobid_alive():
        return empty

    try:
        print(f"[INFO] Enviando a GROBID fulltext: {pdf_path.name}")

        with open(pdf_path, "rb") as f:
            files = {
                "input": (pdf_path.name, f, "application/pdf")
            }

            data = {
                "consolidateHeader": "0",
                "consolidateCitations": "0",
                "includeRawAffiliations": "1",
            }

            headers = {
                "Accept": "application/xml"
            }

            r = requests.post(
                f"{GROBID_URL}/api/processFulltextDocument",
                files=files,
                data=data,
                headers=headers,
                timeout=GROBID_TIMEOUT,
            )

        print(f"[DEBUG] GROBID fulltext status: {r.status_code}")

        if r.status_code != 200:
            print(f"[WARN] GROBID fulltext falló: status {r.status_code}")
            print(f"[WARN] Respuesta: {r.text[:300]}")
            return empty

        meta = parse_grobid_response(r.text)
        meta["source"] = "grobid_fulltext"

        return meta

    except Exception as e:
        print(f"[WARN] Error usando GROBID fulltext en {pdf_path.name}: {e}")
        return empty


# ============================================================
# PYMUPDF FALLBACK
# ============================================================

def extract_pdf_text(pdf_path, max_pages=8):
    try:
        import fitz
    except ImportError:
        print("[WARN] PyMuPDF no está instalado. pip install pymupdf")
        return ""

    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        return ""

    try:
        doc = fitz.open(str(pdf_path))
        pages = []

        for i in range(min(max_pages, len(doc))):
            pages.append(doc[i].get_text("text"))

        return clean_text("\n".join(pages))
    except Exception as e:
        print(f"[WARN] No se pudo extraer texto PDF: {pdf_path}")
        print(f"[WARN] Motivo: {e}")
        return ""


def get_pdf_first_page_lines(pdf_path):
    try:
        import fitz
    except ImportError:
        return []

    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        return []

    try:
        doc = fitz.open(str(pdf_path))
        page = doc[0]

        return [
            clean_text(x)
            for x in page.get_text("text").splitlines()
            if clean_text(x)
        ]
    except Exception:
        return []


def is_good_title(title):
    title = clean_text(title)

    if not title:
        return False

    if len(title) < 8:
        return False

    title_l = title.lower()

    if title_l in BAD_TITLE_LINES:
        return False

    bad_fragments = [
        "microsoft word",
        "untitled",
        "template",
        "download",
        "supplementary",
        "preprint",
    ]

    if any(x in title_l for x in bad_fragments):
        return False

    if title_l.startswith("http"):
        return False

    if "doi.org" in title_l:
        return False

    if title.count(" ") < 1 and len(title) < 20:
        return False

    return True


def extract_title_from_pdf_metadata(pdf_path):
    try:
        import fitz
    except ImportError:
        return ""

    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        return ""

    try:
        doc = fitz.open(str(pdf_path))
        meta = doc.metadata or {}
        title = clean_text(meta.get("title", ""))

        if is_good_title(title):
            return title
    except Exception:
        pass

    return ""


def extract_title_from_first_lines(pdf_path):
    lines = get_pdf_first_page_lines(pdf_path)

    if not lines:
        return ""

    title_lines = []

    for line in lines[:12]:
        line = clean_text(line)
        line_l = line.lower()

        if not line:
            continue

        if line_l in BAD_TITLE_LINES:
            break

        if line_l.startswith("arxiv:"):
            continue

        if "doi.org" in line_l:
            continue

        if "@" in line:
            break

        if len(title_lines) >= 5:
            break

        title_lines.append(line)

    title = clean_text(" ".join(title_lines))

    if is_good_title(title):
        return title

    return ""


def extract_abstract_from_text(text):
    text = clean_text(text)

    patterns = [
        r"Abstract\.?\s+(.*?)(?:\s+1\s+Introduction|\s+Introduction|\s+Background\s*&\s*Summary|\s+Background|\s+Methods|\s+Keywords)",
        r"ABSTRACT\s+(.*?)(?:\s+1\s+Introduction|\s+Introduction|\s+Background\s*&\s*Summary|\s+Background|\s+Methods|\s+Keywords)",
    ]

    for p in patterns:
        m = re.search(p, text, re.I)

        if m:
            abstract = clean_text(m.group(1))

            if len(abstract) > 30:
                return abstract

    return ""



# ============================================================
# ENRIQUECIMIENTO DE AUTORES CON ORCID / CROSSREF
# ============================================================

def normalize_for_match(value):
    value = clean_text(value).lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def similarity(a, b):
    a = normalize_for_match(a)
    b = normalize_for_match(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def split_author_name(name):
    """Separación sencilla: primer bloque como given, último como family."""
    name = clean_text(name)
    parts = [p for p in name.split(" ") if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def clean_orcid(orcid_value):
    orcid_value = clean_text(orcid_value)
    if not orcid_value:
        return ""
    m = re.search(r"(\d{4}-\d{4}-\d{4}-[\dX]{4})", orcid_value, re.I)
    return m.group(1).upper() if m else ""


def request_json(url, params=None, headers=None, timeout=METADATA_TIMEOUT):
    headers = headers or {}
    headers.setdefault("Accept", "application/json")
    headers.setdefault("User-Agent", "Paper2RO-YAML-generator/1.0")
    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        print(f"[WARN] No se pudo consultar metadata externa: {url} | {e}")
        return None


def crossref_headers():
    headers = {"Accept": "application/json", "User-Agent": "Paper2RO-YAML-generator/1.0"}
    mailto = clean_text(CROSSREF_MAILTO)
    if mailto:
        headers["User-Agent"] = f"Paper2RO-YAML-generator/1.0 (mailto:{mailto})"
    return headers


def get_crossref_work_by_doi(doi):
    doi = clean_text(doi)
    if not doi:
        return None
    doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
    url = f"{CROSSREF_API}/{doi}"
    data = request_json(url, headers=crossref_headers())
    if not data:
        return None
    return data.get("message") if isinstance(data, dict) else None


def search_crossref_work_by_title(title):
    title = clean_text(title)
    if not title:
        return None

    params = {
        "query.title": title,
        "rows": 5,
        "select": "DOI,title,author,issued,URL,type",
    }
    data = request_json(CROSSREF_API, params=params, headers=crossref_headers())
    items = (((data or {}).get("message") or {}).get("items") or [])
    if not items:
        return None

    best = None
    best_score = 0.0
    for item in items:
        item_title = ""
        if isinstance(item.get("title"), list) and item.get("title"):
            item_title = clean_text(item["title"][0])
        score = similarity(title, item_title)
        if score > best_score:
            best = item
            best_score = score

    if best and best_score >= MIN_TITLE_SIMILARITY:
        print(f"[OK] Paper localizado en Crossref por título | similitud={best_score:.2f} | DOI={best.get('DOI','')}")
        return best

    print("[WARN] Crossref no encontró un paper suficientemente parecido por título.")
    return None


def crossref_authors_from_work(work):
    result = []
    if not isinstance(work, dict):
        return result

    for a in work.get("author", []) or []:
        given = clean_text(a.get("given", ""))
        family = clean_text(a.get("family", ""))
        name = clean_text(f"{given} {family}") or clean_text(a.get("name", ""))
        orcid = clean_orcid(a.get("ORCID", ""))
        if name:
            item = {"name": name}
            if orcid:
                item["orcid"] = orcid
            result.append(item)
    return unique_list(result)


def find_matching_crossref_author(author_name, crossref_authors):
    best = None
    best_score = 0.0
    for ca in crossref_authors:
        score = similarity(author_name, ca.get("name", ""))
        if score > best_score:
            best = ca
            best_score = score
    if best and best_score >= MIN_AUTHOR_NAME_SIMILARITY:
        return best, best_score
    return None, best_score


def orcid_expanded_search(name):
    given, family = split_author_name(name)
    if not family:
        q = f'"{name}"'
    elif given:
        q = f'given-names:"{given}" AND family-name:"{family}"'
    else:
        q = f'family-name:"{family}"'

    url = f"{ORCID_API}/expanded-search/"
    data = request_json(url, params={"q": q, "rows": 5}, headers={"Accept": "application/json"})
    expanded = (((data or {}).get("expanded-result") or []))
    candidates = []
    for item in expanded:
        orcid = clean_orcid(item.get("orcid-id", ""))
        given_names = clean_text(item.get("given-names", ""))
        family_names = clean_text(item.get("family-names", ""))
        credit_name = clean_text(item.get("credit-name", ""))
        full_name = credit_name or clean_text(f"{given_names} {family_names}")
        if orcid and full_name and similarity(name, full_name) >= MIN_AUTHOR_NAME_SIMILARITY:
            candidates.append({"name": full_name, "orcid": orcid})
    return unique_list(candidates)


def orcid_profile_has_similar_work(orcid, paper_title):
    """Valida ORCID comprobando títulos públicos de obras en el perfil."""
    orcid = clean_orcid(orcid)
    paper_title = clean_text(paper_title)
    if not orcid or not paper_title:
        return False

    url = f"{ORCID_API}/{orcid}/works"
    data = request_json(url, headers={"Accept": "application/json"})
    groups = (((data or {}).get("group") or []))

    for group in groups:
        summaries = group.get("work-summary", []) or []
        for w in summaries:
            title_obj = ((w.get("title") or {}).get("title") or {})
            title_value = clean_text(title_obj.get("value", ""))
            if title_value and similarity(title_value, paper_title) >= MIN_TITLE_SIMILARITY:
                return True
    return False


def enrich_authors_with_orcid(authors, paper_title="", paper_doi=""):
    """
    Devuelve autores listos para ya2ro:
    - si encuentra ORCID fiable: {orcid: ..., role: ...}
    - si no: {name: ..., role: ...}

    Prioridad:
    1) Crossref por DOI del paper.
    2) Crossref por título si no hay DOI.
    3) ORCID expanded-search + validación contra títulos de obras públicas.
    """
    if not ENABLE_AUTHOR_ORCID_ENRICHMENT:
        return authors

    clean_authors = clean_detected_authors(authors, trusted=True)
    if not clean_authors:
        return []

    work = None
    if paper_doi:
        work = get_crossref_work_by_doi(paper_doi)
        if work:
            print(f"[OK] Metadata Crossref obtenida por DOI: {paper_doi}")

    if not work and paper_title:
        work = search_crossref_work_by_title(paper_title)

    crossref_authors = crossref_authors_from_work(work) if work else []
    enriched = []

    for idx, a in enumerate(clean_authors):
        name = clean_text(a.get("name", ""))
        if not name:
            continue

        item = {"name": name, "role": "author"}

        matched_crossref, score = find_matching_crossref_author(name, crossref_authors)
        if matched_crossref and matched_crossref.get("orcid"):
            item = {"orcid": matched_crossref["orcid"], "role": "author"}
            print(f"[OK] ORCID por Crossref: {name} -> {matched_crossref['orcid']} | similitud={score:.2f}")
        else:
            # Fallback más conservador: solo acepta ORCID si el perfil contiene una obra con título parecido.
            for cand in orcid_expanded_search(name):
                if orcid_profile_has_similar_work(cand.get("orcid", ""), paper_title):
                    item = {"orcid": cand["orcid"], "role": "author"}
                    print(f"[OK] ORCID por ORCID API + obra coincidente: {name} -> {cand['orcid']}")
                    break

        # Si no hay ORCID, ya2ro acepta name. Añadimos position/description solo si lo tienes claro.
        if "orcid" not in item:
            item = {"name": name, "role": "author"}

        enriched.append(item)

    return unique_list(enriched)


def choose_paper_doi(pdf_meta, title):
    """Intenta asegurar un DOI de paper para bibliography y para enriquecer autores."""
    paper_doi = clean_text(pdf_meta.get("paper_doi", ""))
    if paper_doi:
        return paper_doi

    # Evita usar DOI de datasets como DOI principal del paper.
    for doi in pdf_meta.get("dois", []) or []:
        doi_url_value = doi_to_url(doi)
        if not is_probably_dataset_doi(doi_url_value):
            return doi

    work = search_crossref_work_by_title(title)
    if work and work.get("DOI"):
        return clean_text(work.get("DOI"))

    return ""

def clean_detected_authors(authors, trusted=False):
    clean_authors = []

    bad_words = {
        "dataset",
        "segmentation",
        "histological",
        "images",
        "abstract",
        "university",
        "institute",
        "department",
        "available",
        "doi",
        "github",
        "kaggle",
        "paper",
        "method",
        "methods",
        "study",
        "analysis",
        "introduction",
        "conclusion",
        "references",
    }

    for a in authors:
        if isinstance(a, dict):
            name = clean_text(a.get("name", ""))
        else:
            name = clean_text(a)

        name = re.sub(r"\d+", "", name)
        name = name.replace("✉", "")
        name = name.replace("*", "")
        name = name.replace("†", "")
        name = clean_text(name)

        if not name:
            continue

        if "http" in name.lower() or "@" in name:
            continue

        if not trusted:
            if len(name.split()) > 6:
                continue

            if any(w in name.lower() for w in bad_words):
                continue

        if trusted:
            if len(name.split()) > 10:
                continue

            if any(w in name.lower() for w in ["abstract", "introduction", "references"]):
                continue

        clean_authors.append({"name": name})

    return unique_list(clean_authors)


def extract_pdf_metadata(pdf_path):
    text = extract_pdf_text(pdf_path, max_pages=8)

    meta = {
        "title": "",
        "summary": "",
        "authors": [],
        "authors_source": "",
        "urls": [],
        "dois": [],
        "paper_doi": "",
    }

    if text:
        meta["urls"] = extract_urls_from_text(text)

        dois = re.findall(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, re.I)
        dois = [d.rstrip(".,;) ]}") for d in dois]
        meta["dois"] = unique_list(dois)

    grobid_header = extract_grobid_header_metadata(pdf_path)

    grobid_fulltext = {
        "title": "",
        "summary": "",
        "authors": [],
        "doi": "",
        "source": "",
    }

    if not grobid_header.get("summary"):
        grobid_fulltext = extract_grobid_fulltext_metadata(pdf_path)

    grobid_title = clean_text(
        grobid_header.get("title")
        or grobid_fulltext.get("title")
        or ""
    )

    if is_good_title(grobid_title):
        meta["title"] = grobid_title
    else:
        title1 = extract_title_from_pdf_metadata(pdf_path)
        title2 = extract_title_from_first_lines(pdf_path)

        if is_good_title(title1):
            meta["title"] = title1
        elif is_good_title(title2):
            meta["title"] = title2

    grobid_summary = clean_text(
        grobid_header.get("summary")
        or grobid_fulltext.get("summary")
        or ""
    )

    if len(grobid_summary) > 40:
        meta["summary"] = grobid_summary
    elif text:
        meta["summary"] = extract_abstract_from_text(text)

    grobid_authors = grobid_header.get("authors") or grobid_fulltext.get("authors") or []

    if grobid_authors:
        meta["authors"] = grobid_authors
        meta["authors_source"] = "grobid"
    else:
        meta["authors"] = []
        meta["authors_source"] = ""

    paper_doi = clean_text(grobid_header.get("doi") or grobid_fulltext.get("doi") or "")

    if paper_doi:
        meta["paper_doi"] = paper_doi

    print("[INFO] Metadata PDF:")
    print(f"       title: {meta['title']}")
    print(f"       authors_source: {meta['authors_source']}")
    print(f"       authors: {meta['authors'][:5]}")

    return meta


# ============================================================
# YAML YA2RO
# ============================================================

def valid_gap_dataset_name(name):
    name = clean_text(name)

    if not name:
        return False

    if name.lower() in BAD_DATASET_NAMES:
        return False

    if len(name) < 3:
        return False

    return True



def strip_query_and_fragment(url):
    """Quita query y fragment para evitar enlaces tipo ?download=1 en ya2ro."""
    url = normalize_url(url)
    if not url:
        return ""

    try:
        parsed = urlparse(url)
        return parsed._replace(query="", fragment="").geturl().rstrip("/")
    except Exception:
        return url.split("?")[0].split("#")[0].rstrip("/")


def normalize_repository_record_url(url):
    """
    Convierte enlaces directos a ficheros/API de repositorios en páginas de registro.
    Esto evita que ya2ro intente descargar ficheros pesados (.h5, .csv, .zip, etc.).
    """
    url = normalize_url(url)

    if not url:
        return ""

    # Zenodo:
    # https://zenodo.org/records/13327692/files/file.h5?download=1 -> https://zenodo.org/records/13327692
    # https://zenodo.org/api/records/13327692/files/file.csv -> https://zenodo.org/records/13327692
    m = re.search(r"https?://(?:www\.)?zenodo\.org/(?:api/)?records?/(\d+)", url, re.I)
    if m:
        return f"https://zenodo.org/records/{m.group(1)}"

    # Figshare: deja la página del artículo/dataset, no el fichero.
    m = re.search(r"https?://[^/]*figshare\.com/(?:articles|ndownloader/articles)/(?:dataset|figure|online_resource|journal_contribution)?/?([^/?#]+)?/?(\d+)", url, re.I)
    if m:
        article_id = m.group(2)
        if article_id:
            return f"https://figshare.com/articles/dataset/{article_id}"

    # Mendeley Data.
    m = re.search(r"https?://data\.mendeley\.com/datasets/([^/?#]+)(?:/(\d+))?", url, re.I)
    if m:
        base = f"https://data.mendeley.com/datasets/{m.group(1)}"
        if m.group(2):
            base += f"/{m.group(2)}"
        return base

    # Dryad DOI o landing.
    if "datadryad.org" in url.lower():
        return strip_query_and_fragment(url)

    # Kaggle: quita /data u otros sufijos cuando es posible.
    if is_kaggle_dataset_url(url):
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[0].lower() == "datasets" and len(parts) >= 3:
            return f"https://www.kaggle.com/datasets/{parts[1]}/{parts[2]}"
        if len(parts) >= 2:
            return f"https://www.kaggle.com/{parts[0]}/{parts[1]}"

    # HuggingFace datasets: conserva solo /datasets/owner/name.
    if "huggingface.co/datasets/" in url.lower():
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 3 and parts[0].lower() == "datasets":
            return f"https://huggingface.co/datasets/{parts[1]}/{parts[2]}"

    return url


def parent_url_for_file(url):
    """Devuelve una URL padre razonable para un fichero directo."""
    url = strip_query_and_fragment(url)

    try:
        parsed = urlparse(url)
        path = parsed.path or ""
        if "/" not in path.strip("/"):
            return url

        parent_path = str(Path(path).parent).replace("\\", "/")
        if parent_path == ".":
            parent_path = "/"

        return parsed._replace(path=parent_path, query="", fragment="").geturl().rstrip("/")
    except Exception:
        return url


def normalize_dataset_url_for_ya2ro(url):
    """
    Normaliza una URL antes de meterla en el YAML de ya2ro.

    Objetivo:
    - DOI dataset -> DOI limpio.
    - Zenodo/Figshare/Mendeley/etc. -> página de registro.
    - Evitar pasar ficheros directos pesados (.h5, .zip, .csv...) a ya2ro.
    """
    url = normalize_url(url)

    if not url:
        return ""

    doi = extract_doi_from_text(url)
    if doi:
        return doi_to_url(doi)

    repo_url = normalize_repository_record_url(url)

    # Si tras normalizar a registro ya no es fichero directo, se puede pasar como string.
    if repo_url and not is_direct_dataset_file(repo_url):
        return repo_url

    return repo_url or url


def make_manual_dataset_from_file_url(url, gap_info):
    """
    Para ficheros directos que no tienen landing clara, crea un dataset manual
    para que ya2ro no intente descargar el fichero pesado.
    """
    url = normalize_url(url)
    parsed = urlparse(strip_query_and_fragment(url))
    file_name = Path(parsed.path).name or "dataset file"
    landing = parent_url_for_file(url)

    if valid_gap_dataset_name(gap_info.get("name", "")):
        dataset_name = gap_info["name"]
    else:
        dataset_name = Path(file_name).stem or "Dataset file detected by heuristics"

    description = f"Dataset file detected by the heuristics: {file_name}."
    if gap_info.get("description"):
        description += " Context from GAP-KGE/datastet: " + gap_info["description"]

    item = {
        "name": clean_text(dataset_name),
        "description": clean_text(description),
    }

    # Enlace al padre, no al fichero directo, para reducir bloqueos de ya2ro.
    if landing and landing != url:
        item["link"] = landing

    return item

def build_ya2ro_datasets(dataset_urls, gap_info):
    """
    Construye la sección datasets del YAML evitando URLs que bloquean ya2ro.

    Mejora principal:
    - No se envían a ya2ro ficheros directos grandes de Zenodo u otros repositorios.
    - Se prefieren DOI/landing pages de repositorio.
    - Los ficheros directos sin landing clara se describen como dataset manual.
    """
    datasets = []
    github_file_groups = {}

    for raw_url in dataset_urls:
        raw_url = normalize_url(raw_url)
        if not raw_url:
            continue

        url = normalize_dataset_url_for_ya2ro(raw_url)
        if not url:
            continue

        doi = extract_doi_from_text(url)

        # DOI dataset: es lo más estable para ya2ro.
        if doi:
            datasets.append(doi_to_url(doi))
            continue

        # GitHub con fichero directo: agrupar por repositorio.
        if is_github_url(raw_url) and is_direct_dataset_file(raw_url):
            repo_url, file_name = github_blob_to_repo_and_file(raw_url)

            if repo_url:
                github_file_groups.setdefault(repo_url, [])

                if file_name:
                    github_file_groups[repo_url].append(file_name)

            continue

        # Si sigue siendo un fichero directo, no se lo damos a ya2ro como string.
        # Lo convertimos en entrada manual para evitar descargas pesadas.
        if is_direct_dataset_file(url):
            datasets.append(make_manual_dataset_from_file_url(url, gap_info))
            continue

        # Repositorio o landing page normal.
        datasets.append(url)

    for repo_url, files in github_file_groups.items():
        files = unique_list(files)
        repo_name = Path(urlparse(repo_url).path).name

        dataset_name = repo_name

        if valid_gap_dataset_name(gap_info.get("name", "")):
            dataset_name = gap_info["name"]

        description = "Dataset files detected by the heuristics in the GitHub repository."

        if files:
            description += " Files: " + ", ".join(files) + "."

        if gap_info.get("description"):
            description += " Context from GAP-KGE/datastet: " + gap_info["description"]

        datasets.append({
            "name": clean_text(dataset_name),
            "description": clean_text(description),
            "link": repo_url,
        })

    datasets = unique_list(datasets)

    if datasets:
        return datasets

    manual_dataset = {
        "name": gap_info.get("name") if valid_gap_dataset_name(gap_info.get("name", "")) else "Dataset detected by GAP-KGE/datastet",
        "description": gap_info.get("description") or "Dataset mention detected in the paper, but no dataset URL was confirmed.",
    }

    if gap_info.get("role"):
        manual_dataset["relation_to_paper"] = gap_info["role"]

    if gap_info.get("data_types"):
        manual_dataset["data_types"] = gap_info["data_types"]

    return [manual_dataset]



def choose_title(pdf_meta, gap_info, pdf_path):
    title = clean_text(pdf_meta.get("title", ""))

    if is_good_title(title):
        return title

    dataset_name = clean_text(gap_info.get("name", ""))

    if valid_gap_dataset_name(dataset_name) and is_good_title(dataset_name):
        return dataset_name

    return Path(pdf_path).stem


def choose_summary(pdf_meta, gap_info, dataset_urls):
    summary = clean_text(pdf_meta.get("summary", ""))

    if len(summary) > 40:
        return summary

    gap_desc = clean_text(gap_info.get("description", ""))

    if len(gap_desc) > 20:
        return gap_desc

    if dataset_urls:
        return f"Research Object generated from a paper with datasets available at: {', '.join(dataset_urls)}"

    return "Research Object generated from the paper and its detected dataset mentions."


def build_yaml(dataset_json_path, pdf_path, h1_df, h2_df, audit_rows):
    print(f"[INFO] Leyendo .dataset.json: {dataset_json_path}")

    data = load_json(dataset_json_path)

    gap_info = extract_main_dataset_info(data)
    pdf_meta = extract_pdf_metadata(pdf_path)
    pdf_text = extract_pdf_text(pdf_path, max_pages=8)

    all_urls = []

    all_urls.extend(gap_info.get("urls", []))
    all_urls.extend(pdf_meta.get("urls", []))
    all_urls.extend(extract_urls_from_text(pdf_text))

    # No meter todos los DOI del PDF, solo DOI dataset.
    for doi in pdf_meta.get("dois", []):
        doi_url_value = doi_to_url(doi)

        if is_probably_dataset_doi(doi_url_value):
            all_urls.append(doi_url_value)

    all_urls = unique_list([normalize_url(u) for u in all_urls if u])

    pdf_name = Path(pdf_path).name

    print(f"[INFO] Título elegido: {pdf_meta.get('title')}")
    print(f"[INFO] Dataset principal GAP-KGE/datastet: {gap_info.get('name')}")
    print(f"[INFO] URLs detectadas en este paper: {len(all_urls)}")

    dataset_urls = heuristic_positive_urls(
        h1_df=h1_df,
        h2_df=h2_df,
        urls_in_this_paper=all_urls,
        pdf_name=pdf_name,
        audit_rows=audit_rows,
    )

    # Software desactivado:
    # No se añaden repositorios GitHub en el campo "software" porque ya2ro/SOCA
    # puede fallar al generar la tarjeta HTML del software si no obtiene una fecha
    # de última actualización con tipo Date válido.

    title = choose_title(pdf_meta, gap_info, pdf_path)
    summary = choose_summary(pdf_meta, gap_info, dataset_urls)

    ro = {
        "type": "paper",
        "title": title,
        "summary": summary,
        "datasets": build_ya2ro_datasets(dataset_urls, gap_info),
    }

    # Seguridad extra: aunque en el futuro se añada accidentalmente,
    # eliminamos siempre la clave "software" antes de guardar el YAML.
    ro.pop("software", None)

    # DOI principal del paper: mejor ponerlo directamente en bibliography,
    # porque ya2ro puede resolver DOI de publicaciones. Evitamos textos tipo
    # "Paper DOI: ..." porque ya2ro lo interpreta peor que un DOI/URL limpio.
    paper_doi = choose_paper_doi(pdf_meta, title)
    if paper_doi and not pdf_meta.get("paper_doi"):
        pdf_meta["paper_doi"] = paper_doi

    authors_trusted = pdf_meta.get("authors_source", "") == "grobid"
    raw_authors = clean_detected_authors(
        pdf_meta.get("authors", []),
        trusted=authors_trusted,
    )

    authors = enrich_authors_with_orcid(
        authors=raw_authors,
        paper_title=title,
        paper_doi=paper_doi,
    )

    if authors:
        ro["authors"] = authors

    bibliography = []

    if paper_doi:
        bibliography.append(doi_to_url(paper_doi))

    # Dejamos trazabilidad como cita textual, pero detrás del DOI real.
    bibliography.extend([
        f"Source PDF: {Path(pdf_path).name}",
        f"Dataset extraction file: {Path(dataset_json_path).name}",
    ])

    ro["bibliography"] = unique_list(bibliography)

    return ro


# ============================================================
# GENERACIÓN PRINCIPAL
# ============================================================

def generate(base_dir=".", pdfs_dir="pdfs", output_dir="ya2ro_generated"):
    base = Path(base_dir).resolve()
    pdfs = (base / pdfs_dir).resolve()
    out = (base / output_dir).resolve()
    yamls_dir = out / "yamls"
    audit_path = out / "audit_candidates.csv"

    print("\n[INFO] Iniciando generación de YAMLs para ya2ro")
    print(f"[INFO] Carpeta base: {base}")
    print(f"[INFO] Carpeta PDFs/.dataset.json: {pdfs}")
    print(f"[INFO] Carpeta salida: {out}")
    print(f"[INFO] Carpeta YAMLs: {yamls_dir}")

    if not pdfs.exists():
        print(f"[ERROR] No existe carpeta pdfs: {pdfs}")
        return []

    out.mkdir(parents=True, exist_ok=True)

    if CLEAR_OLD_YAMLS and yamls_dir.exists():
        for old_yaml in yamls_dir.glob("*.yaml"):
            try:
                old_yaml.unlink()
            except Exception:
                pass

    yamls_dir.mkdir(parents=True, exist_ok=True)

    h1_df, h2_df = load_heuristics(base)

    dataset_json_files = sorted(pdfs.glob("*.dataset.json"))

    print(f"[INFO] .dataset.json encontrados: {len(dataset_json_files)}")

    if not dataset_json_files:
        return []

    created = []
    audit_rows = []

    for dataset_json_path in dataset_json_files:
        stem = dataset_json_path.name.replace(".dataset.json", "")
        pdf_path = pdfs / f"{stem}.pdf"

        print("\n----------------------------------------")
        print(f"[INFO] Procesando paper: {stem}")
        print(f"[INFO] .dataset.json: {dataset_json_path}")
        print(f"[INFO] PDF esperado: {pdf_path}")

        if not pdf_path.exists():
            print(f"[WARN] No existe PDF asociado: {pdf_path}")

        try:
            ro = build_yaml(
                dataset_json_path=dataset_json_path,
                pdf_path=pdf_path,
                h1_df=h1_df,
                h2_df=h2_df,
                audit_rows=audit_rows,
            )

            out_path = yamls_dir / f"{stem}.yaml"

            with open(out_path, "w", encoding="utf-8", newline="\n") as f:
                yaml.safe_dump(
                    ro,
                    f,
                    allow_unicode=False,
                    sort_keys=False,
                    default_flow_style=False,
                    width=120,
                )

            created.append(out_path)

            print("[OK] YAML generado")
            print(f"[OK] Guardado en: {out_path}")

        except Exception as e:
            print(f"[ERROR] Fallo procesando {stem}")
            print(f"[ERROR] Motivo: {e}")

    if audit_rows:
        try:
            pd.DataFrame(audit_rows).to_csv(audit_path, index=False, encoding="utf-8-sig")
            print(f"[OK] Auditoría guardada en: {audit_path}")
        except Exception as e:
            print(f"[WARN] No se pudo guardar auditoría: {e}")

    print("\n========================================")
    print("[RESUMEN FINAL]")
    print(f"[OK] YAML creados: {len(created)}")
    print(f"[OK] Carpeta YAMLs: {yamls_dir}")

    for path in created:
        print(f"  - {path}")

    print("\n[COMANDO YA2RO NORMAL]")
    print(f'ya2ro -i "{yamls_dir}" -o "{out / "ro_output"}"')

    print("\n[COMANDO YA2RO ESTABLE SIN SOMEF]")
    print(f'ya2ro -i "{yamls_dir}" -o "{out / "ro_output"}" -ns')

    print("========================================\n")

    return created


if __name__ == "__main__":
    print("[DEBUG] Entrando en main")

    generate(
        base_dir=".",
        pdfs_dir="pdfs",
        output_dir="ya2ro_generated",
    )