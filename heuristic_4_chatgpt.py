import csv
import json
import os
import re
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

from openai import OpenAI


# ==============================
# CONFIGURACIÓN
# ==============================

INPUT_CSV = "outputs/all_links_normalized.csv"

OUTPUT_CSV = "outputs/heuristic_4_chatgpt_results.csv"
OUTPUT_JSON = "outputs/heuristic_4_chatgpt_results.json"

# Modelo por defecto. Si este te falla, prueba:
# set OPENAI_MODEL=gpt-4.1-mini
# o en PowerShell:
# $env:OPENAI_MODEL="gpt-4.1-mini"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

# Si quieres usar ChatGPT para TODAS las URLs, pon True.
# Si lo dejas en False, primero usa reglas rápidas y solo pregunta a ChatGPT en casos dudosos.
USE_CHATGPT_FOR_ALL = False

# Pausa pequeña entre llamadas para evitar saturar la API
SLEEP_BETWEEN_REQUESTS = 0.2

# Reintentos si la API falla temporalmente
MAX_RETRIES = 2


# ==============================
# EXTENSIONES Y DOMINIOS
# ==============================

STRONG_DATA_EXTENSIONS = {
    ".csv", ".tsv",
    ".xlsx", ".xls",
    ".parquet",
    ".h5", ".hdf5",
    ".arff",
    ".db", ".sqlite", ".sqlite3",
    ".sav", ".dta", ".feather"
}

AMBIGUOUS_DATA_EXTENSIONS = {
    ".json", ".xml", ".rdf",
    ".pkl", ".pickle",
    ".npy", ".npz",
    ".mat", ".dat", ".data",
    ".txt"
}

COMPRESSED_EXTENSIONS = {
    ".zip", ".gz", ".tar", ".tgz", ".7z", ".rar", ".bz2", ".xz"
}

NON_DATA_EXTENSIONS = {
    ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js",
    ".bib", ".ris",
    ".doc", ".docx",
    ".ppt", ".pptx",
    ".py", ".java", ".c", ".cpp", ".h", ".hpp",
    ".md", ".rst",
    ".ipynb",
    ".html", ".htm"
}

TRUSTED_DATASET_DOMAINS = {
    "zenodo.org",
    "figshare.com",
    "datadryad.org",
    "dryad.org",
    "dataverse.harvard.edu",
    "kaggle.com",
    "archive.ics.uci.edu",
    "openml.org",
    "physionet.org",
    "huggingface.co",
    "data.gov",
    "data.europa.eu",
    "data.world",
    "osf.io",
    "mendeley.com",
    "data.mendeley.com",
    "registry.opendata.aws"
}

PAPER_DOMAINS = {
    "doi.org",
    "dx.doi.org",
    "arxiv.org",
    "semanticscholar.org",
    "acm.org",
    "doi.acm.org",
    "ieee.org",
    "ieeexplore.ieee.org",
    "springer.com",
    "link.springer.com",
    "sciencedirect.com",
    "nature.com",
    "frontiersin.org"
}

DATASET_KEYWORDS = {
    "dataset", "datasets",
    "data",
    "corpus", "corpora",
    "benchmark", "benchmarks",
    "database",
    "dataverse",
    "annotations", "annotation",
    "labels", "label",
    "training", "train",
    "testing", "test",
    "validation", "valid", "dev",
    "download", "downloads",
    "supplementary-data", "supplemental-data",
    "samples", "records",
    "images", "masks",
    "features",
    "csv", "json", "xlsx", "tsv"
}

NEGATIVE_KEYWORDS = {
    "paper", "article", "citation", "bibtex",
    "docs", "documentation", "wiki",
    "login", "signin", "signup",
    "contact", "about",
    "privacy", "terms",
    "favicon", "static", "assets",
    "readme", "license",
    "software", "code",
    "github"
}


# ==============================
# UTILIDADES URL
# ==============================

def normalize_url(url: str) -> str:
    if not url:
        return ""

    url = str(url).strip()
    url = re.sub(r"#.*$", "", url)
    url = url.rstrip(".,;:!?)]}>'\"")
    return url


def get_domain(url: str) -> str:
    try:
        domain = urlparse(url).netloc.lower().strip()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def get_path(url: str) -> str:
    try:
        return urlparse(url).path.lower()
    except Exception:
        return ""


def get_filename(url: str) -> str:
    try:
        return Path(urlparse(url).path).name.lower()
    except Exception:
        return ""


