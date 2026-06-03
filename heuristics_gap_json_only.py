# heuristics_gap_json_smart_v2.py
# Versión más permisiva e inteligente para usar los .dataset.json de GAP-KGE / DataStet
#
# Entrada:
#   outputs/all_links_normalized.csv
#
# Salida:
#   outputs/heuristics_gap_json_smart_v2_results.csv
#   outputs/heuristics_gap_json_smart_v2_results.json

import csv
import json
import re
from pathlib import Path
from urllib.parse import urlparse, unquote
from functools import lru_cache


# ==============================
# RUTAS
# ==============================

INPUT_CSV = "outputs/all_links_normalized.csv"

OUTPUT_CSV = "outputs/heuristics_gap_json_smart_v2_results.csv"
OUTPUT_JSON = "outputs/heuristics_gap_json_smart_v2_results.json"

# Carpetas donde buscar .dataset.json
JSON_SEARCH_DIRS = [
    "pdfs",
    "outputs",
    "."
]


# ==============================
# CONFIGURACIÓN
# ==============================

# Más bajo que antes para mejorar recall
DATASET_THRESHOLD = 4

DATA_EXTENSIONS = {
    ".csv", ".tsv", ".json", ".xml", ".rdf",
    ".xlsx", ".xls", ".parquet", ".h5", ".hdf5",
    ".zip", ".gz", ".tar", ".tgz", ".7z",
    ".npy", ".npz", ".pkl", ".pickle",
    ".sqlite", ".sqlite3", ".db"
}

POSITIVE_DATA_DOMAINS = {
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
    "huggingface.co",
    "paperswithcode.com",
    "tensorflow.org",
    "pytorch.org"
}

NEGATIVE_DOMAINS = {
    "arxiv.org",
    "doi.org",
    "dx.doi.org",
    "openreview.net",
    "aclanthology.org",
    "ieeexplore.ieee.org",
    "link.springer.com",
    "sciencedirect.com",
    "nature.com"
}

URL_POSITIVE_KEYWORDS = {
    "dataset", "datasets", "data", "download", "downloads",
    "corpus", "benchmark", "benchmarks", "challenge",
    "repository", "archive", "collection", "train", "training",
    "test", "testing", "dev", "validation", "split",
    "annotations", "labels", "samples", "resources",
    "database", "db", "records"
}

URL_NEGATIVE_KEYWORDS = {
    "paper", "pdf", "docs", "documentation", "wiki",
    "blog", "slides", "tutorial", "readme",
    "code", "software", "implementation", "publication"
}

CONTEXT_AVAILABLE_KEYWORDS = {
    "available",
    "freely available",
    "publicly available",
    "available at",
    "available from",
    "can be accessed",
    "accessed",
    "download",
    "downloaded",
    "downloadable",
    "released",
    "provided",
    "shared",
    "obtained from",
    "repository",
    "hosted",
    "accessible"
}

DATASET_GENERIC_WORDS = {
    "dataset", "datasets", "data", "database", "corpus",
    "benchmark", "benchmarks", "set", "training", "train",
    "test", "testing", "validation", "dev", "challenge"
}

URL_REGEX = re.compile(
    r'https?://[^\s"\'<>]+',
    re.IGNORECASE
)

DOI_REGEX = re.compile(
    r'(?:https?://(?:dx\.)?doi\.org/|doi:)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)',
    re.IGNORECASE
)


# ==============================
# UTILIDADES URL / TEXTO
# ==============================

