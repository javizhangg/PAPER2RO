import os
import sys
import time
import shutil
import zipfile
from datetime import datetime
import subprocess
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue
import threading
import os
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    import yaml
except Exception:
    yaml = None


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
PDF_DIR = BASE_DIR / "pdfs"
OUTPUTS_DIR = BASE_DIR / "outputs"
YA2RO_DIR = BASE_DIR / "ya2ro_generated"
YAMLS_DIR = YA2RO_DIR / "yamls"
RO_OUTPUT_DIR = YA2RO_DIR / "ro_output"
ZIP_RESULTS = BASE_DIR / "streamlit_resultados.zip"
BACKUP_PREFIX = "_backup_limpieza"
GAP_BATCH_DIR = BASE_DIR / "gap_kge_batches"
DEFAULT_GAP_KGE_WORKDIR = BASE_DIR / "software_mentions_client"

SCRIPTS = {
    "extract_links": BASE_DIR / "extractLinks.py",
    "normalize": BASE_DIR / "normalizeUrl.py",
    "heuristic_1": BASE_DIR / "heuristic_1_page_download.py",
    "heuristic_2": BASE_DIR / "heuristic_2_http_metadata.py",
    "generate_yamls": BASE_DIR / "generate_yamls.py",
}


# ============================================================
# UTILIDADES
# ============================================================

def ensure_dirs():
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    YA2RO_DIR.mkdir(parents=True, exist_ok=True)
    YAMLS_DIR.mkdir(parents=True, exist_ok=True)
    GAP_BATCH_DIR.mkdir(parents=True, exist_ok=True)


def safe_remove_path(path: Path):
    """Borra una ruta evitando que la app se caiga si Windows/OneDrive bloquea algún archivo."""
    try:
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except PermissionError:
        st.warning(f"No se pudo borrar porque está bloqueado por Windows/OneDrive: {path}")
    except Exception as e:
        st.warning(f"No se pudo borrar {path}: {e}")


