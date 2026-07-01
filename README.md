# PAPER2RO: From Scientific Papers to Research Objects

## Description

**PAPER2RO** is a semi-automated workflow for analysing scientific articles in PDF format, detecting potential datasets associated with them, and generating **Research Objects** in **RO-Crate** format.

Scientific articles often include complementary resources such as datasets, source code, external repositories or supplementary material. However, these resources are not always described in a structured way or included in the article metadata. They may appear scattered across the PDF text, data availability sections, references, DOIs, supplementary material or external web pages.

This project aims to improve the traceability between scientific publications and their associated resources by:

- Extracting URLs and DOIs from scientific PDF articles.
- Normalizing and cleaning extracted links.
- Applying dataset detection heuristics.
- Integrating dataset mentions extracted with GAP-KGE / DataStet.
- Generating YAML files compatible with YA2RO.
- Creating Research Objects in RO-Crate format.
- Providing a Streamlit interface to execute and review the workflow.

The final output is a structured Research Object containing the paper metadata, detected datasets and related resources, generated as JSON-LD metadata and an HTML preview.

---

## Main Objectives

The main objectives of PAPER2RO are:

- **Extract links and DOIs** from scientific articles in PDF format.
- **Normalize URLs** to remove duplicates, tracking parameters, malformed characters and extraction errors.
- **Detect potential dataset links** using two complementary heuristics:
  - **H1:** detection based on HTTP headers, downloadable files, known repositories and HTML analysis.
  - **H2:** detection based on content negotiation and structured metadata.
- **Use GAP-KGE / DataStet** to detect textual mentions of datasets inside the paper.
- **Generate YAML files** containing the article metadata, datasets and related resources.
- **Create Research Objects** using YA2RO and RO-Crate.
- **Evaluate the heuristics** against a manually annotated benchmark corpus.

---

## Project Architecture

The project follows a modular architecture based on intermediate files. Each module performs one step of the pipeline and produces outputs that are used by the next module.

General workflow:

```text
Scientific PDF
    ↓
Extract URLs and DOIs
    ↓
Normalize and clean URLs
    ↓
Apply H1 and H2 heuristics
    ↓
Extract dataset mentions with GAP-KGE / DataStet
    ↓
Generate YA2RO-compatible YAML files
    ↓
Generate RO-Crate Research Objects with YA2RO
```

Main components:

| Component | Main script / tool | Description |
|---|---|---|
| Link extraction | `extractLinks.py` | Extracts URLs and DOIs from PDFs. |
| URL normalization | `normalizeUrl.py` | Cleans, normalizes and deduplicates extracted URLs. |
| Heuristic 1 | `heuristic_1_page_download.py` | Detects datasets using headers, file extensions, repositories and downloadable resources. |
| Heuristic 2 | `heuristic_2_http_metadata.py` | Detects datasets using content negotiation and metadata. |
| Dataset mentions | GAP-KGE / DataStet | Extracts dataset mentions from the article text. |
| YAML generation | `generate_yamls.py` | Combines metadata, heuristics and GAP-KGE output into YA2RO YAML files. |
| RO generation | `ya2ro` | Converts YAML files into RO-Crate Research Objects. |
| Interface | `streamlit_app.py` | Provides a graphical interface to execute and inspect the pipeline. |

---

## Repository Structure

A simplified structure of the repository is shown below:

```text
PAPER2RO/
│
├── pdfs/                         # Input scientific papers and .dataset.json files
├── outputs/                      # Intermediate CSV/JSON results
│   ├── all_links.csv
│   ├── all_links_normalized.csv
│   ├── removed_urls.csv
│   ├── heuristic_1_results.csv
│   ├── heuristic_1_results.json
│   ├── heuristic_2_results.csv
│   └── heuristic_2_results.json
│
├── Benchmark/                    # Manual benchmark files
├── benchmark_Results/            # Benchmark evaluation results
│   ├── benchmark_h1_h2_comparison.csv
│   ├── benchmark_h1_h2_summary.csv
│   └── benchmark_h1_h2_report.txt
│
├── ya2ro_generated/
│   ├── yamls/                    # YAML files generated for YA2RO
│   └── ro_output/                # Final RO-Crate outputs
│
├── extractLinks.py
├── normalizeUrl.py
├── heuristic_1_page_download.py
├── heuristic_2_http_metadata.py
├── generate_yamls.py
├── streamlit_app.py
├── environment.yml
└── README.md
```

