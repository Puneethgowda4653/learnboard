import csv, io, json, os, re, secrets, sqlite3
from datetime import datetime
from functools import wraps
from flask import (Flask, g, redirect, render_template, request, session,
                   url_for, flash, abort)
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("LEARNHUB_DB", os.path.join(BASE, "learnhub.db"))
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

BOARDS = ["NCERT/CBSE", "ICSE", "IGCSE", "State Board"]
SUBJECTS = ["Mathematics", "Science", "English"]
GRADES = list(range(1, 11))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE,
 pw TEXT, role TEXT DEFAULT 'student', board TEXT, grade INTEGER, active INTEGER DEFAULT 1,
 created TEXT);
CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY, board TEXT, grade INTEGER,
 subject TEXT, topic TEXT, qtype TEXT, text TEXT, options TEXT, answer TEXT,
 explanation TEXT, marks REAL DEFAULT 1, source TEXT DEFAULT 'original', year INTEGER);
CREATE TABLE IF NOT EXISTS tests(id INTEGER PRIMARY KEY, title TEXT, board TEXT, grade INTEGER,
 subject TEXT, kind TEXT, year INTEGER, duration INTEGER DEFAULT 30, published INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS test_questions(test_id INTEGER, question_id INTEGER,
 PRIMARY KEY(test_id, question_id));
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, user_id INTEGER, test_id INTEGER,
 started TEXT, submitted TEXT, score REAL, total REAL);
CREATE TABLE IF NOT EXISTS answers(attempt_id INTEGER, question_id INTEGER, response TEXT,
 correct INTEGER, awarded REAL, PRIMARY KEY(attempt_id, question_id));
