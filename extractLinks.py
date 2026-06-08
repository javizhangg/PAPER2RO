# extractLinks.py
# Workflow sencillo:
# - Lee todos los PDF de la carpeta pdfs/
# - Extrae URLs http, https, www y DOIs
# - Convierte DOIs puros a https://doi.org/...
# - Guarda SOLO dos columnas: pdf, url
#
# Uso:
#   py extractLinks.py
#
# Salida:
#   outputs/all_links.csv

import argparse
import csv
import html
import re
from pathlib import Path

import requests

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


# ==============================
# REGEX
# ==============================

URL_REGEX = re.compile(
    r"""(?ix)
    (?:
        https?://
        | ftp://
        | www\.
    )
    [^\s<>"'\]\[{}|\\^`]+
    """
)

DOI_REGEX = re.compile(
    r"""(?ix)
    (?:
        doi:\s*
        |
        https?://(?:dx\.)?doi\.org/
    )?
    (?P<doi>10\.\d{4,9}/[^\s<>"'\\]+)
    """
)


# ==============================
# GROBID
# ==============================

def check_url_alive(url: str, timeout: int = 5) -> bool:
    try:
        response = requests.get(url, timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        return False


def resolve_grobid_url(explicit_url: str | None = None) -> str | None:
    if explicit_url:
        return explicit_url.rstrip("/") + "/api/processFulltextDocument"

    candidates = [
        "http://grobid:8070",
        "http://localhost:8070",
    ]

    for base in candidates:
        if check_url_alive(base + "/api/isalive"):
            return base + "/api/processFulltextDocument"

    return None


def process_pdf_with_grobid(
    pdf_path: Path,
    grobid_process_url: str,
    timeout: int = 180,
) -> str | None:
    data = {
        "includeRawCitations": "1",
        "includeRawAffiliations": "1",
        "teiCoordinates": "ref,biblStruct,note,figure",
    }

    try:
        with pdf_path.open("rb") as pdf_file:
            files = {"input": pdf_file}
            response = requests.post(
                grobid_process_url,
                files=files,
                data=data,
                timeout=timeout,
            )
    except requests.RequestException as e:
        print(f"[WARN] GROBID no pudo procesar {pdf_path.name}: {e}")
        return None

    if response.status_code != 200:
        print(f"[WARN] GROBID falló con {pdf_path.name}: HTTP {response.status_code}")
        return None

    return response.text


# ==============================
# TEXTO PDF
# ==============================

def extract_text_with_pymupdf(pdf_path: Path) -> str:
    if fitz is None:
        print("[WARN] PyMuPDF no está instalado. Ejecuta: pip install pymupdf")
        return ""

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"[WARN] No se pudo abrir {pdf_path.name}: {e}")
        return ""

    all_text = []

    for page_index in range(len(doc)):
        try:
            page = doc[page_index]
            text = page.get_text("text")
            all_text.append(text)
        except Exception as e:
            print(f"[WARN] Error leyendo página {page_index + 1} de {pdf_path.name}: {e}")

    doc.close()

    return "\n".join(all_text)


# ==============================
# LIMPIEZA Y NORMALIZACIÓN
# ==============================

def normalize_text_for_extraction(text: str) -> str:
    if not text:
        return ""

    text = html.unescape(text)

    text = text.replace("\u200b", "")
    text = text.replace("\ufeff", "")
    text = text.replace("\u00ad", "")
    text = text.replace("\xa0", " ")

    text = text.replace("\r", " ")
    text = text.replace("\n", " ")

    # Arregla espacios raros en protocolos:
    # https : // ejemplo.com -> https://ejemplo.com
    text = re.sub(r"http\s*:\s*/\s*/\s*", "http://", text, flags=re.I)
    text = re.sub(r"https\s*:\s*/\s*/\s*", "https://", text, flags=re.I)
    text = re.sub(r"ftp\s*:\s*/\s*/\s*", "ftp://", text, flags=re.I)

    # https:// ejemplo.com -> https://ejemplo.com
    text = re.sub(r"(https?://|ftp://|www\.)\s+", r"\1", text, flags=re.I)

    # Repara URLs cortadas por espacios o saltos de línea.
    # Ejemplo:
    # https://zenodo.org/ records/12345
    # pasa a:
    # https://zenodo.org/records/12345
    for _ in range(10):
        text = re.sub(
            r"((?:https?://|ftp://|www\.)[^\s<>'\"]*[\/#?&=._~:%-])\s+([A-Za-z0-9._~:/?#@!$&()*+,;=%-]+)",
            r"\1\2",
            text,
            flags=re.I,
        )

    # Repara DOI partido:
    # 10.5194/ essd-17-7359-2025
    # pasa a:
    # 10.5194/essd-17-7359-2025
    for _ in range(6):
        text = re.sub(
            r"(10\.\d{4,9}/[A-Za-z0-9._;()/:+-]*)\s+([A-Za-z0-9._;()/:+-]+)",
            r"\1\2",
            text,
            flags=re.I,
        )

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def strip_trailing_punctuation(value: str) -> str:
    if not value:
        return ""

    value = value.strip()
    value = value.strip("<>[]{}\"'")

    value = value.rstrip(".,;:!?\"'•·")

    pairs = [
        ("(", ")"),
        ("[", "]"),
        ("{", "}"),
    ]

    changed = True

    while changed and value:
        changed = False

        for left, right in pairs:
            if value.endswith(right) and value.count(right) > value.count(left):
                value = value[:-1]
                value = value.rstrip(".,;:!?\"'•·")
                changed = True

    return value.strip()


