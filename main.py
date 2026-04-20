# main.py
# Ejecuta la pipeline completa:
# 1. extracción de links
# 2. normalización de URLs
# 3. aplicación de heurísticas

from pathlib import Path
import extract_links
import normalize_urls
import heuristics


def main():
    print("===================================")
    print("INICIO DE PIPELINE PAPER2RO")
    print("===================================\n")

    # ==================================================
    # PASO 1: EXTRACCIÓN DE LINKS
    # ==================================================
    print("[1/3] Extrayendo links desde PDFs...")

    try:
        extract_links.main()
        print("Extracción completada.\n")
    except Exception as e:
        print(f"Error en extracción: {e}")
        return

    # Verificar que se generó all_links.csv
    if not Path(normalize_urls.INPUT_CSV).exists():
        print(f"No existe {normalize_urls.INPUT_CSV}")
        return

    # ==================================================
    # PASO 2: NORMALIZACIÓN
    # ==================================================
    print("[2/3] Normalizando URLs...")

    rows = normalize_urls.load_csv(normalize_urls.INPUT_CSV)
    print(f"URLs originales leídas: {len(rows)}")

    kept, removed = normalize_urls.process_rows(rows)

    normalize_urls.save_csv(kept, normalize_urls.OUTPUT_CSV)
    normalize_urls.save_json(kept, normalize_urls.OUTPUT_JSON)
    normalize_urls.save_csv(removed, normalize_urls.REMOVED_CSV)

    print(f"URLs conservadas: {len(kept)}")
    print(f"URLs eliminadas: {len(removed)}")

    print(f"Archivo generado: {normalize_urls.OUTPUT_CSV}")
    print(f"Archivo generado: {normalize_urls.OUTPUT_JSON}")
    print(f"Archivo generado: {normalize_urls.REMOVED_CSV}\n")

    # ==================================================
    # PASO 3: HEURÍSTICAS
    # ==================================================
    print("[3/3] Aplicando heurísticas...")

    if not Path(heuristics.INPUT_CSV).exists():
        print(f"No existe {heuristics.INPUT_CSV}")
        return

    normalized_rows = heuristics.load_normalized_csv(
        heuristics.INPUT_CSV
    )

    print(f"URLs normalizadas leídas: {len(normalized_rows)}")

    # Cambiar a True para activar metadatos HTTP
    use_http = False

    results_csv, results_json = heuristics.process_rows(
        normalized_rows,
        use_http=use_http
    )

    heuristics.save_csv(results_csv, heuristics.OUTPUT_CSV)
    heuristics.save_json(results_json, heuristics.OUTPUT_JSON)

    dataset_count = sum(
        1 for r in results_csv if r["label"] == "dataset"
    )

    maybe_count = sum(
        1 for r in results_csv if r["label"] == "maybe_dataset"
    )

    not_count = sum(
        1 for r in results_csv if r["label"] == "not_dataset"
    )

    print(f"Archivo generado: {heuristics.OUTPUT_CSV}")
    print(f"Archivo generado: {heuristics.OUTPUT_JSON}\n")

    # ==================================================
    # RESUMEN FINAL
    # ==================================================
    print("Resumen final:")
    print(f"- dataset: {dataset_count}")
    print(f"- maybe_dataset: {maybe_count}")
    print(f"- not_dataset: {not_count}")

    print("\n===================================")
    print("PIPELINE TERMINADA")
    print("===================================")


if __name__ == "__main__":
    main()