from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse, unquote
from datetime import datetime
import re

import pandas as pd


# ==============================
# RUTAS
# ==============================

BASE_DIR = Path(__file__).resolve().parent

MANUAL_FILE = BASE_DIR / "Benchmark" / "UrlManual_normalized.xlsx"
H1_FILE = BASE_DIR / "outputs" / "heuristic_1_results.csv"
H2_FILE = BASE_DIR / "outputs" / "heuristic_2_results.csv"

OUT_DIR = BASE_DIR / "benchmark_Results"
OUT_COMPARISON = OUT_DIR / "benchmark_h1_h2_comparison.csv"
OUT_SUMMARY = OUT_DIR / "benchmark_h1_h2_summary.csv"
OUT_REPORT = OUT_DIR / "benchmark_h1_h2_report.txt"
OUT_COMPARISON_XLSX = OUT_DIR / "benchmark_h1_h2_comparison_simple.xlsx"
OUT_SUMMARY_XLSX = OUT_DIR / "benchmark_h1_h2_summary_simple.xlsx"

# Con esta funcion estricta, 0.94 acepta:
# - exacto = 1.00
# - una URL contiene a la otra en el mismo dominio = 0.97 / 0.94
# No acepta similitud por tokens ni SequenceMatcher.
MATCH_THRESHOLD = 0.94


# ==============================
# UTILIDADES
# ==============================

def clean_url_exact(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_bool(value: Any) -> int:
    if pd.isna(value):
        return 0

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, (int, float)):
        return 1 if value != 0 else 0

    value = str(value).strip().lower()

    positives = {
        "true", "1", "yes", "y", "si", "sí", "dataset",
        "positive", "positivo"
    }

    return 1 if value in positives else 0


def url_for_matching(value: Any) -> str:
    """
    Normaliza SOLO para comparar.
    No cambia la URL que se guarda en el resultado.
    """
    if pd.isna(value):
        return ""

    url = str(value).strip().lower()
    url = re.sub(r"\s+", "", url)

    if not url:
        return ""

    # Quitar fragmentos y parametros para comparar el recurso base
    url = url.split("#")[0]
    url = url.split("?")[0]

    # Comparacion ligera: http/www no deben crear URLs distintas
    url = url.replace("http://", "https://")
    url = url.replace("https://www.", "https://")
    url = url.rstrip("/")

    return url


def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def get_path(url: str) -> str:
    try:
        return unquote(urlparse(url).path.lower()).rstrip("/")
    except Exception:
        return ""


def simplify_path(path: str) -> str:
    path = unquote((path or "").lower()).strip()
    path = re.sub(r"/{2,}", "/", path)

    if path != "/":
        path = path.rstrip("/")

    # Quitar finales que suelen ser vistas del mismo recurso
    path = re.sub(
        r"/(data|download|downloads|files|file|view|preview|metadata|code|versions)$",
        "",
        path,
    )

    # Quitar extensiones de paginas, no extensiones de datos
    path = re.sub(r"\.(html|htm|php|aspx)$", "", path)

    return path.strip("/")


def is_general_page(path: str) -> bool:
    """
    Evita que paginas generales se emparejen con datasets concretos.
    """
    p = "/" + path.strip("/") + "/"

    general_patterns = [
        "/communities/",
        "/search/",
        "/explore/",
        "/topics/",
        "/docs/",
        "/documentation/",
        "/about/",
        "/help/",
        "/login/",
        "/signup/",
        "/repositories/",
        "/collections/",
    ]

    return any(pattern in p for pattern in general_patterns)


def url_match_score(manual_url: str, heuristic_url: str) -> Tuple[float, str]:
    """
    Match estricto.

    Acepta:
    - exacto tras normalizacion ligera
    - mismo dominio y una URL contiene claramente a la otra

    Rechaza:
    - dominios distintos
    - similitud por tokens
    - SequenceMatcher
    - paginas generales
    """
    m = url_for_matching(manual_url)
    h = url_for_matching(heuristic_url)

    if not m or not h:
        return 0.0, "empty_url"

    if m == h:
        return 1.0, "exact_match_after_light_normalization"

    dm = get_domain(m)
    dh = get_domain(h)

    if not dm or not dh:
        return 0.0, "missing_domain"

    if dm != dh:
        return 0.0, "different_domain"

    pm = simplify_path(get_path(m))
    ph = simplify_path(get_path(h))

    if not pm or not ph:
        return 0.0, "empty_path"

    if is_general_page(pm) or is_general_page(ph):
        return 0.0, "general_page_rejected"

    # Caso claro:
    # manual:     zenodo.org/records/12345
    # heuristica: zenodo.org/records/12345/files/data.csv
    if m in h or h in m:
        return 0.97, "same_domain_one_url_contains_the_other"

    if pm in ph or ph in pm:
        return 0.94, "same_domain_path_contains_other"

    return 0.0, "no_match_strict"


