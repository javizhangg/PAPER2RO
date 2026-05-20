# benchmark.py
# Compara las URLs detectadas por heurísticas contra el benchmark manual.
#
# CAMBIO IMPORTANTE:
# Ya NO se exige que la URL detectada y la URL manual sean exactamente iguales.
# Se usa comparación aproximada:
#   - misma URL normalizada
#   - una URL contiene a la otra
#   - mismo dominio y ruta parecida
#   - tokens / identificadores relevantes compartidos
#
# Esto evita falsos "no está en manual" cuando, por ejemplo:
#   manual:    https://zenodo.org/records/12345
#   detectada: https://zenodo.org/records/12345/files/data.csv

import re
from pathlib import Path
from urllib.parse import urlparse
from difflib import SequenceMatcher

import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    confusion_matrix,
    classification_report,
)

BASE_DIR = Path(__file__).resolve().parent

MANUAL_FILE = BASE_DIR / "Benchmark" / "UrlManual.xlsx"
HEURISTICS_FILE = BASE_DIR / "outputs" / "heuristics_results.csv"

OUTPUT_COMPARISON_FILE = BASE_DIR / "outputs" / "benchmark_comparison.csv"
OUTPUT_SUMMARY_FILE = BASE_DIR / "outputs" / "benchmark_summary.csv"
OUTPUT_REPORT_FILE = BASE_DIR / "outputs" / "benchmark_report.txt"


# ==========================================================
# Columnas esperadas del CSV de heuristics.py
# ==========================================================
HEURISTIC_COLUMNS = {
    "heuristica_1_extension_url_o_archivo": "heuristica_1_extension_url_o_archivo_matched",
    "heuristica_2_http_metadata": "heuristica_2_http_metadata_matched",
    "heuristica_3_gap_kge": "heuristica_3_gap_kge_matched",
    "prediccion_final_label": "label",
}


# ==========================================================
# Normalización básica
# ==========================================================
def normalize_bool(value) -> bool:
    if pd.isna(value):
        return False

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value != 0

    value = str(value).strip().lower()

    return value in {
        "true", "1", "yes", "y", "si", "sí", "dataset",
        "maybe_dataset", "positive", "positivo"
    }


def normalize_label(value):
    """
    Normaliza etiquetas manuales o predichas a:
    1 -> dataset
    0 -> no dataset
    None -> desconocido
    """
    if pd.isna(value):
        return None

    value = str(value).strip().lower()

    positive_values = {
        "1", "true", "yes", "y", "si", "sí", "dataset",
        "maybe_dataset", "positive", "positivo", "data", "datos"
    }

    negative_values = {
        "0", "false", "no", "not_dataset", "negative",
        "negativo", "no dataset", "not dataset", "no_data", "sin datos"
    }

    if value in positive_values:
        return 1

    if value in negative_values:
        return 0

    return None


# ==========================================================
# Normalización y comparación aproximada de URLs
# ==========================================================
def normalize_url_for_matching(url):
    if pd.isna(url):
        return ""

    url = str(url).strip().lower()

    if not url:
        return ""

    # Quitar espacios raros
    url = re.sub(r"\s+", "", url)

    # Quitar fragmentos y query params
    url = url.split("#")[0]
    url = url.split("?")[0]

    # Normalizar protocolo
    url = url.replace("http://", "https://")

    # Quitar www.
    url = url.replace("https://www.", "https://")

    # Quitar barra final
    url = url.rstrip("/")

    return url


def get_domain_for_matching(url):
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def get_path_for_matching(url):
    try:
        return urlparse(url).path.lower().rstrip("/")
    except Exception:
        return ""


