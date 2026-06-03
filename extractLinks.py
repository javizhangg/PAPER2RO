# extract_links_improved.py
# Extrae URLs y DOIs desde PDFs usando GROBID TEI.
# Mejoras principales:
# - extracción más robusta de URLs rotas por espacios/saltos de línea
# - limpieza de HTML entities (&amp;, &lt;, &gt;)
# - captura de DOI en forma pura, doi:10..., https://doi.org/...
# - elimina duplicados por paper de forma estable
# - guarda contexto cercano para poder aplicar heurísticas de dataset después

import argparse
import csv
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

NS = {"tei": "http://www.tei-c.org/ns/1.0"}

DEFAULT_EXCLUDED_EXACT_URLS = {
    "https://github.com/kermitt2/grobid",
    "http://github.com/kermitt2/grobid",
}

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
    (?:doi:\s*|https?://(?:dx\.)?doi\.org/)?
    (?P<doi>10\.\d{4,9}/[^\s<>"'\\]+)
    """
)


def check_url_alive(url: str, timeout: int = 5) -> bool:
    try:
        response = requests.get(url, timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        return False


def resolve_grobid_url(explicit_url: str | None = None) -> str:
    if explicit_url:
        return explicit_url.rstrip("/") + "/api/processFulltextDocument"

    candidates = [
        "http://grobid:8070",
        "http://localhost:8070",
    ]

    for base in candidates:
        if check_url_alive(base + "/api/isalive"):
            return base + "/api/processFulltextDocument"

    raise ConnectionError(
        "No encuentro GROBID. Arranca GROBID en Docker/local o usa --grobid-base-url."
    )


def process_pdf(pdf_path: Path, grobid_process_url: str, timeout: int = 180) -> str | None:
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
        print(f"[ERROR] No se pudo conectar con GROBID para {pdf_path.name}: {e}")
        return None

    if response.status_code != 200:
        print(f"[ERROR] GROBID falló con {pdf_path.name}: HTTP {response.status_code}")
        return None

    return response.text


def normalize_text_for_extraction(text: str) -> str:
    """
    Prepara texto de TEI para detectar URLs/DOIs.
    No normaliza la URL final; solo arregla casos típicos de PDFs/GROBID.
    """
    if not text:
        return ""

    text = html.unescape(text)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("\r", " ").replace("\n", " ")

    # https:// example.com -> https://example.com
    text = re.sub(r"(https?://|ftp://|www\.)\s+", r"\1", text, flags=re.I)

    # Repara roturas típicas dentro de URL:
    # https://example.com/ dataset.zip -> https://example.com/dataset.zip
    # https://doi.org/10.1145/ 123456 -> https://doi.org/10.1145/123456
    for _ in range(3):
        text = re.sub(
            r"((?:https?://|ftp://|www\.)[^\s<>'\"]*[\/#?&=._~%-])\s+([A-Za-z0-9._~:/?#@!$&()*+,;=%-]+)",
            r"\1\2",
            text,
            flags=re.I,
        )

    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_external_url(value: str) -> bool:
    return bool(
        value
        and value.strip().lower().startswith(
            ("http://", "https://", "ftp://", "www.")
        )
    )


def strip_balanced_trailing_punctuation(value: str) -> str:
    """
    Quita puntuación final sin cargarse paréntesis que pueden pertenecer a un DOI/URL.
    """
    value = value.strip().strip("<>[]{}\"'")
    value = value.rstrip(".,;:!?\"'•·")

    pairs = [("(", ")"), ("[", "]"), ("{", "}")]
    changed = True

    while changed and value:
        changed = False

        for left, right in pairs:
            if value.endswith(right) and value.count(right) > value.count(left):
                value = value[:-1].rstrip(".,;:!?\"'•·")
                changed = True

    return value


def clean_extracted_link(link: str, excluded_exact_urls: set[str]) -> str | None:
    if not link:
        return None

    link = html.unescape(str(link)).strip()
    link = link.replace("\u200b", "").replace("\ufeff", "")
    link = strip_balanced_trailing_punctuation(link)

    if not is_external_url(link):
        return None

    normalized_for_compare = link.lower().rstrip("/")
    excluded = {x.lower().rstrip("/") for x in excluded_exact_urls}

    if normalized_for_compare in excluded:
        return None

    return link


def clean_extracted_doi(raw: str) -> str | None:
    if not raw:
        return None

    raw = html.unescape(str(raw)).strip()
    raw = raw.replace("\u200b", "").replace("\ufeff", "")
    raw = strip_balanced_trailing_punctuation(raw)

    match = DOI_REGEX.search(raw)

    if not match:
        return None

    doi = strip_balanced_trailing_punctuation(match.group("doi"))

    # Evita DOIs claramente rotos o que terminan en palabras pegadas del texto.
    doi = re.sub(
        r"(?i)(?:\.?accessed|\.?available|\.?retrieved|\.?figure|\.?table|\.?section).*$",
        "",
        doi,
    )

    doi = strip_balanced_trailing_punctuation(doi)

    if not re.fullmatch(r"10\.\d{4,9}/.+", doi, flags=re.I):
        return None

    return doi


def get_context(text: str, start: int, end: int, window: int = 140) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    context = text[left:right]
    return re.sub(r"\s+", " ", context).strip()


def extract_urls_from_text(text: str) -> list[dict]:
    """
    Devuelve elementos:
    {
      "link": "...",
      "kind": "url" | "doi",
      "context": "texto cercano"
    }
    """
    text = normalize_text_for_extraction(text)

    results: list[dict] = []
    url_spans: list[tuple[int, int]] = []

    # URLs normales
    for match in URL_REGEX.finditer(text):
        raw = match.group(0)
        clean = clean_extracted_link(raw, DEFAULT_EXCLUDED_EXACT_URLS)

        if clean:
            url_spans.append(match.span())
            results.append(
                {
                    "link": clean,
                    "kind": "url",
                    "context": get_context(text, match.start(), match.end()),
                }
            )

    # DOI puros. Si el DOI ya estaba dentro de una URL capturada, no lo duplicamos.
    for match in DOI_REGEX.finditer(text):
        span = match.span()

        if any(span[0] >= a and span[1] <= b for a, b in url_spans):
            continue

        clean = clean_extracted_doi(match.group(0))

        if clean:
            results.append(
                {
                    "link": clean,
                    "kind": "doi",
                    "context": get_context(text, match.start(), match.end()),
                }
            )

    return results


def safe_itertext(element: ET.Element) -> str:
    return "".join(element.itertext()) if element is not None else ""


def add_unique(
    links: list[dict],
    seen: set[str],
    paper: str,
    section: str,
    raw_link: str,
    kind: str,
    context: str = "",
) -> None:
    clean = clean_extracted_link(raw_link, DEFAULT_EXCLUDED_EXACT_URLS)

    if not clean:
        clean = clean_extracted_doi(raw_link)

    if not clean:
        return

    key = clean.lower().rstrip("/")

    if key in seen:
        return

    seen.add(key)

    links.append(
        {
            "paper": paper,
            "section": section,
            "kind": kind,
            "link": clean,
            "context": re.sub(r"\s+", " ", context).strip(),
        }
    )


def extract_links_from_tei(tei_xml: str, filename: str) -> list[dict]:
    root = ET.fromstring(tei_xml)

    links: list[dict] = []
    seen: set[str] = set()

    # 1) Atributos target/ref de TEI.
    for elem in root.findall(".//*[@target]"):
        target = elem.attrib.get("target", "").strip()

        if target:
            add_unique(
                links=links,
                seen=seen,
                paper=filename,
                section="target",
                raw_link=target,
                kind="url" if is_external_url(target) else "doi",
                context=safe_itertext(elem),
            )

    # 2) idno estructurados: DOI, URI, URL, arXiv, etc.
    for elem in root.findall(".//tei:idno", NS):
        id_type = elem.attrib.get("type", "").lower().strip()
        value = safe_itertext(elem).strip()

        if not value:
            continue

        if id_type == "doi":
            add_unique(
                links=links,
                seen=seen,
                paper=filename,
                section="idno_doi",
                raw_link=value,
                kind="doi",
                context=value,
            )

        elif id_type in {"url", "uri", "arxiv"}:
            add_unique(
                links=links,
                seen=seen,
                paper=filename,
                section=f"idno_{id_type}",
                raw_link=value,
                kind="url",
                context=value,
            )

        for item in extract_urls_from_text(value):
            add_unique(
                links=links,
                seen=seen,
                paper=filename,
                section="idno_text",
                raw_link=item["link"],
                kind=item["kind"],
                context=item["context"],
            )

    # 3) Texto de secciones importantes.
    sections = {
        "front": ".//tei:front",
        "abstract": ".//tei:abstract",
        "body": ".//tei:body",
        "note": ".//tei:note",
        "reference": ".//tei:listBibl",
        "back": ".//tei:back",
        "table": ".//tei:table",
        "figure": ".//tei:figure",
    }

    for section, path in sections.items():
        for element in root.findall(path, NS):
            text = safe_itertext(element)

            for item in extract_urls_from_text(text):
                add_unique(
                    links=links,
                    seen=seen,
                    paper=filename,
                    section=section,
                    raw_link=item["link"],
                    kind=item["kind"],
                    context=item["context"],
                )

    return links


def save_outputs(all_links: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / "all_links.csv"
    json_path = output_dir / "all_links.json"

    fields = ["paper", "section", "kind", "link", "context"]

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_links)

    grouped: dict[str, list[dict]] = {}

    for row in all_links:
        grouped.setdefault(row["paper"], []).append(
            {
                "section": row["section"],
                "kind": row["kind"],
                "link": row["link"],
                "context": row["context"],
            }
        )

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(grouped, f, indent=2, ensure_ascii=False)

    print(f"[OK] Guardadas {len(all_links)} URLs/DOIs en {csv_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-dir", default="pdfs")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument(
        "--grobid-base-url",
        default=None,
        help="Ejemplo: http://localhost:8070",
    )
    parser.add_argument("--timeout", type=int, default=180)

    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir)
    output_dir = Path(args.output_dir)

    if not pdf_dir.is_dir():
        raise FileNotFoundError(f"No existe la carpeta de PDFs: {pdf_dir}")

    grobid_process_url = resolve_grobid_url(args.grobid_base_url)
    print(f"[INFO] Usando GROBID: {grobid_process_url}")

    all_links: list[dict] = []
    pdf_files = sorted(pdf_dir.glob("*.pdf"))

    if not pdf_files:
        print(f"[WARN] No hay PDFs en {pdf_dir}")
        return

    for pdf_path in pdf_files:
        print(f"[INFO] Procesando {pdf_path.name}...")

        tei_xml = process_pdf(
            pdf_path=pdf_path,
            grobid_process_url=grobid_process_url,
            timeout=args.timeout,
        )

        if not tei_xml:
            continue

        try:
            links = extract_links_from_tei(tei_xml, pdf_path.name)
        except ET.ParseError as e:
            print(f"[ERROR] XML mal formado en {pdf_path.name}: {e}")
            continue

        print(f"       -> {len(links)} URLs/DOIs extraídos")
        all_links.extend(links)

    save_outputs(all_links, output_dir)
    print("[DONE]")


if __name__ == "__main__":
    main()