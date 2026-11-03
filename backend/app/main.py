from __future__ import annotations

import os
import secrets
import re
import subprocess
from io import BytesIO
from copy import deepcopy
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from enum import Enum
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from docx import Document
from pypdf import PdfReader


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./exam_papers.db")
engine_args = {"connect_args": {"check_same_thread": False}} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, **engine_args)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
# pbkdf2_sha256 is available in passlib without a platform-specific bcrypt
# extension, which keeps local Windows development dependable.
passwords = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
security = HTTPBearer()
sessions: dict[str, int] = {}
TEMPLATE_DOCX = Path(os.getenv("PAPER_TEMPLATE_PATH", r"E:\COLLEGE\IA2 QP DS.docx"))
GENERATED_PAPERS = Path(os.getenv("GENERATED_PAPERS_DIR", "./generated_papers"))


class Base(DeclarativeBase):
    pass


class Role(str, Enum):
    ADMIN = "ADMIN"
    HOD = "HOD"
    TEACHER = "TEACHER"


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    academic_year: Mapped[str] = mapped_column(String(20))
    users: Mapped[list[User]] = relationship(back_populates="department")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    department: Mapped[Department | None] = relationship(back_populates="users")


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    marks: Mapped[int] = mapped_column(Integer)
    co: Mapped[str] = mapped_column(String(20))
    rbt: Mapped[str] = mapped_column(String(8))
    module: Mapped[int] = mapped_column(Integer)
    subject_code: Mapped[str] = mapped_column(String(30))
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


Base.metadata.create_all(engine)


class BootstrapIn(BaseModel):
    name: str
    email: str
    password: str = Field(min_length=8)


class LoginIn(BaseModel):
    email: str
    password: str


class DepartmentIn(BaseModel):
    name: str
    code: str
    academic_year: str


class UserIn(BaseModel):
    name: str
    email: str
    password: str = Field(min_length=8)


class QuestionIn(BaseModel):
    body: str = Field(min_length=8)
    marks: int = Field(ge=1, le=10)
    co: str = Field(pattern=r"^CO[1-9][0-9]*$")
    rbt: str = Field(pattern=r"^L[1-6]$")
    module: int = Field(ge=1, le=12)
    subject_code: str


class QuestionBulkIn(BaseModel):
    questions: list[QuestionIn] = Field(min_length=1, max_length=300)


class GenerateIn(BaseModel):
    subject_code: str
    subject_name: str = Field(min_length=2)
    semester: str
    exam_name: str = "CIE Test 2"
    exam_date: str
    time: str


class PaperSlot(BaseModel):
    part: int
    subpart: str
    marks: int
    co: str
    rbt: str
    alternative_co: str | None = None
    alternative_rbt: str | None = None


DEFAULT_BLUEPRINT = [
    PaperSlot(part=1, subpart="a", marks=4, co="CO3", rbt="L2"),
    PaperSlot(part=1, subpart="b", marks=6, co="CO3", rbt="L3"),
    PaperSlot(part=2, subpart="a", marks=4, co="CO3", rbt="L2"),
    PaperSlot(part=2, subpart="b", marks=6, co="CO3", rbt="L3"),
    # The supplied paper maps Question 5 to CO3 and its OR alternative,
    # Question 6, to CO4. The schema preserves that exceptional rule.
    PaperSlot(part=3, subpart="a", marks=5, co="CO3", rbt="L2", alternative_co="CO4"),
    PaperSlot(part=3, subpart="b", marks=5, co="CO3", rbt="L3", alternative_co="CO4"),
    PaperSlot(part=4, subpart="a", marks=4, co="CO4", rbt="L2"),
    PaperSlot(part=4, subpart="b", marks=6, co="CO4", rbt="L3"),
    PaperSlot(part=5, subpart="a", marks=4, co="CO5", rbt="L3"),
    PaperSlot(part=5, subpart="b", marks=6, co="CO5", rbt="L3"),
]


def tag_from(value: str, pattern: str) -> str | None:
    match = re.search(pattern, value.upper())
    return match.group(0) if match else None


def mark_from(value: str) -> int | None:
    match = re.search(r"(?<!\d)([1-9]|10)(?!\d)", value)
    return int(match.group(1)) if match else None