# ==============================
# CARGA DE DATOS
# ==============================

def load_manual(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"No existe el Excel manual: {path}")

    df = pd.read_excel(path)

    required = {"url", "es_dataset"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas en el Excel manual: {missing}")

    if "pdf" not in df.columns:
        df["pdf"] = ""

    df = df.copy()
    df["url"] = df["url"].apply(clean_url_exact)
    df["es_dataset_manual"] = df["es_dataset"].apply(normalize_bool)
    df = df[df["url"] != ""].copy()

    # IMPORTANTE:
    # El benchmark es por URL, no por fila/PDF.
    # Si la misma URL aparece varias veces, se queda una sola.
    # Si alguna repeticion estaba marcada dataset, se conserva dataset=1.
    grouped = (
        df.groupby("url", as_index=False)
        .agg({
            "pdf": "first",
            "es_dataset_manual": "max",
        })
    )

    return grouped.reset_index(drop=True)


def load_heuristic_rows(path: Path, heuristic_name: str) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"No existe el CSV de {heuristic_name}: {path}")

    df = pd.read_csv(path)

    required = {"url", "heuristica"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas en {path.name}: {missing}")

    rows = []

    for idx, row in df.iterrows():
        url = clean_url_exact(row.get("url", ""))

        if not url:
            continue

        rows.append({
            "id": idx,
            "url": url,
            "prediction": normalize_bool(row.get("heuristica", 0)),
        })

    return rows


# ==============================
# MATCH ONE-TO-ONE
# ==============================

def get_one_to_one_predictions(
    manual_urls: List[str],
    heuristic_rows: List[Dict[str, Any]],
    threshold: float = MATCH_THRESHOLD,
) -> Tuple[List[int], int, int]:
    """
    Devuelve predicciones para cada URL manual.

    Regla clave:
    - Cada URL de la heuristica solo puede usarse una vez.
    - Asi una misma URL positiva no puede marcar 5 URLs manuales como dataset.
    """
    candidates = []

    for manual_idx, manual_url in enumerate(manual_urls):
        for h_idx, hrow in enumerate(heuristic_rows):
            score, reason = url_match_score(manual_url, hrow["url"])

            if score >= threshold:
                candidates.append({
                    "manual_idx": manual_idx,
                    "heuristic_idx": h_idx,
                    "score": score,
                    "prediction": int(hrow["prediction"]),
                    "reason": reason,
                })

    # Primero matches mas claros.
    # No priorizamos positivos, porque eso puede inflar datasets.
    candidates.sort(key=lambda x: x["score"], reverse=True)

    predictions = [0] * len(manual_urls)
    used_manual = set()
    used_heuristic = set()
    matched_count = 0

    for c in candidates:
        m = c["manual_idx"]
        h = c["heuristic_idx"]

        if m in used_manual:
            continue

        if h in used_heuristic:
            continue

        predictions[m] = c["prediction"]
        used_manual.add(m)
        used_heuristic.add(h)
        matched_count += 1

    raw_positive_count = sum(int(r["prediction"]) for r in heuristic_rows)

    return predictions, matched_count, raw_positive_count


# ==============================
# METRICAS
# ==============================

def confusion_counts(y_true: List[int], y_pred: List[int]) -> Dict[str, int]:
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 0)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)
    return {"TP": tp, "TN": tn, "FP": fp, "FN": fn}


def safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def metrics_from_counts(c: Dict[str, int]) -> Dict[str, float]:
    tp = c["TP"]
    tn = c["TN"]
    fp = c["FP"]
    fn = c["FN"]
    total = tp + tn + fp + fn

    precision = safe_div(tp, tp + fp)
    recall = safe_div(tp, tp + fn)

    return {
        "accuracy": safe_div(tp + tn, total),
        "precision": precision,
        "recall": recall,
        "f1_score": safe_div(2 * precision * recall, precision + recall),
        "specificity": safe_div(tn, tn + fp),
    }


# ==============================
# GUARDADO SEGURO
# ==============================

def safe_to_csv(df: pd.DataFrame, path: Path) -> Path:
    try:
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return path
    except PermissionError:
        alt = path.with_name(f"{path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}")
        df.to_csv(alt, index=False, encoding="utf-8-sig")
        return alt


def safe_to_excel(df: pd.DataFrame, path: Path) -> Path:
    try:
        df.to_excel(path, index=False)
        return path
    except PermissionError:
        alt = path.with_name(f"{path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}")
        df.to_excel(alt, index=False)
        return alt


def safe_write_text(text: str, path: Path) -> Path:
    try:
        path.write_text(text, encoding="utf-8")
        return path
    except PermissionError:
        alt = path.with_name(f"{path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}")
        alt.write_text(text, encoding="utf-8")
        return alt


