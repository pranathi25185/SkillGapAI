import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import (
    Flask, flash, g, redirect, render_template, request,
    send_file, send_from_directory, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from utils.resume_parser import extract_resume_text, extract_resume_details
from utils.skill_extractor import extract_keywords, extract_skills
from utils.matcher import calculate_match


BASE_DIR = Path(__file__).resolve().parent
DATABASE_DIR = BASE_DIR / "database"
UPLOAD_DIR = BASE_DIR / "uploads"
DATABASE_PATH = DATABASE_DIR / "skillgap.db"

DATABASE_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "SKILLGAP_SECRET_KEY", "dev-only-change-this-secret-key"
)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config["ALLOWED_EXTENSIONS"] = {"pdf", "docx"}


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DATABASE_PATH))
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db:
        db.close()


def table_columns(name):
    return {r["name"] for r in get_db().execute(f"PRAGMA table_info({name})").fetchall()}


def ensure_column(table, column, definition):
    if column not in table_columns(table):
        get_db().execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            theme TEXT DEFAULT 'light'
        )
    """)

    exists = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='analyses'"
    ).fetchone()
    if exists:
        cols = table_columns("analyses")
        if "resume_name" not in cols or "user_id" not in cols:
            legacy = f"analyses_legacy_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            db.execute(f"ALTER TABLE analyses RENAME TO {legacy}")

    db.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            resume_name TEXT NOT NULL,
            job_title TEXT NOT NULL,
            job_description TEXT,
            match_score INTEGER DEFAULT 0,
            skill_score INTEGER DEFAULT 0,
            keyword_score INTEGER DEFAULT 0,
            completeness_score INTEGER DEFAULT 0,
            resume_skills TEXT DEFAULT '[]',
            required_skills TEXT DEFAULT '[]',
            matching_skills TEXT DEFAULT '[]',
            missing_skills TEXT DEFAULT '[]',
            keywords TEXT DEFAULT '[]',
            roadmap TEXT DEFAULT '[]',
            improvements TEXT DEFAULT '[]',
            resume_details TEXT DEFAULT '{}',
            recommendations TEXT DEFAULT '[]',
            interview_questions TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    for col, definition in [
        ("job_description", "TEXT"), ("match_score", "INTEGER DEFAULT 0"),
        ("skill_score", "INTEGER DEFAULT 0"), ("keyword_score", "INTEGER DEFAULT 0"),
        ("completeness_score", "INTEGER DEFAULT 0"), ("resume_skills", "TEXT DEFAULT '[]'"),
        ("required_skills", "TEXT DEFAULT '[]'"), ("matching_skills", "TEXT DEFAULT '[]'"),
        ("missing_skills", "TEXT DEFAULT '[]'"), ("keywords", "TEXT DEFAULT '[]'"),
        ("roadmap", "TEXT DEFAULT '[]'"), ("improvements", "TEXT DEFAULT '[]'"),
        ("resume_details", "TEXT DEFAULT '{}'"), ("recommendations", "TEXT DEFAULT '[]'"),
        ("interview_questions", "TEXT DEFAULT '[]'"),
    ]:
        ensure_column("analyses", col, definition)

    db.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            is_read INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    db.commit()


def jload(value, default=None):
    if default is None:
        default = []
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def jdump(value):
    return json.dumps(value, ensure_ascii=False)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]


@app.before_request
def load_user():
    g.user = None
    uid = session.get("user_id")
    if uid:
        g.user = get_db().execute(
            "SELECT id, name, email, created_at, theme FROM users WHERE id=?",
            (uid,)
        ).fetchone()


@app.context_processor
def globals_for_templates():
    unread = 0
    if g.get("user"):
        unread = get_db().execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id=? AND is_read=0",
            (g.user["id"],)
        ).fetchone()["c"]
    return {"current_user": g.get("user"), "unread_notifications": unread, "current_year": datetime.now().year}


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not g.user:
            flash("Please login to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def latest_analysis():
    return get_db().execute(
        "SELECT * FROM analyses WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (g.user["id"],)
    ).fetchone()


def prepare(row):
    if not row:
        return None
    data = dict(row)
    for field in [
        "resume_skills", "required_skills", "matching_skills", "missing_skills",
        "keywords", "roadmap", "improvements", "recommendations", "interview_questions"
    ]:
        data[field] = jload(data.get(field), [])
    data["resume_details"] = jload(data.get("resume_details"), {})
    return data


def skill_level(score):
    if score >= 85:
        return {"label": "Expert", "class": "expert"}
    if score >= 70:
        return {"label": "Advanced", "class": "advanced"}
    if score >= 50:
        return {"label": "Intermediate", "class": "intermediate"}
    if score >= 30:
        return {"label": "Developing", "class": "developing"}
    return {"label": "Beginner", "class": "beginner"}


def build_roadmap(missing):
    descriptions = {
        "Python": "Practice Python, OOP, modules, error handling and problem solving.",
        "JavaScript": "Learn ES6+, DOM, async programming and API integration.",
        "React": "Build components, hooks, routing and API-driven interfaces.",
        "Flask": "Build routes, templates, APIs, validation and database-backed applications.",
        "SQL": "Practice joins, subqueries, aggregation, indexes and optimization.",
        "Git": "Practice branches, pull requests, merges and conflict resolution.",
        "Docker": "Learn Dockerfiles, images, containers, volumes and networking.",
        "AWS": "Learn core cloud services and deploy a small web application.",
        "Data Structures": "Practice arrays, linked lists, stacks, queues, trees and hashing.",
        "Algorithms": "Practice searching, sorting, recursion, greedy algorithms and complexity.",
        "DBMS": "Study keys, normalization, transactions, indexes and concurrency.",
        "Operating Systems": "Study processes, threads, scheduling, memory and deadlocks.",
        "Computer Networks": "Study TCP/IP, HTTP, DNS, routing and network troubleshooting.",
        "Machine Learning": "Study preprocessing, supervised learning, evaluation and model selection.",
    }
    return [
        {"skill": s, "description": descriptions.get(s, f"Study {s} fundamentals and build a small project.")}
        for s in missing
    ]


ROLE_PROFILES = {
    "Python Developer": {"skills": {"Python", "Flask", "Django", "SQL", "Git", "REST API", "OOP"}},
    "Full Stack Developer": {"skills": {"HTML", "CSS", "JavaScript", "React", "Node.js", "SQL", "Git", "REST API"}},
    "Data Analyst": {"skills": {"Python", "SQL", "Excel", "Pandas", "NumPy", "Power BI", "Data Analysis"}},
    "Machine Learning Engineer": {"skills": {"Python", "Machine Learning", "scikit-learn", "Pandas", "NumPy", "TensorFlow", "SQL"}},
    "Backend Developer": {"skills": {"Python", "Flask", "Django", "Node.js", "SQL", "REST API", "Git", "Docker"}},
    "Software Engineer": {"skills": {"Python", "Java", "Data Structures", "Algorithms", "OOP", "SQL", "Git", "DBMS"}},
}


def career_recommendations(resume_skills):
    present = {x.lower() for x in resume_skills}
    scored = []
    for role, profile in ROLE_PROFILES.items():
        target = profile["skills"]
        hits = sum(x.lower() in present for x in target)
        pct = round(hits / len(target) * 100)
        missing = [x for x in target if x.lower() not in present]
        scored.append({"role": role, "score": pct, "missing": missing[:4]})
    return sorted(scored, key=lambda x: x["score"], reverse=True)[:4]


def interview_questions(job_title, resume_skills, missing):
    questions = [
        f"Explain a project from your resume that is most relevant to a {job_title} role.",
        f"What was the most difficult technical problem you solved in your projects?",
        "How do you test and debug your code before deployment?",
        "Describe a time you had to learn a technology quickly. How did you approach it?",
    ]
    for skill in resume_skills[:4]:
        questions.append(f"Explain how you have used {skill} in a practical project.")
    for skill in missing[:3]:
        questions.append(f"What do you know about {skill}, and how would you learn it for this role?")
    return questions[:10]


def build_improvements(details, resume_skills, missing, job_title):
    suggestions = []
    if details.get("summary") == "Not detected":
        suggestions.append("Add a concise professional summary tailored to the target role.")
    if details.get("projects") == "Not detected":
        suggestions.append("Add 2–3 strong projects with your contribution, technologies and measurable outcomes.")
    if details.get("experience") == "Not detected":
        suggestions.append("Clearly label internship/work experience and use action-oriented bullet points.")
    if details.get("education") == "Not detected":
        suggestions.append("Add a clearly labelled Education section with degree and relevant coursework.")
    suggestions += [
        f"Tailor your headline and summary toward the {job_title} role.",
        "Move the most relevant technical skills near the top of the resume.",
        "Use measurable results where possible: %, time saved, users, accuracy, scale or performance.",
    ]
    if missing:
        suggestions.append("Add only genuine missing skills after you have learned or demonstrated them.")
    return suggestions


@app.route("/")
@login_required
def home():
    latest = prepare(latest_analysis())
    rows = get_db().execute(
        "SELECT match_score, created_at FROM analyses WHERE user_id=? ORDER BY id DESC LIMIT 8",
        (g.user["id"],)
    ).fetchall()
    total = get_db().execute(
        "SELECT COUNT(*) AS c FROM analyses WHERE user_id=?", (g.user["id"],)
    ).fetchone()["c"]
    avg = get_db().execute(
        "SELECT COALESCE(ROUND(AVG(match_score)),0) AS a FROM analyses WHERE user_id=?",
        (g.user["id"],)
    ).fetchone()["a"]
    return render_template(
        "index.html",
        latest=latest,
        total_analyses=total,
        average_score=avg,
        trend=[{"date": r["created_at"][:10], "score": r["match_score"]} for r in reversed(rows)]
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("home"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        error = None
        if not name:
            error = "Name is required."
        elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            error = "Enter a valid email."
        elif len(password) < 6:
            error = "Password must contain at least 6 characters."
        elif password != confirm:
            error = "Passwords do not match."
        if not error:
            try:
                cur = get_db().execute(
                    "INSERT INTO users(name,email,password_hash,created_at) VALUES(?,?,?,?)",
                    (name, email, generate_password_hash(password), datetime.now().isoformat(timespec="seconds"))
                )
                get_db().commit()
                session.clear()
                session["user_id"] = cur.lastrowid
                add_notification(cur.lastrowid, "Welcome to SkillGap AI", "Your career intelligence dashboard is ready.")
                flash("Account created successfully.", "success")
                return redirect(url_for("home"))
            except sqlite3.IntegrityError:
                error = "An account with this email already exists."
        flash(error, "error")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(request.args.get("next") if request.args.get("next", "").startswith("/") else url_for("home"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            flash("Please enter a valid name and email.", "error")
        else:
            try:
                get_db().execute("UPDATE users SET name=?,email=? WHERE id=?", (name, email, g.user["id"]))
                get_db().commit()
                flash("Profile updated.", "success")
            except sqlite3.IntegrityError:
                flash("That email is already in use.", "error")
        return redirect(url_for("profile"))
    return render_template("profile.html", user=get_db().execute(
        "SELECT * FROM users WHERE id=?", (g.user["id"],)
    ).fetchone())


@app.route("/change-password", methods=["POST"])
@login_required
def change_password():
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    user = get_db().execute("SELECT * FROM users WHERE id=?", (g.user["id"],)).fetchone()
    if not check_password_hash(user["password_hash"], current):
        flash("Current password is incorrect.", "error")
    elif len(new) < 6:
        flash("New password must contain at least 6 characters.", "error")
    elif new != confirm:
        flash("New passwords do not match.", "error")
    else:
        get_db().execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), g.user["id"]))
        get_db().commit()
        flash("Password changed successfully.", "success")
    return redirect(url_for("profile"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    reset_link = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_db().execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        # For this local project, show a development reset link. In production,
        # send this link through a mail provider instead of displaying it.
        if user:
            token = secrets.token_urlsafe(32)
            expires = (datetime.now() + timedelta(minutes=30)).isoformat(timespec="seconds")
            get_db().execute(
                "INSERT INTO password_resets(user_id,token,expires_at) VALUES(?,?,?)",
                (user["id"], token, expires)
            )
            get_db().commit()
            reset_link = url_for("reset_password", token=token, _external=True)
        flash("If that email exists, a password reset link has been generated.", "success")
    return render_template("forgot_password.html", reset_link=reset_link)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    row = get_db().execute(
        "SELECT * FROM password_resets WHERE token=? AND used=0", (token,)
    ).fetchone()
    if not row or datetime.fromisoformat(row["expires_at"]) < datetime.now():
        flash("This reset link is invalid or expired.", "error")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 6:
            flash("Password must contain at least 6 characters.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        else:
            get_db().execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (generate_password_hash(password), row["user_id"])
            )
            get_db().execute("UPDATE password_resets SET used=1 WHERE id=?", (row["id"],))
            get_db().commit()
            flash("Password reset successfully. Please login.", "success")
            return redirect(url_for("login"))
    return render_template("reset_password.html", token=token)


def add_notification(user_id, title, message):
    get_db().execute(
        "INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
        (user_id, title, message, datetime.now().isoformat(timespec="seconds"))
    )
    get_db().commit()


@app.route("/notifications")
@login_required
def notifications():
    rows = get_db().execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50",
        (g.user["id"],)
    ).fetchall()
    get_db().execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (g.user["id"],))
    get_db().commit()
    return render_template("notifications.html", notifications=rows)


@app.route("/analyze", methods=["GET", "POST"])
@login_required
def analyze():
    if request.method == "POST":
        resume = request.files.get("resume")
        job_title = request.form.get("job_title", "").strip()
        job_description = request.form.get("job_description", "").strip()
        if not resume or not resume.filename:
            flash("Please select a PDF or DOCX resume.", "error")
            return redirect(url_for("analyze"))
        if not allowed_file(resume.filename):
            flash("Only PDF and DOCX files are supported.", "error")
            return redirect(url_for("analyze"))
        if not job_title:
            flash("Please enter the target job title.", "error")
            return redirect(url_for("analyze"))

        original = secure_filename(resume.filename)
        stored = f"{g.user['id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}_{original}"
        path = UPLOAD_DIR / stored
        try:
            resume.save(str(path))
            text = extract_resume_text(str(path))
            details = extract_resume_details(text)
            resume_skills = extract_skills(text)
            required = extract_skills(job_description)
            keywords = extract_keywords(job_description)

            score, matching, missing, skill_score, keyword_score, completeness_score = calculate_match(
                resume_skills, required, text, job_description
            )
            roadmap = build_roadmap(missing)
            improvements = build_improvements(details, resume_skills, missing, job_title)
            recommendations = career_recommendations(resume_skills)
            questions = interview_questions(job_title, resume_skills, missing)

            cur = get_db().execute("""
                INSERT INTO analyses(
                    user_id,resume_name,job_title,job_description,match_score,
                    skill_score,keyword_score,completeness_score,resume_skills,
                    required_skills,matching_skills,missing_skills,keywords,
                    roadmap,improvements,resume_details,recommendations,
                    interview_questions,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                g.user["id"], original, job_title, job_description, score,
                skill_score, keyword_score, completeness_score,
                jdump(resume_skills), jdump(required), jdump(matching), jdump(missing),
                jdump(keywords), jdump(roadmap), jdump(improvements), jdump(details),
                jdump(recommendations), jdump(questions),
                datetime.now().isoformat(timespec="seconds")
            ))
            get_db().commit()

            add_notification(
                g.user["id"], "Resume analysis completed",
                f"{job_title}: {score}% overall match."
            )
            flash("Resume analyzed successfully.", "success")
            return redirect(url_for("result", analysis_id=cur.lastrowid))
        except Exception as exc:
            path.unlink(missing_ok=True)
            flash(str(exc), "error")
            return redirect(url_for("analyze"))
    return render_template("analyze.html")