def question_candidates_from_docx(raw: bytes) -> list[dict]:
    document = Document(BytesIO(raw))
    candidates: list[dict] = []
    for table in document.tables:
        if not table.rows:
            continue
        headers = [cell.text.strip().lower() for cell in table.rows[0].cells]
        question_index = next((i for i, value in enumerate(headers) if "question" in value), None)
        marks_index = next((i for i, value in enumerate(headers) if "mark" in value), None)
        rbt_index = next((i for i, value in enumerate(headers) if "rbt" in value or "level" in value), None)
        co_index = next((i for i, value in enumerate(headers) if value in {"co", "cos"} or "course outcome" in value), None)
        if question_index is None or marks_index is None:
            continue
        inherited_co: str | None = None
        for row_number, row in enumerate(table.rows[1:], start=2):
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            question = cells[question_index] if question_index < len(cells) else ""
            if not question or question.upper() == "OR" or "revised bloom" in question.lower():
                continue
            marks = mark_from(cells[marks_index] if marks_index < len(cells) else "")
            if not marks:
                continue
            rbt = tag_from(cells[rbt_index], r"L[1-6]") if rbt_index is not None and rbt_index < len(cells) else None
            current_co = tag_from(cells[co_index], r"CO\d+") if co_index is not None and co_index < len(cells) else None
            if current_co:
                inherited_co = current_co
            co = current_co or inherited_co
            candidates.append({"source_row": row_number, "body": question, "marks": marks, "co": co, "rbt": rbt})
    if candidates:
        return candidates

    # Question banks are often simple numbered lists or headerless tables.
    # Import their questions even when they do not contain marks/CO/RBT columns;
    # the review grid then requires the teacher to supply missing mappings.
    question_prefix = re.compile(r"^\s*(?:q(?:uestion)?\s*)?\d+\s*[.)-]\s*(.+)$", re.IGNORECASE)
    question_start = re.compile(r"^(?:Explain|Define|Describe|Discuss|Illustrate|Write|Construct|Compare|Represent|Analyse|Analyze|Evaluate|Implement|Differentiate|What|How|Why|List|State)\b", re.IGNORECASE)
    seen: set[str] = set()
    source_items = [paragraph.text.strip() for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            source_items.extend(cell.text.strip().replace("\n", " ") for cell in row.cells)
    for source_row, text in enumerate(source_items, start=1):
        match = question_prefix.match(text)
        body = (match.group(1) if match else text).strip()
        normalized = re.sub(r"\s+", " ", body)
        if normalized in seen or len(normalized) < 12 or not (match or question_start.match(normalized)):
            continue
        seen.add(normalized)
        candidates.append({"source_row": source_row, "body": normalized, "marks": None, "co": None, "rbt": None})
    return candidates


def question_candidates_from_pdf(raw: bytes) -> list[dict]:
    text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(raw)).pages)
    candidates: list[dict] = []
    # Works for PDFs whose question-bank rows retain their text order. If a PDF's
    # columns are flattened differently, the review screen still lets a teacher fix it.
    pattern = re.compile(r"(?ms)^\s*(?:\d+\s+)?([ab])\s+(.*?)(?:\s+)([1-9]|10)\s+(L[1-6])\s*(CO\d+)?(?=^\s*(?:\d+\s+)?[ab]\s+|\Z)")
    for row_number, match in enumerate(pattern.finditer(text), start=1):
        body = re.sub(r"\s+", " ", match.group(2)).strip()
        if len(body) >= 8:
            candidates.append({"source_row": row_number, "body": body, "marks": int(match.group(3)), "co": match.group(5), "rbt": match.group(4)})
    if candidates:
        return candidates
    fallback = re.compile(r"(?m)^\s*(?:q(?:uestion)?\s*)?\d+\s*[.)-]\s*(.+)$", re.IGNORECASE)
    for row_number, match in enumerate(fallback.finditer(text), start=1):
        body = re.sub(r"\s+", " ", match.group(1)).strip()
        if len(body) >= 12:
            candidates.append({"source_row": row_number, "body": body, "marks": None, "co": None, "rbt": None})
    return candidates


def _replace_paragraph(paragraph, text: str) -> None:
    """Replace text while retaining the template paragraph's style and run formatting."""
    run_properties = deepcopy(paragraph.runs[0]._r.rPr) if paragraph.runs and paragraph.runs[0]._r.rPr is not None else None
    paragraph.clear()
    run = paragraph.add_run(text)
    if run_properties is not None:
        run._r.insert(0, run_properties)


def _replace_cell(cell, text: str) -> None:
    first = cell.paragraphs[0]
    _replace_paragraph(first, text)
    for paragraph in cell.paragraphs[1:]:
        paragraph._element.getparent().remove(paragraph._element)


def _fill_question_row(row, number: int | str, subpart: str, text: str, marks: int | str, rbt: str, co: str) -> None:
    values = [str(number), subpart, text, str(marks), rbt, co]
    for cell, value in zip(row.cells, values):
        _replace_cell(cell, value)


