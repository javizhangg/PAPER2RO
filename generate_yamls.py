# generate_ya2ro_yamls.py
# ------------------------------------------------------------
# Genera ficheros YAML compatibles con ya2ro a partir de
# outputs/heuristics_results.csv
#
# Entrada esperada:
#   outputs/heuristics_results.csv
#
# Salida:
#   outputs/ya2ro_yamls/<paper>.yaml       -> un YAML por paper
#   outputs/ya2ro_yamls/all_papers.yaml    -> un YAML global opcional
#   outputs/ya2ro_yamls/summary.csv        -> resumen de URLs incluidas
# ------------------------------------------------------------

import csv
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    import yaml
except ImportError:
    yaml = None


# =========================
# CONFIGURACIÓN
# =========================
INPUT_CSV = Path("outputs/heuristics_results.csv")
OUTPUT_DIR = Path("outputs/ya2ro_yamls")

# Columna donde está la predicción final generada por heuristics.py
LABEL_COLUMN = "label"

# Columna con la URL normalizada
URL_COLUMN = "normalized_url"

# Columna con el nombre del paper
PAPER_COLUMN = "paper"

# Si quieres usar solo GAP-KGE en vez de la predicción final,
# cambia USE_FINAL_LABEL a False.
USE_FINAL_LABEL = True

# Si USE_FINAL_LABEL = False, se usará esta columna:
GAP_KGE_COLUMN = "heuristica_3_gap_kge_matched"

# Generar también un YAML global con todos los papers juntos
GENERATE_GLOBAL_YAML = True


# =========================
# UTILIDADES
# =========================
def clean_text(value: str) -> str:
    if value is None:
        return ""
    return str(value).strip()


def safe_filename(name: str) -> str:
    """
    Convierte el nombre del paper en un nombre seguro de fichero.
    """
    name = clean_text(name)
    if not name:
        name = "paper_without_name"

    # Quitar extensión .pdf si existe
    if name.lower().endswith(".pdf"):
        name = name[:-4]

    # Sustituir caracteres raros
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)
    name = name.strip("._-")

    return name[:120] or "paper_without_name"


def normalize_url(url: str) -> str:
    url = clean_text(url)
    if not url:
        return ""

    # Quitar espacios y caracteres finales raros frecuentes al extraer de PDFs
    url = url.strip().rstrip(".,;)]}")

    return url


def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def is_true(value) -> bool:
    """
    Convierte valores del CSV a booleano.
    Acepta True, true, 1, yes, dataset, etc.
    """
    if value is None:
        return False

    value = str(value).strip().lower()
    return value in {"true", "1", "yes", "y", "dataset", "si", "sí"}


def is_dataset_label(value) -> bool:
    return str(value).strip().lower() == "dataset"


def is_code_repository(url: str) -> bool:
    """
    URLs de código que ya2ro debería meter en software, no en datasets.
    """
    domain = get_domain(url)
    return (
        domain == "github.com"
        or domain.endswith(".github.com")
        or domain == "gitlab.com"
        or domain.endswith(".gitlab.com")
        or domain == "bitbucket.org"
        or domain == "sourceforge.net"
    )


def is_doi_url(url: str) -> bool:
    domain = get_domain(url)
    return domain in {"doi.org", "dx.doi.org"}


def doi_from_url(url: str) -> str:
    """
    Convierte https://doi.org/10.xxxx/xxxx en 10.xxxx/xxxx.
    Si no es DOI, devuelve cadena vacía.
    """
    if not is_doi_url(url):
        return ""

    try:
        parsed = urlparse(url)
        doi = parsed.path.strip("/")
        return doi
    except Exception:
        return ""


def make_dataset_entry(url: str) -> dict:
    """
    Formato compatible con ya2ro.
    Para DOI usa 'doi'. Para URL normal usa 'link'.
    """
    doi = doi_from_url(url)
    if doi:
        return {"doi": doi}
    return {"link": url}


def make_software_entry(url: str) -> dict:
    return {"link": url}


def unique_preserve_order(items):
    seen = set()
    result = []
    for item in items:
        marker = repr(item)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


# =========================
# LECTURA DEL CSV
# =========================
def should_include_row(row: dict) -> bool:
    """
    Decide si una URL debe ir al YAML.
    Por defecto usa label == dataset.
    Si USE_FINAL_LABEL = False, usa heuristica_3_gap_kge_matched.
    """
    if USE_FINAL_LABEL:
        return is_dataset_label(row.get(LABEL_COLUMN, ""))

    return is_true(row.get(GAP_KGE_COLUMN, ""))


