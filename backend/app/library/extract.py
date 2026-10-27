"""Plain text from course documents, for the agent to search and read.

PDF pages are separated by "--- page N ---" lines so the agent can cite and open the right page.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from urllib.parse import urljoin
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from markdownify import markdownify

TEXT_EXTENSIONS = {
    ".txt", ".md", ".c", ".h", ".s", ".py", ".java", ".rs", ".go", ".sh", ".json", ".csv", ".tex", ".mk",
}  # fmt: skip
DOCUMENT_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx", ".pptx", ".html", ".htm"}
PAGE_MARK = "--- page {} ---"


@dataclass
class Extracted:
    text: str | None
    pages: int | None = None
    note: str | None = None  # why the text is missing or partial


def extract(content: bytes, name: str, mime: str | None = None) -> Extracted:
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    mime = (mime or "").split(";")[0].strip().lower()
    try:
        if ext == ".pdf" or mime == "application/pdf" or content[:5] == b"%PDF-":
            return pdf_text(content)
        if ext == ".docx":
            return Extracted(_docx_text(content))
        if ext == ".pptx":
            return _pptx_text(content)
        if ext in (".html", ".htm") or mime == "text/html":
            return Extracted(html_to_markdown(content.decode("utf-8", "replace")))
        if ext in TEXT_EXTENSIONS or mime.startswith("text/"):
            return Extracted(content.decode("utf-8", "replace"))
    except Exception as e:  # a corrupt file shouldn't stop the library sync
        return Extracted(None, note=f"couldn't read it ({type(e).__name__})")
    return Extracted(None, note=f"no text version for {ext or mime or 'this file type'}")


def pdf_text(content: bytes) -> Extracted:
    import pymupdf  # imported here: it's big, and only library syncs need it

    with pymupdf.open(stream=content, filetype="pdf") as doc:
        pages = [page.get_text("text") for page in doc]
    body = "\n\n".join(f"{PAGE_MARK.format(i)}\n\n{t.strip()}" for i, t in enumerate(pages, 1))
    letters = sum(len(t.strip()) for t in pages)
    note = None
    if pages and letters < 40 * len(pages):
        note = "little or no text layer (scanned or image slides): open the PDF to read it"
    return Extracted(body, pages=len(pages), note=note)


_DROP = ["script", "style", "noscript", "nav", "header", "footer", "form", "svg", "iframe", "button"]


def html_to_markdown(html: str, base_url: str | None = None, *, soup: BeautifulSoup | None = None) -> str:
    soup = soup or BeautifulSoup(html, "html.parser")
    for tag in soup(_DROP):
        tag.decompose()
    for tag in soup.select(".navbar, [role=navigation], .skip-link"):
        tag.decompose()
    if base_url:
        for a in soup.find_all("a", href=True):
            a["href"] = urljoin(base_url, a["href"])
    node = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.find(id="content") or soup.body or soup
    text = markdownify(str(node), heading_style="ATX", strip=["img"], bullets="-")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _docx_text(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    paragraphs = ("".join(t.text or "" for t in p.iter(f"{_W}t")) for p in root.iter(f"{_W}p"))
    return "\n\n".join(p for p in paragraphs if p.strip())


def _pptx_text(content: bytes) -> Extracted:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        names.sort(key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        slides = []
        for n in names:
            root = ElementTree.fromstring(z.read(n))
            lines = ("".join(t.text or "" for t in p.iter(f"{_A}t")) for p in root.iter(f"{_A}p"))
            slides.append("\n".join(line for line in lines if line.strip()))
    body = "\n\n".join(f"{PAGE_MARK.format(i)}\n\n{t}" for i, t in enumerate(slides, 1))
    return Extracted(body, pages=len(slides))