def _prepare_question_table(table) -> None:
    """Remove source-question drawings and fixed tall rows from the copied table."""
    for row in table.rows[1:-1]:
        row_properties = row._tr.get_or_add_trPr()
        for element in list(row_properties):
            if element.tag.endswith("}trHeight"):
                row_properties.remove(element)
        for cell in row.cells:
            for element in list(cell._tc.iter()):
                if element.tag.endswith("}drawing") or element.tag.endswith("}pict"):
                    parent = element.getparent()
                    if parent is not None:
                        parent.remove(element)


def create_question_paper_docx(paper: dict, subject_name: str) -> Path:
    if not TEMPLATE_DOCX.exists():
        raise HTTPException(500, "The question-paper template is missing.")
    document = Document(TEMPLATE_DOCX)
    header = paper["header"]
    # Preserve the existing header typography and only substitute its content.
    _replace_paragraph(document.paragraphs[0], header["exam_name"])
    reference_lines = Document(TEMPLATE_DOCX).paragraphs
    date_position = reference_lines[1].text.index("Date:")
    time_position = reference_lines[2].text.index("Time:")
    marks_position = reference_lines[3].text.index("Max. Marks:")
    sem_line = f"Sem: {header['semester']}"
    subject_line = f"Subject: {subject_name}"
    code_line = f"Subject Code: {header['subject_code']}"
    _replace_paragraph(document.paragraphs[1], sem_line + " " * max(1, date_position - len(sem_line)) + f"Date: {header['exam_date']}")
    _replace_paragraph(document.paragraphs[2], subject_line + " " * max(1, time_position - len(subject_line)) + f"Time: {header['time']}")
    _replace_paragraph(document.paragraphs[3], code_line + " " * max(1, marks_position - len(code_line)) + "Max. Marks: 50")

    question_table = document.tables[1]
    _prepare_question_table(question_table)
    rows_by_part = {part: sorted((item for item in paper["questions"] if item["part"] == part), key=lambda item: item["subpart"]) for part in range(1, 6)}
    # Row 29 is the taxonomy note, so it is deliberately never touched.
    row_groups = [(1, 2, 4, 5), (7, 8, 10, 11), (12, 13, 15, 16), (18, 19, 21, 22), (24, 25, 27, 28)]
    question_numbers = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10)]
    for part, rows, numbers in zip(range(1, 6), row_groups, question_numbers):
        slots = rows_by_part[part]
        if len(slots) != 2:
            raise HTTPException(500, f"The paper blueprint is missing Part {part}.")
        for slot, first_row, second_row in zip(slots, rows[:2], rows[2:]):
            _fill_question_row(question_table.rows[first_row], numbers[0] if slot["subpart"] == "a" else "", slot["subpart"], slot["question"], slot["marks"], slot["rbt"], slot["co"])
            _fill_question_row(question_table.rows[second_row], numbers[1] if slot["subpart"] == "a" else "", slot["subpart"], slot["or_question"], slot["marks"], slot["or_rbt"], slot["or_co"])
        # The template's OR line is retained in the question column.
        _replace_cell(question_table.rows[rows[1] + 1].cells[2], "OR")

    GENERATED_PAPERS.mkdir(parents=True, exist_ok=True)
    filename = f"{header['subject_code']}_{header['exam_date']}_question_paper.docx".replace("/", "-")
    output = GENERATED_PAPERS / filename
    document.save(output)
    return output


def create_question_paper_pdf(docx_path: Path) -> Path:
    """Use the local Word PDF engine so the reference template's layout is retained."""
    pdf_path = docx_path.with_suffix(".pdf")
    if pdf_path.exists():
        pdf_path.unlink()
    docx_literal = str(docx_path.resolve()).replace("'", "''")
    pdf_literal = str(pdf_path.resolve()).replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop'; "
        "$word=New-Object -ComObject Word.Application; $word.Visible=$false; "
        f"$document=$word.Documents.Open('{docx_literal}'); "
        f"$document.ExportAsFixedFormat('{pdf_literal}',17); "
        "$document.Close(); $word.Quit()"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=60)
    # Word can disconnect its COM object while closing after a successful export;
    # the exported file is the reliable completion signal.
    if not pdf_path.exists():
        raise HTTPException(500, "Word could not export the question paper to PDF on this computer.")
    return pdf_path


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    user_id = sessions.get(credentials.credentials)
    user = db.get(User, user_id) if user_id else None
    if not user or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in again.")
    return user