def backup_before_clean():
    """Crea una copia de seguridad antes de limpiar PDFs/resultados."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BASE_DIR / f"{BACKUP_PREFIX}_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    folders_to_backup = [
        PDF_DIR,
        OUTPUTS_DIR,
        YA2RO_DIR,
        BASE_DIR / "papers",
    ]

    # Copia también ZIPs de resultados si existen.
    files_to_backup = list(BASE_DIR.glob("streamlit_resultados*.zip"))

    copied_anything = False

    for folder in folders_to_backup:
        if folder.exists() and folder.is_dir():
            destination = backup_dir / folder.name
            try:
                shutil.copytree(folder, destination, dirs_exist_ok=True)
                copied_anything = True
            except Exception as e:
                st.warning(f"No se pudo copiar {folder} al backup: {e}")

    for file in files_to_backup:
        if file.exists() and file.is_file():
            try:
                shutil.copy2(file, backup_dir / file.name)
                copied_anything = True
            except Exception as e:
                st.warning(f"No se pudo copiar {file.name} al backup: {e}")

    # Si no había nada que copiar, dejamos una nota para que se sepa que se creó vacío.
    if not copied_anything:
        (backup_dir / "backup_vacio.txt").write_text(
            "No había PDFs/resultados que copiar en el momento de limpiar.\n",
            encoding="utf-8",
        )

    return backup_dir


def clean_previous_outputs(remove_pdfs: bool = False, create_backup: bool = False):
    """Limpia resultados anteriores para evitar mezclar ejecuciones. Opcionalmente crea backup."""
    backup_dir = None

    if create_backup:
        backup_dir = backup_before_clean()

    # No borramos ZIP_RESULTS directamente porque en Windows/OneDrive puede estar bloqueado.
    # Borramos carpetas de trabajo y dejamos los ZIP antiguos como respaldo adicional.
    paths_to_remove = [OUTPUTS_DIR, YA2RO_DIR]

    for path in paths_to_remove:
        safe_remove_path(path)

    if remove_pdfs and PDF_DIR.exists():
        safe_remove_path(PDF_DIR)

    ensure_dirs()
    return backup_dir


def save_uploaded_pdfs(uploaded_files):
    ensure_dirs()
    saved = []

    for uploaded in uploaded_files:
        target = PDF_DIR / uploaded.name
        with open(target, "wb") as f:
            f.write(uploaded.getbuffer())
        saved.append(target)

    return saved


def file_exists(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def read_csv_if_exists(path: Path):
    if not file_exists(path):
        return None
    try:
        return pd.read_csv(path)
    except Exception:
        try:
            return pd.read_csv(path, encoding="utf-8-sig")
        except Exception:
            return None


def run_command_live(command, title, log_box, cwd=BASE_DIR, background_queue=None, background_log_box=None, background_lines=None):
    """Ejecuta un comando y muestra stdout/stderr en vivo dentro de Streamlit.

    Acepta tanto listas de argumentos como comandos en string.
    Los comandos en string se ejecutan con shell=True, útil para módulos Python, Docker
    o plantillas escritas en la interfaz.
    """
    log_lines = []

    if isinstance(command, str):
        display_command = command
        popen_command = command
        use_shell = True
    else:
        display_command = " ".join(map(str, command))
        popen_command = [str(x) for x in command]
        use_shell = False

    log_lines.append(f"$ {display_command}\n")
    log_box.code("".join(log_lines), language="bash")
    if background_queue is not None:
        drain_background_log(background_queue, background_log_box, background_lines or [])

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    process = subprocess.Popen(
        popen_command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
        shell=use_shell,
        env=env,
    )

    for line in process.stdout:
        log_lines.append(line)
        if len(log_lines) > 450:
            log_lines = log_lines[-450:]
        log_box.code("".join(log_lines), language="bash")
        if background_queue is not None:
            drain_background_log(background_queue, background_log_box, background_lines or [])

    return_code = process.wait()
    if background_queue is not None:
        drain_background_log(background_queue, background_log_box, background_lines or [])

    if return_code == 0:
        log_lines.append(f"\n[OK] {title} terminado correctamente.\n")
    else:
        log_lines.append(f"\n[ERROR] {title} terminó con código {return_code}.\n")

    log_box.code("".join(log_lines), language="bash")
    return return_code == 0, "".join(log_lines)


def run_python_script(script_path: Path, title: str, log_box, extra_args=None, background_queue=None, background_log_box=None, background_lines=None):
    if not script_path.exists():
        log_box.error(f"No existe el script: {script_path}")
        return False, f"No existe el script: {script_path}"

    command = [sys.executable, script_path]
    if extra_args:
        command.extend(extra_args)

    return run_command_live(
        command,
        title,
        log_box,
        background_queue=background_queue,
        background_log_box=background_log_box,
        background_lines=background_lines,
    )




def _reader_thread(process, output_queue):
    """Lee stdout de un proceso y lo mete en una cola para actualizar Streamlit desde el hilo principal."""
    try:
        for line in process.stdout:
            output_queue.put(line)
    finally:
        output_queue.put(None)


def _render_log(log_box, lines):
    if len(lines) > 450:
        lines[:] = lines[-450:]
    log_box.code("".join(lines), language="bash")


def drain_background_log(event_queue, log_box, lines):
    """Vacía logs pendientes de una tarea en segundo plano sin bloquear."""
    if event_queue is None or log_box is None:
        return

    changed = False
    while True:
        try:
            item = event_queue.get_nowait()
        except queue.Empty:
            break

        if isinstance(item, dict):
            text = item.get("text", "")
        else:
            text = str(item)

        if text:
            lines.append(text)
            changed = True

    if changed:
        _render_log(log_box, lines)


def run_commands_parallel_live(tasks, background_queue=None, background_log_box=None, background_lines=None):
    """
    Ejecuta varios comandos a la vez y muestra logs en vivo.

    tasks = [
      {"key": "h1", "title": "Heurística 1", "command": [...], "log_box": st.empty(), "cwd": BASE_DIR},
      ...
    ]
    """
    processes = {}
    queues = {}
    threads = {}
    log_lines = {}
    finished_streams = set()
    results = {}

    for task in tasks:
        key = task["key"]
        title = task["title"]
        command = task["command"]
        cwd = task.get("cwd", BASE_DIR)
        log_box = task["log_box"]

        if isinstance(command, str):
            display_command = command
            popen_command = command
            use_shell = True
        else:
            display_command = " ".join(map(str, command))
            popen_command = [str(x) for x in command]
            use_shell = False

        log_lines[key] = [f"$ {display_command}\n"]
        log_box.code("".join(log_lines[key]), language="bash")

        process = subprocess.Popen(
            popen_command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            shell=use_shell,
        )

        q = queue.Queue()
        t = threading.Thread(target=_reader_thread, args=(process, q), daemon=True)
        t.start()

        processes[key] = (process, title, log_box)
        queues[key] = q
        threads[key] = t

    while len(results) < len(processes):
        if background_queue is not None:
            drain_background_log(background_queue, background_log_box, background_lines or [])

        for key, q in queues.items():
            if key in results:
                continue

            changed = False
            while True:
                try:
                    item = q.get_nowait()
                except queue.Empty:
                    break

                if item is None:
                    finished_streams.add(key)
                    continue

                log_lines[key].append(item)
                changed = True

            if changed:
                _render_log(processes[key][2], log_lines[key])

            process, title, log_box = processes[key]
            return_code = process.poll()
            if return_code is not None and key in finished_streams:
                ok = return_code == 0
                if ok:
                    log_lines[key].append(f"\n[OK] {title} terminado correctamente.\n")
                else:
                    log_lines[key].append(f"\n[ERROR] {title} terminó con código {return_code}.\n")
                _render_log(log_box, log_lines[key])
                results[key] = {
                    "ok": ok,
                    "return_code": return_code,
                    "log": "".join(log_lines[key]),
                }

        time.sleep(0.15)

    if background_queue is not None:
        drain_background_log(background_queue, background_log_box, background_lines or [])

    return results


def chunk_list(items, chunk_size: int):
    """Divide una lista en lotes de tamaño chunk_size."""
    chunk_size = max(1, int(chunk_size))
    for i in range(0, len(items), chunk_size):
        yield items[i:i + chunk_size]


def move_file_safe(src: Path, dst: Path):
    """Mueve un archivo evitando conflictos si el destino ya existe."""
    dst.parent.mkdir(parents=True, exist_ok=True)

    if dst.exists():
        # Si ya existe, lo sustituimos. En este flujo interesa que el último resultado
        # del lote sea el que quede en pdfs/.
        if dst.is_file():
            dst.unlink()
        else:
            shutil.rmtree(dst, ignore_errors=True)

    shutil.move(str(src), str(dst))


def run_gap_kge_for_pdf_batch(batch_pdfs: list[Path], batch_index: int, command_template: str, gap_workdir: Path):
    """
    Ejecuta GAP-KGE/datastet por lotes.

    Flujo:
      1. Crea una carpeta temporal para el lote.
      2. Mueve ahí 2 PDFs, o los que indique batch_size.
      3. Ejecuta el comando GAP-KGE sobre esa carpeta.
      4. Devuelve los PDFs a pdfs/.
      5. Devuelve también los .dataset.json generados a pdfs/.

    Placeholders disponibles en el comando:
      {batch_dir}   carpeta temporal con solo los PDFs del lote
      {pdf_dir}     carpeta principal pdfs/
      {base_dir}    carpeta del proyecto
      {outputs_dir} carpeta outputs/
      {gap_workdir} carpeta desde donde se ejecuta GAP-KGE/software_mentions_client
      {batch_index} número de lote
    """
    batch_dir = GAP_BATCH_DIR / f"batch_{batch_index:03d}"
    safe_remove_path(batch_dir)
    batch_dir.mkdir(parents=True, exist_ok=True)

    moved_pdfs = []
    expected_json_names = [f"{pdf.stem}.dataset.json" for pdf in batch_pdfs]

    # Si todos los .dataset.json ya existen, no ejecutamos nada.
    existing_jsons = [PDF_DIR / name for name in expected_json_names]
    if existing_jsons and all(path.exists() and path.stat().st_size > 0 for path in existing_jsons):
        return {
            "batch": batch_index,
            "ok": True,
            "skipped": True,
            "pdfs": [pdf.name for pdf in batch_pdfs],
            "dataset_jsons": [str(path) for path in existing_jsons],
            "log": "[SKIP] Ya existían todos los .dataset.json del lote.",
        }

    if not command_template.strip():
        return {
            "batch": batch_index,
            "ok": False,
            "skipped": True,
            "pdfs": [pdf.name for pdf in batch_pdfs],
            "dataset_jsons": [],
            "log": "[SKIP] No se configuró comando GAP-KGE/datastet.",
        }

    # GAP-KGE/software_mentions_client debe ejecutarse desde su propia carpeta.
    # Si no existe, usamos BASE_DIR para no romper la app y dejamos aviso en el log.
    gap_workdir = Path(gap_workdir)
    workdir_warning = ""
    if not gap_workdir.exists():
        workdir_warning = f"[WARN] No existe la carpeta de ejecución GAP-KGE: {gap_workdir}. Se usará BASE_DIR: {BASE_DIR}\n"
        gap_workdir = BASE_DIR

    try:
        # 1) Mover PDFs del lote a la carpeta temporal.
        for pdf in batch_pdfs:
            if not pdf.exists():
                continue
            dst = batch_dir / pdf.name
            move_file_safe(pdf, dst)
            moved_pdfs.append(dst)

        # 2) Ejecutar comando sobre la carpeta temporal del lote.
        command = command_template.format(
            batch_dir=str(batch_dir),
            pdf_dir=str(PDF_DIR),
            base_dir=str(BASE_DIR),
            outputs_dir=str(OUTPUTS_DIR),
            gap_workdir=str(gap_workdir),
            batch_index=batch_index,
        )

        completed = subprocess.run(
            command,
            cwd=str(gap_workdir),
            shell=True,
            capture_output=True,
            text=True,
            timeout=None,
            encoding="utf-8",
            errors="replace",
        )

        log = workdir_warning + f"[INFO] CWD GAP-KGE: {gap_workdir}\n" + f"[INFO] Comando: {command}\n" + (completed.stdout or "") + (completed.stderr or "")

        # 3) Buscar todos los .dataset.json generados dentro del lote.
        generated_jsons = sorted(batch_dir.rglob("*.dataset.json"))
        returned_jsons = []

        for json_path in generated_jsons:
            target = PDF_DIR / json_path.name
            move_file_safe(json_path, target)
            returned_jsons.append(str(target))

        # 4) Devolver PDFs originales a pdfs/.
        for moved_pdf in list(moved_pdfs):
            if moved_pdf.exists():
                move_file_safe(moved_pdf, PDF_DIR / moved_pdf.name)

        ok = completed.returncode == 0 and len(returned_jsons) > 0

        return {
            "batch": batch_index,
            "ok": ok,
            "skipped": False,
            "pdfs": [pdf.name for pdf in batch_pdfs],
            "dataset_jsons": returned_jsons,
            "log": log if log else f"Comando terminado con código {completed.returncode}",
        }

    except Exception as e:
        return {
            "batch": batch_index,
            "ok": False,
            "skipped": False,
            "pdfs": [pdf.name for pdf in batch_pdfs],
            "dataset_jsons": [],
            "log": str(e),
        }

    finally:
        # Pase lo que pase, intentamos devolver PDFs a la carpeta principal.
        for moved_pdf in list(moved_pdfs):
            try:
                if moved_pdf.exists():
                    move_file_safe(moved_pdf, PDF_DIR / moved_pdf.name)
            except Exception:
                pass

        # Si quedan .dataset.json sueltos, intentamos devolverlos también.
        try:
            for json_path in sorted(batch_dir.rglob("*.dataset.json")):
                if json_path.exists():
                    move_file_safe(json_path, PDF_DIR / json_path.name)
        except Exception:
            pass

        # No borramos la carpeta de lote si ha quedado algo raro; así puedes inspeccionarla.
        try:
            if batch_dir.exists() and not any(batch_dir.iterdir()):
                batch_dir.rmdir()
        except Exception:
            pass




def copy_file_safe(src: Path, dst: Path):
    """Copia un archivo sustituyendo el destino si ya existe."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if dst.is_file():
            dst.unlink()
        else:
            shutil.rmtree(dst, ignore_errors=True)
    shutil.copy2(str(src), str(dst))


