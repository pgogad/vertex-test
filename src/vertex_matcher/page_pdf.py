"""Bounded subprocess entry point for extracting text from downloaded PDFs."""

import io
import sys

from pypdf import PdfReader


def main() -> None:
    reader = PdfReader(io.BytesIO(sys.stdin.buffer.read(2_000_001)))
    remaining = 30000
    for page in reader.pages[:5]:
        text = (page.extract_text() or "")[:remaining]
        sys.stdout.write(text + "\n")
        remaining -= len(text)
        if remaining <= 0:
            break


if __name__ == "__main__":
    main()
