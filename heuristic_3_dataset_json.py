import csv
import json
import re
from pathlib import Path
from urllib.parse import urlparse

# ==============================
# RUTAS
# ==============================
INPUT_CSV = "outputs/all_links_normalized.csv"
OUTPUT_CSV = "outputs/heuristic_3_results.csv"
OUTPUT_JSON = "outputs/heuristic_3_results.json"
GAP_KGE_JSON_DIR = "pdfs"

# ==============================
# CONFIGURACIÓN DE PRECISIÓN
# ==============================
ENABLE_REPOSITORY_LEVEL_MATCH = True
ENABLE_DATASET_NAME_MATCH = True
ENABLE_TRUSTED_REPOSITORY_MATCH = True

# Subimos a 5 para exigir combinaciones de señales fuertes y limpiar falsos positivos
MATCH_THRESHOLD = 5

# ==============================
# DICCIONARIOS CALIBRADOS (ALTA PRECISIÓN)
# ==============================
STRONG_DATA_EXTENSIONS = {
    ".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".h5", ".hdf5", 
    ".arff", ".db", ".sqlite", ".sqlite3", ".jsonl", ".ndjson",
    ".nt", ".ttl", ".rdf", ".owl"
}

AMBIGUOUS_DATA_EXTENSIONS = {
    ".json", ".xml", ".pkl", ".pickle", ".npy", ".npz", ".mat", ".dat", ".data"
}

COMPRESSED_EXTENSIONS = {
    ".zip", ".gz", ".tar", ".tgz", ".7z", ".rar", ".bz2", ".xz"
}

NON_DATA_EXTENSIONS = {
    ".html", ".htm", ".php", ".asp", ".aspx", ".js", ".css",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".bib", ".ris",
    ".md", ".rst", ".py", ".java", ".c", ".cpp", ".h", ".hpp",
    ".ipynb", ".yml", ".yaml", ".toml", ".ini", ".lock", ".exe", ".dll", ".so"
}

# Nombres exactos de ficheros de texto plano típicos de datasets en KGE
KGE_DATA_FILENAMES = {
    "train.txt", "test.txt", "valid.txt", "dev.txt", 
    "entities.txt", "relations.txt", "triples.txt"
}

TECHNICAL_FILENAMES = {
    "manifest.json", "site.webmanifest", "asset-manifest.json", "robots.txt",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "requirements.txt", "environment.yml", "metadata.json", "info.json", "config.json"
}

# Palabras inequívocas de datasets (Eliminadas palabras de código como graph, kg, relation, split)
DATASET_KEYWORDS = {
    "dataset", "datasets", "benchmark", "benchmarks", "corpus", "database",
    "dataverse", "zenodo", "figshare", "kaggle", "huggingface", "datadryad",
    # Datasets concretos y famosos de la literatura KGE
    "wn18", "wn18rr", "fb15k", "fb15k237", "yago", "yago3", "dbpedia", "wikidata", 
    "nell", "nell995", "kinship", "nations", "umls", "codex"
}

# Segmentos de ruta que delatan CÓDIGO FUENTE o DOCUMENTACIÓN (Causa de falsos positivos)
CODE_OR_DOC_PATHS = {
    "/src/", "/main/", "/models/", "/scripts/", "/utils/", "/tests/", "/layers/", 
    "/encoders/", "/modules/", "/notebooks/", "/docs/", "/wiki/", "/demo/", "/example/",
    "setup.py", "main.py", "run.py", "train.py", "evaluate.py", "model.py"
}

NEGATIVE_KEYWORDS = {
    "paper", "article", "citation", "bibtex", "reference", "documentation", 
    "blog", "login", "signin", "contact", "about", "license", "terms", "privacy", 
    "favicon", "static", "assets", "bundle", "webpack", "readme", "issue", "pull"
}

PAPER_DOMAINS = {
    "doi.org", "dx.doi.org", "arxiv.org", "www.arxiv.org", "semanticscholar.org", 
    "api.semanticscholar.org", "acm.org", "doi.acm.org", "ieee.org", 
    "ieeexplore.ieee.org", "springer.com", "link.springer.com", "usenix.org"
}

TRUSTED_DATASET_REPOSITORY_DOMAINS = {
    "zenodo.org", "www.zenodo.org", "figshare.com", "www.figshare.com",
    "datadryad.org", "dataverse.harvard.edu", "kaggle.com", "www.kaggle.com", 
    "archive.ics.uci.edu", "openml.org", "huggingface.co"
}

GENERIC_DATASET_NAMES = {
    "github", "gitlab", "bitbucket", "arxiv", "semanticscholar", "doi", "zenodo", 
    "figshare", "kaggle", "dataset", "datasets", "data", "repository", "code"
}

