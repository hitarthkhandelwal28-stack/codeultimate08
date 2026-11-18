from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import zipfile

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from ai_engine import ask_codeai, generate_project, plan_automation
from project_manager import (
    safe_write_project_files, list_project_files, read_project_file,
    read_all_project_files, validate_relative_path, make_project_zip
)

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "database"
UPLOAD_DIR = BASE_DIR / "uploads"
WORKSPACE_DIR = BASE_DIR / "workspaces"
for folder in (DB_DIR, UPLOAD_DIR, WORKSPACE_DIR):
    folder.mkdir(parents=True, exist_ok=True)

try:
    MAX_UPLOAD_MB = max(1, min(int(os.getenv("MAX_UPLOAD_MB", "5")), 20))
except ValueError:
    MAX_UPLOAD_MB = 5

app = Flask(__name__, static_folder=str(BASE_DIR / "static"), static_url_path="/static")
app.config.update(
    MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY") or os.urandom(32).hex(),
    JSON_SORT_KEYS=False,
)
DB_PATH = Path(os.getenv("DATABASE_PATH", str(DB_DIR / "codeai.db")))

ALLOWED_UPLOADS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json",
    ".md", ".txt", ".java", ".cpp", ".c", ".h", ".cs", ".php",
    ".go", ".rs", ".sql", ".yaml", ".yml", ".toml", ".xml", ".sh"
}
MAX_TEXT_CHARS = 12000
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+$")


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS contact_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rate_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rate_limits_bucket_time
            ON rate_limits(bucket, created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id, id);
        CREATE TABLE IF NOT EXISTS automation_modes (
            chat_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL CHECK(mode IN ('approval', 'automatic')),
            FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS automation_tasks (
            id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            task TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending', 'applied', 'rejected')),
            created_at TEXT NOT NULL,
            FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_automation_tasks_chat
            ON automation_tasks(chat_id, created_at);
        """)


def now():
    return datetime.now(timezone.utc).isoformat()


def json_error(message, status):
    return jsonify({"error": message}), status


def request_data():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def same_origin_or_no_origin():
    origin = request.headers.get("Origin")
    if not origin:
        return True
    try:
        return urlsplit(origin).netloc.lower() == request.host.lower()
    except ValueError:
        return False


def allow_request(scope, limit, window_seconds):
    # Hash the address so raw IP addresses are not stored in the rate-limit table.
    address = request.remote_addr or "unknown"
    bucket = hashlib.sha256(
        (app.config["SECRET_KEY"] + "|" + scope + "|" + address).encode("utf-8")
    ).hexdigest()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat()
    with db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM rate_limits WHERE bucket=? AND created_at>=?",
            (bucket, cutoff)
        ).fetchone()[0]
        if count >= limit:
            return False
        conn.execute(
            "INSERT INTO rate_limits(bucket, created_at) VALUES(?, ?)",
            (bucket, now())
        )
        # Keep the small limiter table bounded.
        conn.execute("DELETE FROM rate_limits WHERE created_at < ?", (
            (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        ))
    return True


def chat_exists(chat_id):
    if not re.fullmatch(r"[a-f0-9]{32}", str(chat_id or "")):
        return False
    with db() as conn:
        return conn.execute(
            "SELECT 1 FROM chats WHERE id=?", (chat_id,)
        ).fetchone() is not None


def get_chat_upload_context(chat_id):
    folder = UPLOAD_DIR / chat_id
    if not folder.exists():
        return ""
    chunks = []
    with db() as conn:
        rows = conn.execute(
            "SELECT stored_name FROM uploads WHERE chat_id=? ORDER BY id",
            (chat_id,)
        ).fetchall()
    for row in rows:
        path = folder / row["stored_name"]
        if path.is_file() and path.suffix.lower().split("_")[-1] in ALLOWED_UPLOADS:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                chunks.append("\n--- Uploaded file ---\n" + content[:MAX_TEXT_CHARS])
            except OSError:
                logger.warning("Could not read an uploaded context file")
    return "\n".join(chunks)[:30000]


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/portfolio")
def portfolio():
    return render_template("portfolio.html")


@app.get("/healthz")
def health():
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
        return jsonify({"status": "ok", "database": "ok"}), 200
    except sqlite3.Error:
        logger.exception("Health check database failure")
        return jsonify({"status": "degraded", "database": "unavailable"}), 503


@app.post("/api/contact")
def submit_contact():
    if not same_origin_or_no_origin():
        return json_error("Cross-origin request rejected", 403)
    if not allow_request("contact", 5, 3600):
        return json_error("Too many messages. Please try again later.", 429)

    data = request_data()
    if data is None:
        return json_error("Send the form as JSON", 400)

    # Honeypot: silently accept bots without storing their submissions.
    if str(data.get("website", "")).strip():
        return jsonify({"message": "Thank you. Your message was received."}), 202

    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    message = str(data.get("message", "")).strip()

    if not name or len(name) > 80:
        return json_error("Name is required and must be 80 characters or fewer", 400)
    if not EMAIL_RE.fullmatch(email) or len(email) > 254:
        return json_error("Enter a valid email address", 400)
    if not message or len(message) > 3000:
        return json_error("Message is required and must be 3,000 characters or fewer", 400)

    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO contact_messages(name,email,message,created_at) VALUES(?,?,?,?)",
                (name, email, message, now())
            )
    except sqlite3.Error:
        logger.exception("Contact form database write failed")
        return json_error("We could not save your message. Please try again later.", 503)

    return jsonify({
        "message": "Your message was received successfully. Thank you for contacting me."
    }), 201


@app.get("/api/chats")
def get_chats():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM chats ORDER BY updated_at DESC"
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/chats")
def create_chat():
    if not allow_request("create-chat", 30, 60):
        return json_error("Too many requests. Please wait a moment.", 429)
    chat_id = uuid4().hex
    timestamp = now()
    with db() as conn:
        conn.execute(
            "INSERT INTO chats(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (chat_id, "New chat", timestamp, timestamp)
        )
    return jsonify({"id": chat_id, "title": "New chat"}), 201


@app.get("/api/chats/<chat_id>/messages")
def get_messages(chat_id):
    if not chat_exists(chat_id):
        return json_error("Chat not found", 404)
    with db() as conn:
        rows = conn.execute(
            "SELECT role,content,created_at FROM messages WHERE chat_id=? ORDER BY id",
            (chat_id,)
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/chats/<chat_id>/upload")
def upload_file(chat_id):
    if not chat_exists(chat_id):
        return json_error("Chat not found", 404)
    if not allow_request("upload", 12, 3600):
        return json_error("Upload limit reached. Please try again later.", 429)

    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return json_error("Choose a file first", 400)

    original = secure_filename(uploaded.filename)
    suffix = Path(original).suffix.lower()
    if not original or suffix not in ALLOWED_UPLOADS:
        return json_error("Unsupported file type", 400)

    stored = uuid4().hex + "_" + original
    folder = UPLOAD_DIR / chat_id
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / stored
    try:
        uploaded.save(destination)
        with db() as conn:
            conn.execute(
                "INSERT INTO uploads(chat_id,filename,stored_name,created_at) VALUES(?,?,?,?)",
                (chat_id, original, stored, now())
            )
    except (OSError, sqlite3.Error):
        destination.unlink(missing_ok=True)
        logger.exception("File upload failed")
        return json_error("The file could not be saved", 503)

    return jsonify({"filename": original, "message": "File uploaded"}), 201


@app.post("/api/chat")
def chat():
    if not same_origin_or_no_origin():
        return json_error("Cross-origin request rejected", 403)
    if not allow_request("chat", 20, 60):
        return json_error("Too many AI requests. Please wait a moment.", 429)
    data = request_data()
    if data is None:
        return json_error("Send the request as JSON", 400)

    chat_id = str(data.get("chat_id", ""))
    message = str(data.get("message", "")).strip()
    if not chat_exists(chat_id):
        return json_error("Create or select a chat first", 404)
    if not message:
        return json_error("Message cannot be empty", 400)
    if len(message) > 12000:
        return json_error("Message is too long (12,000 characters maximum)", 400)

    timestamp = now()
    with db() as conn:
        conn.execute(
            "INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)",
            (chat_id, "user", message, timestamp)
        )
        previous = conn.execute(
            "SELECT role,content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 16",
            (chat_id,)
        ).fetchall()
        conn.execute(
            "UPDATE chats SET updated_at=?, title=CASE WHEN title='New chat' THEN ? ELSE title END WHERE id=?",
            (timestamp, message[:48], chat_id)
        )

    history = [{"role": row["role"], "content": row["content"]} for row in reversed(previous)]
    try:
        answer = ask_codeai(history, get_chat_upload_context(chat_id))
    except Exception:
        logger.exception("AI provider request failed")
        answer = "The AI service is temporarily unavailable. Please try again later."
        with db() as conn:
            conn.execute(
                "INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)",
                (chat_id, "assistant", answer, now())
            )
        return jsonify({"reply": answer}), 502

    with db() as conn:
        conn.execute(
            "INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)",
            (chat_id, "assistant", answer, now())
        )
    return jsonify({"reply": answer})


@app.post("/api/projects/generate")
def create_project():
    if not same_origin_or_no_origin():
        return json_error("Cross-origin request rejected", 403)
    if not allow_request("project-generation", 3, 3600):
        return json_error("Project generation limit reached. Try again later.", 429)
    data = request_data()
    if data is None:
        return json_error("Send the request as JSON", 400)

    chat_id = str(data.get("chat_id", ""))
    prompt = str(data.get("prompt", "")).strip()
    if not chat_exists(chat_id):
        return json_error("Chat not found", 404)
    if not prompt:
        return json_error("Describe the project first", 400)
    if len(prompt) > 8000:
        return json_error("Project description is too long", 400)

    try:
        project = generate_project(prompt, get_chat_upload_context(chat_id))
        files_written = safe_write_project_files(WORKSPACE_DIR, chat_id, project["files"])
        with db() as conn:
            conn.execute(
                "INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)",
                (chat_id, "assistant",
                 "Generated project: " + str(project.get("project_name", "project")) +
                 " (" + str(len(files_written)) + " files). Review the files panel and download the ZIP.",
                 now())
            )
        return jsonify({
            "project_name": project.get("project_name", "project"),
            "description": project.get("description", ""),
            "files": files_written
        })
    except Exception:
        logger.exception("Project generation failed")
        return json_error("Project generation failed. Check the server logs and try again.", 502)


@app.get("/api/chats/<chat_id>/files")
def project_files(chat_id):
    if not chat_exists(chat_id):
        return json_error("Chat not found", 404)
    return jsonify(list_project_files(WORKSPACE_DIR, chat_id))


@app.get("/api/chats/<chat_id>/files/<path:relative_path>")
def project_file_content(chat_id, relative_path):
    if not chat_exists(chat_id):
        return json_error("Chat not found", 404)
    try:
        content = read_project_file(WORKSPACE_DIR, chat_id, relative_path)
        return jsonify({"path": relative_path, "content": content})
    except (ValueError, FileNotFoundError):
        return json_error("File not found", 404)


@app.get("/api/chats/<chat_id>/download")
def download_project(chat_id):
    if not chat_exists(chat_id):
        return json_error("Chat not found", 404)
    archive = make_project_zip(WORKSPACE_DIR, chat_id)
    if archive is None:
        return json_error("No generated project files yet", 404)
    return send_file(
        io.BytesIO(archive),
        mimetype="application/zip",
        as_attachment=True,
        download_name="codeai_project.zip"
    )


@app.get("/api/automation/mode")
def get_automation_mode():
    chat_id = str(request.args.get("chat_id", ""))
    if not chat_exists(chat_id):
        return json_error("Chat not found", 404)
    with db() as conn:
        row = conn.execute(
            "SELECT mode FROM automation_modes WHERE chat_id=?", (chat_id,)
        ).fetchone()
    return jsonify({"mode": row["mode"] if row else "approval"})


@app.post("/api/automation/mode")
def set_automation_mode():
    if not same_origin_or_no_origin():
        return json_error("Cross-origin request rejected", 403)
    data = request_data()
    if data is None:
        return json_error("Send the request as JSON", 400)
    chat_id = str(data.get("chat_id", ""))
    mode = str(data.get("mode", ""))
    if not chat_exists(chat_id):
        return json_error("Chat not found", 404)
    if mode not in ("approval", "automatic"):
        return json_error("Mode must be approval or automatic", 400)
    with db() as conn:
        conn.execute(
            "INSERT INTO automation_modes(chat_id, mode) VALUES(?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET mode=excluded.mode",
            (chat_id, mode)
        )
    return jsonify({"mode": mode})


@app.post("/api/automation/plan")
def create_automation_plan():
    if not same_origin_or_no_origin():
        return json_error("Cross-origin request rejected", 403)
    if not allow_request("automation-plan", 5, 60):
        return json_error("Automation request limit reached. Please wait.", 429)
    data = request_data()
    if data is None:
        return json_error("Send the request as JSON", 400)
    chat_id = str(data.get("chat_id", ""))
    task = str(data.get("task", "")).strip()
    if not chat_exists(chat_id):
        return json_error("Chat not found", 404)
    if not task or len(task) > 5000:
        return json_error("Task must be between 1 and 5,000 characters", 400)

    with db() as conn:
        row = conn.execute(
            "SELECT mode FROM automation_modes WHERE chat_id=?", (chat_id,)
        ).fetchone()
    mode = row["mode"] if row else "approval"

    try:
        existing_files = read_all_project_files(WORKSPACE_DIR, chat_id)
        plan = plan_automation(task, existing_files)
        if not isinstance(plan, dict) or not isinstance(plan.get("actions"), list):
            return json_error("The AI returned an invalid automation plan", 502)
        actions = plan["actions"]
        if not actions or len(actions) > 10:
            return json_error("Automation plans must contain 1 to 10 file actions", 400)

        normalized = []
        seen = set()
        for action in actions:
            if not isinstance(action, dict) or action.get("type") != "write_file":
                return json_error("Only project file creation or editing is supported", 400)
            relative = validate_relative_path(action.get("path")).as_posix()
            content = action.get("content")
            if not isinstance(content, str) or len(content) > 40000:
                return json_error("Each planned file must be text under 40,000 characters", 400)
            if relative in seen:
                return json_error("The plan contains duplicate file paths", 400)
            seen.add(relative)
            normalized.append({
                "path": relative,
                "content": content,
                "reason": str(action.get("reason", ""))[:300]
            })

        if mode == "automatic":
            existing_paths = set(existing_files)
            collisions = [item["path"] for item in normalized if item["path"] in existing_paths]
            if collisions:
                return jsonify({
                    "error": "Automatic mode only creates new files. Existing files need approval.",
                    "requires_approval": True,
                    "conflicts": collisions
                }), 409
            written = safe_write_project_files(WORKSPACE_DIR, chat_id, normalized)
            task_id = uuid4().hex
            with db() as conn:
                conn.execute(
                    "INSERT INTO automation_tasks(id,chat_id,task,plan_json,status,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (task_id, chat_id, task, json.dumps(normalized), "applied", now())
                )
            return jsonify({
                "mode": mode,
                "applied": True,
                "task_id": task_id,
                "summary": str(plan.get("summary", "Automation completed"))[:500],
                "files": written
            }), 200

        task_id = uuid4().hex
        with db() as conn:
            conn.execute(
                "INSERT INTO automation_tasks(id,chat_id,task,plan_json,status,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (task_id, chat_id, task, json.dumps(normalized),
                 "pending", now())
            )
        return jsonify({
            "mode": mode,
            "applied": False,
            "task_id": task_id,
            "summary": str(plan.get("summary", "Review the proposed changes"))[:500],
            "actions": [
                {
                    "path": item["path"],
                    "reason": item["reason"],
                    "preview": item["content"][:2500],
                    "truncated": len(item["content"]) > 2500
                }
                for item in normalized
            ]
        }), 200
    except (ValueError, OSError) as exc:
        return json_error(str(exc), 400)
    except Exception:
        logger.exception("Automation planning failed")
        return json_error("Automation planning failed. Check server logs and try again.", 502)


@app.post("/api/automation/tasks/<task_id>/approve")
def approve_automation_task(task_id):
    if not same_origin_or_no_origin():
        return json_error("Cross-origin request rejected", 403)
    data = request_data()
    if data is None:
        return json_error("Send the request as JSON", 400)
    chat_id = str(data.get("chat_id", ""))
    if not chat_exists(chat_id):
        return json_error("Chat not found", 404)
    with db() as conn:
        row = conn.execute(
            "SELECT plan_json,status FROM automation_tasks WHERE id=? AND chat_id=?",
            (task_id, chat_id)
        ).fetchone()
        if not row or row["status"] != "pending":
            return json_error("Pending automation task not found", 404)
        actions = json.loads(row["plan_json"])
        try:
            written = safe_write_project_files(WORKSPACE_DIR, chat_id, actions)
        except (ValueError, OSError) as exc:
            return json_error(str(exc), 400)
        conn.execute(
            "UPDATE automation_tasks SET status='applied' WHERE id=? AND chat_id=?",
            (task_id, chat_id)
        )
    return jsonify({"applied": True, "files": written})


@app.post("/api/automation/tasks/<task_id>/reject")
def reject_automation_task(task_id):
    if not same_origin_or_no_origin():
        return json_error("Cross-origin request rejected", 403)
    data = request_data()
    if data is None:
        return json_error("Send the request as JSON", 400)
    chat_id = str(data.get("chat_id", ""))
    if not chat_exists(chat_id):
        return json_error("Chat not found", 404)
    with db() as conn:
        cursor = conn.execute(
            "UPDATE automation_tasks SET status='rejected' "
            "WHERE id=? AND chat_id=? AND status='pending'",
            (task_id, chat_id)
        )
        if cursor.rowcount != 1:
            return json_error("Pending automation task not found", 404)
    return jsonify({"rejected": True})


@app.errorhandler(413)
def too_large(_error):
    return json_error("Upload too large. Check MAX_UPLOAD_MB in the service environment.", 413)


@app.errorhandler(500)
def internal_error(_error):
    logger.exception("Unhandled server error")
    return json_error("An unexpected server error occurred. Please try again later.", 500)


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