def run_gap_kge_for_pdf_batch_copy(batch_pdfs: list[Path], batch_index: int, command_template: str, gap_workdir: Path):
    """
    Versión segura para ejecución paralela con extracción de enlaces.

    En vez de mover los PDFs originales fuera de pdfs/, los copia a una carpeta temporal.
    Así extractLinks.py puede seguir leyendo pdfs/ mientras GAP-KGE/datastet procesa los lotes.
    Al terminar, copia los .dataset.json generados de vuelta a pdfs/.
    """
    batch_dir = GAP_BATCH_DIR / f"batch_{batch_index:03d}"
    safe_remove_path(batch_dir)
    batch_dir.mkdir(parents=True, exist_ok=True)

    expected_json_names = [f"{pdf.stem}.dataset.json" for pdf in batch_pdfs]
    existing_jsons = [PDF_DIR / name for name in expected_json_names]

    if existing_jsons and all(path.exists() and path.stat().st_size > 0 for path in existing_jsons):
        return {
            "batch": batch_index,
            "ok": True,
            "skipped": True,
            "pdfs": [pdf.name for pdf in batch_pdfs],
            "dataset_jsons": [str(path) for path in existing_jsons],
            "log": "[SKIP] Ya existían todos los .dataset.json del lote.",
        }

    if not command_template.strip():
        return {
            "batch": batch_index,
            "ok": False,
            "skipped": True,
            "pdfs": [pdf.name for pdf in batch_pdfs],
            "dataset_jsons": [],
            "log": "[SKIP] No se configuró comando GAP-KGE/datastet.",
        }

    gap_workdir = Path(gap_workdir)
    workdir_warning = ""
    if not gap_workdir.exists():
        workdir_warning = f"[WARN] No existe la carpeta de ejecución GAP-KGE: {gap_workdir}. Se usará BASE_DIR: {BASE_DIR}\n"
        gap_workdir = BASE_DIR

    try:
        for pdf in batch_pdfs:
            if pdf.exists():
                copy_file_safe(pdf, batch_dir / pdf.name)

        command = command_template.format(
            batch_dir=str(batch_dir),
            pdf_dir=str(PDF_DIR),
            base_dir=str(BASE_DIR),
            outputs_dir=str(OUTPUTS_DIR),
            gap_workdir=str(gap_workdir),
            batch_index=batch_index,
        )

        completed = subprocess.run(
            command,
            cwd=str(gap_workdir),
            shell=True,
            capture_output=True,
            text=True,
            timeout=None,
            encoding="utf-8",
            errors="replace",
        )

        log = (
            workdir_warning
            + f"[INFO] CWD GAP-KGE: {gap_workdir}\n"
            + f"[INFO] Carpeta lote: {batch_dir}\n"
            + f"[INFO] Comando: {command}\n"
            + (completed.stdout or "")
            + (completed.stderr or "")
        )

        generated_jsons = sorted(batch_dir.rglob("*.dataset.json"))
        returned_jsons = []

        for json_path in generated_jsons:
            target = PDF_DIR / json_path.name
            copy_file_safe(json_path, target)
            returned_jsons.append(str(target))

        ok = completed.returncode == 0 and len(returned_jsons) > 0

        return {
            "batch": batch_index,
            "ok": ok,
            "skipped": False,
            "pdfs": [pdf.name for pdf in batch_pdfs],
            "dataset_jsons": returned_jsons,
            "log": log if log else f"Comando terminado con código {completed.returncode}",
        }

    except Exception as e:
        return {
            "batch": batch_index,
            "ok": False,
            "skipped": False,
            "pdfs": [pdf.name for pdf in batch_pdfs],
            "dataset_jsons": [],
            "log": str(e),
        }


def run_gap_kge_in_batches_background(command_template: str, batch_size: int, gap_workdir: Path, event_queue=None):
    """Ejecuta GAP-KGE/datastet en segundo plano por lotes copiando PDFs."""
    def emit(text):
        if event_queue is not None:
            event_queue.put({"text": text})

    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if not pdfs:
        emit("No hay PDFs para ejecutar GAP-KGE/datastet.\n")
        return []

    batches = list(chunk_list(pdfs, batch_size))
    results = []

    emit(
        "Ejecutando GAP-KGE/datastet en segundo plano por lotes.\n"
        f"PDFs totales: {len(pdfs)}\n"
        f"Tamaño de lote: {batch_size}\n"
        f"Lotes totales: {len(batches)}\n"
        f"Carpeta temporal: {GAP_BATCH_DIR}\n"
        f"Carpeta ejecución GAP-KGE: {gap_workdir}\n"
        "Modo seguro paralelo: se COPIAN los PDFs al lote, no se mueven, para no interferir con extractLinks.py.\n\n"
    )

    for batch_index, batch_pdfs in enumerate(batches, start=1):
        pdf_names = [pdf.name for pdf in batch_pdfs]
        emit("\n" + "=" * 70 + "\n")
        emit(f"[LOTE {batch_index}/{len(batches)}] PDFs: {', '.join(pdf_names)}\n")
        emit("Copiando PDFs a carpeta temporal y ejecutando GAP-KGE/datastet...\n")

        result = run_gap_kge_for_pdf_batch_copy(batch_pdfs, batch_index, command_template, gap_workdir)
        results.append(result)

        status = "OK" if result.get("ok") else "WARN"
        if result.get("skipped") and result.get("ok"):
            status = "SKIP"

        emit(f"[{status}] Lote {batch_index}\n")
        dataset_jsons = result.get("dataset_jsons", [])
        if dataset_jsons:
            emit(".dataset.json copiados a pdfs/:\n")
            for json_path in dataset_jsons:
                emit(f"  - {json_path}\n")
        else:
            emit("No se detectaron .dataset.json generados en este lote.\n")
        emit(str(result.get("log", ""))[:6000] + "\n")

    emit("\n[DONE] GAP-KGE/datastet en segundo plano finalizado.\n")
    return results


