# normalize_urls.py
# Limpia URLs, elimina ruido de GROBID, normaliza formatos y elimina duplicados

import csv
import json
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, unquote

INPUT_CSV = "outputs/all_links.csv"
OUTPUT_CSV = "outputs/all_links_normalized.csv"
OUTPUT_JSON = "outputs/all_links_normalized.json"
REMOVED_CSV = "outputs/removed_urls.csv"

REMOVE_RAW_TEI = True
REMOVE_STRUCTURED_DOI = False

DATA_EXTENSIONS = {
    ".csv", ".tsv", ".json", ".xml", ".rdf",
    ".xlsx", ".xls", ".parquet", ".h5", ".hdf5",
    ".zip", ".gz", ".tar", ".tgz", ".7z",
    ".pkl", ".pickle", ".npy", ".npz",
    ".db", ".sqlite", ".sqlite3"
}

TRACKING_PARAMS_PREFIXES = ("utm_",)

TRACKING_PARAMS_EXACT = {
    "fbclid", "gclid", "dclid", "mc_cid", "mc_eid",
    "igshid", "ref", "ref_src", "source", "spm"
}

INTERNAL_GROBID_DOMAINS = {
    "www.tei-c.org",
    "tei-c.org",
    "www.w3.org",
    "w3.org",
}

DOI_REGEX = re.compile(
    r'(?:https?://(?:dx\.)?doi\.org/|doi:)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)',
    re.IGNORECASE
)


def clean_url(url: str) -> str:
    if not url:
        return ""

    url = str(url).strip()
    url = url.replace("\u200b", "")
    url = url.replace("\ufeff", "")

    url = url.replace("&gt;", "")
    url = url.replace("&lt;", "")
    url = url.replace("&amp;", "&")

    url = re.sub(r"\s+", "", url)

    url = url.strip("<>()[]{}\"'")
    url = url.rstrip(".,;:!?)]}>\"'•·")

    return url


def remove_trailing_garbage(url: str) -> str:
    if not url:
        return ""

    old = None

    while old != url:
        old = url

        url = url.strip()
        url = url.strip("<>()[]{}\"'")
        url = url.rstrip(".,;:!?)]}>\"'•·")

        url = re.sub(r'/\.[A-Za-z].*$', '', url)

        url = re.sub(
            r'\.(Second|First|Third|The|This|These|Figure|Table|Section|Appendix|Related|Introducing|Rethinking|Towards|Exploring|Using|From|With|And|For).*$',
            '',
            url,
            flags=re.IGNORECASE
        )

        url = re.sub(r'\.Ac-?cessed:?.*$', '', url, flags=re.IGNORECASE)

        url = re.sub(
            r'(Accessed|Retrieved|Available\s*at|Available\s*from).*$',
            '',
            url,
            flags=re.IGNORECASE
        )

        url = re.sub(r'\.[A-Z][A-Za-z-]{8,}.*$', '', url)

        url = re.sub(r'(?<=\d)\.[A-Z][A-Za-z]+.*$', '', url)

        url = re.sub(r',\d{4}.*$', '', url)

    return url


def is_valid_domain(netloc: str) -> bool:
    if not netloc:
        return False

    netloc = netloc.lower().strip()

    if "@" in netloc:
        netloc = netloc.split("@")[-1]

    if ":" in netloc:
        netloc = netloc.split(":")[0]

    if "." not in netloc:
        return False

    if netloc.startswith(".") or netloc.endswith("."):
        return False

    return True


def is_internal_grobid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        return domain in INTERNAL_GROBID_DOMAINS
    except Exception:
        return False


def normalize_doi(url: str) -> str:
    if not url:
        return ""

    match = DOI_REGEX.search(url)

    if not match:
        return url

    doi = match.group(1)
    doi = doi.strip()
    doi = doi.rstrip(".,;:!?)]}>\"'•·")

    return f"https://doi.org/{doi}"


def normalize_arxiv(parsed):
    path = parsed.path.strip("/")

    m = re.match(
        r"(abs|pdf)/([0-9]{4}\.[0-9]{4,5})(v\d+)?(?:\.pdf)?",
        path,
        re.IGNORECASE
    )

    if not m:
        return None

    arxiv_id = m.group(2)
    version = m.group(3) or ""

    return f"https://arxiv.org/abs/{arxiv_id}{version}"


def normalize_github(parsed):
    path = parsed.path.rstrip("/")

    return urlunparse((
        "https",
        "github.com",
        path,
        "",
        "",
        ""
    ))