def clean_url(raw_url: str) -> str | None:
    if not raw_url:
        return None

    url = html.unescape(str(raw_url)).strip()

    url = url.replace("\u200b", "")
    url = url.replace("\ufeff", "")
    url = url.replace("\u00ad", "")
    url = url.replace("\\", "")

    url = strip_trailing_punctuation(url)

    if not url:
        return None

    if url.lower().startswith("www."):
        url = "https://" + url

    if not url.lower().startswith(("http://", "https://", "ftp://")):
        return None

    return url


def clean_doi(raw: str) -> str | None:
    if not raw:
        return None

    raw = html.unescape(str(raw)).strip()

    raw = raw.replace("\u200b", "")
    raw = raw.replace("\ufeff", "")
    raw = raw.replace("\u00ad", "")

    raw = strip_trailing_punctuation(raw)

    match = DOI_REGEX.search(raw)

    if not match:
        return None

    doi = match.group("doi")
    doi = strip_trailing_punctuation(doi)

    # Corta basura típica pegada al DOI
    doi = re.sub(
        r"(?i)(?:\.?accessed|\.?available|\.?retrieved|\.?figure|\.?table|\.?section|\.?supplementary|\.?appendix).*$",
        "",
        doi,
    )

    doi = strip_trailing_punctuation(doi)

    if not re.fullmatch(r"10\.\d{4,9}/.+", doi, flags=re.I):
        return None

    return f"https://doi.org/{doi}"


# ==============================
# EXTRACCIÓN
# ==============================

def extract_links_from_text(text: str, pdf_name: str) -> list[dict]:
    """
    Devuelve solo:
    {
        "pdf": nombre_pdf,
        "url": url_extraida
    }

    No elimina duplicados.
    """
    text = normalize_text_for_extraction(text)

    results = []
    url_spans = []

    # 1. Extraer URLs normales
    for match in URL_REGEX.finditer(text):
        raw = match.group(0)
        url = clean_url(raw)

        if url:
            url_spans.append(match.span())
            results.append({
                "pdf": pdf_name,
                "url": url
            })

    # 2. Extraer DOI puro
    for match in DOI_REGEX.finditer(text):
        start, end = match.span()

        # Si el DOI ya estaba dentro de una URL tipo https://doi.org/..., no lo repetimos desde el mismo texto.
        inside_existing_url = any(start >= a and end <= b for a, b in url_spans)

        if inside_existing_url:
            continue

        raw = match.group(0)
        doi_url = clean_doi(raw)

        if doi_url:
            results.append({
                "pdf": pdf_name,
                "url": doi_url
            })

    return results


def extract_from_pdf(pdf_path: Path, grobid_process_url: str | None, timeout: int) -> list[dict]:
    """
    Intenta extraer enlaces de un PDF usando:
    1. PyMuPDF directamente.
    2. GROBID TEI si está disponible.

    Devuelve solo columnas pdf y url.
    """
    all_links = []

    # Método 1: texto directo del PDF
    pdf_text = extract_text_with_pymupdf(pdf_path)

    if pdf_text:
        links_pymupdf = extract_links_from_text(pdf_text, pdf_path.name)
        all_links.extend(links_pymupdf)
        print(f"       PyMuPDF -> {len(links_pymupdf)} enlaces")
    else:
        print("       PyMuPDF -> 0 enlaces")

    # Método 2: GROBID
    if grobid_process_url:
        tei_xml = process_pdf_with_grobid(
            pdf_path=pdf_path,
            grobid_process_url=grobid_process_url,
            timeout=timeout,
        )

        if tei_xml:
            links_grobid = extract_links_from_text(tei_xml, pdf_path.name)
            all_links.extend(links_grobid)
            print(f"       GROBID  -> {len(links_grobid)} enlaces")
        else:
            print("       GROBID  -> 0 enlaces")
    else:
        print("       GROBID  -> no disponible")

    return all_links


# ==============================
# GUARDADO
# ==============================

def save_csv(rows: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    output_csv = output_dir / "all_links.csv"

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["pdf", "url"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n[OK] CSV guardado en: {output_csv}")
    print(f"[INFO] Total enlaces guardados: {len(rows)}")


# ==============================
# MAIN
# ==============================

def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("--pdf-dir", default="pdfs")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--grobid-base-url",
        default=None,
        help="Ejemplo: http://localhost:8070"
    )

    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir)
    output_dir = Path(args.output_dir)

    if not pdf_dir.exists():
        raise FileNotFoundError(f"No existe la carpeta: {pdf_dir}")

    pdf_files = sorted(pdf_dir.glob("*.pdf"))

    if not pdf_files:
        print(f"[WARN] No hay PDFs en: {pdf_dir}")
        return

    grobid_process_url = resolve_grobid_url(args.grobid_base_url)

    if grobid_process_url:
        print(f"[INFO] GROBID detectado: {grobid_process_url}")
    else:
        print("[WARN] GROBID no detectado. Se usará solo PyMuPDF.")

    all_rows = []

    for pdf_path in pdf_files:
        print(f"\n[INFO] Procesando {pdf_path.name}")

        links = extract_from_pdf(
            pdf_path=pdf_path,
            grobid_process_url=grobid_process_url,
            timeout=args.timeout,
        )

        all_rows.extend(links)

        print(f"       TOTAL   -> {len(links)} enlaces")

    save_csv(all_rows, output_dir)

    print("\n[DONE]")


if __name__ == "__main__":
    main()