def require(*roles: Role):
    def check(user: Annotated[User, Depends(current_user)]) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="You do not have permission for this action.")
        return user
    return check


app = FastAPI(title="Department Question Paper System", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat()}


@app.post("/api/bootstrap", status_code=201)
def bootstrap(payload: BootstrapIn, db: Annotated[Session, Depends(get_db)]):
    if db.scalar(select(User.id).limit(1)):
        raise HTTPException(409, "The initial Admin already exists.")
    admin = User(name=payload.name, email=payload.email.lower(), password_hash=passwords.hash(payload.password), role=Role.ADMIN.value)
    db.add(admin)
    db.commit()
    return {"message": "Admin account created."}


@app.post("/api/auth/login")
def login(payload: LoginIn, db: Annotated[Session, Depends(get_db)]):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not user.active or not passwords.verify(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password.")
    token = secrets.token_urlsafe(32)
    sessions[token] = user.id
    return {"access_token": token, "role": user.role, "name": user.name}


@app.post("/api/departments", status_code=201)
def create_department(payload: DepartmentIn, db: Annotated[Session, Depends(get_db)], _: Annotated[User, Depends(require(Role.ADMIN))]):
    if db.scalar(select(Department.id).where((Department.name == payload.name) | (Department.code == payload.code))):
        raise HTTPException(409, "A department with this name or code already exists.")
    department = Department(**payload.model_dump())
    db.add(department)
    db.commit()
    db.refresh(department)
    return {"id": department.id, "name": department.name, "code": department.code}


@app.post("/api/departments/{department_id}/hods", status_code=201)
def create_hod(department_id: int, payload: UserIn, db: Annotated[Session, Depends(get_db)], _: Annotated[User, Depends(require(Role.ADMIN))]):
    if not db.get(Department, department_id):
        raise HTTPException(404, "Department not found.")
    if db.scalar(select(User.id).where(User.email == payload.email.lower())):
        raise HTTPException(409, "An account already uses this email address.")
    hod = User(**payload.model_dump(exclude={"password"}), password_hash=passwords.hash(payload.password), role=Role.HOD.value, department_id=department_id)
    db.add(hod)
    db.commit()
    return {"id": hod.id, "message": "HOD account created."}


@app.post("/api/teachers", status_code=201)
def create_teacher(payload: UserIn, db: Annotated[Session, Depends(get_db)], hod: Annotated[User, Depends(require(Role.HOD))]):
    if db.scalar(select(User.id).where(User.email == payload.email.lower())):
        raise HTTPException(409, "An account already uses this email address.")
    teacher = User(**payload.model_dump(exclude={"password"}), password_hash=passwords.hash(payload.password), role=Role.TEACHER.value, department_id=hod.department_id)
    db.add(teacher)
    db.commit()
    return {"id": teacher.id, "message": "Teacher account created."}


@app.get("/api/teachers")
def list_teachers(db: Annotated[Session, Depends(get_db)], hod: Annotated[User, Depends(require(Role.HOD))]):
    teachers = db.scalars(select(User).where(User.role == Role.TEACHER.value, User.department_id == hod.department_id).order_by(User.name)).all()
    return [{"id": teacher.id, "name": teacher.name, "email": teacher.email, "active": teacher.active} for teacher in teachers]


@app.patch("/api/teachers/{teacher_id}/access")
def change_teacher_access(teacher_id: int, enabled: bool, db: Annotated[Session, Depends(get_db)], hod: Annotated[User, Depends(require(Role.HOD))]):
    teacher = db.get(User, teacher_id)
    if not teacher or teacher.role != Role.TEACHER.value or teacher.department_id != hod.department_id:
        raise HTTPException(404, "Teacher not found in your department.")
    teacher.active = enabled
    db.commit()
    return {"id": teacher.id, "active": teacher.active}


@app.post("/api/questions", status_code=201)
def add_question(payload: QuestionIn, db: Annotated[Session, Depends(get_db)], teacher: Annotated[User, Depends(require(Role.TEACHER))]):
    question = Question(**payload.model_dump(), department_id=teacher.department_id, author_id=teacher.id)
    db.add(question)
    db.commit()
    return {"id": question.id, "message": "Question added to the bank."}


@app.post("/api/question-bank/import")
async def import_question_bank(
    file: Annotated[UploadFile, File(...)],
    subject_code: Annotated[str, Form(...)],
    module: Annotated[int, Form(...)],
    _: Annotated[User, Depends(require(Role.TEACHER))],
):
    """Extract Word/PDF table rows, retaining unmapped rows for teacher review."""
    filename = file.filename or ""
    extension = os.path.splitext(filename.lower())[1]
    if extension not in {".docx", ".pdf"}:
        raise HTTPException(415, "Upload a Word (.docx) or PDF file.")
    if module < 1 or module > 12:
        raise HTTPException(422, "Module must be between 1 and 12.")
    raw = await file.read()
    if not raw or len(raw) > 15 * 1024 * 1024:
        raise HTTPException(413, "Upload a non-empty file smaller than 15 MB.")
    try:
        candidates = question_candidates_from_docx(raw) if extension == ".docx" else question_candidates_from_pdf(raw)
    except Exception as exc:
        raise HTTPException(422, "The file could not be read. Try a digitally created PDF or a .docx file.") from exc
    questions = []
    for candidate in candidates:
        mapped = bool(candidate.get("marks") and candidate.get("co") and candidate.get("rbt"))
        questions.append({**candidate, "subject_code": subject_code.upper(), "module": module, "status": "mapped" if mapped else "needs_review"})
    return {"filename": filename, "questions": questions, "mapped": sum(q["status"] == "mapped" for q in questions), "needs_review": sum(q["status"] == "needs_review" for q in questions)}


@app.post("/api/questions/bulk", status_code=201)
def save_imported_questions(payload: QuestionBulkIn, db: Annotated[Session, Depends(get_db)], teacher: Annotated[User, Depends(require(Role.TEACHER))]):
    questions = [Question(**item.model_dump(), department_id=teacher.department_id, author_id=teacher.id) for item in payload.questions]
    db.add_all(questions)
    db.commit()
    return {"saved": len(questions), "message": "Mapped questions added to the bank."}


@app.get("/api/questions")
def list_questions(subject_code: str, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require(Role.HOD, Role.TEACHER))]):
    items = db.scalars(select(Question).where(Question.department_id == user.department_id, Question.subject_code == subject_code, Question.active.is_(True))).all()
    return [{"id": q.id, "body": q.body, "marks": q.marks, "co": q.co, "rbt": q.rbt, "module": q.module} for q in items]