def get_analysis(aid):
    return get_db().execute(
        "SELECT * FROM analyses WHERE id=? AND user_id=?", (aid, g.user["id"])
    ).fetchone()


@app.route("/result/<int:analysis_id>")
@login_required
def result(analysis_id):
    analysis = prepare(get_analysis(analysis_id))
    if not analysis:
        flash("Analysis not found.", "error")
        return redirect(url_for("history"))
    analysis["level"] = skill_level(analysis["match_score"])
    return render_template("result.html", analysis=analysis)


@app.route("/skill-gap")
@login_required
def skill_gap():
    a = prepare(latest_analysis())
    if not a:
        flash("Analyze a resume first.", "warning")
        return redirect(url_for("analyze"))
    a["level"] = skill_level(a["match_score"])
    return render_template("skill_gap.html", analysis=a)


@app.route("/keywords")
@login_required
def keywords():
    a = prepare(latest_analysis())
    if not a:
        flash("Analyze a resume first.", "warning")
        return redirect(url_for("analyze"))
    return render_template("keywords.html", analysis=a)


@app.route("/roadmap")
@login_required
def roadmap():
    a = prepare(latest_analysis())
    if not a:
        flash("Analyze a resume first.", "warning")
        return redirect(url_for("analyze"))
    return render_template("roadmap.html", analysis=a)