def load_detected_resources(csv_path: Path) -> dict:
    """
    Devuelve un diccionario:
    {
      paper_name: {
        "datasets": [url1, url2, ...],
        "software": [url3, ...],
        "rows": [...]
      }
    }
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"No existe el archivo de entrada: {csv_path}")

    grouped = {}

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        required_columns = {PAPER_COLUMN, URL_COLUMN, LABEL_COLUMN}
        missing = required_columns - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "Faltan columnas en el CSV: " + ", ".join(sorted(missing))
            )

        for row in reader:
            if not should_include_row(row):
                continue

            paper = clean_text(row.get(PAPER_COLUMN, "")) or "paper_without_name"
            url = normalize_url(row.get(URL_COLUMN, ""))

            if not url:
                continue

            grouped.setdefault(paper, {
                "datasets": [],
                "software": [],
                "rows": []
            })

            if is_code_repository(url):
                grouped[paper]["software"].append(url)
            else:
                grouped[paper]["datasets"].append(url)

            grouped[paper]["rows"].append(row)

    # Quitar duplicados conservando orden
    for paper in grouped:
        grouped[paper]["datasets"] = unique_preserve_order(grouped[paper]["datasets"])
        grouped[paper]["software"] = unique_preserve_order(grouped[paper]["software"])

    return grouped


# =========================
# CREACIÓN DEL YAML
# =========================
def build_paper_yaml(paper_name: str, datasets: list, software: list) -> dict:
    """
    Construye un YAML de tipo paper compatible con ya2ro.
    """
    paper_title = clean_text(paper_name)
    if paper_title.lower().endswith(".pdf"):
        paper_title = paper_title[:-4]

    data = {
        "type": "paper",
        "title": paper_title or "Research Object generated from paper",
        "summary": (
            "Research Object generated automatically from URLs extracted "
            "from a scientific paper and classified as datasets by the heuristic system."
        ),
        "datasets": [make_dataset_entry(url) for url in datasets],
        "software": [make_software_entry(url) for url in software],
        "bibliography": [],
        "authors": []
    }

    return data


def build_global_yaml(grouped: dict) -> dict:
    """
    Construye un YAML global con todos los recursos detectados.
    Útil si quieres crear un único RO con todo.
    """
    all_datasets = []
    all_software = []

    for paper_data in grouped.values():
        all_datasets.extend(paper_data["datasets"])
        all_software.extend(paper_data["software"])

    all_datasets = unique_preserve_order(all_datasets)
    all_software = unique_preserve_order(all_software)

    return {
        "type": "paper",
        "title": "Research Object generated from detected dataset URLs",
        "summary": (
            "Global Research Object generated automatically from all URLs "
            "classified as datasets by the heuristic system."
        ),
        "datasets": [make_dataset_entry(url) for url in all_datasets],
        "software": [make_software_entry(url) for url in all_software],
        "bibliography": [],
        "authors": []
    }


def write_yaml(data: dict, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if yaml is None:
        raise ImportError(
            "No tienes instalado PyYAML. Instálalo con: pip install pyyaml"
        )

    with open(output_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False
        )


# =========================
# RESUMEN
# =========================
def write_summary(grouped: dict, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "paper",
        "yaml_file",
        "dataset_count",
        "software_count",
        "dataset_urls",
        "software_urls"
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for paper, data in grouped.items():
            filename = safe_filename(paper) + ".yaml"
            writer.writerow({
                "paper": paper,
                "yaml_file": str(OUTPUT_DIR / filename),
                "dataset_count": len(data["datasets"]),
                "software_count": len(data["software"]),
                "dataset_urls": " | ".join(data["datasets"]),
                "software_urls": " | ".join(data["software"]),
            })


# =========================
# MAIN
# =========================
def main():
    try:
        grouped = load_detected_resources(INPUT_CSV)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    if not grouped:
        print("No se encontraron URLs con label = dataset.")
        print(f"Revisa el archivo: {INPUT_CSV}")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    generated_files = []

    for paper, data in grouped.items():
        yaml_data = build_paper_yaml(
            paper_name=paper,
            datasets=data["datasets"],
            software=data["software"]
        )

        output_file = OUTPUT_DIR / f"{safe_filename(paper)}.yaml"
        write_yaml(yaml_data, output_file)
        generated_files.append(output_file)

    if GENERATE_GLOBAL_YAML:
        global_yaml = build_global_yaml(grouped)
        global_output = OUTPUT_DIR / "all_papers.yaml"
        write_yaml(global_yaml, global_output)
        generated_files.append(global_output)

    summary_file = OUTPUT_DIR / "summary.csv"
    write_summary(grouped, summary_file)

    print("YAMLs generados correctamente.")
    print(f"Carpeta de salida: {OUTPUT_DIR}")
    print(f"Número de papers con datasets: {len(grouped)}")
    print(f"Número de YAMLs generados: {len(generated_files)}")
    print(f"Resumen: {summary_file}")
    print("\nArchivos generados:")
    for path in generated_files:
        print(f"- {path}")


if __name__ == "__main__":
    main()
