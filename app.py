from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
import hashlib
import io
import logging
import os
import re
import sqlite3
import zipfile

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from ai_engine import ask_codeai, generate_project
from project_manager import (
    safe_write_project_files, list_project_files, read_project_file,
    make_project_zip
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