DATASET_NAME_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "by", "from"
}

URL_REGEX = re.compile(r'https?://[^\s"\'<>\)\]]+', re.IGNORECASE)
TOKEN_SPLIT_REGEX = re.compile(r"[/\\\-_.?=&:#\s]+")
NON_ALPHANUM_REGEX = re.compile(r"[^a-z0-9]")

_URL_PARSE_CACHE = {}

# ==============================
# UTILIDADES DE PARSEO
# ==============================
def parse_url_cached(url: str):
    if url in _URL_PARSE_CACHE:
        return _URL_PARSE_CACHE[url]
    try:
        parsed = urlparse(url)
        filename = Path(parsed.path).name.lower()
        suffixes = [s.lower() for s in Path(filename).suffixes]
        suffix = suffixes[-1] if suffixes else ""
        
        res = {
            "domain": parsed.netloc.lower(),
            "path": parsed.path.lower(),
            "filename": filename,
            "extension": suffix,
            "extensions": suffixes,
            "query": parsed.query.lower()
        }
    except Exception:
        res = {"domain": "", "path": "", "filename": "", "extension": "", "extensions": [], "query": ""}
    _URL_PARSE_CACHE[url] = res
    return res

def normalize_url(url: str) -> str:
    if not url: return ""
    url = str(url).strip().lower()
    url = url.split("#")[0]
    return url.rstrip("/.,;:!?)]}>'\"")

def root_domain(domain: str) -> str:
    if not domain: return ""
    if domain.startswith("www."): domain = domain[4:]
    parts = domain.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else domain

def tokenize_url(url_props: dict) -> set:
    raw = f"{url_props['domain']} {url_props['path']} {url_props['query']}"
    return {t for t in TOKEN_SPLIT_REGEX.split(raw) if t}

def repo_identity(url_props: dict) -> str:
    domain = url_props["domain"]
    parts = [p for p in url_props["path"].strip("/").split("/") if p]
    if domain in {"github.com", "www.github.com", "raw.githubusercontent.com", "gitlab.com", "www.gitlab.com"} and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return ""

def is_code_or_doc_artifact(url_props: dict) -> bool:
    """Detecta de forma estricta si la URL apunta a infraestructura de código o documentación"""
    path = url_props["path"]
    if any(pattern in path for pattern in CODE_OR_DOC_PATHS):
        return True
    if url_props["extension"] in {".py", ".ipynb", ".java", ".cpp", ".h", ".sh", ".bat"}:
        return True
    return False

# ==============================
# CLASIFICACIÓN DE ENLACES
# ==============================
def data_file_match_level(url_props: dict, tokens: set, r_domain: str) -> str:
    if url_props["filename"] in TECHNICAL_FILENAMES: return "technical"
    if is_code_or_doc_artifact(url_props): return "code_artifact"
    
    if url_props["filename"] in KGE_DATA_FILENAMES:
        return "strong_data_file"
        
    if any(e in COMPRESSED_EXTENSIONS for e in url_props["extensions"]):
        return "compressed_with_context" if tokens.intersection(DATASET_KEYWORDS) else "compressed"
        
    ext = url_props["extension"]
    if ext in NON_DATA_EXTENSIONS: return "non_data_file"
    if ext in STRONG_DATA_EXTENSIONS: return "strong_data_file"
    
    if ext in AMBIGUOUS_DATA_EXTENSIONS:
        return "ambiguous_data_file_with_context" if tokens.intersection(DATASET_KEYWORDS) else "ambiguous_data_file"
        
    if domain_in_set(url_props["domain"], r_domain, TRUSTED_DATASET_REPOSITORY_DOMAINS): 
        return "trusted_dataset_repository"
        
    if tokens.intersection(DATASET_KEYWORDS): 
        return "dataset_keyword_url"
        
    return "none"

def domain_in_set(domain: str, r_domain: str, target_set: set) -> bool:
    return domain in target_set or r_domain in target_set

