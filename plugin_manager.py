"""Read-only GitHub and Render integrations for CodeAI."""
import os
from urllib.parse import urlsplit

import requests
from flask import Blueprint, jsonify, request

plugin_bp = Blueprint("plugin_manager", __name__)


def _same_origin():
    origin = request.headers.get("Origin")
    if not origin:
        return True
    try:
        return urlsplit(origin).netloc.lower() == request.host.lower()
    except ValueError:
        return False


@plugin_bp.get("/api/plugins")
def list_plugins():
    return jsonify({"plugins": [
        {"id": "github", "name": "GitHub", "configured": bool(os.getenv("GITHUB_TOKEN")),
         "description": "Read repository and account metadata", "mode": "read-only"},
        {"id": "render", "name": "Render", "configured": bool(os.getenv("RENDER_API_KEY")),
         "description": "Read services and deployment metadata", "mode": "read-only"}
    ]})


@plugin_bp.post("/api/plugins/<plugin_id>/test")
def test_plugin(plugin_id):
    if not _same_origin():
        return jsonify({"error": "Cross-origin request rejected"}), 403

    if plugin_id == "github":
        token = os.getenv("GITHUB_TOKEN", "").strip()
        if not token:
            return jsonify({"error": "GITHUB_TOKEN is not configured on the server."}), 400
        try:
            response = requests.get(
                "https://api.github.com/user",
                headers={"Authorization": "Bearer " + token,
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"},
                timeout=12
            )
            if response.status_code == 401:
                return jsonify({"error": "GitHub rejected this token. Check the server secret."}), 401
            response.raise_for_status()
            return jsonify({"connected": True,
                            "account": response.json().get("login", "GitHub account"),
                            "message": "GitHub connection verified. Requests are read-only."})
        except requests.RequestException:
            return jsonify({"error": "Could not reach GitHub. Check the token and try again."}), 502

    if plugin_id == "render":
        token = os.getenv("RENDER_API_KEY", "").strip()
        if not token:
            return jsonify({"error": "RENDER_API_KEY is not configured on the server."}), 400
        try:
            response = requests.get(
                "https://api.render.com/v1/services?limit=1",
                headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                timeout=12
            )
            if response.status_code in (401, 403):
                return jsonify({"error": "Render rejected this API key or its permissions."}), response.status_code
            response.raise_for_status()
            return jsonify({"connected": True,
                            "message": "Render connection verified. Requests are read-only."})
        except requests.RequestException:
            return jsonify({"error": "Could not reach Render. Check the API key and try again."}), 502

    return jsonify({"error": "Unknown plugin"}), 404