# ==============================
# MAIN
# ==============================

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Cargando manual...")
    manual = load_manual(MANUAL_FILE)

    print("Cargando H1...")
    h1_rows = load_heuristic_rows(H1_FILE, "h1")

    print("Cargando H2...")
    h2_rows = load_heuristic_rows(H2_FILE, "h2")

    manual_urls = manual["url"].tolist()

    print("Comparando URLs manuales contra H1 con match estricto one-to-one...")
    h1_predictions, h1_matched_count, h1_raw_positive_count = get_one_to_one_predictions(manual_urls, h1_rows)

    print("Comparando URLs manuales contra H2 con match estricto one-to-one...")
    h2_predictions, h2_matched_count, h2_raw_positive_count = get_one_to_one_predictions(manual_urls, h2_rows)

    comparison = pd.DataFrame({
        "pdf": manual["pdf"],
        "url": manual["url"],
        "es_dataset_manual": manual["es_dataset_manual"].astype(int),
        "h1_prediction": h1_predictions,
        "h2_prediction": h2_predictions,
    })

    summary_rows = []
    report_lines = []

    for heuristic_name, pred_col, raw_positive_count, matched_count in [
        ("heuristica_1", "h1_prediction", h1_raw_positive_count, h1_matched_count),
        ("heuristica_2", "h2_prediction", h2_raw_positive_count, h2_matched_count),
    ]:
        y_true = comparison["es_dataset_manual"].astype(int).tolist()
        y_pred = comparison[pred_col].astype(int).tolist()

        counts = confusion_counts(y_true, y_pred)
        metrics = metrics_from_counts(counts)
        benchmark_positive_count = int(comparison[pred_col].sum())

        summary_rows.append({
            "heuristica": heuristic_name,
            "match_threshold": MATCH_THRESHOLD,
            "total_manual_urls_unicas_usadas_en_benchmark": len(comparison),
            "manual_positives_dataset": int(comparison["es_dataset_manual"].sum()),
            "manual_negatives_no_dataset": int(len(comparison) - comparison["es_dataset_manual"].sum()),
            "raw_positive_count_in_heuristic_csv": raw_positive_count,
            "positive_count_after_benchmark_matching": benchmark_positive_count,
            "matched_manual_urls_count": matched_count,
            "accuracy": metrics["accuracy"],
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "f1_score": metrics["f1_score"],
            "specificity": metrics["specificity"],
            "true_positive": counts["TP"],
            "true_negative": counts["TN"],
            "false_positive": counts["FP"],
            "false_negative": counts["FN"],
        })

        report_lines.append("=" * 70)
        report_lines.append(heuristic_name)
        report_lines.append("=" * 70)
        report_lines.append(f"Umbral de parecido usado: {MATCH_THRESHOLD}")
        report_lines.append(f"Total URLs manuales unicas usadas: {len(comparison)}")
        report_lines.append(f"Datasets reales manuales: {int(comparison['es_dataset_manual'].sum())}")
        report_lines.append(f"No datasets reales manuales: {int(len(comparison) - comparison['es_dataset_manual'].sum())}")
        report_lines.append(f"Positivos en CSV original de la heuristica: {raw_positive_count}")
        report_lines.append(f"Positivos tras matching del benchmark: {benchmark_positive_count}")
        report_lines.append(f"URLs manuales emparejadas con algun resultado: {matched_count}")
        report_lines.append("")
        report_lines.append(f"TP: {counts['TP']}")
        report_lines.append(f"TN: {counts['TN']}")
        report_lines.append(f"FP: {counts['FP']}")
        report_lines.append(f"FN: {counts['FN']}")
        report_lines.append("")
        report_lines.append(f"Accuracy   : {metrics['accuracy']:.4f}")
        report_lines.append(f"Precision  : {metrics['precision']:.4f}")
        report_lines.append(f"Recall     : {metrics['recall']:.4f}")
        report_lines.append(f"F1-score   : {metrics['f1_score']:.4f}")
        report_lines.append(f"Specificity: {metrics['specificity']:.4f}")
        report_lines.append("")

    summary = pd.DataFrame(summary_rows)

    saved_comparison = safe_to_csv(comparison, OUT_COMPARISON)
    saved_comparison_xlsx = safe_to_excel(comparison, OUT_COMPARISON_XLSX)
    saved_summary = safe_to_csv(summary, OUT_SUMMARY)
    saved_summary_xlsx = safe_to_excel(summary, OUT_SUMMARY_XLSX)
    saved_report = safe_write_text("\n".join(report_lines), OUT_REPORT)

    print("\nBenchmark terminado.")
    print(f"Comparacion CSV:  {saved_comparison}")
    print(f"Comparacion XLSX: {saved_comparison_xlsx}")
    print(f"Resumen CSV:      {saved_summary}")
    print(f"Resumen XLSX:     {saved_summary_xlsx}")
    print(f"Reporte:          {saved_report}")
    print("")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
