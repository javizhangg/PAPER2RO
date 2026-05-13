import pandas as pd
from pathlib import Path
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    confusion_matrix,
    classification_report
)

BASE_DIR = Path(__file__).resolve().parent

MANUAL_FILE = BASE_DIR / "Benchmark" / "UrlManual.xlsx"
HEURISTICS_FILE = BASE_DIR / "outputs" / "heuristics_results.csv"

OUTPUT_FILE = BASE_DIR / "outputs" / "benchmark_comparison.csv"


# ==================================
# Normalización URL
# ==================================
def normalize_url(url):
    if pd.isna(url):
        return ""

    url = str(url).strip().lower()
    url = url.rstrip("/")

    return url


# ==================================
# Normalización etiquetas
# ==================================
def normalize_label(value):
    if pd.isna(value):
        return None

    value = str(value).strip().lower()

    positive = {
        "si", "sí", "yes", "true", "1",
        "dataset", "is_dataset", "positive",
        "maybe_dataset"
    }

    negative = {
        "no", "false", "0",
        "not_dataset", "no_dataset", "negative"
    }

    if value in positive:
        return 1

    if value in negative:
        return 0

    return None


# ==================================
# MAIN
# ==================================
def main():
    print("Iniciando benchmark...")

    manual_df = pd.read_excel(MANUAL_FILE)
    heuristics_df = pd.read_csv(HEURISTICS_FILE)

    print("Columnas manual:", manual_df.columns.tolist())
    print("Columnas heurísticas:", heuristics_df.columns.tolist())

    # =========================
    # Columnas
    # =========================
    manual_url_col = "URL detectada"
    manual_label_col = "¿Parece dataset?"

    heuristics_url_col = "normalized_url"
    heuristics_label_col = "label"

    # =========================
    # Normalización
    # =========================
    manual_df["url_norm"] = manual_df[manual_url_col].apply(normalize_url)
    heuristics_df["url_norm"] = heuristics_df[heuristics_url_col].apply(normalize_url)

    manual_df["manual_label"] = manual_df[manual_label_col].apply(normalize_label)
    heuristics_df["heuristic_label"] = heuristics_df[heuristics_label_col].apply(normalize_label)

    # =========================
    # QUEDARSE SOLO CON COLUMNAS NECESARIAS
    # =========================
    manual_df = manual_df[["url_norm", "manual_label"]]
    heuristics_df = heuristics_df[["url_norm", "heuristic_label", "total_score"]]

    # =========================
    # ELIMINAR DUPLICADOS
    # =========================
    manual_df = manual_df.drop_duplicates(subset=["url_norm"])
    heuristics_df = heuristics_df.drop_duplicates(subset=["url_norm"])

    # =========================
    # FULL OUTER JOIN
    # IMPORTANTE:
    # así contamos True Negatives también
    # =========================
    comparison = manual_df.merge(
        heuristics_df,
        on="url_norm",
        how="outer"
    )

    # =========================
    # REGLAS IMPORTANTES
    # =========================

    # Si una URL NO existe en manual:
    # asumimos NO DATASET
    comparison["manual_label"] = comparison["manual_label"].fillna(0)

    # Si heurística no clasificó:
    # asumimos NO DATASET
    comparison["heuristic_label"] = comparison["heuristic_label"].fillna(0)

    # Convertir a int
    comparison["manual_label"] = comparison["manual_label"].astype(int)
    comparison["heuristic_label"] = comparison["heuristic_label"].astype(int)

    # =========================
    # MATCH
    # =========================
    comparison["match"] = (
        comparison["manual_label"]
        == comparison["heuristic_label"]
    )

    # =========================
    # GUARDAR CSV
    # =========================
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    comparison.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    # =========================
    # MÉTRICAS
    # =========================
    y_true = comparison["manual_label"]
    y_pred = comparison["heuristic_label"]

    print("\n===== BENCHMARK RESULTS =====")

    print(f"Total URLs manuales: {len(manual_df)}")
    print(f"Total URLs heurísticas: {len(heuristics_df)}")
    print(f"Total URLs evaluadas: {len(comparison)}")

    print(f"\nAccuracy:  {accuracy_score(y_true, y_pred):.4f}")
    print(f"Precision: {precision_score(y_true, y_pred, zero_division=0):.4f}")
    print(f"Recall:    {recall_score(y_true, y_pred, zero_division=0):.4f}")
    print(f"F1-score:  {f1_score(y_true, y_pred, zero_division=0):.4f}")

    print("\nMatriz de confusión:")
    print(confusion_matrix(y_true, y_pred))

    print("\nClassification report:")
    print(classification_report(y_true, y_pred, zero_division=0))

    # =========================
    # DEBUG EXTRA
    # =========================
    false_positives = comparison[
        (comparison["manual_label"] == 0)
        & (comparison["heuristic_label"] == 1)
    ]

    false_negatives = comparison[
        (comparison["manual_label"] == 1)
        & (comparison["heuristic_label"] == 0)
    ]

    print(f"\nFalse Positives: {len(false_positives)}")
    print(f"False Negatives: {len(false_negatives)}")

    print(f"\nArchivo benchmark guardado en:")
    print(OUTPUT_FILE.resolve())


if __name__ == "__main__":
    main()