"""Reading a PDF back in the tests: its pages as text, with the spaces flattened."""

import re
from io import BytesIO

from pypdf import PdfReader


def pages_of(content: bytes) -> list[str]:
    reader = PdfReader(BytesIO(content))
    return [re.sub(r"\s+", " ", page.extract_text() or "") for page in reader.pages]


def text_of(content: bytes) -> str:
    return " ".join(pages_of(content))