# ==============================
# EMPAREJAMIENTO DE NOMBRES DE ALTA PRECISIÓN
# ==============================
def url_matches_dataset_name_precise(url_props: dict, tokens: set, dataset_names: list) -> tuple[bool, str]:
    target_text = f"{url_props['domain']} {url_props['path']}".lower()
    compact_target = NON_ALPHANUM_REGEX.sub("", target_text)

    for name in dataset_names:
        name_low = name.lower().strip()
        if len(name_low) < 3 or name_low in GENERIC_DATASET_NAMES: continue
        
        # Si es un acrónimo corto (ej: WN18, FB15k), requerimos coincidencia de TOKEN exacto en la URL, no substring
        if len(name_low) <= 5:
            name_tokens = {t for t in NON_ALPHANUM_REGEX.split(name_low) if t}
            if name_tokens and name_tokens.issubset(tokens):
                return True, name
        else:
            # Para nombres largos, podemos usar tokenización estándar o substring largo continuo
            compact_name = NON_ALPHANUM_REGEX.sub("", name_low)
            if len(compact_name) >= 6 and compact_name in compact_target:
                return True, name
                
            name_tokens = {t for t in NON_ALPHANUM_REGEX.split(name_low) if len(t) >= 3 and t not in DATASET_NAME_STOPWORDS}
            if name_tokens and len(name_tokens.intersection(tokens)) >= 2:
                return True, name
        
    return False, ""

# ==============================
# PROCESAMIENTO EXTRACTOR JSON
# ==============================
def iter_json_strings_and_dicts(obj):
    stack = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield "dict", current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str):
            yield "str", current

def extract_data_from_json(data) -> tuple[list, list]:
    found_urls = set()
    names = set()
    name_keys = {"rawform", "raw_form", "normalizedform", "normalized_form", "mention", "name", "dataset", "dataset_name", "title"}

    for item_type, item in iter_json_strings_and_dicts(data):
        if item_type == "str":
            for match in URL_REGEX.findall(item):
                found_urls.add(normalize_url(match))
        elif item_type == "dict":
            for k, v in item.items():
                if isinstance(k, str) and k.lower().replace("-", "_").replace("_", "") in name_keys:
                    if isinstance(v, str) and len(v.strip()) > 2:
                        names.add(v.strip())
    return sorted(found_urls), sorted(names)

_CACHE = {}

def summarize_dataset_json(json_path: str) -> dict:
    if json_path in _CACHE: return _CACHE[json_path]
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        out = {"exists": False, "urls": [], "dataset_names": [], "repo_identities": []}
        _CACHE[json_path] = out
        return out

    urls, dataset_names = extract_data_from_json(data)
    repo_ids = sorted({repo_identity(parse_url_cached(u)) for u in urls if repo_identity(parse_url_cached(u))})

    out = {
        "exists": True, "urls": urls, "dataset_names": dataset_names, "repo_identities": repo_ids,
        "url_count": len(urls), "dataset_name_count": len(dataset_names)
    }
    _CACHE[json_path] = out
    return out

# ==============================
# HEURÍSTICA MEJORADA BALANCED/PRECISE
# ==============================
def heuristic_3_gapkge_balanced(url: str, paper: str, base_dir: str = GAP_KGE_JSON_DIR) -> dict:
    stem = paper[:-4] if paper.lower().endswith(".pdf") else paper
    json_path = str(Path(base_dir) / f"{stem}.dataset.json") if stem else ""

    url_props = parse_url_cached(url)
    u_domain = url_props["domain"]
    u_r_domain = root_domain(u_domain)
    tokens = tokenize_url(url_props)
    
    input_level = data_file_match_level(url_props, tokens, u_r_domain)
    input_repo = repo_identity(url_props)
    
    has_dataset_signal = input_level in {"strong_data_file", "ambiguous_data_file_with_context", "trusted_dataset_repository", "dataset_keyword_url", "compressed_with_context"}
    has_negative_signal = bool(tokens.intersection(NEGATIVE_KEYWORDS)) or input_level in {"technical", "code_artifact"}

    value_template = {
        "json_path": json_path, "input_domain": u_domain, "input_match_level": input_level,
        "matched_dataset_name": "", "signals": []
    }

    if not json_path or not Path(json_path).exists():
        return {"matched": False, "score": 0, "reason": "json_not_found", "value": value_template}

    summary = summarize_dataset_json(json_path)
    if not summary["exists"]:
        return {"matched": False, "score": 0, "reason": "json_unreadable", "value": value_template}

    input_norm = normalize_url(url)
    score = 0
    signals = []

    # 1. Coincidencia exacta o ruta parcial legítima
    if input_norm in summary["urls"]:
        # Si es un archivo de código exacto listado por Grobid, no queremos considerarlo dataset a menos que tenga señales de datos claras
        score += 7 if input_level != "code_artifact" else 2
        signals.append("exact_url_found_in_gapkge_json")
    else:
        # Subruta compartida (solo si no es código fuente)
        if input_level != "code_artifact":
            for json_url in summary["urls"]:
                jp = parse_url_cached(json_url)
                if jp["domain"] == u_domain and len(jp["path"]) > 4 and jp["path"] in url_props["path"]:
                    score += 4
                    signals.append("url_path_is_subpath_of_json_url")
                    break

    # 2. Mismo Repositorio de Código (Bajamos la puntuación base para mitigar falsos positivos en código)
    if ENABLE_REPOSITORY_LEVEL_MATCH and input_repo and input_repo in summary["repo_identities"]:
        if has_dataset_signal:
            score += 5
            signals.append("matches_code_repository_with_data_signal")
        else:
            score += 1  # Un repositorio idéntico sin señales de datos casi nunca es el dataset en sí
            signals.append("matches_code_repository_only")

    # 3. Emparejamiento por Nombres del Dataset
    if ENABLE_DATASET_NAME_MATCH:
        matched_name_found, matched_name = url_matches_dataset_name_precise(url_props, tokens, summary["dataset_names"])
        if matched_name_found:
            value_template["matched_dataset_name"] = matched_name
            score += 5 if has_dataset_signal else 3
            signals.append("dataset_name_or_acronym_found_in_url")

    # 4. Repositorio de datos seguro conocido (Zenodo, HF...)
    if ENABLE_TRUSTED_REPOSITORY_MATCH and domain_in_set(u_domain, u_r_domain, TRUSTED_DATASET_REPOSITORY_DOMAINS):
        score += 3
        signals.append("trusted_dataset_domain")

    # PENALIZACIONES SEVERAS (Blindaje contra falsos positivos)
    if input_level == "code_artifact":
        score -= 7  # Penalización crítica: es un script/carpeta de desarrollo
        signals.append("code_artifact_heavy_penalty")
    if domain_in_set(u_domain, u_r_domain, PAPER_DOMAINS):
        score -= 6
        signals.append("paper_domain_penalty")
    if has_negative_signal:
        score -= 4
        signals.append("negative_keyword_penalty")
    if input_level == "non_data_file":
        score -= 3
        signals.append("non_data_extension_penalty")

    score = max(score, 0)
    matched = score >= MATCH_THRESHOLD
    
    value_template.update({"score": score, "signals": signals})
    return {
        "matched": matched, "score": score,
        "reason": "dataset_confirmed" if matched else "not_confirmed",
        "value": value_template
    }

