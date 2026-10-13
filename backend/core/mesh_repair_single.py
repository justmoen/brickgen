import time
from pathlib import Path
from playwright.sync_api import sync_playwright
from stl import mesh  # Used to parse geometry matrices

FORMWARE_URL = "https://fixer.formware.co/"
REPAIR_TIMEOUT_SECONDS = 4 * 60
POLL_SECONDS = 2


def get_page_text(page):
    try:
        return " ".join(page.locator("body").inner_text().split())
    except Exception:
        return ""


def get_status(page):
    text = get_page_text(page)
    statuses = [
        "Uploading..",
        "In queue..",
        "Assigned to repair node",
        "Transfering to repair node",
        "Transfer done.",
        "Analyzing file",
        "Repairing",
        "Converting back",
        "File repaired",
        "Ready for download",
    ]
    for status in statuses:
        if status.lower() in text.lower():
            return status
    return text


def find_download_button(page):
    button = page.locator("#downloadbtn")
    if button.count() == 0:
        return None
    try:
        if button.first.is_visible():
            return button.first
    except Exception:
        pass
    return None


def wait_for_upload(page):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status = get_status(page)
        if any(
            phrase in status.lower()
            for phrase in [
                "uploading",
                "in queue",
                "assigned",
                "transfer",
                "analyzing",
                "repairing",
                "converting",
                "file repaired",
                "ready for download",
            ]
        ):
            return True
        time.sleep(POLL_SECONDS)
    return False


def wait_for_download_button(page):
    deadline = time.monotonic() + REPAIR_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = get_status(page)
        button = find_download_button(page)
        if button is not None:
            return button
        lower = status.lower()
        if any(phrase in lower for phrase in ["timed out", "timeout", "failed", "error", "cancelled", "canceled"]):
            raise RuntimeError(f"Formware reported failure: {status}")
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"Formware did not expose download button within {REPAIR_TIMEOUT_SECONDS // 60} minutes.")


def manually_transcode_to_ascii_text(temp_binary_path: Path, final_output_path: Path):
    """
    Reads the downloaded geometry and manually writes pure ASCII text lines,
    mirroring stl_orientation.py file handling style.
    """
    stl_mesh = mesh.Mesh.from_file(str(temp_binary_path))
    
    with open(final_output_path, "w", encoding="utf-8") as f:
        f.write("solid mesh\n")
        for facet in stl_mesh.vectors:
            f.write("  facet normal 0.0 0.0 0.0\n")
            f.write("    outer loop\n")
            f.write(f"      vertex {facet[0][0]:.6f} {facet[0][1]:.6f} {facet[0][2]:.6f}\n")
            f.write(f"      vertex {facet[1][0]:.6f} {facet[1][1]:.6f} {facet[1][2]:.6f}\n")
            f.write(f"      vertex {facet[2][0]:.6f} {facet[2][1]:.6f} {facet[2][2]:.6f}\n")
            f.write("    endloop\n")
            f.write("  endfacet\n")
        f.write("endsolid mesh\n")


def _isolated_repair_worker(file_path: Path):
    """Launches your working headful browser script, repairing the target path in-place."""
    temp_download = file_path.parent / f"{file_path.name}.download.tmp"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page(accept_downloads=True)

        try:
            page.goto(FORMWARE_URL, wait_until="domcontentloaded", timeout=120_000)
            page.wait_for_timeout(5000)

            file_inputs = page.locator('input[type="file"]')
            count = file_inputs.count()

            if count < 2:
                raise RuntimeError(f"Expected at least two file inputs, but found {count}.")

            upload = file_inputs.nth(1)
            upload.set_input_files(str(file_path))

            if not wait_for_upload(page):
                raise RuntimeError("Formware did not acknowledge the upload within 60 seconds.")

            download_button = wait_for_download_button(page)

            with page.expect_download(timeout=30_000) as download_info:
                download_button.click()
            download = download_info.value
            download.save_as(str(temp_download))

            # Rewrite the file path completely in-place as a clean text file
            if temp_download.exists() and temp_download.stat().st_size > 0:
                manually_transcode_to_ascii_text(temp_download, file_path)
                print(f"[Formware Inplace Success] Repaired and formatted asset: {file_path.name}")
                time.sleep(2)

        finally:
            browser.close()
            if temp_download.exists():
                try:
                    temp_download.unlink()
                except Exception:
                    pass


def repair_single_file_inplace(file_path: Path):
    """Thread offloader to isolate your headful script from FastAPI's event loop loops."""
    if not file_path.exists():
        return

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_isolated_repair_worker, file_path)
        future.result()