def run_gap_kge_in_batches(command_template: str, batch_size: int, log_box, gap_workdir: Path):
    """Ejecuta GAP-KGE/datastet moviendo PDFs de dos en dos a una carpeta temporal."""
    pdfs = sorted(PDF_DIR.glob("*.pdf"))

    if not pdfs:
        log_box.warning("No hay PDFs para ejecutar GAP-KGE/datastet.")
        return []

    batches = list(chunk_list(pdfs, batch_size))
    results = []

    logs = [
        f"Ejecutando GAP-KGE/datastet por lotes.\n",
        f"PDFs totales: {len(pdfs)}\n",
        f"Tamaño de lote: {batch_size}\n",
        f"Lotes totales: {len(batches)}\n",
        f"Carpeta temporal: {GAP_BATCH_DIR}\n",
        f"Carpeta ejecución GAP-KGE: {gap_workdir}\n\n",
    ]
    log_box.code("".join(logs), language="bash")

    for batch_index, batch_pdfs in enumerate(batches, start=1):
        pdf_names = [pdf.name for pdf in batch_pdfs]
        logs.append("\n" + "=" * 70 + "\n")
        logs.append(f"[LOTE {batch_index}/{len(batches)}] PDFs: {', '.join(pdf_names)}\n")
        logs.append("Moviendo PDFs a carpeta temporal y ejecutando GAP-KGE/datastet...\n")
        log_box.code("".join(logs[-80:]), language="bash")

        result = run_gap_kge_for_pdf_batch(batch_pdfs, batch_index, command_template, gap_workdir)
        results.append(result)

        status = "OK" if result.get("ok") else "WARN"
        if result.get("skipped") and result.get("ok"):
            status = "SKIP"

        logs.append(f"[{status}] Lote {batch_index}\n")
        logs.append(f"PDFs devueltos a: {PDF_DIR}\n")

        dataset_jsons = result.get("dataset_jsons", [])
        if dataset_jsons:
            logs.append(".dataset.json devueltos/generados:\n")
            for json_path in dataset_jsons:
                logs.append(f"  - {json_path}\n")
        else:
            logs.append("No se detectaron .dataset.json generados en este lote.\n")

        logs.append(str(result.get("log", ""))[:6000] + "\n")
        log_box.code("".join(logs[-80:]), language="bash")

    return results


def run_ya2ro(log_box, use_no_somef: bool = True):
    if not YAMLS_DIR.exists() or not list(YAMLS_DIR.glob("*.yaml")):
        log_box.warning("No hay YAMLs para convertir con ya2ro.")
        return False, "No hay YAMLs"

    command = ["ya2ro", "-i", str(YAMLS_DIR), "-o", str(RO_OUTPUT_DIR)]
    if use_no_somef:
        command.append("-ns")

    return run_command_live(command, "YA2RO", log_box)