@app.route("/improvement")
@login_required
def improvement():
    a = prepare(latest_analysis())
    if not a:
        flash("Analyze a resume first.", "warning")
        return redirect(url_for("analyze"))
    return render_template("improvement.html", analysis=a)


@app.route("/resume-details")
@login_required
def resume_details():
    a = prepare(latest_analysis())
    if not a:
        flash("Analyze a resume first.", "warning")
        return redirect(url_for("analyze"))
    return render_template("resume_details.html", analysis=a)


@app.route("/recommendations")
@login_required
def recommendations():
    a = prepare(latest_analysis())
    if not a:
        flash("Analyze a resume first.", "warning")
        return redirect(url_for("analyze"))
    return render_template("recommendations.html", analysis=a)


@app.route("/interview")
@login_required
def interview():
    a = prepare(latest_analysis())
    if not a:
        flash("Analyze a resume first.", "warning")
        return redirect(url_for("analyze"))
    return render_template("interview.html", analysis=a)


@app.route("/progress")
@login_required
def progress():
    rows = get_db().execute(
        "SELECT match_score,skill_score,keyword_score,created_at,job_title "
        "FROM analyses WHERE user_id=? ORDER BY id ASC",
        (g.user["id"],)
    ).fetchall()
    data = [
        {"score": r["match_score"], "skill": r["skill_score"], "keyword": r["keyword_score"],
         "date": r["created_at"][:10], "job": r["job_title"]}
        for r in rows
    ]
    return render_template("progress.html", data=data)


