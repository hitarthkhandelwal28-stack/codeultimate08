from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
import os
import sqlite3
import zipfile
import io

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

from ai_engine import ask_codeai, generate_project
from project_manager import (
    safe_write_project_files, list_project_files, read_project_file,
    make_project_zip, validate_relative_path
)

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "database"
UPLOAD_DIR = BASE_DIR / "uploads"
WORKSPACE_DIR = BASE_DIR / "workspaces"
for folder in (DB_DIR, UPLOAD_DIR, WORKSPACE_DIR):
    folder.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "5")) * 1024 * 1024
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")
DB_PATH = DB_DIR / "codeai.db"

ALLOWED_UPLOADS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json",
    ".md", ".txt", ".java", ".cpp", ".c", ".h", ".cs", ".php",
    ".go", ".rs", ".sql", ".yaml", ".yml", ".toml", ".xml", ".sh"
}
MAX_TEXT_CHARS = 12000


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(chat_id) REFERENCES chats(id)
        );
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(chat_id) REFERENCES chats(id)
        );
        """)


def now():
    return datetime.now(timezone.utc).isoformat()


def chat_exists(chat_id):
    with db() as conn:
        return conn.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone() is not None


def get_chat_upload_context(chat_id):
    folder = UPLOAD_DIR / chat_id
    if not folder.exists():
        return ""
    chunks = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in ALLOWED_UPLOADS:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                chunks.append(f"\n--- Uploaded file: {path.name} ---\n{content[:MAX_TEXT_CHARS]}")
            except OSError:
                continue
    return "\n".join(chunks)[:30000]


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/chats")
def get_chats():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM chats ORDER BY updated_at DESC"
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/chats")
def create_chat():
    chat_id = uuid4().hex
    timestamp = now()
    title = "New chat"
    with db() as conn:
        conn.execute(
            "INSERT INTO chats(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            (chat_id, title, timestamp, timestamp)
        )
    return jsonify({"id": chat_id, "title": title}), 201


@app.get("/api/chats/<chat_id>/messages")
def get_messages(chat_id):
    if not chat_exists(chat_id):
        return jsonify({"error": "Chat not found"}), 404
    with db() as conn:
        rows = conn.execute(
            "SELECT role,content,created_at FROM messages WHERE chat_id=? ORDER BY id",
            (chat_id,)
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/chats/<chat_id>/upload")
def upload_file(chat_id):
    if not chat_exists(chat_id):
        return jsonify({"error": "Chat not found"}), 404
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "Choose a file first"}), 400

    original = secure_filename(uploaded.filename)
    suffix = Path(original).suffix.lower()
    if not original or suffix not in ALLOWED_UPLOADS:
        return jsonify({"error": "Unsupported file type"}), 400

    stored = f"{uuid4().hex}_{original}"
    folder = UPLOAD_DIR / chat_id
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / stored
    uploaded.save(destination)

    with db() as conn:
        conn.execute(
            "INSERT INTO uploads(chat_id,filename,stored_name,created_at) VALUES(?,?,?,?)",
            (chat_id, original, stored, now())
        )
    return jsonify({"filename": original, "message": "File uploaded"}), 201


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    chat_id = str(data.get("chat_id", ""))
    message = str(data.get("message", "")).strip()
    if not chat_exists(chat_id):
        return jsonify({"error": "Create or select a chat first"}), 404
    if not message:
        return jsonify({"error": "Message cannot be empty"}), 400
    if len(message) > 12000:
        return jsonify({"error": "Message is too long (12,000 characters maximum)"}), 400

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
    upload_context = get_chat_upload_context(chat_id)

    try:
        answer = ask_codeai(history, upload_context)
    except Exception as exc:
        answer = f"AI request failed: {str(exc)[:300]}"
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
    data = request.get_json(silent=True) or {}
    chat_id = str(data.get("chat_id", ""))
    prompt = str(data.get("prompt", "")).strip()
    if not chat_exists(chat_id):
        return jsonify({"error": "Chat not found"}), 404
    if not prompt:
        return jsonify({"error": "Describe the project first"}), 400
    if len(prompt) > 8000:
        return jsonify({"error": "Project description is too long"}), 400

    try:
        project = generate_project(prompt, get_chat_upload_context(chat_id))
        files_written = safe_write_project_files(WORKSPACE_DIR, chat_id, project["files"])
        with db() as conn:
            conn.execute(
                "INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)",
                (chat_id, "assistant",
                 f"Generated project: {project.get('project_name', 'project')} "
                 f"({len(files_written)} files). Review the files panel and download the ZIP.",
                 now())
            )
        return jsonify({
            "project_name": project.get("project_name", "project"),
            "description": project.get("description", ""),
            "files": files_written
        })
    except Exception as exc:
        return jsonify({"error": f"Project generation failed: {str(exc)[:300]}"}), 502


@app.get("/api/chats/<chat_id>/files")
def project_files(chat_id):
    if not chat_exists(chat_id):
        return jsonify({"error": "Chat not found"}), 404
    return jsonify(list_project_files(WORKSPACE_DIR, chat_id))


@app.get("/api/chats/<chat_id>/files/<path:relative_path>")
def project_file_content(chat_id, relative_path):
    if not chat_exists(chat_id):
        return jsonify({"error": "Chat not found"}), 404
    try:
        content = read_project_file(WORKSPACE_DIR, chat_id, relative_path)
        return jsonify({"path": relative_path, "content": content})
    except (ValueError, FileNotFoundError):
        return jsonify({"error": "File not found"}), 404


@app.get("/api/chats/<chat_id>/download")
def download_project(chat_id):
    if not chat_exists(chat_id):
        return jsonify({"error": "Chat not found"}), 404
    archive = make_project_zip(WORKSPACE_DIR, chat_id)
    if archive is None:
        return jsonify({"error": "No generated project files yet"}), 404
    return send_file(
        io.BytesIO(archive),
        mimetype="application/zip",
        as_attachment=True,
        download_name="codeai_project.zip"
    )


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "Upload too large. Check MAX_UPLOAD_MB in .env"}), 413


init_db()

if __name__ == "__main__":
    # Local development only. Do not expose Flask's debug server to the public internet.
    app.run(host="127.0.0.1", port=5000, debug=False)
