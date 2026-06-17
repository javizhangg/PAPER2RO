import csv
import json
import re
from pathlib import Path
from urllib.parse import urlparse, unquote
from datetime import datetime

import requests


# ==============================
# RUTAS
# ==============================

INPUT_CSV = "outputs/all_links_normalized.csv"
OUTPUT_CSV = "outputs/heuristic_2_results.csv"
OUTPUT_JSON = "outputs/heuristic_2_results.json"

REQUEST_TIMEOUT = 12
MAX_RESPONSE_BYTES = 2_000_000


# ==============================
# CONTENT NEGOTIATION HEADERS
# ==============================

CONTENT_NEGOTIATION_ACCEPTS = [
    {
        "name": "schemaorg_ld_json",
        "accept": "application/vnd.schemaorg.ld+json"
    },
    {
        "name": "datacite_json",
        "accept": "application/vnd.datacite.datacite+json"
    },
    {
        "name": "citeproc_json",
        "accept": "application/vnd.citationstyles.csl+json"
    },
    {
        "name": "json_ld",
        "accept": "application/ld+json"
    },
    {
        "name": "generic_json",
        "accept": "application/json"
    }
]


# ==============================
# UTILIDADES BÁSICAS
# ==============================

def normalize_url(url: str) -> str:
    if not url:
        return ""

    url = str(url).strip()
    url = url.rstrip(".,;:!?)]}>'\"")
    return url


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


