import os
import json
import re
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
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

BAD_TRAILING_WORDS = {
    "the", "and", "for", "with", "from", "using", "input", "output",
    "figure", "table", "section", "appendix", "related", "second",
    "first", "third", "introducing", "rethinking", "towards",
    "exploring", "probabilistic", "random", "globalavailability",
    "braindnns", "qanet", "inductive"
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
# URL HELPERS
# =========================
def normalize_text_for_urls(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r", " ")
    text = text.replace("\n", " ")

    # Une palabras cortadas por salto de línea: Ac-\ncessed -> Accessed
    text = re.sub(r'(\w)-\s+(\w)', r'\1\2', text)

    # Compacta espacios
    text = re.sub(r'\s+', ' ', text)

    # Repara separaciones tras esquema
    text = re.sub(r'(https?://)\s+', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'(ftp://)\s+', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'(www\.)\s+', r'\1', text, flags=re.IGNORECASE)

    # Repara separaciones internas comunes
    text = re.sub(r'(?<=\w)\s*/\s*(?=\w)', '/', text)
    text = re.sub(r'(?<=\w)\s*\.\s*(?=\w)', '.', text)
    text = re.sub(r'(?<=\w)\s*-\s*(?=\w)', '-', text)

    return text


def is_probably_external_url(value: str) -> bool:
    if not value:
        return False

    value = value.strip()
    if value.startswith("#"):
        return False

    return value.lower().startswith(("http://", "https://", "ftp://", "www."))


def fix_www_scheme(url: str) -> str:
    if url.lower().startswith("www."):
        return "https://" + url
    return url


def normalize_doi_url(url: str) -> str:
    if not url:
        return url

    m = re.match(r'https?://(?:dx\.)?doi\.org/(.+)', url, flags=re.IGNORECASE)
    if m:
        return "https://doi.org/" + m.group(1)

    return url


def strip_common_trailing_garbage(url: str) -> str:
    if not url:
        return url

    url = url.strip()
    url = url.strip(" \t\n\r<>()[]{}\"'")

    for _ in range(6):
        old = url

        # puntuación final
        url = url.rstrip(".,;:!?)]}>'\"")
        url = url.rstrip("•·")

        # cosas tipo /.Second
        url = re.sub(r'/\.(?:[A-Za-z].*)$', '/', url)

        # Accessed / fechas / años pegados
        url = re.sub(r'\.Ac-?cessed:.*$', '', url, flags=re.IGNORECASE)
        url = re.sub(r'\b(?:Accessed|accessed)\b.*$', '', url)
        url = re.sub(r',\d{4}.*$', '', url)

        # quita palabra basura al final si está tras punto
        url = re.sub(
            r'\.(?:' + "|".join(BAD_TRAILING_WORDS) + r')$',
            '',
            url,
            flags=re.IGNORECASE
        )

        # quita palabra basura al final si está tras barra
        url = re.sub(
            r'(?<=/)(?:' + "|".join(BAD_TRAILING_WORDS) + r')$',
            '',
            url,
            flags=re.IGNORECASE
        )

        if url == old:
            break

    return url


def split_concatenated_urls(candidate: str) -> list[str]:
    if not candidate:
        return []

    starts = list(re.finditer(r'(https?://|ftp://|www\.)', candidate, flags=re.IGNORECASE))
    if not starts:
        return [candidate]

    parts = []
    for i, match in enumerate(starts):
        start_idx = match.start()
        end_idx = starts[i + 1].start() if i + 1 < len(starts) else len(candidate)
        part = candidate[start_idx:end_idx].strip()
        if part:
            parts.append(part)

    return parts


def is_excluded_url(url: str) -> bool:
    if not url:
        return True

    u = url.strip().lower().rstrip("/")
    excluded = {x.lower().rstrip("/") for x in EXCLUDED_EXACT_URLS}
    return u in excluded


def clean_single_url(url: str) -> str | None:
    if not url:
        return None

    url = url.strip()
    url = fix_www_scheme(url)
    url = strip_common_trailing_garbage(url)
    url = normalize_doi_url(url)

    if not is_probably_external_url(url):
        return None

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https", "ftp"):
        return None
    if not parsed.netloc:
        return None
    if re.search(r'\s', url):
        return None

    if is_excluded_url(url):
        return None

    return url


def extract_urls_from_text(text: str) -> list[str]:
    text = normalize_text_for_urls(text)
    raw_candidates = URL_REGEX.findall(text)

    urls = []
    for candidate in raw_candidates:
        for part in split_concatenated_urls(candidate):
            cleaned = clean_single_url(part)
            if cleaned:
                urls.append(cleaned)

    for doi in DOI_REGEX.findall(text):
        doi_url = clean_single_url(f"https://doi.org/{doi}")
        if doi_url:
            urls.append(doi_url)

    return urls


# =========================
# TEI EXTRACTION
# =========================
def extract_links(tei_xml: str, filename: str) -> None:
    root = ET.fromstring(tei_xml)
    links = []
    seen = set()

    def add_link(section: str, link: str) -> None:
        clean_link = clean_single_url(link)
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

    # 1) target solo en zonas útiles del paper, no en todo el XML
    target_paths = {
        "front": ".//tei:front//*[@target]",
        "abstract": ".//tei:abstract//*[@target]",
        "body": ".//tei:body//*[@target]",
        "note": ".//tei:note//*[@target]",
        "reference": ".//tei:listBibl//*[@target]",
        "back": ".//tei:back//*[@target]",
        "table": ".//tei:table//*[@target]"
    }

    for section, path in target_paths.items():
        for elem in root.findall(path, NS):
            target = elem.attrib.get("target", "").strip()
            if is_probably_external_url(target):
                add_link(section, target)

    # 2) DOI estructurado
    for elem in root.findall(".//tei:idno", NS):
        id_type = elem.attrib.get("type", "").lower().strip()
        value = "".join(elem.itertext()).strip()

        if id_type == "doi" and value:
            add_link("doi", f"https://doi.org/{value}")

        for url in extract_urls_from_text(value):
            add_link("idno", url)

    # 3) texto plano en secciones relevantes
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
            for url in extract_urls_from_text(text):
                add_link(section, url)

    links_per_paper[filename] = links


# =========================
# MAIN
# =========================
def process_all_pdfs() -> None:
    if not os.path.isdir(PDF_DIR):
        raise FileNotFoundError(f"PDF directory not found: {PDF_DIR}")

    pdf_files = sorted([f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")])

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
    links_df.to_csv(os.path.join(OUTPUT_DIR, "all_links.csv"), index=False, encoding="utf-8")

    with open(os.path.join(OUTPUT_DIR, "all_links.json"), "w", encoding="utf-8") as f:
        json.dump(links_per_paper, f, indent=4, ensure_ascii=False)

    print(f"Saved {len(all_links)} links to '{OUTPUT_DIR}'")


if __name__ == "__main__":
    process_all_pdfs()
    save_outputs()
    print("Done.")