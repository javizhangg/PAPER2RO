# heuristics.py
# Heurísticas para identificar URLs candidatas a dataset
# H1 -> extensiones de datos
# H2 -> metadatos HTTP / content negotiation
# H3 -> señales reutilizables tipo GAP-KGE
# H4 -> señales basadas en la propia URL

import csv
import json
import re
from pathlib import Path
from urllib.parse import urlparse
import requests

INPUT_CSV = "outputs/all_links_normalized.csv"
OUTPUT_CSV = "outputs/heuristics_results.csv"
OUTPUT_JSON = "outputs/heuristics_results.json"

# ==================================
# H1: extensiones de ficheros de datos
# ==================================
DATA_EXTENSIONS = {
    ".csv", ".tsv", ".json", ".xml", ".rdf",
    ".xlsx", ".xls", ".parquet", ".h5", ".hdf5"
}

# Por ahora descartamos ZIP
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
    "application/octet-stream"
}


# ==================================
# Utilidades
# ==================================
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
        if match:
            return match.group(1)
    except Exception:
        pass
    return ""


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


def is_doi_url(url: str) -> bool:
    """
    Solo información auxiliar.
    No es una heurística independiente.
    """
    return get_domain(url) == "doi.org"


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
    """
    Heurística 2:
    Extrae metadatos de la URL mediante HTTP
    (cabeceras, redirecciones, content negotiation)
    para estimar si apunta a un dataset.
    """
    try:
        response = requests.head(
            url,
            allow_redirects=True,
            timeout=timeout
        )

        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        content_length = response.headers.get("Content-Length", "")
        disposition = response.headers.get("Content-Disposition", "").lower()

        # Si HEAD falla o no da suficiente info, probar con GET
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

        # MIME type compatible con datos
        if content_type in DATA_CONTENT_TYPES:
            signals.append("data_content_type")
            score += 3

        # Extensión final compatible con datos
        if final_ext in DATA_EXTENSIONS:
            signals.append("final_data_extension")
            score += 2

        # Descarga directa
        if "attachment" in disposition:
            signals.append("attachment_download")
            score += 1

        # Dominio típico de dataset
        matched_domain = ""
        for known in KNOWN_DATASET_DOMAINS:
            if final_domain == known or final_domain.endswith("." + known):
                matched_domain = known
                signals.append("known_dataset_domain")
                score += 1
                break

        # Recurso real con tamaño declarado
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
    """
    Señales reutilizables inspiradas en una pipeline de extracción:
    - dominio conocido de datasets
    - patrones léxicos en URL
    - penalización por señales de software/paper
    """
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
# Aplicación global
# ==================================
def apply_heuristics(url: str, use_http: bool = False) -> dict:
    results = {
        "url": url,
        "heuristics": []
    }

    h1 = heuristic_extension(url)
    h3 = heuristic_gap_kge_style(url)
    h4 = heuristic_url_pattern(url)

    results["heuristics"].extend([h1, h3, h4])

    if use_http:
        h2 = heuristic_http_metadata(url)
        results["heuristics"].append(h2)

    total_score = sum(h["score"] for h in results["heuristics"])
    results["total_score"] = total_score

    if total_score >= 5:
        label = "dataset"
    elif total_score >= 2:
        label = "maybe_dataset"
    else:
        label = "not_dataset"

    results["label"] = label
    return results


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
def process_rows(rows, use_http=False):
    results_csv = []
    results_json = []

    for row in rows:
        url = row["normalized_url"]
        result = apply_heuristics(url, use_http=use_http)

        heuristic_map = {h["heuristic"]: h for h in result["heuristics"]}

        row_result = {
            "paper": row["paper"],
            "section": row["section"],
            "original_url": row["original_url"],
            "normalized_url": row["normalized_url"],
            "domain": row["domain"],
            "extension": row["extension"],

            "extension_matched": heuristic_map["extension"]["matched"],
            "extension_score": heuristic_map["extension"]["score"],

            "gap_kge_style_matched": heuristic_map["gap_kge_style"]["matched"],
            "gap_kge_style_score": heuristic_map["gap_kge_style"]["score"],

            "url_pattern_matched": heuristic_map["url_pattern"]["matched"],
            "url_pattern_score": heuristic_map["url_pattern"]["score"],

            "total_score": result["total_score"],
            "label": result["label"]
        }

        if use_http and "http_metadata" in heuristic_map:
            http_value = heuristic_map["http_metadata"]["value"]
            row_result["http_matched"] = heuristic_map["http_metadata"]["matched"]
            row_result["http_score"] = heuristic_map["http_metadata"]["score"]
            row_result["http_status_code"] = http_value.get("status_code", "")
            row_result["http_content_type"] = http_value.get("content_type", "")
            row_result["http_content_length"] = http_value.get("content_length", "")
            row_result["http_content_disposition"] = http_value.get("content_disposition", "")
            row_result["http_final_url"] = http_value.get("final_url", "")
            row_result["http_final_domain"] = http_value.get("final_domain", "")
            row_result["http_final_extension"] = http_value.get("final_extension", "")
            row_result["http_matched_domain"] = http_value.get("matched_domain", "")
            row_result["http_is_doi_input"] = http_value.get("is_doi_input", "")
            row_result["http_signals"] = "|".join(http_value.get("signals", []))
        else:
            row_result["http_matched"] = ""
            row_result["http_score"] = ""
            row_result["http_status_code"] = ""
            row_result["http_content_type"] = ""
            row_result["http_content_length"] = ""
            row_result["http_content_disposition"] = ""
            row_result["http_final_url"] = ""
            row_result["http_final_domain"] = ""
            row_result["http_final_extension"] = ""
            row_result["http_matched_domain"] = ""
            row_result["http_is_doi_input"] = ""
            row_result["http_signals"] = ""

        results_csv.append(row_result)

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

    # Pon use_http=True cuando quieras activar la heurística 2
    results_csv, results_json = process_rows(rows, use_http=False)

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