def sequence_similarity(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def extract_url_tokens(url):
    """
    Extrae tokens útiles de la URL.
    Sirve para detectar coincidencias por identificadores, repositorios, records, etc.
    """
    url = normalize_url_for_matching(url)
    parsed = urlparse(url)

    raw = f"{parsed.netloc} {parsed.path}".lower()
    tokens = re.split(r"[/_\-.=&?:#]+", raw)

    tokens = {t for t in tokens if len(t) >= 3}

    stop_tokens = {
        "www", "com", "org", "net", "edu", "gov", "https", "http",
        "dataset", "datasets", "data", "download", "downloads",
        "file", "files", "record", "records", "article", "articles",
        "view", "main", "tree", "blob", "raw", "source", "index",
        "paper", "papers", "docs", "documentation", "html", "php"
    }

    return tokens - stop_tokens


def url_match_score(url1, url2):
    """
    Devuelve una puntuación de parecido entre 0 y 1.
    A partir de 0.75 se considera match aproximado.
    """
    u1 = normalize_url_for_matching(url1)
    u2 = normalize_url_for_matching(url2)

    if not u1 or not u2:
        return 0.0, "empty_url"

    # 1. Coincidencia exacta tras normalización
    if u1 == u2:
        return 1.0, "exact_normalized_match"

    # 2. Una URL contiene a la otra
    # Ejemplo: /records/12345 y /records/12345/files/data.csv
    if u1 in u2 or u2 in u1:
        return 0.95, "url_contains_other_url"

    d1 = get_domain_for_matching(u1)
    d2 = get_domain_for_matching(u2)

    p1 = get_path_for_matching(u1)
    p2 = get_path_for_matching(u2)

    # Si los dominios son distintos, solo permitimos match si comparten
    # identificadores largos muy claros. En general, distinto dominio = no match.
    same_domain = d1 and d2 and d1 == d2

    tokens1 = extract_url_tokens(u1)
    tokens2 = extract_url_tokens(u2)
    common_tokens = tokens1.intersection(tokens2)

    long_common_tokens = [t for t in common_tokens if len(t) >= 6]

    if not same_domain:
        if long_common_tokens:
            return 0.78, "different_domain_but_shared_long_identifier"
        return 0.0, "different_domain"

    # 3. Mismo dominio y una ruta contiene a la otra
    if p1 and p2 and (p1 in p2 or p2 in p1):
        return 0.92, "same_domain_path_contains_other_path"

    # 4. Mismo dominio y rutas parecidas
    path_sim = sequence_similarity(p1, p2)
    if path_sim >= 0.75:
        return path_sim, "same_domain_similar_path"

    # 5. Tokens relevantes compartidos
    if tokens1 and tokens2:
        min_size = min(len(tokens1), len(tokens2))
        overlap_ratio = len(common_tokens) / min_size if min_size > 0 else 0

        if overlap_ratio >= 0.60:
            return max(0.75, overlap_ratio), "same_domain_token_overlap"

    # 6. Identificador largo compartido
    if long_common_tokens:
        return 0.85, "same_domain_shared_long_identifier"

    return 0.0, "no_match"


def urls_roughly_match(url1, url2, threshold=0.75):
    score, _ = url_match_score(url1, url2)
    return score >= threshold


# ==========================================================
# Detección flexible de columnas en Excel/CSV
# ==========================================================
def normalize_column_name(col):
    return str(col).strip().lower().replace(" ", "_").replace("-", "_")


def find_first_existing_column(df, candidates):
    normalized_map = {normalize_column_name(c): c for c in df.columns}

    for candidate in candidates:
        c_norm = normalize_column_name(candidate)
        if c_norm in normalized_map:
            return normalized_map[c_norm]

    # Segunda pasada: columnas que contengan el texto candidato
    for candidate in candidates:
        c_norm = normalize_column_name(candidate)
        for normalized, original in normalized_map.items():
            if c_norm in normalized:
                return original

    return None


def detect_url_column(df):
    candidates = [
        "normalized_url", "url", "URL", "manual_url", "url_manual",
        "original_url", "link", "enlace", "dataset_url", "data_url"
    ]
    return find_first_existing_column(df, candidates)


def detect_label_column(df):
    candidates = [
        "label", "manual_label", "is_dataset", "dataset", "es_dataset",
        "is_data", "ground_truth", "class", "clase", "tipo", "resultado"
    ]
    return find_first_existing_column(df, candidates)


# ==========================================================
# Carga de manual y heurísticas
# ==========================================================
def load_manual_file(path):
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo manual: {path}")

    manual_df = pd.read_excel(path)

    url_col = detect_url_column(manual_df)
    if not url_col:
        raise ValueError(
            "No encuentro la columna de URL en el Excel manual. "
            "Usa un nombre como 'url', 'normalized_url', 'manual_url' o 'original_url'."
        )

    label_col = detect_label_column(manual_df)

    manual_rows = []

    for _, row in manual_df.iterrows():
        url = row.get(url_col, "")
        norm_url = normalize_url_for_matching(url)

        if not norm_url:
            continue

        if label_col:
            manual_label = normalize_label(row.get(label_col, None))
            # Si la etiqueta no se entiende, asumimos que esa fila manual es positiva.
            # Esto permite usar un Excel que solo contiene datasets anotados.
            if manual_label is None:
                manual_label = 1
        else:
            manual_label = 1

        manual_rows.append({
            "manual_url": str(url).strip(),
            "manual_url_norm": norm_url,
            "manual_label": manual_label,
        })

    return manual_rows, url_col, label_col


def load_heuristics_file(path):
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo de heurísticas: {path}")

    df = pd.read_csv(path)

    if "normalized_url" not in df.columns:
        raise ValueError("El CSV de heurísticas debe tener una columna 'normalized_url'.")

    return df


# ==========================================================
# Buscar mejor coincidencia manual para cada URL detectada
# ==========================================================
def find_best_manual_match(url, manual_rows, threshold=0.75):
    best = {
        "matched_manual_url": "",
        "manual_label": 0,
        "match_score": 0.0,
        "match_reason": "no_match",
        "found_in_manual": False,
    }

    for manual in manual_rows:
        score, reason = url_match_score(url, manual["manual_url_norm"])

        if score > best["match_score"]:
            best = {
                "matched_manual_url": manual["manual_url"],
                "manual_label": manual["manual_label"],
                "match_score": score,
                "match_reason": reason,
                "found_in_manual": score >= threshold,
            }

    if not best["found_in_manual"]:
        best["matched_manual_url"] = ""
        best["manual_label"] = 0
        best["match_reason"] = "no_match_above_threshold"

    return best


# ==========================================================
# Predicciones
# ==========================================================
def get_prediction_from_row(row, column_name):
    if column_name not in row.index:
        return 0

    value = row[column_name]

    # Caso especial: label final
    if column_name == "label":
        label = normalize_label(value)
        return 1 if label == 1 else 0

    return 1 if normalize_bool(value) else 0


# ==========================================================
# Benchmark
# ==========================================================
def build_comparison_dataframe(heuristics_df, manual_rows):
    comparison_rows = []

    for _, row in heuristics_df.iterrows():
        url = row.get("normalized_url", "")
        match = find_best_manual_match(url, manual_rows)

        output_row = {
            "paper": row.get("paper", ""),
            "section": row.get("section", ""),
            "original_url": row.get("original_url", ""),
            "normalized_url": url,
            "domain": row.get("domain", ""),
            "extension": row.get("extension", ""),

            "found_in_manual": match["found_in_manual"],
            "matched_manual_url": match["matched_manual_url"],
            "manual_label": match["manual_label"],
            "match_score": round(match["match_score"], 4),
            "match_reason": match["match_reason"],
        }

        # Añadimos predicción de cada heurística principal
        for heuristic_name, column in HEURISTIC_COLUMNS.items():
            output_row[f"{heuristic_name}_prediction"] = get_prediction_from_row(row, column)

            if column in row.index:
                output_row[f"{heuristic_name}_raw_value"] = row[column]
            else:
                output_row[f"{heuristic_name}_raw_value"] = "COLUMN_NOT_FOUND"

        # Datos útiles de trazabilidad si existen
        for optional_col in [
            "decision_reason",
            "total_score",
            "heuristica_1_extension_url_o_archivo_score",
            "heuristica_2_http_metadata_score",
            "heuristica_3_gap_kge_score",
            "gap_kge_style_matched",
            "gap_kge_style_score",
        ]:
            if optional_col in row.index:
                output_row[optional_col] = row.get(optional_col, "")

        comparison_rows.append(output_row)

    return pd.DataFrame(comparison_rows)


def evaluate_predictions(comparison_df):
    summary_rows = []
    report_lines = []

    y_true = comparison_df["manual_label"].astype(int).tolist()

    for heuristic_name in HEURISTIC_COLUMNS.keys():
        pred_col = f"{heuristic_name}_prediction"

        if pred_col not in comparison_df.columns:
            continue

        y_pred = comparison_df[pred_col].astype(int).tolist()

        accuracy = accuracy_score(y_true, y_pred)
        precision = precision_score(y_true, y_pred, zero_division=0)
        recall = recall_score(y_true, y_pred, zero_division=0)
        f1 = f1_score(y_true, y_pred, zero_division=0)

        cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
        tn, fp, fn, tp = cm.ravel()

        urls_found_in_manual = int(comparison_df["found_in_manual"].sum())
        urls_not_found_in_manual = int((~comparison_df["found_in_manual"].astype(bool)).sum())

        summary_rows.append({
            "heuristic": heuristic_name,
            "total_urls": len(comparison_df),
            "urls_found_in_manual_roughly": urls_found_in_manual,
            "urls_not_found_in_manual_roughly": urls_not_found_in_manual,
            "accuracy": accuracy,
            "precision": precision,
            "recall": recall,
            "f1_score": f1,
            "true_positive": int(tp),
            "true_negative": int(tn),
            "false_positive": int(fp),
            "false_negative": int(fn),
        })

        report_lines.append("=" * 80)
        report_lines.append(f"RESULTADOS PARA: {heuristic_name}")
        report_lines.append("=" * 80)
        report_lines.append(f"Accuracy : {accuracy:.4f}")
        report_lines.append(f"Precision: {precision:.4f}")
        report_lines.append(f"Recall   : {recall:.4f}")
        report_lines.append(f"F1-score : {f1:.4f}")
        report_lines.append("")
        report_lines.append("Matriz de confusión [labels 0, 1]:")
        report_lines.append(str(cm))
        report_lines.append("")
        report_lines.append("Classification report:")
        report_lines.append(classification_report(y_true, y_pred, zero_division=0))
        report_lines.append("")

    summary_df = pd.DataFrame(summary_rows)
    return summary_df, "\n".join(report_lines)


# ==========================================================
# Guardado
# ==========================================================
def ensure_output_dir():
    (BASE_DIR / "outputs").mkdir(parents=True, exist_ok=True)


def save_outputs(comparison_df, summary_df, report_text):
    ensure_output_dir()

    comparison_df.to_csv(OUTPUT_COMPARISON_FILE, index=False, encoding="utf-8")
    summary_df.to_csv(OUTPUT_SUMMARY_FILE, index=False, encoding="utf-8")

    with open(OUTPUT_REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report_text)


# ==========================================================
# MAIN
# ==========================================================
def main():
    print("Cargando benchmark manual...")
    manual_rows, manual_url_col, manual_label_col = load_manual_file(MANUAL_FILE)

    print(f"Filas manuales cargadas: {len(manual_rows)}")
    print(f"Columna URL manual: {manual_url_col}")
    print(f"Columna label manual: {manual_label_col if manual_label_col else 'NO DETECTADA; se asume que todas son dataset'}")

    print("\nCargando resultados de heurísticas...")
    heuristics_df = load_heuristics_file(HEURISTICS_FILE)
    print(f"URLs en heurísticas: {len(heuristics_df)}")

    print("\nConstruyendo comparación aproximada...")
    comparison_df = build_comparison_dataframe(heuristics_df, manual_rows)

    print("Evaluando métricas...")
    summary_df, report_text = evaluate_predictions(comparison_df)

    save_outputs(comparison_df, summary_df, report_text)

    print("\nResultados guardados en:")
    print(f"- {OUTPUT_COMPARISON_FILE}")
    print(f"- {OUTPUT_SUMMARY_FILE}")
    print(f"- {OUTPUT_REPORT_FILE}")

    print("\nResumen:")
    if not summary_df.empty:
        print(summary_df.to_string(index=False))

        best = summary_df.sort_values("f1_score", ascending=False).iloc[0]
        print("\n===== MEJOR HEURÍSTICA SEGÚN F1-SCORE =====")
        print(best)
    else:
        print("No se pudieron calcular métricas.")


if __name__ == "__main__":
    main()
