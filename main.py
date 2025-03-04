import os
import requests
import xml.etree.ElementTree as ET
import re
import matplotlib.pyplot as plt
from wordcloud import WordCloud
import pandas as pd
import unittest
from unittest.mock import patch

# GROBID Server URLs
GROBID_DOCKER_URL = "http://grobid:8070/api/isalive"
GROBID_LOCAL_URL = "http://localhost:8070/api/isalive"

def check_grobid_status(url):
    """Checks if the GROBID service is active."""
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return True
    except requests.exceptions.RequestException:
        pass
    return False

# Automatically select the GROBID URL
if check_grobid_status(GROBID_DOCKER_URL):
    GROBID_URL = "http://grobid:8070/api/processFulltextDocument"
    print(f"Using GROBID at {GROBID_URL}")
elif check_grobid_status(GROBID_LOCAL_URL):
    GROBID_URL = "http://localhost:8070/api/processFulltextDocument"
    print(f"Using GROBID at {GROBID_URL}")
else:
    raise ConnectionError("No instance of GROBID (Docker or Local) is available!")

# Directories
PDF_DIR = "pdfs/"
OUTPUT_DIR = "outputs/"

# Ensure the output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Store extracted data
abstracts = []
figures_count = {}
links_per_paper = {}

# Function to process PDFs with GROBID
def process_pdf(pdf_path):
    with open(pdf_path, "rb") as pdf_file:
        files = {"input": pdf_file}
        response = requests.post(GROBID_URL, files=files, data={"teiCoordinates": "figure"})
    if response.status_code == 200:
        return response.text  # Returns TEI XML
    else:
        print(f"Error processing {pdf_path}: {response.status_code}")
        return None

# Extract abstract, figures, and all links from TEI XML
def extract_info(tei_xml, filename):
    root = ET.fromstring(tei_xml)

    # Extract abstract
    abstract = ""
    for ab in root.findall(".//{http://www.tei-c.org/ns/1.0}abstract"):
        abstract += " ".join(ab.itertext()).strip()
    
    abstracts.append(abstract)

    # Count figures
    num_figures = len(root.findall(".//{http://www.tei-c.org/ns/1.0}figure"))
    figures_count[filename] = num_figures

# Function to extract all links from different sections of the TEI XML
def extract_links(tei_xml, filename):
    root = ET.fromstring(tei_xml)
    links = []

    # Define different sections to extract links
    sections = {
        "abstract": ".//{http://www.tei-c.org/ns/1.0}abstract",
        "body": ".//{http://www.tei-c.org/ns/1.0}body",
        "reference": ".//{http://www.tei-c.org/ns/1.0}listBibl",
        "note": ".//{http://www.tei-c.org/ns/1.0}note",
        "table": ".//{http://www.tei-c.org/ns/1.0}table"
    }

    for section, path in sections.items():
        for element in root.findall(path):
            text = "".join(element.itertext()).strip()
            found_links = re.findall(r'https?://[^\s<>"]+|www\.[^\s<>"]+', text)
            for link in found_links:
                links.append({"paper": filename, "section": section, "link": link})

    links_per_paper[filename] = links

# Process all PDFs
for pdf in os.listdir(PDF_DIR):
    if pdf.endswith(".pdf"):
        pdf_path = os.path.join(PDF_DIR, pdf)
        print(f"Processing {pdf_path}...")
        tei_xml = process_pdf(pdf_path)
        if tei_xml:
            extract_info(tei_xml, pdf)
            extract_links(tei_xml, pdf)  # Extract all links

# Save extracted abstracts to a text file
with open(os.path.join(OUTPUT_DIR, "abstracts.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(abstracts))

# Save figures count to CSV
figures_df = pd.DataFrame(list(figures_count.items()), columns=["Paper", "Figure Count"])
figures_df.to_csv(os.path.join(OUTPUT_DIR, "figures.csv"), index=False)

# Save extracted links to CSV
all_links = []
for paper, links in links_per_paper.items():
    all_links.extend(links)

links_df = pd.DataFrame(all_links, columns=["paper", "section", "link"])
links_df.to_csv(os.path.join(OUTPUT_DIR, "all_links.csv"), index=False)

# Generate and display word cloud from abstracts
abstract_text = " ".join(abstracts)
wordcloud = WordCloud(width=800, height=400, background_color="white").generate(abstract_text)

plt.figure(figsize=(10, 5))
plt.imshow(wordcloud, interpolation="bilinear")
plt.axis("off")
plt.title("Keyword Cloud from Abstracts")
plt.savefig(os.path.join(OUTPUT_DIR, "wordcloud.png"))
plt.show()

# Generate bar chart for figure count
plt.figure(figsize=(10, 5))
plt.bar(figures_count.keys(), figures_count.values(), color="skyblue")
plt.xlabel("Paper")
plt.ylabel("Number of Figures")
plt.title("Figures Count per Paper")
plt.xticks(rotation=45, ha="right")
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR, "figures_chart.png"))
plt.show()

print("Processing complete! Results saved in the 'outputs/' directory.")
