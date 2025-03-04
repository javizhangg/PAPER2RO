
# Rationale for Scientific Articles Analysis with Grobid

## Introduction
This document provides the rationale behind the choices made in the **Scientific Articles Analysis with Grobid** project. It explains the methodology used, validation of results, and justifications for the tools and techniques employed.

## Methodology
### 1. **Data Collection**
- 10 open-access Bitcoin-related articles were selected for analysis.
- Articles were processed using **Grobid**, which extracts structured data from PDFs.

### 2. **Keyword Extraction & Word Cloud Generation**
- **Technique:** Extracted abstracts were tokenized and preprocessed.
- **Tools Used:** `NLTK` and `wordcloud` Python libraries.
- **Validation:** Common Bitcoin-related terms were checked for frequency to ensure meaningful results.
- **Output:** A word cloud image (`wordcloud.png`) visualizing extracted keywords.

### 3. **Figures Count Per Article**
- **Technique:** Figures were identified using Grobid’s full-text extraction.
- **Tools Used:** Grobid's XML parsing with `lxml`.
- **Validation:** Cross-referenced with manual counts from PDFs.
- **Output:** A CSV file (`figures.csv`) listing the number of figures per article.

### 4. **Extracting Links from Articles**
- **Technique:** Extracted hyperlinks from the full text using regular expressions.
- **Tools Used:** `re` module in Python for pattern recognition.
- **Validation:** Checked the validity of extracted URLs using HTTP response codes.
- **Output:** A CSV file (`all_links.csv`) containing extracted links.

## Error Logging and Handling we detected

### 🛑 **1. Error: `.pdf not read because it is not in the correct format`**

**Description:**  
During testing, we intentionally modified a `.jpg` file (`hello.jpg`) by renaming it to `.pdf` (`hello.pdf`).  
The test aimed to verify how the script handles non-PDF files disguised as PDFs.  

**Expected Behavior:**  
- The script should detect that `hello.pdf` is not a valid PDF and continue processing the remaining valid files without crashing.  

**Test Result:**  
- The script successfully ignored `hello.pdf` and continued processing the other PDFs.  
- No errors occurred in the output file generation.  
- The process completed successfully, saving results in the `outputs/` directory.
- There were the error but, that don't influences the program.
```bash
Error processing pdfs/hello.pdf: 500
```

### 🛑 **2. Error: `ValueError: We need at least 1 word to plot a word cloud, got 0`**

**Description:**  
When running `main.py` whichout pdfs in the directory pdfs/, the following error occurred:  

```bash
ValueError: We need at least 1 word to plot a word cloud, got 0.
```
That mean our code can't execute without some `.pdf` in the directory `/pdfs`.



### 🛑 **2. Error: `ValueError: We need at least 1 word to plot a word cloud, got 0`**

**Description:**  
When running `main.py` whichout pdfs in the directory pdfs/, the following error occurred:  

```bash
ValueError: We need at least 1 word to plot a word cloud, got 0.
```
That mean our code can't execute without some `.pdf` in the directory `/pdfs`.

### 🛑 **3. Error: `Outputs Error`**
During the execution of the project, the following errors may occur in the output files:

#### **abstracts.txt**
- **File Not Generated:** This can happen if Grobid fails to extract abstracts from PDFs.
- **Empty Content:** If the extracted abstracts contain only whitespace or if preprocessing removes all text.
- **Encoding Issues:** Non-UTF-8 characters may cause failures in text reading.

#### **figures.csv**
- **File Not Generated:** Indicates that Grobid failed to detect any figures.
- **Empty Data:** If no figures are present in any article, the CSV might be empty.
- **Missing "Figure Count" Column:** An issue in data processing might result in a malformed CSV.
- **Incorrect Data Type:** The "Figure Count" column must contain numerical values. If it contains text, there may be parsing issues.

#### **all_links.csv**
- **File Not Generated:** Failure in hyperlink extraction from articles.
- **Empty Content:** No hyperlinks were detected, possibly due to incorrect parsing.
- **Missing "link" Column:** The output structure might be incorrect if this column is missing.
- **Invalid URLs:** Extracted links should start with "http". If not, there may be pattern-matching issues.

#### **wordcloud.png**
- **File Not Generated:** The script may fail if the word cloud generation encounters an error.
- **Empty Image:** If text preprocessing removes all words, the word cloud cannot be created.
- **Corrupt File:** The image file is present but unreadable, possibly due to an interrupted write process.

#### **figures_chart.png**
- **File Not Generated:** If there are no figures detected, the chart script may fail.
- **Empty Image:** A blank chart could indicate incorrect data visualization logic.
- **Corrupt File:** Similar to `wordcloud.png`, an incomplete save operation can cause issues.

## Reproducibility
- **Environment Management:** The use of **Conda** and **Docker** ensures reproducibility.
- **Automation:** CI/CD with GitHub Actions validates dependencies and execution.
- **Documentation:** `README.md` and `ReadTheDocs` provide clear setup and execution steps.
- **Outputs Generated:** The processed results are stored in the `outputs/` directory, including keyword abstracts (`abstracts.txt`), extracted links (`all_links.csv`), figure counts (`figures.csv`), and the generated word cloud (`wordcloud.png`).

## Limitations & Improvements
- **Limitations:**
  - Accuracy of figure extraction depends on Grobid's OCR.
  - Some articles may contain incomplete metadata.
- **Future Enhancements:**
  - Integration with Named Entity Recognition (NER) for better keyword extraction.
  - More robust validation for figure detection using computer vision techniques.
  - Improved filtering for extracted links to remove duplicates and broken URLs.

## Conclusion
This rationale explains the choices behind methodology, validation, and reproducibility. The project adheres to best practices in Open Science, ensuring transparency and usability for future research.