def get_extension(url: str) -> str:
    try:
        return Path(get_filename(url)).suffix.lower()
    except Exception:
        return ""


def get_all_extensions(url: str) -> list:
    try:
        return [s.lower() for s in Path(get_filename(url)).suffixes]
    except Exception:
        return []


def tokenize_url(url: str) -> set:
    try:
        parsed = urlparse(url)
        text = f"{parsed.netloc} {parsed.path} {parsed.query}".lower()
        return {t for t in re.split(r"[/\\\-_.?=&:#\s]+", text) if t}
    except Exception:
        return set()


def safe_json_dumps(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def contains_any(text: str, words: set) -> bool:
    text = text.lower()
    return any(w.lower() in text for w in words)


# ==============================
# REGLAS RÁPIDAS
# ==============================

def quick_rule_classify(url: str) -> dict:
    """
    Clasificación rápida antes de usar ChatGPT.

    Devuelve:
    - dataset
    - not_dataset
    - uncertain
    """

    url = normalize_url(url)
    domain = get_domain(url)
    path = get_path(url)
    ext = get_extension(url)
    extensions = get_all_extensions(url)
    tokens = tokenize_url(url)

    has_dataset_keyword = bool(tokens.intersection(DATASET_KEYWORDS))
    has_negative_keyword = bool(tokens.intersection(NEGATIVE_KEYWORDS))

    # 1. Archivos de datos muy claros
    if ext in STRONG_DATA_EXTENSIONS:
        return {
            "label": "dataset",
            "reason": "strong_data_extension"
        }

    # 2. Comprimidos: muchos datasets se descargan como ZIP/TAR/GZ
    if any(e in COMPRESSED_EXTENSIONS for e in extensions):
        if has_dataset_keyword:
            return {
                "label": "dataset",
                "reason": "compressed_file_with_dataset_keyword"
            }
        else:
            return {
                "label": "uncertain",
                "reason": "compressed_file_without_clear_context"
            }

    # 3. Repositorios conocidos de datasets
    if domain in TRUSTED_DATASET_DOMAINS:
        return {
            "label": "dataset",
            "reason": "trusted_dataset_domain"
        }

    # 4. Kaggle datasets
    if domain == "kaggle.com" and "/datasets" in path:
        return {
            "label": "dataset",
            "reason": "kaggle_dataset_path"
        }

    # 5. Hugging Face datasets
    if domain == "huggingface.co" and "/datasets/" in path:
        return {
            "label": "dataset",
            "reason": "huggingface_dataset_path"
        }

    # 6. UCI Machine Learning Repository
    if domain == "archive.ics.uci.edu" and ("dataset" in path or "ml" in path):
        return {
            "label": "dataset",
            "reason": "uci_dataset_repository"
        }

    # 7. URLs técnicas claramente no dataset
    technical_paths = [
        "/static/", "/assets/", "/css/", "/js/",
        "/favicon", "/icons/", "/_next/",
        "/build/", "/dist/", "/webpack/"
    ]

    if any(p in path for p in technical_paths):
        return {
            "label": "not_dataset",
            "reason": "technical_asset_url"
        }

    # 8. Papers claros: no los marcamos como dataset salvo que haya señal fuerte
    if domain in PAPER_DOMAINS and not has_dataset_keyword:
        return {
            "label": "not_dataset",
            "reason": "paper_domain_without_dataset_keyword"
        }

    # 9. Extensiones claramente no dataset
    # Importante: HTML lo dejamos como dudoso si tiene palabras de dataset.
    if ext in NON_DATA_EXTENSIONS and not has_dataset_keyword:
        return {
            "label": "not_dataset",
            "reason": "non_data_extension_without_dataset_keyword"
        }

    # 10. JSON/XML/TXT pueden ser dataset, pero también pueden ser config/readme.
    if ext in AMBIGUOUS_DATA_EXTENSIONS:
        if has_dataset_keyword:
            return {
                "label": "dataset",
                "reason": "ambiguous_data_extension_with_dataset_keyword"
            }
        else:
            return {
                "label": "uncertain",
                "reason": "ambiguous_data_extension_without_context"
            }

    # 11. Si la URL contiene palabras de dataset, preguntamos a ChatGPT
    if has_dataset_keyword:
        return {
            "label": "uncertain",
            "reason": "dataset_keyword_needs_chatgpt_confirmation"
        }

    # 12. GitHub/GitLab/Bitbucket son dudosos: puede ser código o datos
    if domain in {"github.com", "gitlab.com", "bitbucket.org", "raw.githubusercontent.com"}:
        return {
            "label": "uncertain",
            "reason": "code_repository_needs_chatgpt"
        }

    # 13. Resto: dudoso
    return {
        "label": "uncertain",
        "reason": "needs_chatgpt"
    }


# ==============================
# NORMALIZAR RESPUESTA CHATGPT
# ==============================

def normalize_answer(answer: str) -> str:
    """
    Convierte respuesta de ChatGPT a:
    - dataset
    - not_dataset
    - error
    """

    if not answer:
        return "error"

    clean = answer.strip().lower()
    clean = clean.replace("sí", "si")
    clean = clean.replace(".", "")
    clean = clean.replace(",", "")
    clean = clean.replace(":", "")
    clean = clean.replace("\n", " ")

    # Respuestas positivas
    if clean.startswith(("si", "yes", "dataset")):
        return "dataset"

    # Respuestas negativas
    if clean.startswith(("no", "not dataset", "not_dataset", "no dataset")):
        return "not_dataset"

    # Formato JSON posible
    try:
        data = json.loads(answer)
        value = str(data.get("label", "")).lower().strip()
        if value in {"dataset", "yes", "si", "sí"}:
            return "dataset"
        if value in {"not_dataset", "no", "not dataset"}:
            return "not_dataset"
    except Exception:
        pass

    # Frases positivas
    positive_patterns = [
        "es un dataset",
        "corresponde a un dataset",
        "si es un dataset",
        "sí es un dataset",
        "is a dataset",
        "dataset page",
        "contains downloadable data",
        "contains datasets"
    ]

    # Frases negativas
    negative_patterns = [
        "no es un dataset",
        "no corresponde a un dataset",
        "not a dataset",
        "is not a dataset",
        "does not contain a dataset",
        "solo es un paper",
        "only a paper"
    ]

    for pattern in negative_patterns:
        if pattern in clean:
            return "not_dataset"

    for pattern in positive_patterns:
        if pattern in clean:
            return "dataset"

    return "error"


# ==============================
# CSV / JSON
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
                "normalized_url": normalize_url(
                    row.get("normalized_url", "").strip()
                    or row.get("url", "").strip()
                    or row.get("original_url", "").strip()
                ),
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
        alt = str(p.with_name(f"{p.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{p.suffix}"))

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
        alt = str(p.with_name(f"{p.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{p.suffix}"))

        with open(alt, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)

        return alt


# ==============================
# CHATGPT + WEB SEARCH
# ==============================

def ask_chatgpt_if_dataset(url: str) -> dict:
    """
    Pregunta a ChatGPT si la URL es dataset.
    Si hay error, devuelve label='error', NO devuelve not_dataset.
    """

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return {
            "ok": False,
            "answer": "",
            "label": "error",
            "error": "missing_openai_api_key"
        }

    prompt = f"""
Tienes que clasificar una URL como dataset o not_dataset.

URL:
{url}

Marca "dataset" si cumple AL MENOS una condición:
- Es una página de dataset.
- Es una página donde se puede descargar un dataset.
- Es una página que contiene muchos datasets.
- Es un repositorio de datasets.
- Es un archivo descargable de datos, como CSV, TSV, XLSX, JSON, ZIP, TAR, PARQUET, HDF5, ARFF, etc.
- Es un corpus, benchmark, base de datos, conjunto de anotaciones, imágenes anotadas, máscaras, labels o datos experimentales.
- Es una página tipo Zenodo, Figshare, Dryad, Dataverse, Kaggle, OpenML, Hugging Face Datasets, UCI, OSF, PhysioNet o similar.

Marca "not_dataset" si:
- Es solo un paper.
- Es solo un artículo.
- Es solo documentación.
- Es solo código/software sin datos.
- Es una página de login, contacto, about, privacy o terms.
- Es un recurso técnico como CSS, JS, favicon, imagen decorativa, etc.

IMPORTANTE:
- Responde SOLO con una de estas dos etiquetas exactas:
dataset
not_dataset
""".strip()

    last_error = ""

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            client = OpenAI(api_key=api_key)

            response = client.responses.create(
                model=OPENAI_MODEL,
                tools=[
                    {
                        "type": "web_search"
                    }
                ],
                input=prompt
            )

            answer = response.output_text.strip()
            label = normalize_answer(answer)

            return {
                "ok": True,
                "answer": answer,
                "label": label,
                "error": ""
            }

        except Exception as e:
            last_error = str(e)

            if attempt <= MAX_RETRIES:
                time.sleep(1.5 * attempt)
            else:
                break

    return {
        "ok": False,
        "answer": "",
        "label": "error",
        "error": last_error
    }


# ==============================
# CLASIFICADOR FINAL
# ==============================

def classify_url(url: str) -> dict:
    """
    Clasificación final combinando:
    1. reglas rápidas
    2. ChatGPT con búsqueda web
    """

    url = normalize_url(url)

    quick = quick_rule_classify(url)

    # Si queremos que ChatGPT revise absolutamente todo
    if USE_CHATGPT_FOR_ALL:
        chatgpt_result = ask_chatgpt_if_dataset(url)

        return {
            "final_label": chatgpt_result["label"],
            "method": "chatgpt_for_all",
            "quick_label": quick["label"],
            "quick_reason": quick["reason"],
            "chatgpt_ok": chatgpt_result["ok"],
            "chatgpt_answer": chatgpt_result["answer"],
            "chatgpt_label": chatgpt_result["label"],
            "chatgpt_error": chatgpt_result["error"]
        }

    # Si la regla local está bastante segura, no llamamos a ChatGPT
    if quick["label"] in {"dataset", "not_dataset"}:
        return {
            "final_label": quick["label"],
            "method": "quick_rule",
            "quick_label": quick["label"],
            "quick_reason": quick["reason"],
            "chatgpt_ok": "",
            "chatgpt_answer": "",
            "chatgpt_label": "",
            "chatgpt_error": ""
        }

    # Casos dudosos: ChatGPT
    chatgpt_result = ask_chatgpt_if_dataset(url)

    return {
        "final_label": chatgpt_result["label"],
        "method": "chatgpt",
        "quick_label": quick["label"],
        "quick_reason": quick["reason"],
        "chatgpt_ok": chatgpt_result["ok"],
        "chatgpt_answer": chatgpt_result["answer"],
        "chatgpt_label": chatgpt_result["label"],
        "chatgpt_error": chatgpt_result["error"]
    }


# ==============================
# MAIN
# ==============================

def main():
    if not Path(INPUT_CSV).exists():
        print(f"No existe {INPUT_CSV}")
        return

    rows = load_normalized_csv(INPUT_CSV)

    csv_rows = []
    json_rows = []

    for i, row in enumerate(rows, start=1):
        url = row["normalized_url"]

        if not url:
            continue

        print(f"[{i}/{len(rows)}] Analizando: {url}")

        result = classify_url(url)

        csv_row = {
            "paper": row.get("paper", ""),
            "section": row.get("section", ""),
            "original_url": row.get("original_url", ""),
            "normalized_url": url,
            "domain": get_domain(url) or row.get("domain", ""),
            "extension": get_extension(url) or row.get("extension", ""),

            "method": result["method"],

            "quick_label": result["quick_label"],
            "quick_reason": result["quick_reason"],

            "chatgpt_ok": result["chatgpt_ok"],
            "chatgpt_answer": result["chatgpt_answer"],
            "chatgpt_label": result["chatgpt_label"],
            "chatgpt_error": result["chatgpt_error"],

            "label": result["final_label"]
        }

        csv_rows.append(csv_row)

        json_rows.append({
            "row": row,
            "classification_result": result
        })

        if result["method"] in {"chatgpt", "chatgpt_for_all"}:
            time.sleep(SLEEP_BETWEEN_REQUESTS)

    saved_csv = save_csv(csv_rows, OUTPUT_CSV)
    saved_json = save_json(json_rows, OUTPUT_JSON)

    dataset_count = sum(1 for r in csv_rows if r["label"] == "dataset")
    not_dataset_count = sum(1 for r in csv_rows if r["label"] == "not_dataset")
    error_count = sum(1 for r in csv_rows if r["label"] == "error")

    print("\nResultados:")
    print(f"Leídas: {len(rows)}")
    print(f"dataset: {dataset_count}")
    print(f"not_dataset: {not_dataset_count}")
    print(f"error: {error_count}")
    print(f"CSV: {saved_csv}")
    print(f"JSON: {saved_json}")

    if error_count > 0:
        print("\nHay errores. Revisa la columna chatgpt_error del CSV.")

    if not os.getenv("OPENAI_API_KEY"):
        print("\nAviso: falta OPENAI_API_KEY.")


if __name__ == "__main__":
    main()