"use client";

import { FormEvent, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function Home() {
  return <RolePortal />;
}

export function RolePortal({ expectedRole }: { expectedRole?: "ADMIN" | "HOD" | "TEACHER" }) {
  const [token, setToken] = useState("");
  const [role, setRole] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [notice, setNotice] = useState("Sign in with an account created by your Admin or HOD.");

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const response = await fetch(`${API}/api/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) });
    const body = await response.json();
    if (!response.ok) return setNotice(body.detail ?? "Unable to sign in.");
    if (expectedRole && body.role !== expectedRole) return setNotice(`This account is ${body.role}. Please use the correct role link.`);
    setToken(body.access_token);
    setRole(body.role);
    setName(body.name);
    setNotice(`Signed in as ${body.name} (${body.role}). The API is ready for the role dashboard.`);
  }

  if (token && role === "ADMIN") return <AdminDashboard token={token} name={name} />;
  if (token && role === "HOD") return <HodDashboard token={token} name={name} />;
  if (token && role === "TEACHER") return <TeacherDashboard token={token} name={name} />;

  return <main>
    <section className="hero"><p className="eyebrow">{expectedRole ? `${expectedRole} portal` : "Department Examination Portal"}</p><h1>{expectedRole ? `Sign in to the ${expectedRole} portal.` : "Build compliant question papers from your approved question bank."}</h1><p>Admin controls departments and HODs. HODs control teacher access. Teachers contribute questions. Every generated paper follows the configured marks, CO, and RBT blueprint.</p></section>
    <section className="workflow"><article><b>1</b><h2>Admin</h2><p>Creates departments, academic years, and HOD accounts.</p></article><article><b>2</b><h2>HOD</h2><p>Creates or revokes teacher accounts within the department.</p></article><article><b>3</b><h2>Teacher</h2><p>Adds or imports tagged questions: marks, CO, RBT, module.</p></article><article><b>4</b><h2>Generator</h2><p>Creates a 50-mark, five-part paper with alternatives.</p></article></section>
    <section className="panel"><h2>Sign in</h2><form onSubmit={signIn}><label>Email<input value={email} onChange={e => setEmail(e.target.value)} type="email" required /></label><label>Password<input value={password} onChange={e => setPassword(e.target.value)} type="password" required /></label><button>Sign in</button></form><p className={token ? "success" : "notice"}>{notice}</p></section>
  </main>;
}

function AdminDashboard({ token, name }: { token: string; name: string }) {
  const [departmentId, setDepartmentId] = useState<number | null>(null);
  const [message, setMessage] = useState(`Welcome, ${name}. Start by creating a department.`);

  async function createDepartment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const response = await fetch(`${API}/api/departments`, {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ name: form.get("departmentName"), code: form.get("departmentCode"), academic_year: form.get("academicYear") })
    });
    const body = await response.json();
    if (!response.ok) return setMessage(body.detail ?? "Unable to create the department.");
    setDepartmentId(body.id);
    setMessage(`${body.name} created. You can now create its HOD account.`);
  }

  async function createHod(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!departmentId) return setMessage("Create a department first.");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const response = await fetch(`${API}/api/departments/${departmentId}/hods`, {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ name: form.get("hodName"), email: form.get("hodEmail"), password: form.get("hodPassword") })
    });
    const body = await response.json();
    setMessage(response.ok ? "HOD account created. The HOD can now sign in and add teachers." : body.detail ?? "Unable to create the HOD.");
    if (response.ok) formElement.reset();
  }

  return <main>
    <section className="hero"><p className="eyebrow">Admin dashboard</p><h1>Set up departments and delegate access.</h1><p>Only the Admin can create departments and HOD accounts. Each HOD will manage teacher access for their own department.</p></section>
    <p className="notice">{message}</p>
    <section className="dashboard-grid">
      <article className="panel"><h2>1. Create department</h2><form onSubmit={createDepartment}><label>Department name<input name="departmentName" placeholder="Computer Science" required /></label><label>Department code<input name="departmentCode" placeholder="CSE" required /></label><label>Academic year<input name="academicYear" placeholder="2026-27" required /></label><button>Create department</button></form></article>
      <article className="panel"><h2>2. Create HOD account</h2><form onSubmit={createHod}><label>HOD name<input name="hodName" required /></label><label>College email<input name="hodEmail" type="email" required /></label><label>Temporary password<input name="hodPassword" type="password" minLength={8} required /></label><button disabled={!departmentId}>Create HOD</button></form></article>
    </section>
  </main>;
}

type Teacher = { id: number; name: string; email: string; active: boolean };

function HodDashboard({ token, name }: { token: string; name: string }) {
  const [teachers, setTeachers] = useState<Teacher[]>([]);
  const [message, setMessage] = useState(`Welcome, ${name}.`);
  const headers = { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
  async function refreshTeachers() {
    const response = await fetch(`${API}/api/teachers`, { headers });
    const body = await response.json();
    if (response.ok) setTeachers(body);
    else setMessage(body.detail ?? "Unable to load teachers.");
  }
  useEffect(() => { void refreshTeachers(); }, []);
  async function createTeacher(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const response = await fetch(`${API}/api/teachers`, { method: "POST", headers, body: JSON.stringify({ name: form.get("teacherName"), email: form.get("teacherEmail"), password: form.get("teacherPassword") }) });
    const body = await response.json();
    setMessage(response.ok ? "Teacher account created." : body.detail ?? "Unable to create teacher.");
    if (response.ok) { formElement.reset(); await refreshTeachers(); }
  }
  async function setAccess(id: number, enabled: boolean) {
    const response = await fetch(`${API}/api/teachers/${id}/access?enabled=${enabled}`, { method: "PATCH", headers: { Authorization: `Bearer ${token}` } });
    const body = await response.json();
    setMessage(response.ok ? (enabled ? "Teacher access restored." : "Teacher access revoked.") : body.detail ?? "Unable to change access.");
    if (response.ok) await refreshTeachers();
  }
  return <main><section className="hero"><p className="eyebrow">HOD dashboard</p><h1>Manage your department’s teachers.</h1><p>Create teacher accounts and revoke access when required.</p></section><p className="notice">{message}</p><section className="dashboard-grid"><article className="panel"><h2>Create teacher</h2><form onSubmit={createTeacher}><label>Teacher name<input name="teacherName" required /></label><label>College email<input name="teacherEmail" type="email" required /></label><label>Temporary password<input name="teacherPassword" type="password" minLength={8} required /></label><button>Create teacher</button></form></article><article className="panel"><h2>Teacher access</h2><div className="records">{teachers.map(teacher => <div className="record" key={teacher.id}><span><b>{teacher.name}</b><small>{teacher.email}</small></span><button className={teacher.active ? "danger" : "secondary"} onClick={() => void setAccess(teacher.id, !teacher.active)}>{teacher.active ? "Revoke" : "Restore"}</button></div>)}{!teachers.length && <p>No teacher accounts yet.</p>}</div></article></section></main>;
}

type ImportedQuestion = { source_row: number; body: string; marks: number | null; co: string | null; rbt: string | null; module: number; subject_code: string; status: "mapped" | "needs_review" };

function TeacherDashboard({ token, name }: { token: string; name: string }) {
  const [message, setMessage] = useState(`Welcome, ${name}. Upload a question bank to extract and map its questions.`);
  const [paper, setPaper] = useState<{ total_marks: number; questions: { part: number; subpart: string; question: string; or_question: string; marks: number; co: string; rbt: string }[] } | null>(null);
  const [imported, setImported] = useState<ImportedQuestion[]>([]);
  const [exportPayload, setExportPayload] = useState<{ subject_code: string; subject_name: string; semester: string; exam_date: string; time: string } | null>(null);
  const headers = { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
  async function uploadBank(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const response = await fetch(`${API}/api/question-bank/import`, { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: form });
    const body = await response.json();
    if (!response.ok) return setMessage(body.detail ?? "Unable to read the file.");
    setImported(body.questions);
    setMessage(`${body.questions.length} questions extracted. ${body.mapped} were mapped from the file; review every row, then save the bank.`);
  }
  function updateImported(index: number, field: keyof ImportedQuestion, value: string | number) {
    setImported(items => items.map((item, itemIndex) => itemIndex === index ? { ...item, [field]: value, status: "mapped" } : item));
  }
  async function saveImported() {
    const incomplete = imported.find(item => !item.body || !item.marks || !item.co || !item.rbt);
    if (incomplete) return setMessage("Complete marks, CO, and RBT for every extracted question before saving.");
    const questions = imported.map(({ body, marks, co, rbt, module, subject_code }) => ({ body, marks, co, rbt, module, subject_code }));
    const response = await fetch(`${API}/api/questions/bulk`, { method: "POST", headers, body: JSON.stringify({ questions }) });
    const body = await response.json();
    if (!response.ok) return setMessage(body.detail ?? "Unable to save imported questions.");
    setMessage(`${body.saved} questions added to the bank.`); setImported([]);
  }
  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload = { subject_code: String(form.get("paperSubject")), subject_name: String(form.get("subjectName")), semester: String(form.get("semester")), exam_date: String(form.get("examDate")), time: String(form.get("time")) };
    const response = await fetch(`${API}/api/papers/generate`, { method: "POST", headers, body: JSON.stringify(payload) });
    const body = await response.json();
    if (!response.ok) return setMessage(body.detail ?? "Unable to generate paper.");
    setPaper(body); setExportPayload(payload); setMessage("Paper blueprint generated successfully. Download the Word paper in the supplied format.");
  }
  async function downloadPaper(format: "docx" | "pdf") {
    if (!exportPayload) return;
    const response = await fetch(`${API}/api/papers/export-${format}`, { method: "POST", headers, body: JSON.stringify(exportPayload) });
    if (!response.ok) { const body = await response.json(); return setMessage(body.detail ?? "Unable to create the question paper."); }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob); const link = document.createElement("a");
    link.href = url; link.download = `${exportPayload.subject_code}_question_paper.${format}`; link.click(); URL.revokeObjectURL(url);
    setMessage(`Question paper downloaded in the supplied ${format.toUpperCase()} format.`);
  }
  return <main><section className="hero"><p className="eyebrow">Teacher dashboard</p><h1>Upload your question bank.</h1><p>Upload a Word or PDF question bank. The system extracts its questions and maps marks, CO, and RBT tags found in the file. You confirm or correct the mapping before it enters the bank.</p></section><p className="notice">{message}</p><section className="dashboard-grid"><article className="panel"><h2>1. Upload Word or PDF</h2><form onSubmit={uploadBank}><label>Question-bank file<input name="file" type="file" accept=".docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" required /></label><label>Subject code<input name="subject_code" placeholder="BCS304" required /></label><label>Module for this upload<input name="module" type="number" min="1" max="12" required /></label><button>Extract and map questions</button></form></article><article className="panel"><h2>3. Generate paper</h2><form onSubmit={generate}><label>Subject code<input name="paperSubject" placeholder="BCS304" required /></label><label>Subject name<input name="subjectName" placeholder="Data Structures and Applications" required /></label><label>Semester<input name="semester" placeholder="III" required /></label><label>Exam date<input name="examDate" type="date" required /></label><label>Time<input name="time" placeholder="9:30-11:00 AM" required /></label><button>Check rules and generate</button></form></article></section>{imported.length > 0 && <section className="panel import-review"><h2>2. Review extracted mappings</h2><p>Rows marked “needs review” did not contain all required tags in the uploaded file. Complete or correct them before saving.</p>{imported.map((item, index) => <div className="import-row" key={`${item.source_row}-${index}`}><textarea value={item.body} onChange={event => updateImported(index, "body", event.target.value)} /><select value={item.marks ?? ""} onChange={event => updateImported(index, "marks", Number(event.target.value))}><option value="">Marks</option><option value="4">4</option><option value="5">5</option><option value="6">6</option></select><select value={item.co ?? ""} onChange={event => updateImported(index, "co", event.target.value)}><option value="">CO</option><option>CO1</option><option>CO2</option><option>CO3</option><option>CO4</option><option>CO5</option></select><select value={item.rbt ?? ""} onChange={event => updateImported(index, "rbt", event.target.value)}><option value="">RBT</option><option>L1</option><option>L2</option><option>L3</option><option>L4</option><option>L5</option><option>L6</option></select><span className={item.status}>{item.status === "mapped" ? "Mapped" : "Needs review"}</span></div>)}<button onClick={() => void saveImported()}>Save all mapped questions</button></section>}{paper && <section className="panel generated"><h2>Generated 50-mark blueprint</h2><div className="download-actions"><button onClick={() => void downloadPaper("pdf")}>Download PDF in template format</button><button className="secondary" onClick={() => void downloadPaper("docx")}>Download editable Word copy</button></div>{paper.questions.map(item => <p key={`${item.part}${item.subpart}`}><b>Part {item.part}{item.subpart} — {item.marks} marks · {item.co} · {item.rbt}</b><br />{item.question}<br /><em>OR</em><br />{item.or_question}</p>)}</section>}</main>;
}
