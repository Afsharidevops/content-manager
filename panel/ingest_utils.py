from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from typing import Any

# Extremely lightweight article extraction fallback 
# since panel does not depend on content_bot.extract
def extract_text_from_html(html_content: str) -> str:
    """Strip script/style and return raw text."""
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html_content, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def fetch_url(url: str, timeout: int = 15) -> tuple[str, str]:
    """Fetch URL and return (title, text_content)."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Hermes-Content-Manager/1.0", "Accept": "text/html,application/xhtml+xml"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "pdf" in content_type.lower():
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
                    tf.write(response.read())
                    tf.close()
                    try:
                        return _extract_pdf(tf.name, url)
                    finally:
                        os.unlink(tf.name)
            
            raw = response.read()
            # simple encoding detection
            charset = "utf-8"
            match = re.search(r'charset=["\']?([\w-]+)', content_type, re.IGNORECASE)
            if match:
                charset = match.group(1)
            html_text = raw.decode(charset, errors="replace")
            
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
            title = title_match.group(1).strip() if title_match else url
            return title, extract_text_from_html(html_text)
    except urllib.error.URLError as exc:
        raise ValueError(f"Failed to fetch {url}: {exc.reason}") from exc

def _extract_pdf(pdf_path: str, default_title: str) -> tuple[str, str]:
    """Extract text using pdftotext."""
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", "-nopgbrk", pdf_path, "-"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30
        )
        return default_title, result.stdout
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise ValueError("Failed to extract PDF (pdftotext may be missing or failed)") from exc