def clean_text(text: str) -> str:
    if text is None:
        return ""

    text = str(text)
    text = text.replace("\n", " ")
    text = text.replace("\\n", " ")
    text = text.replace("\u00ad", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_url_loose(url: str) -> str:
    if not url:
        return ""

    url = str(url).strip()
    url = url.replace("\n", "")
    url = url.replace("\\n", "")
    url = url.replace("&amp;", "&")
    url = unquote(url)
    url = re.sub(r"#.*$", "", url)
    url = url.rstrip(".,;:!?)]}>\"'")
    url = url.rstrip("/")
    return url.lower()


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
        m = re.search(r"(\.[a-z0-9]+)$", path)
        if m:
            return m.group(1)
    except Exception:
        pass

    return ""


def normalize_doi(text: str) -> str:
    if not text:
        return ""

    match = DOI_REGEX.search(text)

    if not match:
        return ""

    doi = match.group(1).strip().rstrip(".,;:!?)]}>\"'")
    return f"https://doi.org/{doi.lower()}"


def tokenize_text(text: str) -> set:
    text = clean_text(text).lower()
    text = text.replace("_", " ")
    text = text.replace("-", " ")

    tokens = re.split(r"[^a-z0-9]+", text)

    result = set()
    for token in tokens:
        if not token:
            continue
        if len(token) < 3:
            continue
        if token in DATASET_GENERIC_WORDS:
            continue
        result.add(token)

    return result


def tokenize_url(url: str) -> set:
    norm = normalize_url_loose(url)

    try:
        parsed = urlparse(norm)
        raw = f"{parsed.netloc} {parsed.path} {parsed.query}"
    except Exception:
        raw = norm

    raw = raw.lower()
    raw = raw.replace("_", " ")
    raw = raw.replace("-", " ")

    tokens = re.split(r"[^a-z0-9]+", raw)

    return {t for t in tokens if t and len(t) >= 2}


def domain_matches(d1: str, d2: str) -> bool:
    if not d1 or not d2:
        return False

    if d1 == d2:
        return True

    if d1.endswith("." + d2):
        return True

    if d2.endswith("." + d1):
        return True

    return False


def is_positive_data_domain(domain: str) -> bool:
    for known in POSITIVE_DATA_DOMAINS:
        if domain == known or domain.endswith("." + known):
            return True
    return False


def is_negative_domain(domain: str) -> bool:
    for known in NEGATIVE_DOMAINS:
        if domain == known or domain.endswith("." + known):
            return True
    return False


def get_attr_score(attrs: dict, name: str) -> float:
    if not isinstance(attrs, dict):
        return 0.0

    value = attrs.get(name, {})

    if not isinstance(value, dict):
        return 0.0

    try:
        return float(value.get("score", 0.0))
    except Exception:
        return 0.0


def get_attr_bool(attrs: dict, name: str) -> bool:
    if not isinstance(attrs, dict):
        return False

    value = attrs.get(name, {})

    if not isinstance(value, dict):
        return False

    return bool(value.get("value", False))


# ==============================
# BÚSQUEDA ROBUSTA DE JSON
# ==============================

@lru_cache(maxsize=4096)
def find_gap_json_path(paper_name: str) -> str:
    """
    Busca el .dataset.json aunque no esté exactamente en pdfs/.
    """
    paper_name = (paper_name or "").strip()

    if not paper_name:
        return ""

    if paper_name.lower().endswith(".pdf"):
        stem = paper_name[:-4]
    else:
        stem = paper_name

    possible_names = [
        f"{stem}.dataset.json",
        f"{stem}.json"
    ]

    for base in JSON_SEARCH_DIRS:
        base_path = Path(base)

        if not base_path.exists():
            continue

        for name in possible_names:
            direct = base_path / name
            if direct.exists():
                return str(direct)

        for name in possible_names:
            matches = list(base_path.rglob(name))
            if matches:
                return str(matches[0])

    return ""


# ==============================
# LECTURA DE JSON
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


def extract_urls_from_anywhere(obj) -> list:
    found = set()

    for node in iter_json_nodes(obj):
        if isinstance(node, str):
            for match in URL_REGEX.findall(node):
                found.add(normalize_url_loose(match))

            doi_norm = normalize_doi(node)
            if doi_norm:
                found.add(normalize_url_loose(doi_norm))

    return sorted(found)


def extract_urls_from_node(node) -> list:
    found = set()

    for subnode in iter_json_nodes(node):
        if isinstance(subnode, str):
            for match in URL_REGEX.findall(subnode):
                found.add(normalize_url_loose(match))

            doi_norm = normalize_doi(subnode)
            if doi_norm:
                found.add(normalize_url_loose(doi_norm))

    return sorted(found)


def extract_mentions(data) -> list:
    mentions = []

    if isinstance(data, dict) and isinstance(data.get("mentions"), list):
        raw_mentions = data["mentions"]
    else:
        raw_mentions = []
        for node in iter_json_nodes(data):
            if isinstance(node, dict) and (
                "rawForm" in node
                or "normalizedForm" in node
                or "dataset-name" in node
                or "dataset-implicit" in node
            ):
                raw_mentions.append(node)

    for node in raw_mentions:
        if not isinstance(node, dict):
            continue

        raw_form = clean_text(node.get("rawForm", ""))
        normalized_form = clean_text(node.get("normalizedForm", ""))
        mention_type = clean_text(node.get("type", ""))
        context = clean_text(node.get("context", ""))

        if not normalized_form:
            for possible_key in ("dataset-name", "dataset-implicit"):
                sub = node.get(possible_key, {})
                if isinstance(sub, dict):
                    normalized_form = clean_text(sub.get("normalizedForm", ""))
                    raw_form = raw_form or clean_text(sub.get("rawForm", ""))

        mention_attrs = node.get("mentionContextAttributes", {})
        document_attrs = node.get("documentContextAttributes", {})

        urls = extract_urls_from_node(node)

        mentions.append({
            "raw_form": raw_form,
            "normalized_form": normalized_form,
            "mention_type": mention_type,
            "context": context,
            "urls": urls,
            "mention_attrs": mention_attrs,
            "document_attrs": document_attrs
        })

    return mentions


@lru_cache(maxsize=2048)
def summarize_json(json_path: str) -> dict:
    data = safe_read_json(json_path)

    if data is None:
        return {
            "exists": False,
            "error": "json_not_readable",
            "all_urls": [],
            "mentions": []
        }

    return {
        "exists": True,
        "error": "",
        "all_urls": extract_urls_from_anywhere(data),
        "mentions": extract_mentions(data)
    }


# ==============================
# EVIDENCIA A NIVEL DE PAPER
# ==============================

def paper_dataset_evidence(mentions: list) -> dict:
    """
    Calcula si el paper tiene evidencia fuerte de datasets según GAP-KGE/DataStet.
    Esto no decide la URL por sí solo, pero ayuda.
    """
    score = 0
    signals = []

    mention_count = len(mentions)
    dataset_name_count = 0
    implicit_count = 0
    shared_high_count = 0
    created_high_count = 0
    used_high_count = 0
    availability_count = 0

    names = []

    for m in mentions:
        mtype = m.get("mention_type", "")
        name = m.get("normalized_form") or m.get("raw_form") or ""
        context = m.get("context", "").lower()

        if name:
            names.append(name)

        if mtype == "dataset-name":
            dataset_name_count += 1

        if mtype == "dataset-implicit":
            implicit_count += 1

        mention_attrs = m.get("mention_attrs", {})
        document_attrs = m.get("document_attrs", {})

        shared_score = max(
            get_attr_score(mention_attrs, "shared"),
            get_attr_score(document_attrs, "shared")
        )

        created_score = max(
            get_attr_score(mention_attrs, "created"),
            get_attr_score(document_attrs, "created")
        )

        used_score = max(
            get_attr_score(mention_attrs, "used"),
            get_attr_score(document_attrs, "used")
        )

        shared_value = (
            get_attr_bool(mention_attrs, "shared")
            or get_attr_bool(document_attrs, "shared")
        )

        created_value = (
            get_attr_bool(mention_attrs, "created")
            or get_attr_bool(document_attrs, "created")
        )

        used_value = (
            get_attr_bool(mention_attrs, "used")
            or get_attr_bool(document_attrs, "used")
        )

        if shared_value and shared_score >= 0.5:
            shared_high_count += 1

        if created_value and created_score >= 0.5:
            created_high_count += 1

        if used_value and used_score >= 0.8:
            used_high_count += 1

        if any(kw in context for kw in CONTEXT_AVAILABLE_KEYWORDS):
            availability_count += 1

    if dataset_name_count > 0:
        score += 1
        signals.append("paper_has_dataset_mentions")

    if dataset_name_count >= 3:
        score += 1
        signals.append("paper_has_many_dataset_mentions")

    if shared_high_count > 0:
        score += 3
        signals.append("paper_has_shared_dataset_signal")

    if created_high_count > 0:
        score += 2
        signals.append("paper_has_created_dataset_signal")

    if used_high_count > 0:
        score += 1
        signals.append("paper_has_used_dataset_signal")

    if availability_count > 0:
        score += 2
        signals.append("paper_has_availability_context")

    return {
        "score": score,
        "signals": signals,
        "mention_count": mention_count,
        "dataset_name_count": dataset_name_count,
        "implicit_count": implicit_count,
        "shared_high_count": shared_high_count,
        "created_high_count": created_high_count,
        "used_high_count": used_high_count,
        "availability_count": availability_count,
        "names": names
    }


# ==============================
# SCORING URL
# ==============================

def score_url_itself(url: str) -> dict:
    norm = normalize_url_loose(url)
    domain = get_domain(norm)
    path = get_path(norm)
    ext = get_extension(norm)
    tokens = tokenize_url(norm)

    score = 0
    signals = []

    if ext in DATA_EXTENSIONS:
        score += 4
        signals.append(f"data_extension:{ext}")

    if is_positive_data_domain(domain):
        score += 3
        signals.append("known_data_domain")

    if is_negative_domain(domain):
        score -= 4
        signals.append("negative_paper_domain")

    positive_tokens = sorted(tokens.intersection(URL_POSITIVE_KEYWORDS))
    negative_tokens = sorted(tokens.intersection(URL_NEGATIVE_KEYWORDS))

    if positive_tokens:
        score += min(3, len(positive_tokens))
        signals.append("positive_url_tokens:" + ",".join(positive_tokens[:6]))

    if negative_tokens:
        score -= min(3, len(negative_tokens))
        signals.append("negative_url_tokens:" + ",".join(negative_tokens[:6]))

    if path.endswith(".pdf"):
        score -= 4
        signals.append("pdf_penalty")

    # GitHub no siempre es malo: puede ser dataset, pero si no hay señales de data, penaliza
    if domain == "github.com":
        if positive_tokens:
            score += 1
            signals.append("github_with_data_signal")
        else:
            score -= 2
            signals.append("github_without_data_signal")

    return {
        "score": score,
        "signals": signals,
        "domain": domain,
        "tokens": sorted(tokens),
        "extension": ext
    }


def score_url_against_json(url: str, all_json_urls: list, mentions: list) -> dict:
    candidate_norm = normalize_url_loose(url)
    candidate_domain = get_domain(candidate_norm)
    candidate_tokens = tokenize_url(candidate_norm)

    score = 0
    signals = []

    json_domains = {get_domain(u) for u in all_json_urls if u}

    # 1. URL exacta en cualquier parte del JSON
    if candidate_norm in all_json_urls:
        score += 8
        signals.append("exact_url_in_json")

    # 2. Dominio presente en URLs del JSON
    matched_domain = ""
    for jd in json_domains:
        if domain_matches(candidate_domain, jd):
            matched_domain = jd
            break

    if matched_domain:
        score += 4
        signals.append(f"same_domain_in_json:{matched_domain}")

    # 3. Menciones: nombre del dataset contra tokens de URL
    best_mention_score = 0
    best_mention_name = ""
    best_context = ""
    best_mention_signals = []

    for m in mentions:
        mention_score = 0
        mention_signals = []

        name = m.get("normalized_form") or m.get("raw_form") or ""
        context = m.get("context", "")
        context_low = context.lower()
        urls = m.get("urls", [])

        name_tokens = tokenize_text(name)
        matched_tokens = sorted(candidate_tokens.intersection(name_tokens))

        # URL exacta dentro de la mención
        if candidate_norm in urls:
            mention_score += 8
            mention_signals.append("exact_url_inside_mention")

        # Dominio de URL candidata coincide con URL dentro de mención
        mention_domains = {get_domain(u) for u in urls if u}
        for md in mention_domains:
            if domain_matches(candidate_domain, md):
                mention_score += 5
                mention_signals.append(f"same_domain_as_mention_url:{md}")
                break

        # Nombre del dataset aparece en la URL
        if len(matched_tokens) >= 2:
            mention_score += 4
            mention_signals.append("dataset_name_tokens_in_url:" + ",".join(matched_tokens[:5]))
        elif len(matched_tokens) == 1 and len(matched_tokens[0]) >= 5:
            mention_score += 3
            mention_signals.append("dataset_name_token_in_url:" + matched_tokens[0])

        # Contexto de disponibilidad
        if mention_score > 0:
            availability_matches = [
                kw for kw in CONTEXT_AVAILABLE_KEYWORDS
                if kw in context_low
            ]

            if availability_matches:
                mention_score += 3
                mention_signals.append("availability_context:" + ",".join(availability_matches[:3]))

        # Atributos GAP-KGE/DataStet
        mention_attrs = m.get("mention_attrs", {})
        document_attrs = m.get("document_attrs", {})

        shared_score = max(
            get_attr_score(mention_attrs, "shared"),
            get_attr_score(document_attrs, "shared")
        )
        created_score = max(
            get_attr_score(mention_attrs, "created"),
            get_attr_score(document_attrs, "created")
        )
        used_score = max(
            get_attr_score(mention_attrs, "used"),
            get_attr_score(document_attrs, "used")
        )

        shared_value = (
            get_attr_bool(mention_attrs, "shared")
            or get_attr_bool(document_attrs, "shared")
        )
        created_value = (
            get_attr_bool(mention_attrs, "created")
            or get_attr_bool(document_attrs, "created")
        )
        used_value = (
            get_attr_bool(mention_attrs, "used")
            or get_attr_bool(document_attrs, "used")
        )

        # Solo reforzamos si ya hay relación con esa mención
        if mention_score > 0:
            if shared_value and shared_score >= 0.5:
                mention_score += 3
                mention_signals.append(f"shared_score:{shared_score:.3f}")

            if created_value and created_score >= 0.5:
                mention_score += 2
                mention_signals.append(f"created_score:{created_score:.3f}")

            if used_value and used_score >= 0.8:
                mention_score += 1
                mention_signals.append(f"used_score:{used_score:.3f}")

        if mention_score > best_mention_score:
            best_mention_score = mention_score
            best_mention_name = name
            best_context = context
            best_mention_signals = mention_signals

    if best_mention_score > 0:
        score += best_mention_score
        signals.extend(best_mention_signals)

    return {
        "score": score,
        "signals": signals,
        "matched_domain": matched_domain,
        "best_mention_score": best_mention_score,
        "best_mention_name": best_mention_name,
        "best_context": best_context
    }


# ==============================
# HEURÍSTICA PRINCIPAL
# ==============================

def heuristic_gap_json_smart_v2(url: str, paper: str) -> dict:
    json_path = find_gap_json_path(paper)

    url_score_info = score_url_itself(url)

    if not json_path:
        final_score = max(0, url_score_info["score"])

        matched = final_score >= DATASET_THRESHOLD

        return {
            "matched": matched,
            "score": final_score,
            "reason": "json_not_found_but_url_signals_used" if matched else "json_not_found",
            "value": {
                "json_path": "",
                "url_score": url_score_info["score"],
                "url_signals": url_score_info["signals"],
                "json_score": 0,
                "json_signals": [],
                "paper_evidence_score": 0,
                "paper_evidence_signals": [],
                "final_signals": url_score_info["signals"],
                "best_mention_name": "",
                "best_mention_context": "",
                "json_url_count": 0,
                "mention_count": 0
            }
        }

    summary = summarize_json(json_path)

    if not summary["exists"]:
        final_score = max(0, url_score_info["score"])
        matched = final_score >= DATASET_THRESHOLD

        return {
            "matched": matched,
            "score": final_score,
            "reason": "json_unreadable_but_url_signals_used" if matched else "json_unreadable",
            "value": {
                "json_path": json_path,
                "url_score": url_score_info["score"],
                "url_signals": url_score_info["signals"],
                "json_score": 0,
                "json_signals": [],
                "paper_evidence_score": 0,
                "paper_evidence_signals": [],
                "final_signals": url_score_info["signals"],
                "best_mention_name": "",
                "best_mention_context": "",
                "json_url_count": 0,
                "mention_count": 0
            }
        }

    all_json_urls = summary["all_urls"]
    mentions = summary["mentions"]

    json_relation = score_url_against_json(
        url=url,
        all_json_urls=all_json_urls,
        mentions=mentions
    )

    paper_evidence = paper_dataset_evidence(mentions)

    score = 0
    final_signals = []

    # A. Señales propias de la URL
    score += url_score_info["score"]
    final_signals.extend(url_score_info["signals"])

    # B. Relación concreta URL ↔ JSON/mención
    score += json_relation["score"]
    final_signals.extend(json_relation["signals"])

    # C. Evidencia a nivel paper
    # Aquí está la mejora importante:
    # si el JSON dice que el paper tiene datasets creados/compartidos/usados,
    # y la URL no es claramente negativa, damos un boost.
    url_not_clearly_negative = url_score_info["score"] >= 0

    url_has_some_positive_signal = (
        url_score_info["score"] > 0
        or json_relation["score"] > 0
    )

    if paper_evidence["score"] >= 4 and url_not_clearly_negative:
        score += 2
        final_signals.append("paper_level_strong_dataset_evidence_boost")

    elif paper_evidence["score"] >= 2 and url_has_some_positive_signal:
        score += 1
        final_signals.append("paper_level_weak_dataset_evidence_boost")

    # D. Caso permisivo:
    # si el paper tiene señal shared/created/available y la URL parece recurso,
    # aunque no aparezca exacta en el JSON, subimos más.
    if (
        paper_evidence["score"] >= 4
        and url_score_info["score"] >= 2
        and json_relation["score"] == 0
    ):
        score += 2
        final_signals.append("no_exact_url_but_paper_and_url_are_dataset_like")

    final_score = max(0, score)
    matched = final_score >= DATASET_THRESHOLD

    return {
        "matched": matched,
        "score": final_score,
        "reason": "gap_json_smart_v2_dataset_signal" if matched else "no_gap_json_smart_v2_dataset_signal",
        "value": {
            "json_path": json_path,
            "url_score": url_score_info["score"],
            "url_signals": url_score_info["signals"],
            "json_score": json_relation["score"],
            "json_signals": json_relation["signals"],
            "paper_evidence_score": paper_evidence["score"],
            "paper_evidence_signals": paper_evidence["signals"],
            "paper_dataset_name_count": paper_evidence["dataset_name_count"],
            "paper_shared_high_count": paper_evidence["shared_high_count"],
            "paper_created_high_count": paper_evidence["created_high_count"],
            "paper_used_high_count": paper_evidence["used_high_count"],
            "paper_availability_count": paper_evidence["availability_count"],
            "matched_json_domain": json_relation["matched_domain"],
            "best_mention_score": json_relation["best_mention_score"],
            "best_mention_name": json_relation["best_mention_name"],
            "best_mention_context": json_relation["best_context"],
            "json_url_count": len(all_json_urls),
            "mention_count": len(mentions),
            "final_signals": final_signals
        }
    }


# ==============================
# APLICACIÓN
# ==============================

def apply_heuristics(url: str, paper: str = "") -> dict:
    h = heuristic_gap_json_smart_v2(url, paper)

    label = "dataset" if h["matched"] else "not_dataset"

    return {
        "url": url,
        "paper": paper,
        "heuristica_gap_json_smart_v2": {
            "name": "gap_json_smart_v2",
            "matched": h["matched"],
            "score": h["score"],
            "signals": h["value"].get("final_signals", []),
            "internal_results": h
        },
        "total_score": h["score"],
        "label": label,
        "decision_reason": h["reason"]
    }


# ==============================
# CSV
# ==============================

def load_normalized_csv(path: str):
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


def process_rows(rows):
    results_csv = []
    results_json = []

    for row in rows:
        result = apply_heuristics(
            url=row["normalized_url"],
            paper=row["paper"]
        )

        h = result["heuristica_gap_json_smart_v2"]
        internal = h["internal_results"]
        value = internal.get("value", {})

        row_result = {
            "paper": row["paper"],
            "section": row["section"],
            "original_url": row["original_url"],
            "normalized_url": row["normalized_url"],
            "domain": row["domain"],
            "extension": row["extension"],

            "heuristica_gap_json_smart_v2_matched": h["matched"],
            "heuristica_gap_json_smart_v2_score": h["score"],
            "heuristica_gap_json_smart_v2_signals": "|".join(h.get("signals", [])),

            "total_score": result["total_score"],
            "label": result["label"],
            "decision_reason": result["decision_reason"],

            "gap_json_path": value.get("json_path", ""),
            "gap_url_score": value.get("url_score", 0),
            "gap_url_signals": "|".join(value.get("url_signals", [])),
            "gap_json_score": value.get("json_score", 0),
            "gap_json_signals": "|".join(value.get("json_signals", [])),
            "gap_paper_evidence_score": value.get("paper_evidence_score", 0),
            "gap_paper_evidence_signals": "|".join(value.get("paper_evidence_signals", [])),
            "gap_paper_dataset_name_count": value.get("paper_dataset_name_count", 0),
            "gap_paper_shared_high_count": value.get("paper_shared_high_count", 0),
            "gap_paper_created_high_count": value.get("paper_created_high_count", 0),
            "gap_paper_used_high_count": value.get("paper_used_high_count", 0),
            "gap_paper_availability_count": value.get("paper_availability_count", 0),
            "gap_matched_json_domain": value.get("matched_json_domain", ""),
            "gap_best_mention_score": value.get("best_mention_score", 0),
            "gap_best_mention_name": value.get("best_mention_name", ""),
            "gap_best_mention_context": value.get("best_mention_context", "")[:500],
            "gap_json_url_count": value.get("json_url_count", 0),
            "gap_mention_count": value.get("mention_count", 0)
        }

        results_csv.append(row_result)

        results_json.append({
            "paper": row["paper"],
            "section": row["section"],
            "original_url": row["original_url"],
            "normalized_url": row["normalized_url"],
            "result": result
        })

    return results_csv, results_json


def save_csv(rows, path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            pass
        return

    fields = rows[0].keys()

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def save_json(rows, path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)


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

    save_csv(results_csv, OUTPUT_CSV)
    save_json(results_json, OUTPUT_JSON)

    dataset_count = sum(1 for r in results_csv if r["label"] == "dataset")
    not_count = sum(1 for r in results_csv if r["label"] == "not_dataset")

    json_found_count = sum(
        1 for r in results_csv
        if r["gap_json_path"]
    )

    json_not_found_count = len(results_csv) - json_found_count

    paper_boost_count = sum(
        1 for r in results_csv
        if "paper_level" in r["heuristica_gap_json_smart_v2_signals"]
    )

    no_exact_but_boost_count = sum(
        1 for r in results_csv
        if "no_exact_url_but_paper_and_url_are_dataset_like" in r["heuristica_gap_json_smart_v2_signals"]
    )

    print("\nResultados guardados en:")
    print(f"- {OUTPUT_CSV}")
    print(f"- {OUTPUT_JSON}")

    print("\nResumen:")
    print(f"- dataset: {dataset_count}")
    print(f"- not_dataset: {not_count}")
    print(f"- JSON encontrado: {json_found_count}")
    print(f"- JSON no encontrado: {json_not_found_count}")
    print(f"- URLs con boost por evidencia del paper: {paper_boost_count}")
    print(f"- URLs aceptadas sin URL exacta pero con señales paper+URL: {no_exact_but_boost_count}")


if __name__ == "__main__":
    main()