def list_uploaded_pdfs():
    """Devuelve una tabla con los PDFs que ya están guardados en la carpeta pdfs/."""
    ensure_dirs()

    rows = []
    for pdf in sorted(PDF_DIR.glob("*.pdf")):
        try:
            stat = pdf.stat()
            rows.append({
                "pdf": pdf.name,
                "ruta": str(pdf.relative_to(BASE_DIR)),
                "tamaño_mb": round(stat.st_size / (1024 * 1024), 2),
                "fecha_modificación": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception:
            rows.append({
                "pdf": pdf.name,
                "ruta": str(pdf),
                "tamaño_mb": "",
                "fecha_modificación": "",
            })

    return pd.DataFrame(rows)


def show_uploaded_pdfs_panel(expanded: bool = True):
    """Muestra en pantalla los PDFs que se han subido/guardado."""
    df_pdfs = list_uploaded_pdfs()

    with st.expander("📄 PDFs subidos actualmente", expanded=expanded):
        if df_pdfs.empty:
            st.info("Todavía no hay PDFs guardados en la carpeta `pdfs/`.")
        else:
            st.dataframe(df_pdfs, use_container_width=True, hide_index=True)
            st.caption(f"Total PDFs guardados: {len(df_pdfs)}")


def list_generated_files():
    interesting_roots = [PDF_DIR, OUTPUTS_DIR, YA2RO_DIR]
    rows = []

    for root in interesting_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                rows.append({
                    "archivo": str(path.relative_to(BASE_DIR)),
                    "tipo": path.suffix or "sin extensión",
                    "tamaño_kb": round(path.stat().st_size / 1024, 2),
                })

    return pd.DataFrame(rows)


def create_results_zip():
    # En Windows/OneDrive a veces el ZIP anterior queda bloqueado si está abierto,
    # descargándose o sincronizándose. Para evitar PermissionError, no lo borramos:
    # creamos siempre un ZIP nuevo con timestamp.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = BASE_DIR / f"streamlit_resultados_{timestamp}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root in [PDF_DIR, OUTPUTS_DIR, YA2RO_DIR]:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    zf.write(path, arcname=str(path.relative_to(BASE_DIR)))

    return zip_path


def show_csv_preview(path: Path, title: str):
    df = read_csv_if_exists(path)
    if df is not None:
        with st.expander(title, expanded=False):
            st.dataframe(df.head(100), use_container_width=True)
            st.caption(f"Mostrando primeras 100 filas de {len(df)}.")


# ============================================================
# VISUALIZADORES: PDF / YAML / DATASET / RO-CRATE
# ============================================================

def safe_read_json(path: Path):
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def safe_read_yaml(path: Path):
    if not path.exists() or not path.is_file() or yaml is None:
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def normalize_pdf_name(value: str) -> str:
    if not value:
        return ""
    return Path(str(value)).name


def filter_rows_by_pdf(df: pd.DataFrame | None, pdf_name: str) -> pd.DataFrame:
    if df is None or df.empty or "pdf" not in df.columns:
        return pd.DataFrame()
    return df[df["pdf"].astype(str).map(normalize_pdf_name) == pdf_name].copy()


def boolish(value) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "si", "sí", "positivo", "positive"}


def get_pdf_artifacts(pdf_path: Path) -> dict:
    stem = pdf_path.stem
    return {
        "pdf": pdf_path,
        "dataset_json": PDF_DIR / f"{stem}.dataset.json",
        "yaml": YAMLS_DIR / f"{stem}.yaml",
        "ro_crate_dir": find_ro_crate_dir_for_stem(stem),
    }


def find_ro_crate_metadata_files() -> list[Path]:
    if not RO_OUTPUT_DIR.exists():
        return []
    return sorted(RO_OUTPUT_DIR.rglob("ro-crate-metadata.json"))


def find_ro_crate_dir_for_stem(stem: str) -> Path | None:
    if not RO_OUTPUT_DIR.exists():
        return None

    # Caso habitual de ya2ro: ro_output/<stem>/ro-crate-metadata.json
    direct = RO_OUTPUT_DIR / stem / "ro-crate-metadata.json"
    if direct.exists():
        return direct.parent

    # Fallback: buscar carpetas que contengan el stem.
    for meta in find_ro_crate_metadata_files():
        if stem.lower() in str(meta.parent.name).lower():
            return meta.parent

    # Fallback adicional: si solo hay un RO-Crate y un PDF, lo mostraremos.
    metas = find_ro_crate_metadata_files()
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if len(metas) == 1 and len(pdfs) == 1:
        return metas[0].parent

    return None


def summarize_dataset_json(path: Path) -> dict:
    data = safe_read_json(path)
    if not data:
        return {"existe": False, "mentions": 0, "nombres": [], "urls": []}

    mentions = data.get("mentions", []) if isinstance(data, dict) else []
    names = []
    urls = []

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in {"rawForm", "normalizedForm", "name", "title"} and isinstance(v, str):
                    if v.strip() and len(v.strip()) < 180:
                        names.append(v.strip())
                if isinstance(v, str) and v.startswith(("http://", "https://")):
                    urls.append(v.strip())
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return {
        "existe": True,
        "mentions": len(mentions) if isinstance(mentions, list) else 0,
        "nombres": list(dict.fromkeys(names))[:10],
        "urls": list(dict.fromkeys(urls))[:10],
    }


def summarize_yaml(path: Path) -> dict:
    data = safe_read_yaml(path)
    if not data:
        return {"existe": False, "title": "", "datasets": 0, "software": 0, "authors": 0}

    datasets = data.get("datasets", []) if isinstance(data, dict) else []
    software = data.get("software", []) if isinstance(data, dict) else []
    authors = data.get("authors", []) if isinstance(data, dict) else []

    return {
        "existe": True,
        "title": data.get("title", "") if isinstance(data, dict) else "",
        "summary": data.get("summary", "") if isinstance(data, dict) else "",
        "datasets": len(datasets) if isinstance(datasets, list) else 1,
        "software": len(software) if isinstance(software, list) else 1,
        "authors": len(authors) if isinstance(authors, list) else 0,
        "raw": data,
    }


def summarize_ro_crate(ro_dir: Path | None) -> dict:
    if ro_dir is None:
        return {"existe": False, "entidades": 0, "tipos": {}, "datasets": [], "software": [], "preview": None, "metadata": None}

    metadata_path = ro_dir / "ro-crate-metadata.json"
    data = safe_read_json(metadata_path)
    if not data:
        return {"existe": False, "entidades": 0, "tipos": {}, "datasets": [], "software": [], "preview": None, "metadata": metadata_path}

    graph = data.get("@graph", []) if isinstance(data, dict) else []
    tipos = {}
    datasets = []
    software = []

    for entity in graph:
        if not isinstance(entity, dict):
            continue
        t = entity.get("@type", "")
        if isinstance(t, list):
            type_values = [str(x) for x in t]
        else:
            type_values = [str(t)] if t else []
        for tv in type_values:
            tipos[tv] = tipos.get(tv, 0) + 1

        joined_type = " ".join(type_values).lower()
        name = entity.get("name") or entity.get("@id") or "sin nombre"
        row = {"nombre": name, "id": entity.get("@id", ""), "tipo": entity.get("@type", "")}
        if "dataset" in joined_type:
            datasets.append(row)
        if "software" in joined_type or "computationalworkflow" in joined_type or "softwareapplication" in joined_type:
            software.append(row)

    preview = ro_dir / "ro-crate-preview.html"
    return {
        "existe": True,
        "entidades": len(graph) if isinstance(graph, list) else 0,
        "tipos": tipos,
        "datasets": datasets,
        "software": software,
        "preview": preview if preview.exists() else None,
        "metadata": metadata_path,
        "raw": data,
    }


def build_pdf_overview_table() -> pd.DataFrame:
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    all_links = read_csv_if_exists(OUTPUTS_DIR / "all_links.csv")
    norm_links = read_csv_if_exists(OUTPUTS_DIR / "all_links_normalized.csv")
    h1 = read_csv_if_exists(OUTPUTS_DIR / "heuristic_1_results.csv")
    h2 = read_csv_if_exists(OUTPUTS_DIR / "heuristic_2_results.csv")

    rows = []
    for pdf in pdfs:
        artifacts = get_pdf_artifacts(pdf)
        pdf_name = pdf.name

        df_links = filter_rows_by_pdf(all_links, pdf_name)
        df_norm = filter_rows_by_pdf(norm_links, pdf_name)
        df_h1 = filter_rows_by_pdf(h1, pdf_name)
        df_h2 = filter_rows_by_pdf(h2, pdf_name)

        h1_pos = 0
        h2_pos = 0
        if not df_h1.empty and "heuristica" in df_h1.columns:
            h1_pos = int(df_h1["heuristica"].map(boolish).sum())
        if not df_h2.empty and "heuristica" in df_h2.columns:
            h2_pos = int(df_h2["heuristica"].map(boolish).sum())

        ds_summary = summarize_dataset_json(artifacts["dataset_json"])
        yaml_summary = summarize_yaml(artifacts["yaml"])
        ro_summary = summarize_ro_crate(artifacts["ro_crate_dir"])

        rows.append({
            "pdf": pdf_name,
            "urls_extraidas": len(df_links),
            "urls_normalizadas": len(df_norm),
            "h1_positivos": h1_pos,
            "h2_positivos": h2_pos,
            "dataset_json": "✅" if ds_summary["existe"] else "❌",
            "menciones_gap": ds_summary.get("mentions", 0),
            "yaml": "✅" if yaml_summary["existe"] else "❌",
            "ro_crate": "✅" if ro_summary["existe"] else "❌",
            "ro_entidades": ro_summary.get("entidades", 0),
        })

    return pd.DataFrame(rows)


def show_file_download(path: Path, label: str, key: str, mime: str = "application/octet-stream"):
    if path and path.exists() and path.is_file():
        with open(path, "rb") as f:
            st.download_button(label, data=f, file_name=path.name, mime=mime, key=key)


def show_pdf_general_visualizer():
    st.subheader("🔎 Visualizador general por PDF")
    st.write("Esta vista junta lo que se ha generado para cada PDF: enlaces, heurísticas, GAP-KGE, YAML y RO-Crate.")

    overview = build_pdf_overview_table()
    if overview.empty:
        st.info("Todavía no hay PDFs en `pdfs/`.")
        return

    st.dataframe(overview, use_container_width=True, hide_index=True)

    selected_pdf_name = st.selectbox(
        "Selecciona un PDF para ver todos sus artefactos",
        overview["pdf"].tolist(),
        key="pdf_visualizer_select",
    )
    pdf_path = PDF_DIR / selected_pdf_name
    artifacts = get_pdf_artifacts(pdf_path)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("PDF", "✅" if artifacts["pdf"].exists() else "❌")
    col2.metric(".dataset.json", "✅" if artifacts["dataset_json"].exists() else "❌")
    col3.metric("YAML", "✅" if artifacts["yaml"].exists() else "❌")
    col4.metric("RO-Crate", "✅" if artifacts["ro_crate_dir"] else "❌")

    all_links = filter_rows_by_pdf(read_csv_if_exists(OUTPUTS_DIR / "all_links.csv"), selected_pdf_name)
    norm_links = filter_rows_by_pdf(read_csv_if_exists(OUTPUTS_DIR / "all_links_normalized.csv"), selected_pdf_name)
    h1 = filter_rows_by_pdf(read_csv_if_exists(OUTPUTS_DIR / "heuristic_1_results.csv"), selected_pdf_name)
    h2 = filter_rows_by_pdf(read_csv_if_exists(OUTPUTS_DIR / "heuristic_2_results.csv"), selected_pdf_name)

    t1, t2, t3, t4, t5 = st.tabs(["Enlaces", "Heurísticas", "GAP-KGE", "YAML", "RO-Crate"])

    with t1:
        st.markdown("#### URLs extraídas")
        if all_links.empty:
            st.info("No hay filas en `outputs/all_links.csv` para este PDF.")
        else:
            st.dataframe(all_links, use_container_width=True, hide_index=True)
        st.markdown("#### URLs normalizadas")
        if norm_links.empty:
            st.info("No hay filas en `outputs/all_links_normalized.csv` para este PDF.")
        else:
            st.dataframe(norm_links, use_container_width=True, hide_index=True)

    with t2:
        st.markdown("#### Heurística 1")
        if h1.empty:
            st.info("No hay resultados de H1 para este PDF.")
        else:
            st.dataframe(h1, use_container_width=True, hide_index=True)
        st.markdown("#### Heurística 2")
        if h2.empty:
            st.info("No hay resultados de H2 para este PDF.")
        else:
            st.dataframe(h2, use_container_width=True, hide_index=True)

    with t3:
        ds = summarize_dataset_json(artifacts["dataset_json"])
        if not ds["existe"]:
            st.warning("No existe `.dataset.json` para este PDF.")
        else:
            st.success(f"Existe: `{artifacts['dataset_json'].relative_to(BASE_DIR)}`")
            st.metric("Menciones detectadas", ds.get("mentions", 0))
            if ds.get("nombres"):
                st.markdown("**Nombres detectados:**")
                st.write(ds["nombres"])
            if ds.get("urls"):
                st.markdown("**URLs detectadas dentro del JSON:**")
                st.write(ds["urls"])
            with st.expander("Ver JSON completo", expanded=False):
                st.json(safe_read_json(artifacts["dataset_json"]))
            show_file_download(artifacts["dataset_json"], "⬇️ Descargar .dataset.json", f"download_dataset_json_{selected_pdf_name}", "application/json")

    with t4:
        ys = summarize_yaml(artifacts["yaml"])
        if not ys["existe"]:
            st.warning("No existe YAML para este PDF.")
        else:
            st.success(f"Existe: `{artifacts['yaml'].relative_to(BASE_DIR)}`")
            st.write(f"**Título:** {ys.get('title', '')}")
            st.write(f"**Datasets:** {ys.get('datasets', 0)} | **Software:** {ys.get('software', 0)} | **Autores:** {ys.get('authors', 0)}")
            with st.expander("Ver YAML completo", expanded=True):
                st.code(artifacts["yaml"].read_text(encoding="utf-8", errors="replace"), language="yaml")
            show_file_download(artifacts["yaml"], "⬇️ Descargar YAML", f"download_yaml_{selected_pdf_name}", "text/yaml")

    with t5:
        rs = summarize_ro_crate(artifacts["ro_crate_dir"])
        if not rs["existe"]:
            st.warning("No se ha encontrado RO-Crate para este PDF.")
        else:
            st.success(f"RO-Crate encontrado en: `{artifacts['ro_crate_dir'].relative_to(BASE_DIR)}`")
            st.metric("Entidades en @graph", rs.get("entidades", 0))
            if rs.get("tipos"):
                st.markdown("**Tipos de entidades:**")
                st.dataframe(pd.DataFrame([{"tipo": k, "cantidad": v} for k, v in rs["tipos"].items()]), use_container_width=True, hide_index=True)
            if rs.get("datasets"):
                st.markdown("**Datasets dentro del RO-Crate:**")
                st.dataframe(pd.DataFrame(rs["datasets"]), use_container_width=True, hide_index=True)
            if rs.get("software"):
                st.markdown("**Software dentro del RO-Crate:**")
                st.dataframe(pd.DataFrame(rs["software"]), use_container_width=True, hide_index=True)
            show_file_download(rs["metadata"], "⬇️ Descargar ro-crate-metadata.json", f"download_ro_metadata_{selected_pdf_name}", "application/json")
            if rs.get("preview"):
                show_file_download(rs["preview"], "⬇️ Descargar ro-crate-preview.html", f"download_ro_preview_{selected_pdf_name}", "text/html")


def show_ro_crate_visualizer():
    st.subheader("📦 Visualizador de RO-Crate")
    st.write("Esta vista lee los `ro-crate-metadata.json` generados por ya2ro y permite inspeccionar el RO-Crate.")

    metadata_files = find_ro_crate_metadata_files()
    if not metadata_files:
        st.info("Todavía no hay `ro-crate-metadata.json` dentro de `ya2ro_generated/ro_output/`.")
        return

    selected_meta = st.selectbox(
        "Selecciona un RO-Crate",
        metadata_files,
        format_func=lambda p: str(p.parent.relative_to(BASE_DIR)),
        key="ro_crate_select",
    )

    ro_dir = selected_meta.parent
    summary = summarize_ro_crate(ro_dir)

    col1, col2, col3 = st.columns(3)
    col1.metric("Entidades", summary.get("entidades", 0))
    col2.metric("Datasets", len(summary.get("datasets", [])))
    col3.metric("Software", len(summary.get("software", [])))

    st.write(f"**Carpeta:** `{ro_dir.relative_to(BASE_DIR)}`")
    st.write(f"**Metadata:** `{selected_meta.relative_to(BASE_DIR)}`")

    ro_tabs = st.tabs(["Resumen", "JSON-LD", "Preview HTML", "Archivos del RO"])

    with ro_tabs[0]:
        if summary.get("tipos"):
            st.markdown("#### Tipos de entidades")
            st.dataframe(pd.DataFrame([{"tipo": k, "cantidad": v} for k, v in summary["tipos"].items()]), use_container_width=True, hide_index=True)
        if summary.get("datasets"):
            st.markdown("#### Datasets")
            st.dataframe(pd.DataFrame(summary["datasets"]), use_container_width=True, hide_index=True)
        if summary.get("software"):
            st.markdown("#### Software")
            st.dataframe(pd.DataFrame(summary["software"]), use_container_width=True, hide_index=True)

    with ro_tabs[1]:
        data = safe_read_json(selected_meta)
        st.json(data)
        show_file_download(selected_meta, "⬇️ Descargar ro-crate-metadata.json", "download_selected_ro_metadata", "application/json")

    with ro_tabs[2]:
        preview = ro_dir / "ro-crate-preview.html"
        if not preview.exists():
            st.info("Este RO-Crate no tiene `ro-crate-preview.html`.")
        else:
            show_file_download(preview, "⬇️ Descargar preview HTML", "download_selected_ro_preview", "text/html")
            show_inline = st.checkbox("Mostrar preview HTML dentro de Streamlit", value=False)
            if show_inline:
                html = preview.read_text(encoding="utf-8", errors="replace")
                components.html(html, height=850, scrolling=True)

    with ro_tabs[3]:
        files = []
        for path in sorted(ro_dir.rglob("*")):
            if path.is_file():
                files.append({
                    "archivo": str(path.relative_to(ro_dir)),
                    "tipo": path.suffix or "sin extensión",
                    "tamaño_kb": round(path.stat().st_size / 1024, 2),
                })
        st.dataframe(pd.DataFrame(files), use_container_width=True, hide_index=True)


# ============================================================
# INTERFAZ STREAMLIT
# ============================================================

st.set_page_config(
    page_title="Paper2RO - Generador de Research Objects",
    page_icon="📄",
    layout="wide",
)

ensure_dirs()

st.title("📄 Paper2RO - Generador paso a paso de Research Objects")
st.write(
    "Sube uno o varios PDFs y la aplicación ejecutará el flujo completo: "
    "extracción de enlaces, normalización, heurísticas, GAP-KGE, YAMLs y RO-Crate con ya2ro."
)

with st.sidebar:
    st.header("⚙️ Configuración")

    clean_before = st.checkbox("Limpiar resultados anteriores antes de procesar", value=True)
    run_gap = st.checkbox("Ejecutar GAP-KGE", value=True)
    run_ya2ro_step = st.checkbox("Ejecutar ya2ro al final", value=True)
    ya2ro_no_somef = st.checkbox("Usar ya2ro -ns", value=True)

    gap_batch_size = st.slider("PDFs por lote para GAP-KGE", min_value=1, max_value=5, value=2)

    default_gap_workdir = BASE_DIR / "software_mentions_client"
    gap_workdir_text = st.text_input(
        "Carpeta desde donde ejecutar GAP-KGE",
        value=str(default_gap_workdir),
        help=(
            "La app ejecutará el comando GAP-KGE desde esta carpeta, "
            "equivalente a hacer cd software_mentions_client antes de lanzarlo."
        ),
    )

    st.markdown("### Comando GAP-KGE")
    st.caption(
        "Ajusta este comando a cómo ejecutas GAP-KGE/datastet en tu equipo. "
        "Debe crear un archivo {stem}.dataset.json."
    )

    gap_command = st.text_area(
        "Plantilla de comando",
        value='python -m software_mentions_client.client --repo-in "{batch_dir}" --datastet --reset',
        height=120,
        placeholder=(
            "Ejemplo:\n"
            'python -m software_mentions_client.client --repo-in "{batch_dir}" --datastet --reset\n\n'
            "Placeholders: {batch_dir}, {pdf_dir}, {base_dir}, {outputs_dir}, {batch_index}"
        ),
    )

    st.markdown("### Scripts detectados")
    for name, path in SCRIPTS.items():
        st.write(f"{'✅' if path.exists() else '❌'} `{path.name}`")

    gap_workdir_path = Path(gap_workdir_text)
    st.write(f"{'✅' if gap_workdir_path.exists() else '❌'} Carpeta GAP-KGE: `{gap_workdir_path}`")


tab_main, tab_pdf_viewer, tab_ro_viewer, tab_outputs, tab_help = st.tabs([
    "🚀 Principal",
    "🔎 Visualizador por PDF",
    "📦 Visualizador RO-Crate",
    "📁 Archivos generados",
    "ℹ️ Ayuda",
])

with tab_main:
    uploaded_pdfs = st.file_uploader(
        "Sube los PDF que quieres pasar a Research Object",
        type=["pdf"],
        accept_multiple_files=True,
    )

    if uploaded_pdfs:
        st.markdown("### PDFs seleccionados para subir")
        selected_rows = []
        for uploaded in uploaded_pdfs:
            selected_rows.append({
                "pdf": uploaded.name,
                "tamaño_mb": round(uploaded.size / (1024 * 1024), 2),
            })
        st.dataframe(pd.DataFrame(selected_rows), use_container_width=True, hide_index=True)

    show_uploaded_pdfs_panel(expanded=True)

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        process_button = st.button("▶️ Procesar PDFs", type="primary", use_container_width=True)
    with col_b:
        refresh_button = st.button("🔄 Refrescar archivos", use_container_width=True)
    with col_c:
        clear_button = st.button("🧹 Limpiar todo", use_container_width=True)

    # ------------------------------------------------------------
    # Limpieza protegida con confirmación + backup automático
    # ------------------------------------------------------------
    if "confirmar_limpieza" not in st.session_state:
        st.session_state.confirmar_limpieza = False

    if clear_button:
        st.session_state.confirmar_limpieza = True
        st.rerun()

    if st.session_state.confirmar_limpieza:
        st.error("⚠️ Vas a borrar los PDFs subidos y los resultados generados.")
        st.warning(
            "Antes de borrar, la app creará una copia de seguridad en una carpeta "
            "`_backup_limpieza_FECHA_HORA`."
        )

        confirm_col1, confirm_col2 = st.columns(2)

        with confirm_col1:
            if st.button("✅ Sí, crear backup y limpiar", type="primary", use_container_width=True):
                backup_dir = clean_previous_outputs(remove_pdfs=True, create_backup=True)
                st.session_state.confirmar_limpieza = False
                st.success(f"Limpieza realizada. Backup creado en: `{backup_dir}`")
                st.rerun()

        with confirm_col2:
            if st.button("❌ Cancelar limpieza", use_container_width=True):
                st.session_state.confirmar_limpieza = False
                st.info("Limpieza cancelada. No se ha borrado nada.")
                st.rerun()

    if refresh_button:
        st.rerun()

    if process_button:
        if not uploaded_pdfs:
            st.error("Primero sube al menos un PDF.")
            st.stop()

        if clean_before:
            backup_dir = clean_previous_outputs(remove_pdfs=True, create_backup=True)
            st.info(f"Se limpiaron resultados anteriores y se creó backup en: `{backup_dir}`")

        saved_pdfs = save_uploaded_pdfs(uploaded_pdfs)

        st.success(f"PDFs guardados: {len(saved_pdfs)}")
        for pdf in saved_pdfs:
            st.write(f"- `{pdf.relative_to(BASE_DIR)}`")

        show_uploaded_pdfs_panel(expanded=True)

        progress = st.progress(0)
        status = st.empty()

        # ============================================================
        # GAP-KGE EN SEGUNDO PLANO
        # ============================================================
        gap_executor = None
        gap_future = None
        gap_queue = None
        gap_log_box = None
        gap_lines = []

        if run_gap:
            st.markdown("### GAP-KGE/datastet en segundo plano")
            gap_log_box = st.empty()
            gap_queue = queue.Queue()
            gap_lines = [
                "[INFO] GAP-KGE/datastet se arranca en paralelo con extracción/normalización.\n",
                "[INFO] Para evitar conflictos, los PDFs se COPIAN a los lotes temporales y los originales permanecen en pdfs/.\n\n",
            ]
            gap_log_box.code("".join(gap_lines), language="bash")
            st.caption("Este panel se irá actualizando mientras se ejecutan extracción y normalización.")

            gap_executor = ThreadPoolExecutor(max_workers=1)
            gap_future = gap_executor.submit(
                run_gap_kge_in_batches_background,
                gap_command,
                gap_batch_size,
                Path(gap_workdir_text),
                gap_queue,
            )
        else:
            st.info("GAP-KGE/datastet omitido por configuración.")

        def refresh_gap_log():
            drain_background_log(gap_queue, gap_log_box, gap_lines)

        # ---------------- PASO 1 ----------------
        status.info("Paso 1/6: Extrayendo URLs y DOIs desde los PDFs mientras GAP-KGE trabaja en paralelo...")
        log1 = st.empty()
        ok1, _ = run_python_script(
            SCRIPTS["extract_links"],
            "Extracción de enlaces",
            log1,
            extra_args=["--pdf-dir", str(PDF_DIR), "--output-dir", str(OUTPUTS_DIR)],
            background_queue=gap_queue,
            background_log_box=gap_log_box,
            background_lines=gap_lines,
        )
        refresh_gap_log()
        progress.progress(18)
        if not ok1:
            st.error("Falló la extracción de enlaces. Revisa el log anterior.")
            st.stop()

        show_csv_preview(OUTPUTS_DIR / "all_links.csv", "Vista previa: outputs/all_links.csv")

        # ---------------- PASO 2 ----------------
        status.info("Paso 2/6: Normalizando URLs mientras GAP-KGE sigue en segundo plano...")
        log2 = st.empty()
        ok2, _ = run_python_script(
            SCRIPTS["normalize"],
            "Normalización de URLs",
            log2,
            background_queue=gap_queue,
            background_log_box=gap_log_box,
            background_lines=gap_lines,
        )
        refresh_gap_log()
        progress.progress(32)
        if not ok2:
            st.error("Falló la normalización. Revisa el log anterior.")
            st.stop()

        show_csv_preview(OUTPUTS_DIR / "all_links_normalized.csv", "Vista previa: outputs/all_links_normalized.csv")

        # ---------------- PASO 3 ----------------
        status.info("Paso 3/6: Ejecutando Heurística 1 y Heurística 2 en paralelo...")
        col_h1, col_h2 = st.columns(2)
        with col_h1:
            st.markdown("#### Heurística 1")
            log3 = st.empty()
        with col_h2:
            st.markdown("#### Heurística 2")
            log4 = st.empty()

        heuristic_tasks = [
            {
                "key": "h1",
                "title": "Heurística 1",
                "command": [sys.executable, SCRIPTS["heuristic_1"]],
                "log_box": log3,
                "cwd": BASE_DIR,
            },
            {
                "key": "h2",
                "title": "Heurística 2",
                "command": [sys.executable, SCRIPTS["heuristic_2"]],
                "log_box": log4,
                "cwd": BASE_DIR,
            },
        ]

        heuristic_results = run_commands_parallel_live(
            heuristic_tasks,
            background_queue=gap_queue,
            background_log_box=gap_log_box,
            background_lines=gap_lines,
        )
        ok3 = heuristic_results.get("h1", {}).get("ok", False)
        ok4 = heuristic_results.get("h2", {}).get("ok", False)
        refresh_gap_log()
        progress.progress(58)

        if not ok3:
            st.warning("Heurística 1 terminó con errores. Se continúa porque puede haber resultados parciales.")
        if not ok4:
            st.warning("Heurística 2 terminó con errores. Se continúa porque puede haber resultados parciales.")

        show_csv_preview(OUTPUTS_DIR / "heuristic_1_results.csv", "Vista previa: outputs/heuristic_1_results.csv")
        show_csv_preview(OUTPUTS_DIR / "heuristic_2_results.csv", "Vista previa: outputs/heuristic_2_results.csv")

        # ---------------- PASO 4 ----------------
        if run_gap:
            status.info("Paso 4/6: Esperando a que GAP-KGE/datastet termine si aún sigue ejecutándose...")

            while gap_future is not None and not gap_future.done():
                refresh_gap_log()
                time.sleep(0.5)

            refresh_gap_log()
            try:
                gap_results = gap_future.result() if gap_future is not None else []
            except Exception as e:
                gap_results = []
                st.error(f"GAP-KGE/datastet falló en segundo plano: {e}")
            finally:
                if gap_executor is not None:
                    gap_executor.shutdown(wait=False)

            gap_ok = sum(1 for r in gap_results if r.get("ok"))
            generated_dataset_jsons = sorted(PDF_DIR.glob("*.dataset.json"))
            st.info(f"GAP-KGE/datastet: {gap_ok}/{len(gap_results)} lote(s) correcto(s).")

            if generated_dataset_jsons:
                st.success(f"Se encontraron {len(generated_dataset_jsons)} archivo(s) .dataset.json en pdfs/.")
                for dataset_json in generated_dataset_jsons:
                    st.write(f"- `{dataset_json.relative_to(BASE_DIR)}`")
            else:
                st.warning("No se encontraron .dataset.json en pdfs/. Revisa el log de GAP-KGE/datastet.")
        else:
            st.info("Paso 4/6: GAP-KGE omitido por configuración.")
        progress.progress(72)

        # ---------------- PASO 5 ----------------
        status.info("Paso 5/6: Generando YAMLs para ya2ro...")
        log6 = st.empty()
        ok6, _ = run_python_script(SCRIPTS["generate_yamls"], "Generación de YAMLs", log6)
        progress.progress(86)
        if not ok6:
            st.error("Falló la generación de YAMLs. Revisa si existen los .dataset.json de GAP-KGE.")
            st.stop()

        # ---------------- PASO 6 ----------------
        if run_ya2ro_step:
            status.info("Paso 6/6: Ejecutando ya2ro para crear RO-Crate...")
            log7 = st.empty()
            ok7, _ = run_ya2ro(log7, use_no_somef=ya2ro_no_somef)
            if not ok7:
                st.warning("ya2ro no se pudo ejecutar. Aun así puedes revisar los YAML generados.")
        else:
            st.info("Paso 6/6: ya2ro omitido por configuración.")

        progress.progress(100)
        status.success("✅ Proceso finalizado")

        st.balloons()
        st.success("🎉 Proceso completado. Puedes observar todos los archivos creados por separado en la pestaña **Archivos generados**.")

        df_files = list_generated_files()
        st.dataframe(df_files, use_container_width=True)

        zip_path = create_results_zip()
        with open(zip_path, "rb") as f:
            st.download_button(
                "⬇️ Descargar todos los resultados en ZIP",
                data=f,
                file_name="paper2ro_resultados.zip",
                mime="application/zip",
                use_container_width=True,
                key="download_zip_after_process",
            )

with tab_pdf_viewer:
    show_pdf_general_visualizer()

with tab_ro_viewer:
    show_ro_crate_visualizer()

with tab_outputs:
    st.subheader("📁 Archivos generados")

    show_uploaded_pdfs_panel(expanded=True)

    df_files = list_generated_files()

    if df_files.empty:
        st.info("Todavía no hay archivos generados.")
    else:
        st.dataframe(df_files, use_container_width=True)

        zip_path = create_results_zip()
        with open(zip_path, "rb") as f:
            st.download_button(
                "⬇️ Descargar todos los resultados en ZIP",
                data=f,
                file_name="paper2ro_resultados.zip",
                mime="application/zip",
                use_container_width=True,
                key="download_zip_outputs_tab",
            )

        st.markdown("### Previsualizaciones")
        show_csv_preview(OUTPUTS_DIR / "all_links.csv", "outputs/all_links.csv")
        show_csv_preview(OUTPUTS_DIR / "all_links_normalized.csv", "outputs/all_links_normalized.csv")
        show_csv_preview(OUTPUTS_DIR / "heuristic_1_results.csv", "outputs/heuristic_1_results.csv")
        show_csv_preview(OUTPUTS_DIR / "heuristic_2_results.csv", "outputs/heuristic_2_results.csv")
        show_csv_preview(YA2RO_DIR / "audit_candidates.csv", "ya2ro_generated/audit_candidates.csv")

        yaml_files = sorted(YAMLS_DIR.glob("*.yaml")) if YAMLS_DIR.exists() else []
        if yaml_files:
            st.markdown("### YAMLs generados")
            selected_yaml = st.selectbox("Selecciona un YAML", yaml_files, format_func=lambda p: p.name)
            st.code(selected_yaml.read_text(encoding="utf-8", errors="replace"), language="yaml")

with tab_help:
    st.subheader("ℹ️ Cómo usar la aplicación")
    st.markdown(
        """
        1. Coloca este archivo `streamlit_app.py` en la misma carpeta que tus scripts:
           `extractLinks.py`, `normalizeUrl.py`, `heuristic_1_page_download.py`,
           `heuristic_2_http_metadata.py` y `generate_yamls.py`.
        2. Ejecuta:

        ```bash
        python -m streamlit run streamlit_app.py
        ```

        3. Sube uno o varios PDFs.
        4. Pulsa **Procesar PDFs**.
        5. La aplicación irá ejecutando cada fase y mostrando los logs.

        **Importante sobre GAP-KGE/datastet:** esta versión trabaja por lotes.
        La app mueve los PDFs de `pdfs/` a una carpeta temporal `gap_kge_batches/batch_001`,
        ejecuta GAP-KGE/datastet sobre esa carpeta y después devuelve a `pdfs/` tanto los PDFs
        como los `.dataset.json` generados.

        Comando recomendado:

        ```bash
        python -m software_mentions_client.client --repo-in "{batch_dir}" --datastet --reset
        ```

        Placeholders disponibles:
        - `{batch_dir}`: carpeta temporal con solo los PDFs del lote actual.
        - `{pdf_dir}`: carpeta principal `pdfs`.
        - `{base_dir}`: carpeta del proyecto.
        - `{outputs_dir}`: carpeta `outputs`.
        - `{gap_workdir}`: carpeta desde donde se ejecuta GAP-KGE/software_mentions_client.
        - `{batch_index}`: número de lote.

        Al final deben quedar archivos como:

        ```text
        pdfs/paper1.pdf
        pdfs/paper1.dataset.json
        pdfs/paper2.pdf
        pdfs/paper2.dataset.json
        ```
        """
    )
