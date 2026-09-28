import re
from pathlib import Path


def extract_resume_text(file_path: str) -> str:
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    raise ValueError("Only PDF and DOCX resume files are supported.")


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "PDF support is not installed. Run: python -m pip install pypdf"
        ) from exc
    try:
        reader = PdfReader(str(path))
        if getattr(reader, "is_encrypted", False):
            raise RuntimeError("This PDF is password protected. Upload an unlocked PDF.")
        parts = []
        for page in reader.pages:
            txt = page.extract_text() or ""
            if txt.strip():
                parts.append(txt)
        result = "\n".join(parts).strip()
        if not result:
            raise RuntimeError(
                "No selectable text was found. This may be a scanned/image-only PDF."
            )
        return result
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Could not read the PDF: {exc}") from exc


def _extract_docx(path: Path) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError(
            "DOCX support is not installed. Run: python -m pip install python-docx"
        ) from exc
    try:
        doc = Document(str(path))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(
                    cell.text.strip() for cell in row.cells if cell.text.strip()
                )
                if row_text:
                    parts.append(row_text)
        result = "\n".join(parts).strip()
        if not result:
            raise RuntimeError("No readable text was found in this DOCX file.")
        return result
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Could not read the DOCX file: {exc}") from exc


def extract_resume_details(text: str) -> dict:
    """Extract common resume details without sending the resume to an external API."""
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]
    first_candidates = []
    for line in lines[:12]:
        if (
            2 <= len(line.split()) <= 5
            and "@" not in line
            and not re.search(r"\d", line)
            and len(line) <= 60
            and line.upper() != line
        ):
            first_candidates.append(line)
    name = first_candidates[0] if first_candidates else (lines[0] if lines else "Not detected")

    emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    phones = re.findall(r"(?:\+91[\s-]?)?[6-9]\d{9}\b", re.sub(r"[()]", "", text))

    linkedin = re.findall(r"(?:https?://)?(?:www\.)?linkedin\.com/[^\s|]+", text, re.I)
    github = re.findall(r"(?:https?://)?(?:www\.)?github\.com/[^\s|]+", text, re.I)

    sections = {}
    current = None
    headers = {
        "summary": ["summary", "profile", "objective", "professional summary"],
        "education": ["education", "academic background", "qualifications"],
        "experience": ["experience", "work experience", "employment", "internship"],
        "projects": ["projects", "academic projects", "personal projects"],
        "certifications": ["certifications", "certificates"],
    }

    for line in lines:
        key = line.lower().strip(" :-")
        matched = None
        for section, variants in headers.items():
            if key in variants or any(key.startswith(v + " ") for v in variants):
                matched = section
                break
        if matched:
            current = matched
            sections.setdefault(current, [])
            continue
        if current:
            sections.setdefault(current, []).append(line)

    education = " ".join(sections.get("education", [])[:6])
    experience = " ".join(sections.get("experience", [])[:8])
    projects = " ".join(sections.get("projects", [])[:8])
    summary = " ".join(sections.get("summary", [])[:5])

    years = re.findall(r"(?:19|20)\d{2}", text)
    experience_years = None
    if len(years) >= 2:
        try:
            nums = [int(y) for y in years]
            span = max(nums) - min(nums)
            if 0 <= span <= 20:
                experience_years = span
        except ValueError:
            pass

    return {
        "name": name,
        "email": emails[0] if emails else "Not detected",
        "phone": phones[0] if phones else "Not detected",
        "linkedin": linkedin[0] if linkedin else "Not detected",
        "github": github[0] if github else "Not detected",
        "summary": summary or "Not detected",
        "education": education or "Not detected",
        "experience": experience or "Not detected",
        "projects": projects or "Not detected",
        "certifications": " ".join(sections.get("certifications", [])[:6]) or "Not detected",
        "experience_years": experience_years,
    }