# ==============================
# PIPELINE CON PROTECCIÓN DE PERMISOS
# ==============================
def main():
    if not Path(INPUT_CSV).exists():
        print(f"Error: No se encuentra {INPUT_CSV}")
        return

    print("Iniciando análisis heurístico de ALTA PRECISIÓN para KGE...")
    csv_rows = []
    json_rows = []

    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        input_rows = list(reader)

    for row in input_rows:
        url = row.get("normalized_url", "").strip() or row.get("url", "").strip()
        if not url: continue

        paper = row.get("paper", "").strip()
        h = heuristic_3_gapkge_balanced(url, paper)
        label = "dataset" if h["matched"] else "not_dataset"
        val = h["value"]

        csv_rows.append({
            **row,  
            "heuristic_matched": h["matched"],
            "heuristic_score": h["score"],
            "heuristic_reason": h["reason"],
            "matched_dataset_name": val["matched_dataset_name"],
            "input_match_level": val["input_match_level"],
            "signals": "|".join(val["signals"]),
            "label": label
        })

        json_rows.append({
            "url": url,
            "paper": paper,
            "heuristic_results": h,
            "label": label
        })

    # Guardar resultados de manera defensiva contra bloqueos de Excel / OneDrive
    Path(OUTPUT_CSV).parent.mkdir(parents=True, exist_ok=True)
    
    try:
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
            if csv_rows:
                writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
                writer.writeheader()
                writer.writerows(csv_rows)
        print(f" -> Resultados guardados con éxito en CSV: {OUTPUT_CSV}")
    except PermissionError:
        print(f"\n❌ ERROR DE PERMISOS: No se pudo escribir en '{OUTPUT_CSV}'.")
        print("Asegúrate de CERRAR el archivo en Microsoft Excel antes de ejecutar de nuevo.")
        print("Los datos NO se han guardado en el CSV para evitar romper tu proceso.")
        return

    try:
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump(json_rows, f, indent=2, ensure_ascii=False)
        print(f" -> Resultados guardados en JSON: {OUTPUT_JSON}")
    except Exception as e:
        print(f"No se pudo escribir el JSON: {e}")

    print(f"\nResumen Estadístico:")
    print(f" Total filas analizadas: {len(csv_rows)}")
    print(f" ➔ Confirmados como DATASET (Aciertos): {sum(1 for r in csv_rows if r['label'] == 'dataset')}")
    print(f" ➔ Descartados (Not Dataset): {sum(1 for r in csv_rows if r['label'] == 'not_dataset')}")

if __name__ == "__main__":
    main()