@app.route("/history")
@login_required
def history():
    q = request.args.get("q", "").strip()
    min_score = request.args.get("min_score", "").strip()
    params = [g.user["id"]]
    sql = "SELECT id,resume_name,job_title,match_score,created_at FROM analyses WHERE user_id=?"
    if q:
        sql += " AND (job_title LIKE ? OR resume_name LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if min_score.isdigit():
        sql += " AND match_score >= ?"
        params.append(int(min_score))
    sql += " ORDER BY id DESC"
    rows = get_db().execute(sql, params).fetchall()
    return render_template("history.html", analyses=rows, q=q, min_score=min_score)


@app.route("/download-report/<int:analysis_id>")
@login_required
def download_report(analysis_id):
    analysis = prepare(get_analysis(analysis_id))
    if not analysis:
        flash("Analysis not found.", "error")
        return redirect(url_for("history"))

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors
    except ImportError:
        flash("PDF export needs ReportLab. Run: python -m pip install reportlab", "error")
        return redirect(url_for("result", analysis_id=analysis_id))

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("Title2", parent=styles["Title"], alignment=TA_CENTER, spaceAfter=16)
    story = [
        Paragraph("SkillGap AI — Career Analysis Report", title),
        Paragraph(f"<b>Target Role:</b> {analysis['job_title']}", styles["BodyText"]),
        Paragraph(f"<b>Resume:</b> {analysis['resume_name']}", styles["BodyText"]),
        Paragraph(f"<b>Overall Match:</b> {analysis['match_score']}%", styles["BodyText"]),
        Spacer(1, 12),
    ]
    rows = [
        ["Metric", "Score"],
        ["Skill Match", f"{analysis['skill_score']}%"],
        ["Keyword Coverage", f"{analysis['keyword_score']}%"],
        ["Resume Completeness", f"{analysis['completeness_score']}%"],
    ]
    table = Table(rows, colWidths=[250, 150])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#6d4aff")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("PADDING", (0,0), (-1,-1), 7),
    ]))
    story += [table, Spacer(1, 15)]
    story.append(Paragraph("<b>Matching Skills</b>", styles["Heading2"]))
    story.append(Paragraph(", ".join(analysis["matching_skills"]) or "None detected", styles["BodyText"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph("<b>Missing Skills</b>", styles["Heading2"]))
    story.append(Paragraph(", ".join(analysis["missing_skills"]) or "None detected", styles["BodyText"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph("<b>Resume Suggestions</b>", styles["Heading2"]))
    for item in analysis["improvements"]:
        story.append(Paragraph("• " + item, styles["BodyText"]))
    doc.build(story)
    buffer.seek(0)
    return send_file(
        buffer, as_attachment=True,
        download_name=f"SkillGap_AI_{analysis_id}.pdf",
        mimetype="application/pdf"
    )


@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    if not filename.startswith(f"{g.user['id']}_"):
        return "Not found", 404
    return send_from_directory(str(UPLOAD_DIR), filename, as_attachment=True)


@app.errorhandler(413)
def too_large(error):
    flash("File is too large. Maximum allowed size is 8 MB.", "error")
    return redirect(url_for("analyze"))


@app.errorhandler(404)
def not_found(error):
    return render_template("404.html"), 404


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)