def safe_json_loads(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


def safe_json_dumps(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


# ==============================
# DOI
# ==============================

DOI_REGEX = re.compile(
    r'(10\.\d{4,9}/[-._;()/:A-Z0-9]+)',
    re.IGNORECASE
)


def extract_doi_from_url_or_text(text: str) -> str:
    """
    Extrae DOI si aparece en una URL o texto.

    Ejemplos:
    - https://doi.org/10.5281/zenodo.123456
    - doi:10.1038/s41597-...
    """

    if not text:
        return ""

    text = unquote(str(text))

    match = DOI_REGEX.search(text)

    if not match:
        return ""

    doi = match.group(1)
    doi = doi.rstrip(".,;:!?)]}>'\"")

    return doi


def build_metadata_target_urls(url: str) -> list:
    """
    Construye URLs donde intentar content negotiation.

    Primero intenta la propia URL.
    Si detecta DOI, intenta también https://doi.org/<doi>.
    Además añade fallback de DataCite API.
    """

    url = normalize_url(url)
    targets = []

    if url:
        targets.append({
            "url": url,
            "source": "original_url",
            "is_content_negotiation": True
        })

    doi = extract_doi_from_url_or_text(url)

    if doi:
        doi_url = f"https://doi.org/{doi}"
        datacite_api_url = f"https://api.datacite.org/dois/{doi.lower()}"

        targets.append({
            "url": doi_url,
            "source": "doi_url",
            "is_content_negotiation": True
        })

        targets.append({
            "url": datacite_api_url,
            "source": "datacite_api_fallback",
            "is_content_negotiation": False
        })

    # Quitar duplicados
    unique = []
    seen = set()

    for target in targets:
        if target["url"] not in seen:
            seen.add(target["url"])
            unique.append(target)

    return unique


# ==============================
# PETICIÓN CON CONTENT NEGOTIATION
# ==============================

def request_metadata_with_accept(url: str, accept: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 metadata-content-negotiation-detector/1.0",
        "Accept": accept
    }

    response = None

    try:
        response = requests.get(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=REQUEST_TIMEOUT,
            stream=True
        )

        status_code = response.status_code

        content_type = response.headers.get(
            "Content-Type", ""
        ).split(";")[0].strip().lower()

        final_url = response.url

        raw = read_limited_response(response, MAX_RESPONSE_BYTES)
        text = decode_response_bytes(raw, response).strip()

        parsed_json = safe_json_loads(text)

        return {
            "ok": 200 <= status_code < 300,
            "status_code": status_code,
            "content_type": content_type,
            "final_url": final_url,
            "text": text,
            "json": parsed_json,
            "error": ""
        }

    except Exception as e:
        return {
            "ok": False,
            "status_code": "",
            "content_type": "",
            "final_url": "",
            "text": "",
            "json": None,
            "error": str(e)
        }

    finally:
        try:
            if response is not None:
                response.close()
        except Exception:
            pass


def request_datacite_api(url: str) -> dict:
    """
    Fallback para DataCite API.

    Esto no es content negotiation puro, pero sirve cuando la URL contiene DOI
    y el servidor doi.org no devuelve bien los metadatos.
    """

    headers = {
        "User-Agent": "Mozilla/5.0 metadata-content-negotiation-detector/1.0",
        "Accept": "application/json"
    }

    response = None

    try:
        response = requests.get(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=REQUEST_TIMEOUT,
            stream=True
        )

        status_code = response.status_code
        content_type = response.headers.get(
            "Content-Type", ""
        ).split(";")[0].strip().lower()

        final_url = response.url

        raw = read_limited_response(response, MAX_RESPONSE_BYTES)
        text = decode_response_bytes(raw, response).strip()

        parsed_json = safe_json_loads(text)

        return {
            "ok": 200 <= status_code < 300,
            "status_code": status_code,
            "content_type": content_type,
            "final_url": final_url,
            "text": text,
            "json": parsed_json,
            "error": ""
        }

    except Exception as e:
        return {
            "ok": False,
            "status_code": "",
            "content_type": "",
            "final_url": "",
            "text": "",
            "json": None,
            "error": str(e)
        }

    finally:
        try:
            if response is not None:
                response.close()
        except Exception:
            pass


# ==============================
# ANÁLISIS DE METADATOS
# ==============================

def normalize_metadata_key(key: str) -> str:
    return str(key or "").lower().replace("-", "").replace("_", "").replace(":", "")


def iter_json_paths(obj, path: str = ""):
    """
    Recorre recursivamente un JSON y devuelve:
    - ruta del campo
    - valor
    """

    if isinstance(obj, dict):
        for key, value in obj.items():
            new_path = f"{path}.{key}" if path else str(key)
            yield new_path, value
            yield from iter_json_paths(value, new_path)

    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            new_path = f"{path}[{i}]"
            yield new_path, item
            yield from iter_json_paths(item, new_path)


def value_contains_dataset(value) -> bool:
    """
    Comprueba si un valor indica claramente Dataset.
    """

    if value is None:
        return False

    if isinstance(value, list):
        return any(value_contains_dataset(v) for v in value)

    if isinstance(value, dict):
        return any(value_contains_dataset(v) for v in value.values())

    value_text = str(value).strip().lower()

    dataset_values = {
        "dataset",
        "data set",
        "datasets",
        "dcat:dataset",
        "schema:dataset",
        "http://schema.org/dataset",
        "https://schema.org/dataset",
        "http://www.w3.org/ns/dcat#dataset",
        "https://www.w3.org/ns/dcat#dataset",
    }

    if value_text in dataset_values:
        return True

    if "schema.org/dataset" in value_text:
        return True

    if "dcat#dataset" in value_text:
        return True

    if value_text.endswith(":dataset"):
        return True

    if value_text.endswith("/dataset"):
        return True

    return False


def metadata_says_dataset(metadata_obj) -> dict:
    """
    Detecta si los metadatos dicen que el recurso es un Dataset.

    Detecta:
    - schema.org: @type = Dataset
    - JSON-LD: type = Dataset
    - DataCite: resourceTypeGeneral = Dataset
    - DataCite: types.resourceTypeGeneral = Dataset
    - CSL: type = dataset
    - DCAT: @type = dcat:Dataset
    - Dublin Core: dc:type / dcterms:type = Dataset
    - additionalType = Dataset
    """

    if metadata_obj is None:
        return {
            "matched": False,
            "field": "",
            "value": "",
            "evidence": ""
        }

    dataset_keys = {
        "@type",
        "type",
        "resourcetypegeneral",
        "resourcetype",
        "genre",
        "additionaltype",
        "dctype",
        "dctermstype",
        "dc.type",
        "dcterms.type",
    }

    for path, value in iter_json_paths(metadata_obj):
        key = path.split(".")[-1]
        key_norm = normalize_metadata_key(key)

        # También miramos la ruta completa por si aparece data.attributes.types.resourceTypeGeneral
        path_norm = normalize_metadata_key(path)

        is_relevant_key = (
            key_norm in dataset_keys
            or path_norm.endswith("resourcetypegeneral")
            or path_norm.endswith("resourcetype")
            or path_norm.endswith("additionaltype")
            or path_norm.endswith("dctype")
            or path_norm.endswith("dctermstype")
        )

        if is_relevant_key and value_contains_dataset(value):
            return {
                "matched": True,
                "field": path,
                "value": safe_json_dumps(value),
                "evidence": f"{path}_indicates_dataset"
            }

    return {
        "matched": False,
        "field": "",
        "value": "",
        "evidence": ""
    }

# ==============================
# HEURÍSTICA 2
# ==============================

def heuristic_2(url: str) -> dict:
    """
    Heurística 2:

    1. Intenta content negotiation sobre la URL.
    2. Si hay DOI, intenta también doi.org.
    3. Si hay DOI, usa DataCite API como fallback.
    4. Si obtiene metadatos JSON, mira si dicen Dataset.
    """

    url = normalize_url(url)

    targets = build_metadata_target_urls(url)

    attempts = []
    content_negotiation_realizada = False
    metadatos_accesibles = False

    best_metadata = None
    best_attempt = None
    dataset_result = {
        "matched": False,
        "field": "",
        "value": "",
        "evidence": ""
    }

    for target in targets:
        target_url = target["url"]
        target_source = target["source"]

        if target["is_content_negotiation"]:
            for accept_item in CONTENT_NEGOTIATION_ACCEPTS:
                content_negotiation_realizada = True

                response_data = request_metadata_with_accept(
                    target_url,
                    accept_item["accept"]
                )

                attempt = {
                    "target_url": target_url,
                    "target_source": target_source,
                    "accept_name": accept_item["name"],
                    "accept_header": accept_item["accept"],
                    "is_content_negotiation": True,
                    "ok": response_data.get("ok", False),
                    "status_code": response_data.get("status_code", ""),
                    "content_type": response_data.get("content_type", ""),
                    "final_url": response_data.get("final_url", ""),
                    "error": response_data.get("error", "")
                }

                parsed_json = response_data.get("json")

                if parsed_json is not None:
                    attempt["metadata_parsed_as_json"] = True
                    metadatos_accesibles = True

                    current_dataset_result = metadata_says_dataset(parsed_json)

                    attempt["metadata_says_dataset"] = current_dataset_result["matched"]
                    attempt["dataset_metadata_field"] = current_dataset_result["field"]
                    attempt["dataset_metadata_value"] = current_dataset_result["value"]
                    attempt["dataset_metadata_evidence"] = current_dataset_result["evidence"]

                    attempts.append(attempt)

                    best_metadata = parsed_json
                    best_attempt = attempt

                    if current_dataset_result["matched"]:
                        dataset_result = current_dataset_result

                        return {
                            "matched": True,
                            "reason": "metadata_says_dataset",
                            "value": {
                                "url": url,
                                "content_negotiation_realizada": content_negotiation_realizada,
                                "metadatos_accesibles": metadatos_accesibles,
                                "metadata_source": target_source,
                                "metadata_url": target_url,
                                "metadata_final_url": response_data.get("final_url", ""),
                                "metadata_format": accept_item["name"],
                                "accept_header": accept_item["accept"],
                                "dataset_metadata_field": dataset_result["field"],
                                "dataset_metadata_value": dataset_result["value"],
                                "dataset_metadata_evidence": dataset_result["evidence"],
                                "attempts": attempts,
                                "metadata_sample": parsed_json
                            }
                        }

                    # Si parseó JSON pero no dice dataset, seguimos probando otros Accept.
                    continue

                else:
                    attempt["metadata_parsed_as_json"] = False
                    attempt["metadata_says_dataset"] = False
                    attempt["dataset_metadata_field"] = ""
                    attempt["dataset_metadata_value"] = ""
                    attempt["dataset_metadata_evidence"] = ""

                    attempts.append(attempt)

        else:
            # Fallback DataCite API
            response_data = request_datacite_api(target_url)

            attempt = {
                "target_url": target_url,
                "target_source": target_source,
                "accept_name": "datacite_api_fallback",
                "accept_header": "application/json",
                "is_content_negotiation": False,
                "ok": response_data.get("ok", False),
                "status_code": response_data.get("status_code", ""),
                "content_type": response_data.get("content_type", ""),
                "final_url": response_data.get("final_url", ""),
                "error": response_data.get("error", "")
            }

            parsed_json = response_data.get("json")

            if parsed_json is not None:
                attempt["metadata_parsed_as_json"] = True
                metadatos_accesibles = True

                current_dataset_result = metadata_says_dataset(parsed_json)

                attempt["metadata_says_dataset"] = current_dataset_result["matched"]
                attempt["dataset_metadata_field"] = current_dataset_result["field"]
                attempt["dataset_metadata_value"] = current_dataset_result["value"]
                attempt["dataset_metadata_evidence"] = current_dataset_result["evidence"]

                attempts.append(attempt)

                best_metadata = parsed_json
                best_attempt = attempt

                if current_dataset_result["matched"]:
                    dataset_result = current_dataset_result

                    return {
                        "matched": True,
                        "reason": "metadata_says_dataset_datacite_api_fallback",
                        "value": {
                            "url": url,
                            "content_negotiation_realizada": content_negotiation_realizada,
                            "metadatos_accesibles": metadatos_accesibles,
                            "metadata_source": target_source,
                            "metadata_url": target_url,
                            "metadata_final_url": response_data.get("final_url", ""),
                            "metadata_format": "datacite_api_fallback",
                            "accept_header": "application/json",
                            "dataset_metadata_field": dataset_result["field"],
                            "dataset_metadata_value": dataset_result["value"],
                            "dataset_metadata_evidence": dataset_result["evidence"],
                            "attempts": attempts,
                            "metadata_sample": parsed_json
                        }
                    }

            else:
                attempt["metadata_parsed_as_json"] = False
                attempt["metadata_says_dataset"] = False
                attempt["dataset_metadata_field"] = ""
                attempt["dataset_metadata_value"] = ""
                attempt["dataset_metadata_evidence"] = ""

                attempts.append(attempt)

    # Si llegó aquí, no encontró metadata positiva.
    if best_attempt is not None:
        metadata_source = best_attempt.get("target_source", "")
        metadata_url = best_attempt.get("target_url", "")
        metadata_final_url = best_attempt.get("final_url", "")
        metadata_format = best_attempt.get("accept_name", "")
        accept_header = best_attempt.get("accept_header", "")
    else:
        metadata_source = ""
        metadata_url = ""
        metadata_final_url = ""
        metadata_format = ""
        accept_header = ""

    return {
        "matched": False,
        "reason": "metadata_not_dataset_or_not_accessible",
        "value": {
            "url": url,
            "content_negotiation_realizada": content_negotiation_realizada,
            "metadatos_accesibles": metadatos_accesibles,
            "metadata_source": metadata_source,
            "metadata_url": metadata_url,
            "metadata_final_url": metadata_final_url,
            "metadata_format": metadata_format,
            "accept_header": accept_header,
            "dataset_metadata_field": "",
            "dataset_metadata_value": "",
            "dataset_metadata_evidence": "",
            "attempts": attempts,
            "metadata_sample": best_metadata
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
# GUARDADO
# ==============================

def save_csv_simple(rows: list, path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "pdf",
        "url",
        "content_negotiation_realizada",
        "metadatos_accesibles",
        "heuristica",
        "metadata_source",
        "metadata_format",
        "metadata_url",
        "metadata_final_url",
        "accept_header",
        "dataset_metadata_field",
        "dataset_metadata_value",
        "dataset_metadata_evidence",
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

    print(f"-> Filas leídas: {len(input_rows)}")
    print("-> Iniciando heurística 2: content negotiation de metadatos...")

    csv_rows = []
    json_rows = []

    for i, row in enumerate(input_rows, start=1):
        pdf = row["pdf"]
        url = row["url"]

        if not url:
            continue

        print(f"[{i}/{len(input_rows)}] Analizando: {url}")

        h = heuristic_2(url)
        value = h.get("value", {})

        csv_rows.append({
            "pdf": pdf,
            "url": url,
            "content_negotiation_realizada": bool(
                value.get("content_negotiation_realizada", False)
            ),
            "metadatos_accesibles": bool(
                value.get("metadatos_accesibles", False)
            ),
            "heuristica": bool(h.get("matched", False)),
            "metadata_source": value.get("metadata_source", ""),
            "metadata_format": value.get("metadata_format", ""),
            "metadata_url": value.get("metadata_url", ""),
            "metadata_final_url": value.get("metadata_final_url", ""),
            "accept_header": value.get("accept_header", ""),
            "dataset_metadata_field": value.get("dataset_metadata_field", ""),
            "dataset_metadata_value": value.get("dataset_metadata_value", ""),
            "dataset_metadata_evidence": value.get("dataset_metadata_evidence", ""),
            "motivo": h.get("reason", "")
        })

        json_rows.append({
            "pdf": pdf,
            "url": url,
            "heuristic_2": h
        })

    saved_csv = save_csv_simple(csv_rows, OUTPUT_CSV)
    saved_json = save_json_full(json_rows, OUTPUT_JSON)

    total_true = sum(1 for r in csv_rows if r["heuristica"] is True)
    total_false = sum(1 for r in csv_rows if r["heuristica"] is False)
    total_metadata = sum(1 for r in csv_rows if r["metadatos_accesibles"] is True)

    print()
    print("================ RESUMEN HEURÍSTICA 2 ================")
    print(f"Filas procesadas: {len(csv_rows)}")
    print(f"Con content negotiation intentada: {sum(1 for r in csv_rows if r['content_negotiation_realizada'] is True)}")
    print(f"Con metadatos accesibles: {total_metadata}")
    print(f"Detectados como DATASET por metadatos: {total_true}")
    print(f"Descartados: {total_false}")
    print(f"CSV/Excel guardado en: {saved_csv}")
    print(f"JSON completo guardado en: {saved_json}")
    print("======================================================")


if __name__ == "__main__":
    main()