"""

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close(_):
    d = g.pop("db", None)
    if d: d.close()

def init_db():
    c = sqlite3.connect(DB); c.executescript(SCHEMA)
    if not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        pw = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(9)
        c.execute("INSERT INTO users(name,email,pw,role,created) VALUES(?,?,?,?,?)",
                  ("Administrator", "admin@learnhub.local", generate_password_hash(pw),
                   "admin", now()))
        c.commit()
        print(f"\n*** Admin created: admin@learnhub.local / {pw}  (change it!) ***\n")
    c.close()

def now(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------- auth helpers ----------
def me():
    if "uid" not in session: return None
    u = db().execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()
    if not u or not u["active"]:
        session.clear(); return None
    return u

def login_required(role=None):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            u = me()
            if not u: return redirect(url_for("login"))
            if role and u["role"] != role: abort(403)
            return f(*a, **k)
        return w
    return deco

@app.before_request
def csrf():
    if "csrf" not in session: session["csrf"] = secrets.token_hex(16)
    if request.method == "POST" and request.form.get("csrf") != session["csrf"]:
        abort(400, "Bad CSRF token")

@app.context_processor
def ctx():
    return dict(user=me(), csrf=session.get("csrf"), BOARDS=BOARDS, SUBJECTS=SUBJECTS, GRADES=GRADES)

# ---------- grading ----------
def norm(s): return re.sub(r"\s+", " ", (s or "").strip().lower().rstrip("."))

def is_correct(q, resp):
    resp = (resp or "").strip()
    if not resp: return False
    if q["qtype"] == "mcq": return resp.upper() == q["answer"].strip().upper()
    for acc in q["answer"].split("|"):
        if norm(resp) == norm(acc): return True
        try:
            if abs(float(resp) - float(acc)) < 1e-6: return True
        except ValueError: pass
    return False

# ---------- public ----------
@app.route("/")
def index():
    u = me()
    if u: return redirect(url_for("admin_home" if u["role"] == "admin" else "dashboard"))
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        if len(f["password"]) < 8:
            flash("Password must be at least 8 characters."); return render_template("register.html")
        try:
            db().execute("INSERT INTO users(name,email,pw,board,grade,created) VALUES(?,?,?,?,?,?)",
                (f["name"].strip(), f["email"].strip().lower(), generate_password_hash(f["password"]),
                 f["board"], int(f["grade"]), now()))
            db().commit()
        except sqlite3.IntegrityError:
            flash("That email is already registered."); return render_template("register.html")
        flash("Account created. Please log in."); return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = db().execute("SELECT * FROM users WHERE email=?", (request.form["email"].strip().lower(),)).fetchone()
        if u and u["active"] and check_password_hash(u["pw"], request.form["password"]):
            session.clear(); session["uid"] = u["id"]; return redirect(url_for("index"))
        flash("Invalid credentials or account disabled.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("index"))

# ---------- student ----------
@app.route("/tests")
@login_required()
def tests():
    u = me(); a = request.args
    board = a.get("board", u["board"] if u["role"] == "student" else "")
    grade = a.get("grade", u["grade"] if u["role"] == "student" else "")
    subject = a.get("subject", ""); kind = a.get("kind", "")
    sql = ("SELECT t.*, (SELECT COUNT(*) FROM test_questions WHERE test_id=t.id) n FROM tests t "
           "WHERE published=1")
    p = []
    for col, v in (("board", board), ("grade", grade), ("subject", subject), ("kind", kind)):
        if v not in ("", None): sql += f" AND t.{col}=?"; p.append(v)
    rows = db().execute(sql + " ORDER BY grade, subject, year DESC", p).fetchall()
    return render_template("tests.html", tests=rows, f=dict(board=board, grade=str(grade), subject=subject, kind=kind))

@app.route("/test/<int:tid>/start", methods=["POST"])
@login_required()
def start(tid):
    t = db().execute("SELECT * FROM tests WHERE id=? AND published=1", (tid,)).fetchone() or abort(404)
    cur = db().execute("INSERT INTO attempts(user_id,test_id,started) VALUES(?,?,?)", (me()["id"], tid, now()))
    db().commit(); return redirect(url_for("attempt", aid=cur.lastrowid))

def get_attempt(aid):
    a = db().execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone() or abort(404)
    if a["user_id"] != me()["id"] and me()["role"] != "admin": abort(403)
    return a

@app.route("/attempt/<int:aid>", methods=["GET", "POST"])
@login_required()
def attempt(aid):
    a = get_attempt(aid)
    if a["submitted"]: return redirect(url_for("report", aid=aid))
    t = db().execute("SELECT * FROM tests WHERE id=?", (a["test_id"],)).fetchone()
    qs = db().execute("SELECT q.* FROM questions q JOIN test_questions tq ON tq.question_id=q.id "
                      "WHERE tq.test_id=? ORDER BY q.id", (t["id"],)).fetchall()
    if request.method == "POST":
        score = total = 0
        for q in qs:
            resp = request.form.get(f"q{q['id']}", "")
            ok = is_correct(q, resp); aw = q["marks"] if ok else 0
            score += aw; total += q["marks"]
            db().execute("INSERT OR REPLACE INTO answers VALUES(?,?,?,?,?)", (aid, q["id"], resp, int(ok), aw))
        db().execute("UPDATE attempts SET submitted=?, score=?, total=? WHERE id=?", (now(), score, total, aid))
        db().commit(); return redirect(url_for("report", aid=aid))
    return render_template("attempt.html", t=t, qs=qs, a=a, opts={q["id"]: json.loads(q["options"] or "[]") for q in qs})

@app.route("/attempt/<int:aid>/report")
@login_required()
def report(aid):
    a = get_attempt(aid)
    if not a["submitted"]: return redirect(url_for("attempt", aid=aid))
    t = db().execute("SELECT * FROM tests WHERE id=?", (a["test_id"],)).fetchone()
    rows = db().execute("SELECT q.*, an.response, an.correct, an.awarded FROM questions q "
        "JOIN test_questions tq ON tq.question_id=q.id LEFT JOIN answers an ON an.question_id=q.id AND an.attempt_id=? "
        "WHERE tq.test_id=? ORDER BY q.id", (aid, t["id"])).fetchall()
    topics = {}
    for r in rows:
        d = topics.setdefault(r["topic"] or "General", [0, 0, 0, 0])
        d[0] += r["awarded"] or 0; d[1] += r["marks"]; d[2] += 1 if r["correct"] else 0; d[3] += 1
    tl = sorted(({"topic": k, "got": v[0], "of": v[1], "pct": round(100 * v[0] / v[1]) if v[1] else 0,
                  "right": v[2], "n": v[3]} for k, v in topics.items()), key=lambda x: x["pct"])
    opts = {r["id"]: json.loads(r["options"] or "[]") for r in rows}
    pct = round(100 * a["score"] / a["total"]) if a["total"] else 0
    return render_template("report.html", a=a, t=t, rows=rows, topics=tl, opts=opts, pct=pct,
                           weak=[x for x in tl if x["pct"] < 60])

@app.route("/dashboard")
@login_required()
def dashboard():
    u = me()
    if u["role"] == "admin": return redirect(url_for("admin_home"))
    at = db().execute("SELECT a.*, t.title, t.subject FROM attempts a JOIN tests t ON t.id=a.test_id "
        "WHERE a.user_id=? AND a.submitted IS NOT NULL ORDER BY a.submitted", (u["id"],)).fetchall()
    hist = [dict(id=x["id"], title=x["title"], subject=x["subject"], when=x["submitted"],
                 pct=round(100 * x["score"] / x["total"]) if x["total"] else 0, score=x["score"], total=x["total"]) for x in at]
    subj = {}
    for h in hist: subj.setdefault(h["subject"], []).append(h["pct"])
    topic = db().execute("SELECT q.subject, q.topic, SUM(an.awarded) got, SUM(q.marks) tot FROM answers an "
        "JOIN attempts a ON a.id=an.attempt_id JOIN questions q ON q.id=an.question_id "
        "WHERE a.user_id=? AND a.submitted IS NOT NULL GROUP BY q.subject,q.topic", (u["id"],)).fetchall()
    tm = sorted(({"subject": r["subject"], "topic": r["topic"] or "General",
                  "pct": round(100 * r["got"] / r["tot"])} for r in topic if r["tot"]), key=lambda x: x["pct"])
    return render_template("dashboard.html", hist=hist, subj={k: round(sum(v) / len(v)) for k, v in subj.items()},
                           topics=tm, best=max([h["pct"] for h in hist], default=0),
                           avg=round(sum(h["pct"] for h in hist) / len(hist)) if hist else 0)

@app.route("/syllabus")
@login_required()
def syllabus():
    rows = db().execute("SELECT board,grade,subject,topic,COUNT(*) n FROM questions "
                        "GROUP BY board,grade,subject,topic ORDER BY board,grade,subject,topic").fetchall()
    return render_template("syllabus.html", rows=rows)

@app.route("/practice")
@login_required()
def practice():
    u = me(); a = request.args
    sql = "SELECT * FROM questions WHERE 1=1"; p = []
    for col in ("board", "grade", "subject", "topic"):
        v = a.get(col, u[col] if col in ("board", "grade") and u["role"] == "student" else "")
        if v: sql += f" AND {col}=?"; p.append(v)
    qs = db().execute(sql + " ORDER BY RANDOM() LIMIT 10", p).fetchall()
    return render_template("practice.html", qs=qs, opts={q["id"]: json.loads(q["options"] or "[]") for q in qs})

# ---------- admin ----------
@app.route("/admin")
@login_required("admin")
def admin_home():
    c = lambda s: db().execute(s).fetchone()[0]
    stats = dict(students=c("SELECT COUNT(*) FROM users WHERE role='student'"),
                 questions=c("SELECT COUNT(*) FROM questions"), tests=c("SELECT COUNT(*) FROM tests"),
                 attempts=c("SELECT COUNT(*) FROM attempts WHERE submitted IS NOT NULL"))
    recent = db().execute("SELECT u.name, t.title, a.score, a.total, a.submitted FROM attempts a JOIN users u ON u.id=a.user_id "
        "JOIN tests t ON t.id=a.test_id WHERE a.submitted IS NOT NULL ORDER BY a.submitted DESC LIMIT 10").fetchall()
    return render_template("admin_home.html", stats=stats, recent=recent)

@app.route("/admin/users", methods=["GET", "POST"])
@login_required("admin")
def admin_users():
    if request.method == "POST":
        f = request.form; act = f["action"]
        if act == "create":
            try:
                db().execute("INSERT INTO users(name,email,pw,role,board,grade,created) VALUES(?,?,?,?,?,?,?)",
                    (f["name"], f["email"].lower(), generate_password_hash(f["password"]), f["role"], f["board"], int(f["grade"]), now()))
            except sqlite3.IntegrityError: flash("Email exists.")
        elif act == "toggle" and int(f["id"]) != me()["id"]:
            db().execute("UPDATE users SET active=1-active WHERE id=?", (f["id"],))
        elif act == "reset":
            db().execute("UPDATE users SET pw=? WHERE id=?", (generate_password_hash(f["password"]), f["id"]))
        elif act == "delete" and int(f["id"]) != me()["id"]:
            for t in ("answers", ):
                db().execute("DELETE FROM answers WHERE attempt_id IN (SELECT id FROM attempts WHERE user_id=?)", (f["id"],))
            db().execute("DELETE FROM attempts WHERE user_id=?", (f["id"],))
            db().execute("DELETE FROM users WHERE id=?", (f["id"],))
        db().commit(); return redirect(url_for("admin_users"))
    users = db().execute("SELECT u.*, (SELECT COUNT(*) FROM attempts WHERE user_id=u.id AND submitted IS NOT NULL) n "
                         "FROM users u ORDER BY role, name").fetchall()
    return render_template("admin_users.html", users=users)

@app.route("/admin/questions", methods=["GET", "POST"])
@login_required("admin")
def admin_questions():
    if request.method == "POST":
        f = request.form
        if f["action"] == "add":
            opts = [o.strip() for o in f.get("options", "").split("\n") if o.strip()]
            db().execute("INSERT INTO questions(board,grade,subject,topic,qtype,text,options,answer,explanation,marks,source,year) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (f["board"], int(f["grade"]), f["subject"], f["topic"], f["qtype"], f["text"],
                json.dumps(opts), f["answer"], f["explanation"], float(f["marks"] or 1), f["source"] or "admin", f["year"] or None))
        elif f["action"] == "csv":
            n = import_csv(f["csv"].encode() if f["csv"] else request.files["file"].read())
            flash(f"Imported {n} questions.")
        elif f["action"] == "delete":
            db().execute("DELETE FROM test_questions WHERE question_id=?", (f["id"],))
            db().execute("DELETE FROM questions WHERE id=?", (f["id"],))
        db().commit(); return redirect(url_for("admin_questions", **{k: v for k, v in request.args.items()}))
    a = request.args; sql = "SELECT * FROM questions WHERE 1=1"; p = []
    for col in ("board", "grade", "subject"):
        if a.get(col): sql += f" AND {col}=?"; p.append(a[col])
    qs = db().execute(sql + " ORDER BY id DESC LIMIT 200", p).fetchall()
    return render_template("admin_questions.html", qs=qs, f=a)

def import_csv(data):
    """Columns: board,grade,subject,topic,qtype,text,options(| separated),answer,explanation,marks,source,year"""
    n = 0
    for r in csv.DictReader(io.StringIO(data.decode("utf-8-sig"))):
        opts = [o.strip() for o in (r.get("options") or "").split("|") if o.strip()]
        db().execute("INSERT INTO questions(board,grade,subject,topic,qtype,text,options,answer,explanation,marks,source,year) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (r["board"], int(r["grade"]), r["subject"], r.get("topic", ""),
            r.get("qtype") or ("mcq" if opts else "short"), r["text"], json.dumps(opts), r["answer"], r.get("explanation", ""),
            float(r.get("marks") or 1), r.get("source") or "csv-import", r.get("year") or None)); n += 1
    return n

@app.route("/admin/tests", methods=["GET", "POST"])
@login_required("admin")
def admin_tests():
    if request.method == "POST":
        f = request.form
        if f["action"] == "create":
            cur = db().execute("INSERT INTO tests(title,board,grade,subject,kind,year,duration) VALUES(?,?,?,?,?,?,?)",
                (f["title"], f["board"], int(f["grade"]), f["subject"], f["kind"], f["year"] or None, int(f["duration"] or 30)))
            ids = db().execute("SELECT id FROM questions WHERE board=? AND grade=? AND subject=? ORDER BY RANDOM() LIMIT ?",
                (f["board"], f["grade"], f["subject"], int(f["count"] or 10))).fetchall() if f.get("auto") else []
            for i in ids: db().execute("INSERT INTO test_questions VALUES(?,?)", (cur.lastrowid, i["id"]))
            for i in re.findall(r"\d+", f.get("qids", "")):
                db().execute("INSERT OR IGNORE INTO test_questions VALUES(?,?)", (cur.lastrowid, int(i)))
        elif f["action"] == "toggle": db().execute("UPDATE tests SET published=1-published WHERE id=?", (f["id"],))
        elif f["action"] == "delete":
            db().execute("DELETE FROM test_questions WHERE test_id=?", (f["id"],))
            db().execute("DELETE FROM tests WHERE id=?", (f["id"],))
        db().commit(); return redirect(url_for("admin_tests"))
    rows = db().execute("SELECT t.*, (SELECT COUNT(*) FROM test_questions WHERE test_id=t.id) n FROM tests t ORDER BY id DESC").fetchall()
    return render_template("admin_tests.html", tests=rows)

init_db()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
