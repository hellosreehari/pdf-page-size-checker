# PDF Page Size Checker & Optimizer 📄⚡

A web application that inspects multi-page PDF documents, measures the size of each individual page, flags pages that exceed 100 KB, and intelligently compresses oversized pages to **just below 100 KB** while maximizing text legibility and image quality.

---

## Features

- **Per-Page Size Analysis:** Extracts and measures each individual page's isolated byte size.
- **Smart Adaptive Optimization:** Compresses pages to just under the 100 KB threshold (targeting ~98–99.5 KB) instead of crushing quality.
- **Text & Vector Preservation:** Keeps fonts and vector paths intact; only compresses background/embedded images using high-quality Lanczos resampling and binary-searched JPEG quality.
- **High-Resolution Adaptive Rasterization:** Intelligently handles vector-heavy pages or documents with large embedded fonts.
- **Drag-and-Drop UI:** Clean, dark-mode web interface with real-time feedback and direct downloads.

---

## Getting Started

### Prerequisites

- Python 3.10+
- `pip`

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/hellosreehari/pdf-page-size-checker.git
   cd pdf-page-size-checker
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:
   ```bash
   python3 app.py
   ```

4. Open your browser and navigate to:
   ```
   http://127.0.0.1:5000
   ```

---

## Tech Stack

- **Backend:** Flask, PyMuPDF (v2.1), Pillow
- **Frontend:** Vanilla HTML/CSS/JavaScript
