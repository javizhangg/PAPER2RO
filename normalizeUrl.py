# normalize_urls.py
# Limpia URLs, elimina duplicados y muestra cuáles se eliminaron

import csv
import json
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

INPUT_CSV = "outputs/all_links.csv"
OUTPUT_CSV = "outputs/all_links_normalized.csv"
OUTPUT_JSON = "outputs/all_links_normalized.json"
REMOVED_CSV = "outputs/removed_urls.csv"

DATA_EXTENSIONS = {
    ".csv", ".tsv", ".json", ".xml", ".rdf",
    ".xlsx", ".xls", ".parquet", ".h5", ".hdf5"
}


# ==================================
# Limpieza básica
# ==================================
def clean_url(url: str) -> str:
    if not url:
        return ""

    url = url.strip()
    url = url.replace(" ", "") #Nos sirve para eliminar los espacion en las urs
    url = url.rstrip(".,;:)]}>\"'") #Nos elimina cualquier simbolo al final de la url ya que al estar en un parrafo puede llegar a tener puntos o parentesis o etc.

    return url


# ==================================
# DOI
# ==================================
def normalize_doi(url: str) -> str:
    if not url:
        return ""

    low = url.lower()

    if low.startswith("doi:"):
        doi = url[4:].strip()
        return f"https://doi.org/{doi}"

    if "dx.doi.org/" in low:
        doi = url.split("dx.doi.org/")[-1]
        return f"https://doi.org/{doi}"

    return url


# ==================================
# Normalización completa
# ==================================
def normalize_url(url: str) -> str:
    url = clean_url(url)
    url = normalize_doi(url)

    if not url:
        return ""

    try:
        parsed = urlparse(url)

        scheme = parsed.scheme.lower() if parsed.scheme else "https"
        netloc = parsed.netloc.lower()
        path = parsed.path

        if not netloc and path:
            reparsed = urlparse("https://" + url)
            netloc = reparsed.netloc.lower()
            path = reparsed.path

        params = parse_qsl(parsed.query, keep_blank_values=True)
        params.sort()
        query = urlencode(params)

        normalized = urlunparse((
            scheme,
            netloc,
            path,
            parsed.params,
            query,
            ""
        ))

        return normalized

    except:
        return url


# ==================================
# Utilidades
# ==================================
def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except:
        return ""


def get_extension(url: str) -> str:
    try:
        path = urlparse(url).path.lower()
        m = re.search(r"(\.[a-z0-9]+)$", path)
        if m:
            return m.group(1)
    except:
        pass
    return ""


def is_data_extension(ext: str) -> bool:
    return ext in DATA_EXTENSIONS


# ==================================
# Leer CSV original
# ==================================
def load_csv(path):
    rows = []

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            rows.append({
                "paper": row.get("paper", "").strip(),
                "section": row.get("section", "").strip(),
                "url": row.get("link", "").strip() or row.get("url", "").strip()
            })

    return rows


# ==================================
# Procesar
# ==================================
def process_rows(rows):
    seen = set()
    kept = []
    removed = []

    for row in rows:
        raw_url = row["url"]
        norm = normalize_url(raw_url)

        if not norm:
            removed.append({
                "paper": row["paper"],
                "section": row["section"],
                "original_url": raw_url,
                "reason": "empty_after_cleaning"
            })
            continue

        if norm in seen:
            removed.append({
                "paper": row["paper"],
                "section": row["section"],
                "original_url": raw_url,
                "normalized_url": norm,
                "reason": "duplicate"
            })
            continue

        seen.add(norm)

        domain = get_domain(norm)
        ext = get_extension(norm)

        kept.append({
            "paper": row["paper"],
            "section": row["section"],
            "original_url": raw_url,
            "normalized_url": norm,
            "domain": domain,
            "extension": ext,
            "is_data_extension": is_data_extension(ext)
        })

    return kept, removed


# ==================================
# Guardar CSV
# ==================================
def save_csv(rows, path):
    if not rows:
        return

    fields = rows[0].keys()

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


# ==================================
# Guardar JSON
# ==================================
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

    rows = load_csv(INPUT_CSV)
    print(f"URLs originales: {len(rows)}")

    kept, removed = process_rows(rows)

    print(f"URLs conservadas: {len(kept)}")
    print(f"URLs eliminadas: {len(removed)}")

    save_csv(kept, OUTPUT_CSV)
    save_json(kept, OUTPUT_JSON)
    save_csv(removed, REMOVED_CSV)

    print(f"\nGuardados:")
    print(f"- {OUTPUT_CSV}")
    print(f"- {OUTPUT_JSON}")
    print(f"- {REMOVED_CSV}")

    # Mostrar por consola las eliminadas
    if removed:
        print("\n=== URLs ELIMINADAS ===")
        for r in removed:
            print(f"[{r['reason']}] {r['original_url']}")

if __name__ == "__main__":
    main()