def normalize_url(url: str) -> str:
    url = clean_url(url)
    url = remove_trailing_garbage(url)

    if not url:
        return ""

    low = url.lower()

    if (
        low.startswith("10.")
        or low.startswith("doi:")
        or "doi.org/" in low
        or "dx.doi.org/" in low
    ):
        return normalize_doi(url)

    if low.startswith("www."):
        url = "https://" + url

    parsed = urlparse(url)

    if not parsed.scheme:
        parsed = urlparse("https://" + url)

    scheme = parsed.scheme.lower()

    if scheme not in {"http", "https", "ftp"}:
        return ""

    netloc = parsed.netloc.lower()

    if not netloc:
        return ""

    if netloc.startswith("www."):
        netloc = netloc[4:]

    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]

    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]

    if not is_valid_domain(netloc):
        return ""

    path = unquote(parsed.path or "")
    path = re.sub(r"/{2,}", "/", path)

    if path != "/":
        path = path.rstrip("/")

    fake_parsed = parsed._replace(
        scheme=scheme,
        netloc=netloc,
        path=path
    )

    if netloc == "arxiv.org":
        arxiv_norm = normalize_arxiv(fake_parsed)
        if arxiv_norm:
            return arxiv_norm

    if netloc == "github.com":
        return normalize_github(fake_parsed)

    query_params = []

    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        klow = k.lower()

        if klow in TRACKING_PARAMS_EXACT:
            continue

        if any(klow.startswith(prefix) for prefix in TRACKING_PARAMS_PREFIXES):
            continue

        query_params.append((k, v))

    query_params.sort()
    query = urlencode(query_params, doseq=True)

    normalized = urlunparse((
        scheme,
        netloc,
        path,
        "",
        query,
        ""
    ))

    return normalized


def dedup_key(url: str) -> str:
    if not url:
        return ""

    parsed = urlparse(url)

    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/").lower()
    query = parsed.query.lower()

    return f"{netloc}{path}?{query}"


def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def get_extension(url: str) -> str:
    try:
        path = urlparse(url).path.lower()
        m = re.search(r"(\.[a-z0-9]+)$", path)

        if m:
            return m.group(1)

    except Exception:
        pass

    return ""


def is_data_extension(ext: str) -> bool:
    return ext in DATA_EXTENSIONS


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


def process_rows(rows):
    seen = {}
    kept = []
    removed = []

    for row in rows:
        raw_url = row["url"]
        section = row["section"].lower().strip()

        if REMOVE_RAW_TEI and section == "raw_tei":
            removed.append({
                "paper": row["paper"],
                "section": row["section"],
                "original_url": raw_url,
                "normalized_url": "",
                "duplicate_of": "",
                "reason": "raw_tei_fallback_noise"
            })
            continue

        if REMOVE_STRUCTURED_DOI and section == "doi":
            removed.append({
                "paper": row["paper"],
                "section": row["section"],
                "original_url": raw_url,
                "normalized_url": "",
                "duplicate_of": "",
                "reason": "grobid_structured_doi_not_manual_url"
            })
            continue

        norm = normalize_url(raw_url)

        if not norm:
            removed.append({
                "paper": row["paper"],
                "section": row["section"],
                "original_url": raw_url,
                "normalized_url": "",
                "duplicate_of": "",
                "reason": "empty_or_invalid_after_normalization"
            })
            continue

        if is_internal_grobid_url(norm):
            removed.append({
                "paper": row["paper"],
                "section": row["section"],
                "original_url": raw_url,
                "normalized_url": norm,
                "duplicate_of": "",
                "reason": "internal_grobid_or_xml_url"
            })
            continue

        key = dedup_key(norm)

        if key in seen:
            removed.append({
                "paper": row["paper"],
                "section": row["section"],
                "original_url": raw_url,
                "normalized_url": norm,
                "duplicate_of": seen[key],
                "reason": "duplicate"
            })
            continue

        seen[key] = norm

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


def save_csv(rows, path):
    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            pass
        return

    fields = []
    for row in rows:
        for key in row.keys():
            if key not in fields:
                fields.append(key)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)


def save_json(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)


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

    print("\nGuardados:")
    print(f"- {OUTPUT_CSV}")
    print(f"- {OUTPUT_JSON}")
    print(f"- {REMOVED_CSV}")

    if removed:
        print("\n=== URLs ELIMINADAS ===")
        for r in removed:
            if r["reason"] == "duplicate":
                print(f"[duplicate] {r['original_url']} -> {r['duplicate_of']}")
            else:
                print(f"[{r['reason']}] {r['original_url']}")


if __name__ == "__main__":
    main()