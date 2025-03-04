import os
import pandas as pd

# Output directory
OUTPUT_DIR = "outputs/"
ABSTRACTS_FILE = os.path.join(OUTPUT_DIR, "abstracts.txt")
FIGURES_FILE = os.path.join(OUTPUT_DIR, "figures.csv")
LINKS_FILE = os.path.join(OUTPUT_DIR, "all_links.csv")
WORDCLOUD_FILE = os.path.join(OUTPUT_DIR, "wordcloud.png")
FIGURES_CHART_FILE = os.path.join(OUTPUT_DIR, "figures_chart.png")

def test_abstracts():
    """Verifies that the abstracts.txt file exists and contains text."""
    assert os.path.exists(ABSTRACTS_FILE), "❌ abstracts.txt has not been generated"
    with open(ABSTRACTS_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()
        assert len(content) > 0, "❌ abstracts.txt is empty"
    print("✅ abstracts.txt successfully generated and contains data")

def test_figures_csv():
    """Verifies that figures.csv exists and contains valid data."""
    assert os.path.exists(FIGURES_FILE), "❌ figures.csv has not been generated"
    df = pd.read_csv(FIGURES_FILE)
    assert not df.empty, "❌ figures.csv is empty"
    assert "Figure Count" in df.columns, "❌ figures.csv does not have the 'Figure Count' column"
    assert df["Figure Count"].dtype in ["int64", "float64"], "❌ figures.csv does not contain numerical values"
    print("✅ figures.csv successfully generated with valid data")

def test_links_csv():
    """Verifies that all_links.csv exists and contains at least one valid link."""
    assert os.path.exists(LINKS_FILE), "❌ all_links.csv has not been generated"
    df = pd.read_csv(LINKS_FILE)
    assert not df.empty, "❌ all_links.csv is empty"
    assert "link" in df.columns, "❌ all_links.csv does not have the 'link' column"
    assert df["link"].str.startswith("http").any(), "❌ No valid links found in all_links.csv"
    print("✅ all_links.csv successfully generated with valid links")

def test_wordcloud():
    """Verifies that wordcloud.png exists and has a size greater than 0 bytes."""
    assert os.path.exists(WORDCLOUD_FILE), "❌ wordcloud.png has not been generated"
    assert os.path.getsize(WORDCLOUD_FILE) > 0, "❌ wordcloud.png is empty"
    print("✅ wordcloud.png successfully generated")

def test_figures_chart():
    """Verifies that figures_chart.png exists and has a size greater than 0 bytes."""
    assert os.path.exists(FIGURES_CHART_FILE), "❌ figures_chart.png has not been generated"
    assert os.path.getsize(FIGURES_CHART_FILE) > 0, "❌ figures_chart.png is empty"
    print("✅ figures_chart.png successfully generated")

if __name__ == "__main__":
    print("\n### Running output validation tests ###\n")
    test_abstracts()
    test_figures_csv()
    test_links_csv()
    test_wordcloud()
    test_figures_chart()
    print("\n✅ All tests have passed successfully")
