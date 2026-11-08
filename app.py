import io
import os
import tempfile
import time
import uuid

from flask import Flask, jsonify, render_template, request, send_file
from PIL import Image
import pymupdf

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload limit

THRESHOLD_KB = 100
THRESHOLD_BYTES = THRESHOLD_KB * 1024
# Target ceiling slightly below 102400 to guarantee page_size_kb < 100.00 KB
TARGET_CEILING_BYTES = 101800

# Temporary in-memory store for optimized PDFs awaiting download
_downloads: dict = {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    file = _validate_upload()
    if isinstance(file, tuple):
        return file

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        results = analyse_pdf(tmp_path)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return jsonify(results)


@app.route("/optimize", methods=["POST"])
def optimize():
    file = _validate_upload()
    if isinstance(file, tuple):
        return file

    original_name = file.filename
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        pdf_bytes, page_results = optimize_pdf(tmp_path)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    dl_id = uuid.uuid4().hex
    name_stem = os.path.splitext(original_name)[0]
    _downloads[dl_id] = {
        "bytes": pdf_bytes,
        "filename": f"{name_stem}_optimized.pdf",
        "created": time.time(),
    }
    _cleanup_downloads()

    return jsonify({
        "download_id": dl_id,
        "pages": page_results,
        "all_under_limit": all(p["under_limit"] for p in page_results),
    })


@app.route("/download/<dl_id>")
def download(dl_id):
    entry = _downloads.get(dl_id)
    if not entry:
        return jsonify({"error": "Download expired or not found"}), 404

    return send_file(
        io.BytesIO(entry["bytes"]),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=entry["filename"],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_upload():
    if "pdf" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["pdf"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "File must be a PDF"}), 400
    return file


def _cleanup_downloads(max_age_s=900):
    now = time.time()
    expired = [k for k, v in _downloads.items() if now - v["created"] > max_age_s]
    for k in expired:
        del _downloads[k]


def _measure_page(doc, page_num: int) -> int:
    """Return the byte-size of a single page extracted as its own PDF."""
    single = pymupdf.open()
    single.insert_pdf(doc, from_page=page_num, to_page=page_num)
    size = len(single.tobytes(deflate=True, garbage=4, clean=True))
    single.close()
    return size


def _pixmap_to_pil(pix: pymupdf.Pixmap) -> Image.Image:
    """Convert a PyMuPDF Pixmap safely to a PIL Image."""
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.colorspace and pix.colorspace.name == "DeviceGray":
        return Image.frombytes("L", [pix.width, pix.height], pix.samples)
    if pix.colorspace and pix.colorspace.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyse_pdf(path: str) -> dict:
    src = pymupdf.open(path)
    total_size = os.path.getsize(path)
    pages = []

    for i in range(len(src)):
        size = _measure_page(src, i)
        size_kb = round(size / 1024, 2)
        pages.append({
            "page": i + 1,
            "size_kb": size_kb,
            "under_limit": size < THRESHOLD_BYTES,
        })

    src.close()
    return {
        "filename": os.path.basename(path),
        "total_pages": len(pages),
        "total_size_kb": round(total_size / 1024, 2),
        "threshold_kb": THRESHOLD_KB,
        "all_pass": all(p["under_limit"] for p in pages),
        "pages": pages,
    }


# ---------------------------------------------------------------------------
# High-Fidelity Smart Optimization
# ---------------------------------------------------------------------------

def optimize_pdf(path: str) -> tuple[bytes, list]:
    src = pymupdf.open(path)
    output = pymupdf.open()
    page_results = []

    for i in range(len(src)):
        orig_size = _measure_page(src, i)

        if orig_size < THRESHOLD_BYTES:
            output.insert_pdf(src, from_page=i, to_page=i)
            page_results.append({
                "page": i + 1,
                "original_kb": round(orig_size / 1024, 2),
                "optimized_kb": round(orig_size / 1024, 2),
                "optimized": False,
                "under_limit": True,
            })
            continue

        opt_doc = _compress_page(src, i)
        opt_size = len(opt_doc.tobytes(deflate=True, garbage=4, clean=True))
        output.insert_pdf(opt_doc, from_page=0, to_page=0)
        opt_doc.close()

        page_results.append({
            "page": i + 1,
            "original_kb": round(orig_size / 1024, 2),
            "optimized_kb": round(opt_size / 1024, 2),
            "optimized": True,
            "under_limit": opt_size < THRESHOLD_BYTES,
        })

    result_bytes = output.tobytes(deflate=True, garbage=4, clean=True)
    output.close()
    src.close()
    return result_bytes, page_results


def _compress_page(src_doc: pymupdf.Document, page_num: int) -> pymupdf.Document:
    """Return a single-page Document compressed to just below 100 KB.

    Preserves text sharpness, contrast, and layout readability using:
    1. Deflate / clean stream optimization.
    2. Vector-preserving embedded image recompression with high-quality Lanczos
       scaling and binary-search JPEG quality targeting 95-99 KB.
    3. High-resolution adaptive page rasterization (if vector overhead/fonts dominate).
    """
    page = src_doc[page_num]

    # --- Step 0: Try lossless clean/deflate first ---
    trial0 = pymupdf.open()
    trial0.insert_pdf(src_doc, from_page=page_num, to_page=page_num)
    raw0 = trial0.tobytes(deflate=True, garbage=4, clean=True)
    if len(raw0) <= TARGET_CEILING_BYTES:
        return trial0
    trial0.close()

    # --- Step 1: Optimize embedded images (keeps text & vector graphics 100% vector-sharp) ---
    trial_p = pymupdf.open()
    trial_p.insert_pdf(src_doc, from_page=page_num, to_page=page_num)
    images = trial_p[0].get_images(full=True)

    if images:
        img_xrefs = []
        pil_images = {}
        for info in images:
            xref = info[0]
            if xref in pil_images:
                continue
            try:
                pix = pymupdf.Pixmap(trial_p, xref)
                im = _pixmap_to_pil(pix)
                img_xrefs.append(xref)
                pil_images[xref] = im
            except Exception:
                pass
        trial_p.close()

        if pil_images:
            scales = [
                1.0, 0.95, 0.90, 0.85, 0.80, 0.75, 0.70,
                0.65, 0.60, 0.55, 0.50, 0.45, 0.40, 0.35, 0.30
            ]

            def test_img_params(scale: float, q: int, to_gray: bool = False):
                t = pymupdf.open()
                t.insert_pdf(src_doc, from_page=page_num, to_page=page_num)
                for xref in img_xrefs:
                    orig_im = pil_images[xref]
                    im = orig_im.convert("L") if to_gray and orig_im.mode != "L" else orig_im
                    w = max(16, int(im.width * scale))
                    h = max(16, int(im.height * scale))
                    im_scaled = (
                        im.resize((w, h), Image.Resampling.LANCZOS)
                        if scale < 1.0
                        else im
                    )
                    buf = io.BytesIO()
                    im_scaled.save(buf, format="JPEG", quality=q, optimize=True)
                    t[0].replace_image(xref, stream=buf.getvalue())
                raw = t.tobytes(deflate=True, garbage=4, clean=True)
                return len(raw), t

            # Try color mode first; if it requires excessive downscaling (< 0.5), try grayscale
            for to_gray in [False, True]:
                best_doc = None
                for scale in scales:
                    # Check if quality 45 fits at this scale
                    sz_low, doc_low = test_img_params(scale, 45, to_gray=to_gray)
                    if sz_low > TARGET_CEILING_BYTES:
                        doc_low.close()
                        continue

                    # Binary search quality in [45, 95] to target just below 100 KB
                    low_q, high_q = 45, 95
                    while low_q <= high_q:
                        mid_q = (low_q + high_q) // 2
                        sz, doc = test_img_params(scale, mid_q, to_gray=to_gray)
                        if sz <= TARGET_CEILING_BYTES:
                            if best_doc:
                                best_doc.close()
                            best_doc = doc
                            low_q = mid_q + 1
                        else:
                            doc.close()
                            high_q = mid_q - 1

                    if best_doc:
                        return best_doc

    # --- Step 2: High-Resolution Page Rasterization ---
    # Used when large fonts/complex vector paths prevent the page from being under 100KB
    dpis = [200, 180, 160, 150, 140, 130, 120, 110, 100, 90, 80]
    page_rect = page.rect

    for to_gray in [False, True]:
        for dpi in dpis:
            pix = page.get_pixmap(dpi=dpi)
            im = _pixmap_to_pil(pix)
            if to_gray and im.mode != "L":
                im = im.convert("L")

            def test_dpi_q(q: int):
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=q, optimize=True)
                t = pymupdf.open()
                p = t.new_page(width=page_rect.width, height=page_rect.height)
                p.insert_image(p.rect, stream=buf.getvalue())
                raw = t.tobytes(deflate=True, garbage=4, clean=True)
                return len(raw), t

            sz_low, doc_low = test_dpi_q(45)
            if sz_low > TARGET_CEILING_BYTES:
                doc_low.close()
                continue

            low_q, high_q = 45, 95
            best_doc = None
            while low_q <= high_q:
                mid_q = (low_q + high_q) // 2
                sz, doc = test_dpi_q(mid_q)
                if sz <= TARGET_CEILING_BYTES:
                    if best_doc:
                        best_doc.close()
                    best_doc = doc
                    low_q = mid_q + 1
                else:
                    doc.close()
                    high_q = mid_q - 1

            if best_doc:
                return best_doc

    # Absolute fallback – 72 DPI, grayscale
    pix = page.get_pixmap(dpi=72)
    im = _pixmap_to_pil(pix).convert("L")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=40, optimize=True)
    t = pymupdf.open()
    p = t.new_page(width=page_rect.width, height=page_rect.height)
    p.insert_image(p.rect, stream=buf.getvalue())
    return t


if __name__ == "__main__":
    app.run(debug=True, port=5000)
