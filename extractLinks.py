import os
import json
import re
import requests
import xml.etree.ElementTree as ET
import pandas as pd

# =========================
# GROBID CONFIG
# =========================
GROBID_DOCKER_URL = "http://grobid:8070/api/isalive"
GROBID_LOCAL_URL = "http://localhost:8070/api/isalive"


def check_grobid_status(url: str) -> bool:
    try:
        response = requests.get(url, timeout=5)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


if check_grobid_status(GROBID_DOCKER_URL):
    GROBID_URL = "http://grobid:8070/api/processFulltextDocument"
    print(f"Using GROBID at {GROBID_URL}")
elif check_grobid_status(GROBID_LOCAL_URL):
    GROBID_URL = "http://localhost:8070/api/processFulltextDocument"
    print(f"Using GROBID at {GROBID_URL}")
else:
    raise ConnectionError("No instance of GROBID (Docker or Local) is available!")


# =========================
# PATHS
# =========================
PDF_DIR = "pdfs/"
OUTPUT_DIR = "outputs/"
os.makedirs(OUTPUT_DIR, exist_ok=True)

NS = {"tei": "http://www.tei-c.org/ns/1.0"}

links_per_paper = {}


# =========================
# FILTERS
# =========================
EXCLUDED_EXACT_URLS = {
    "https://github.com/kermitt2/grobid",
    "http://github.com/kermitt2/grobid",
}

URL_REGEX = re.compile(
    r'(?:(?:https?://)|(?:ftp://)|(?:www\.))[^\s<>"\'\]\[{}|\\^`]+',
    re.IGNORECASE
)

DOI_REGEX = re.compile(
    r'\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b',
    re.IGNORECASE
)


# =========================
# GROBID PROCESSING
# =========================
def process_pdf(pdf_path: str) -> str | None:
    with open(pdf_path, "rb") as pdf_file:
        files = {"input": pdf_file}
        data = {
            "includeRawCitations": "1",
            "includeRawAffiliations": "1",
            "teiCoordinates": "ref,biblStruct,note,figure"
        }

        try:
            response = requests.post(GROBID_URL, files=files, data=data, timeout=180)
        except requests.exceptions.RequestException as e:
            print(f"Error connecting to GROBID for {pdf_path}: {e}")
            return None

    if response.status_code == 200:
        return response.text

    print(f"Error processing {pdf_path}: {response.status_code}")
    return None


# =========================
# URL EXTRACTION HELPERS
# =========================
def normalize_text_for_extraction(text: str) -> str:
    """
    Solo prepara el texto para poder detectar URLs.
    NO normaliza URLs.
    NO cambia http/https.
    NO cambia www.
    NO convierte DOI.
    """

    if not text:
        return ""

    text = text.replace("\r", " ")
    text = text.replace("\n", " ")

    # Repara casos como:
    # https:// example.com -> https://example.com
    # www. example.com -> www.example.com
    text = re.sub(r'(https?://)\s+', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'(ftp://)\s+', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'(www\.)\s+', r'\1', text, flags=re.IGNORECASE)

    # Compacta espacios normales
    text = re.sub(r'\s+', ' ', text)

    return text


def is_external_url(value: str) -> bool:
    if not value:
        return False

    value = value.strip()

    if value.startswith("#"):
        return False

    return value.lower().startswith(("http://", "https://", "ftp://", "www."))


def is_excluded_url(url: str) -> bool:
    if not url:
        return True

    u = url.strip().lower().rstrip("/")
    excluded = {x.lower().rstrip("/") for x in EXCLUDED_EXACT_URLS}

    return u in excluded


def clean_extracted_link(link: str) -> str | None:
    """
    Limpieza mínima para no guardar basura obvia.
    La normalización real va en normalizeUrl.py.
    """

    if not link:
        return None

    link = link.strip()

    # Quita caracteres de cierre típicos que no pertenecen al link
    link = link.strip(" \t\n\r<>()[]{}\"'")
    link = link.rstrip(".,;:!?)]}>'\"")

    if not is_external_url(link):
        return None

    if is_excluded_url(link):
        return None

    return link