@app.post("/api/papers/generate")
def generate_paper(payload: GenerateIn, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require(Role.HOD, Role.TEACHER))]):
    bank = db.scalars(select(Question).where(Question.department_id == user.department_id, Question.subject_code == payload.subject_code, Question.active.is_(True))).all()
    chosen_ids: set[int] = set()
    alternatives: list[dict] = []
    for slot in DEFAULT_BLUEPRINT:
        first_matches = [q for q in bank if q.id not in chosen_ids and q.marks == slot.marks and q.co == slot.co and q.rbt == slot.rbt]
        alternative_co = slot.alternative_co or slot.co
        alternative_rbt = slot.alternative_rbt or slot.rbt
        second_matches = [q for q in bank if q.id not in chosen_ids and q.marks == slot.marks and q.co == alternative_co and q.rbt == alternative_rbt]
        if not first_matches or not second_matches:
            raise HTTPException(422, f"Part {slot.part}{slot.subpart} needs questions tagged {slot.co}/{alternative_co}, {slot.rbt}/{alternative_rbt}, {slot.marks} marks.")
        first = first_matches[0]
        second = next((q for q in second_matches if q.id != first.id), None)
        if not second:
            raise HTTPException(422, f"Part {slot.part}{slot.subpart} needs two distinct eligible questions.")
        chosen_ids.update({first.id, second.id})
        alternatives.append({"part": slot.part, "subpart": slot.subpart, "marks": slot.marks, "co": slot.co, "rbt": slot.rbt, "question": first.body, "or_question": second.body, "or_co": alternative_co, "or_rbt": alternative_rbt})
    return {"header": payload.model_dump(), "total_marks": 50, "instructions": "Answer any 5 full questions, selecting ONE question from each part.", "questions": alternatives, "taxonomy": "L1 Remember, L2 Understand, L3 Apply, L4 Analyze, L5 Evaluate, L6 Create"}


@app.post("/api/papers/export-docx")
def export_question_paper(payload: GenerateIn, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require(Role.HOD, Role.TEACHER))]):
    paper = generate_paper(payload, db, user)
    output = create_question_paper_docx(paper, payload.subject_name)
    return FileResponse(output, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=output.name)


@app.post("/api/papers/export-pdf")
def export_question_paper_pdf(payload: GenerateIn, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require(Role.HOD, Role.TEACHER))]):
    paper = generate_paper(payload, db, user)
    docx_output = create_question_paper_docx(paper, payload.subject_name)
    pdf_output = create_question_paper_pdf(docx_output)
    return FileResponse(pdf_output, media_type="application/pdf", filename=pdf_output.name)