---

## Requirements

### Required software

- [Docker](https://www.docker.com/)
- [Anaconda](https://www.anaconda.com/download)
- Python 3.x
- Git

### External services

The pipeline uses external services that must be running before executing some parts of the system:

- **GROBID**, used to process scientific PDFs and extract structured metadata.
- **DataStet / GAP-KGE**, used to extract dataset mentions from scientific articles.

---

## Installation Instructions

Clone the repository:

```bash
git clone https://github.com/javizhangg/PAPER2RO.git
cd PAPER2RO
```

Create the Conda environment:

```bash
conda env create -f environment.yml
```

Activate the environment:

```bash
conda activate mi_entorno
```

If your environment has a different name, replace `mi_entorno` with the name defined in your `environment.yml` file.

---

## Running External Services

### Start GROBID

```bash
docker pull lfoppiano/grobid:0.8.0
docker run -p 8070:8070 lfoppiano/grobid:0.8.0
```

GROBID will be available at:

```text
http://localhost:8070
```

### Start DataStet

```bash
docker pull grobid/datastet:0.8.1
docker run --rm -it --init --ulimit core=0 -p 8060:8060 grobid/datastet:0.8.1
```

DataStet will be available at:

```text
http://localhost:8060
```

Keep these containers running while processing the papers.

---

## Execution Instructions

There are two main ways to run the project: using the Streamlit interface or executing each step manually from the terminal.

---

## Option 1: Run with Streamlit Interface

The easiest way to run the complete workflow is through the Streamlit application:

```bash
streamlit run streamlit_app.py
```

From the interface, the user can:

- Upload scientific papers in PDF format.
- Execute the complete pipeline.
- Review generated intermediate files.
- Inspect YAML files.
- Visualize generated RO-Crate outputs.
- Open the HTML preview of each Research Object.

---

## Option 2: Manual Pipeline Execution

### 1. Add PDF files

Place the scientific papers inside the `pdfs/` folder:

```text
pdfs/
```

### 2. Run GAP-KGE / DataStet

Go to the `software_mentions_client` folder:

```bash
cd software_mentions_client
```

Run the dataset mention extraction:

```bash
python -m software_mentions_client.client --repo-in ../pdfs --datastet --reset
```

The generated files should follow this structure:

```text
pdfs/<paper_name>.dataset.json
```

Return to the main project folder:

```bash
cd ..
```

### 3. Extract URLs and DOIs

```bash
python extractLinks.py
```

Main output:

```text
outputs/all_links.csv
```

### 4. Normalize URLs

```bash
python normalizeUrl.py
```

Main outputs:

```text
outputs/all_links_normalized.csv
outputs/removed_urls.csv
```

### 5. Run Heuristic 2

Heuristic 2 can be executed before H1 because it does not depend on the `.dataset.json` files:

```bash
python heuristic_2_http_metadata.py
```

Main outputs:

```text
outputs/heuristic_2_results.csv
outputs/heuristic_2_results.json
```

### 6. Run Heuristic 1

Heuristic 1 uses the normalized URLs and may also use GAP-KGE information to validate ambiguous resources such as ZIP files:

```bash
python heuristic_1_page_download.py
```

Main outputs:

```text
outputs/heuristic_1_results.csv
outputs/heuristic_1_results.json
```

### 7. Generate YA2RO YAML files

```bash
python generate_yamls.py
```

Main output folder:

```text
ya2ro_generated/yamls/
```

### 8. Generate Research Objects with YA2RO

```bash
ya2ro -i "ya2ro_generated\yamls" -o "ya2ro_generated\ro_output"
```

Main output folder:

```text
ya2ro_generated/ro_output/
```

Each generated Research Object may include:

```text
ro-crate-metadata.json
ro-crate-preview.html
help.html
```

---

## Benchmark and Evaluation

The system was evaluated using a manually annotated benchmark corpus.

The benchmark contains:

- **544 unique URLs**.
- **97 manually annotated dataset URLs**.
- **447 URLs manually annotated as non-datasets**.

Two heuristics were evaluated:

| Heuristic | Accuracy | Precision | Recall | F1-score | Specificity | TP | TN | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H1 | 0.8676 | 0.8049 | 0.3402 | 0.4783 | 0.9821 | 33 | 439 | 8 | 64 |
| H2 | 0.8750 | 0.8919 | 0.3402 | 0.4925 | 0.9911 | 33 | 443 | 4 | 64 |

The results show that both heuristics have high precision, especially H2, but limited recall. This means that when the system detects a dataset, the prediction is usually reliable, but many datasets identified manually are still not detected automatically.

The main reasons for false negatives are:

- Dataset URLs are sometimes represented as DOIs or intermediate pages.
- Some datasets are only identifiable from the article context.
- Some links point to institutional portals or non-standard repositories.
- Some URLs are malformed during PDF extraction.
- Some web pages block or limit automatic requests.

The main reasons for false positives are:

- Some links are related to data but are not datasets.
- Some repositories contain software, documentation or articles instead of datasets.
- Some DOI links describe scientific resources but not datasets specifically.

---

## Evaluation Corpus

The evaluation corpus is available in the project repository:

```text
https://github.com/javizhangg/PAPER2RO/tree/main/pdfs
```

This corpus includes the scientific papers used for evaluation. The benchmark files and heuristic results are stored in the repository folders related to `Benchmark/`, `outputs/` and `benchmark_Results/`.

For long-term preservation and citation, the corpus may also be published in Zenodo in future versions of the project.

---

## Outputs

The main output files generated by the project are:

| Output | Description |
|---|---|
| `outputs/all_links.csv` | Raw URLs and DOIs extracted from PDFs. |
| `outputs/all_links_normalized.csv` | Cleaned and normalized URLs. |
| `outputs/removed_urls.csv` | URLs removed during normalization and the reason for removal. |
| `outputs/heuristic_1_results.csv` | Results of H1. |
| `outputs/heuristic_2_results.csv` | Results of H2. |
| `pdfs/*.dataset.json` | Dataset mentions extracted by GAP-KGE / DataStet. |
| `ya2ro_generated/yamls/*.yaml` | YAML files compatible with YA2RO. |
| `ya2ro_generated/ro_output/` | Final Research Objects in RO-Crate format. |
| `ro-crate-metadata.json` | JSON-LD metadata of the generated Research Object. |
| `ro-crate-preview.html` | HTML preview of the generated Research Object. |

---

## Documentation

Additional documentation is included in the Bachelor Thesis report and in the project repository.

The report describes:

- The technological context of Research Objects and RO-Crate.
- The architecture of the system.
- The workflow followed by PAPER2RO.
- The construction of the evaluation corpus.
- The dataset detection heuristics.
- The integration with GAP-KGE and YA2RO.
- The benchmark results and limitations.

---

## Preferred Citation

If you use this work, please cite it as:

```bibtex
@misc{Paper2RO2026,
  title        = {PAPER2RO: From Scientific Papers to Research Objects},
  author       = {Zhiwei Zhang},
  howpublished = {\url{https://github.com/javizhangg/PAPER2RO}},
  publisher    = {GitHub},
  year         = {2026}
}
```

---

## License

This project is licensed under the license included in the repository.

If no license file is provided, please contact the author before reusing the code.

---

## Where to Get Help

For questions or issues, please contact:

- **Author:** Zhiwei Zhang
- **Email:** Zhiwei.zha@alumnos.upm.es
- **GitHub:** [https://github.com/javizhangg](https://github.com/javizhangg)

---

## Acknowledgments

This project was developed as a Bachelor Thesis at the **Escuela Técnica Superior de Ingenieros Informáticos, Universidad Politécnica de Madrid**.

Tutors:

- Daniel Garijo
- Esteban González Guardia

The project follows open science practices and uses technologies related to Research Objects, RO-Crate, YA2RO, GROBID, GAP-KGE, DataStet and FAIR data principles.

---

## Structured Metadata

This project can be complemented with a `codemeta.json` file to improve software discovery, citation and reuse.

Recommended metadata file location:

```text
codemeta.json
```

Recommended repository URL:

```text
https://github.com/javizhangg/PAPER2RO
```