def clean_extracted_doi(doi: str) -> str | None:
    """
    Extrae DOI tal cual, sin convertirlo a https://doi.org/.
    """

    if not doi:
        return None

    doi = doi.strip()
    doi = doi.strip(" \t\n\r<>()[]{}\"'")
    doi = doi.rstrip(".,;:!?)]}>'\"")

    if not DOI_REGEX.fullmatch(doi):
        return None

    return doi


def extract_urls_from_text(text: str) -> list[str]:
    text = normalize_text_for_extraction(text)

    links = []

    # URLs normales
    for match in URL_REGEX.finditer(text):
        url = match.group(0)
        clean_url = clean_extracted_link(url)

        if clean_url:
            links.append(clean_url)

    # DOI puros
    for match in DOI_REGEX.finditer(text):
        doi = match.group(0)
        clean_doi = clean_extracted_doi(doi)

        if clean_doi:
            links.append(clean_doi)

    return links


# =========================
# TEI EXTRACTION
# =========================
def extract_links(tei_xml: str, filename: str) -> None:
    root = ET.fromstring(tei_xml)

    links = []
    seen = set()

    def add_link(section: str, link: str) -> None:
        if not link:
            return

        link = link.strip()

        clean_link = clean_extracted_link(link)

        # Si no es URL normal, puede ser DOI puro
        if not clean_link:
            clean_link = clean_extracted_doi(link)

        if not clean_link:
            return

        key = clean_link.lower()

        if key in seen:
            return

        seen.add(key)

        links.append({
            "section": section,
            "link": clean_link
        })

    # 1) Extraer todos los atributos target del XML
    for elem in root.findall(".//*[@target]"):
        target = elem.attrib.get("target", "").strip()

        if is_external_url(target):
            add_link("target", target)

    # 2) Extraer DOI estructurados de GROBID
    for elem in root.findall(".//tei:idno", NS):
        id_type = elem.attrib.get("type", "").lower().strip()
        value = "".join(elem.itertext()).strip()

        if id_type == "doi" and value:
            add_link("doi", value)

        for link in extract_urls_from_text(value):
            add_link("idno", link)

    # 3) Extraer URLs y DOI del texto plano de secciones relevantes
    sections = {
        "front": ".//tei:front",
        "abstract": ".//tei:abstract",
        "body": ".//tei:body",
        "note": ".//tei:note",
        "reference": ".//tei:listBibl",
        "back": ".//tei:back",
        "table": ".//tei:table"
    }

    for section, path in sections.items():
        for element in root.findall(path, NS):
            text = "".join(element.itertext())

            for link in extract_urls_from_text(text):
                add_link(section, link)

    links_per_paper[filename] = links


# =========================
# MAIN
# =========================
def process_all_pdfs() -> None:
    if not os.path.isdir(PDF_DIR):
        raise FileNotFoundError(f"PDF directory not found: {PDF_DIR}")

    pdf_files = sorted([
        f for f in os.listdir(PDF_DIR)
        if f.lower().endswith(".pdf")
    ])

    if not pdf_files:
        print(f"No PDF files found in '{PDF_DIR}'")
        return

    for pdf in pdf_files:
        pdf_path = os.path.join(PDF_DIR, pdf)
        print(f"Processing {pdf_path}...")

        tei_xml = process_pdf(pdf_path)

        if tei_xml:
            try:
                extract_links(tei_xml, pdf)
                print(f"  -> extracted {len(links_per_paper.get(pdf, []))} links")
            except ET.ParseError as e:
                print(f"  -> XML parse error in {pdf}: {e}")
        else:
            print(f"  -> skipped {pdf}")


def save_outputs() -> None:
    all_links = []

    for paper, links in links_per_paper.items():
        for item in links:
            all_links.append({
                "paper": paper,
                "section": item["section"],
                "link": item["link"]
            })

    links_df = pd.DataFrame(all_links, columns=["paper", "section", "link"])

    output_csv = os.path.join(OUTPUT_DIR, "all_links.csv")
    output_json = os.path.join(OUTPUT_DIR, "all_links.json")

    links_df.to_csv(output_csv, index=False, encoding="utf-8")

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(links_per_paper, f, indent=4, ensure_ascii=False)

    print(f"Saved {len(all_links)} links to '{OUTPUT_DIR}'")


if __name__ == "__main__":
    process_all_pdfs()
    save_outputs()
    print("Done.")