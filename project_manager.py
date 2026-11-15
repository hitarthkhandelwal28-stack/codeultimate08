from pathlib import Path, PurePosixPath
import io
import zipfile


def validate_relative_path(relative_path):
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("Empty file path")
    normalized = relative_path.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in ("..", ".", "") for part in path.parts):
        raise ValueError("Unsafe file path")
    if len(path.parts) > 8 or len(normalized) > 180:
        raise ValueError("File path is too long or deeply nested")
    if any(part.startswith(".") for part in path.parts):
        raise ValueError("Hidden paths are not allowed")
    return path


def _project_root(base_dir, chat_id):
    if not chat_id.isalnum():
        raise ValueError("Invalid project identifier")
    root = (Path(base_dir) / chat_id).resolve()
    base = Path(base_dir).resolve()
    if base not in root.parents:
        raise ValueError("Invalid project directory")
    return root


def safe_write_project_files(base_dir, chat_id, files):
    root = _project_root(base_dir, chat_id)
    root.mkdir(parents=True, exist_ok=True)
    written = []
    seen = set()

    for item in files:
        if not isinstance(item, dict):
            continue
        rel = validate_relative_path(item.get("path"))
        content = item.get("content")
        if not isinstance(content, str):
            raise ValueError("File content must be text")
        if len(content) > 40000:
            raise ValueError("A generated file exceeds the 40,000 character limit")
        rel_string = rel.as_posix()
        if rel_string in seen:
            raise ValueError("Duplicate file path: " + rel_string)
        seen.add(rel_string)

        target = (root / Path(*rel.parts)).resolve()
        if root != target and root not in target.parents:
            raise ValueError("File path escapes project workspace")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel_string)

    return written


def read_all_project_files(base_dir, chat_id, max_chars_per_file=12000, max_total_chars=60000):
    """Return {relative_path: content} for every text file in the workspace.
    Content is truncated per-file and overall so it can be used as AI context."""
    root = _project_root(base_dir, chat_id)
    result = {}
    if not root.exists():
        return result
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        content = content[:max_chars_per_file]
        if total + len(content) > max_total_chars:
            break
        total += len(content)
        result[path.relative_to(root).as_posix()] = content
    return result


def list_project_files(base_dir, chat_id):
    root = _project_root(base_dir, chat_id)
    if not root.exists():
        return []
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result.append(path.relative_to(root).as_posix())
    return result


def read_project_file(base_dir, chat_id, relative_path):
    rel = validate_relative_path(relative_path)
    root = _project_root(base_dir, chat_id)
    target = (root / Path(*rel.parts)).resolve()
    if root != target and root not in target.parents:
        raise ValueError("Invalid path")
    if not target.is_file():
        raise FileNotFoundError(relative_path)
    if target.stat().st_size > 200000:
        raise ValueError("File is too large to preview")
    return target.read_text(encoding="utf-8", errors="replace")


def make_project_zip(base_dir, chat_id):
    root = _project_root(base_dir, chat_id)
    if not root.exists() or not any(root.rglob("*")):
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())
    buffer.seek(0)
    return buffer.getvalue()
