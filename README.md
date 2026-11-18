# CodeAI Ultimate

A local AI coding assistant built with Flask, SQLite, OpenRouter, and vanilla JavaScript.

## Features

- Chat UI with saved conversations
- Markdown rendering and syntax highlighting
- Upload source-code and text files for AI context
- Generate multi-file projects from a natural-language specification
- Browse and preview generated project files
- Download generated projects as ZIP archives
- Permission-based local folder import and project-file sync (supported Chromium browsers)
- Excel/CSV import: XLSX workbooks are converted to text/CSV context in the browser
- Workspace path validation and upload extension/size restrictions

## Requirements

- Python 3.10+
- An OpenRouter API key
- Internet connection for AI requests and browser CDN assets

## Setup

1. Open a terminal in this folder.
2. Create a virtual environment:

   ```bash
   python -m venv .venv
   ```

3. Activate it:

   Windows PowerShell:

   ```powershell
   .venv\Scripts\Activate.ps1
   ```

   macOS/Linux:

   ```bash
   source .venv/bin/activate
   ```

4. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

5. Copy `.env.example` to `.env`.
6. Put your OpenRouter key in `.env` and optionally change `OPENROUTER_MODEL`.
7. Start the app:

   ```bash
   python app.py
   ```

8. Open `http://127.0.0.1:5000`.

## Use

- **New chat:** create a separate saved conversation.
- **Upload:** attach supported text/source files. Their text is included as context in AI chat and project generation.
- **Generate multi-file project:** write the project specification in the message box, then select this button.
- **Preview:** select a generated file in the right panel.
- **Download ZIP:** download the generated project.

## Configuration

`.env` settings:

- `OPENROUTER_API_KEY`: required API key.
- `OPENROUTER_MODEL`: model identifier available to your OpenRouter account.
- `FLASK_SECRET_KEY`: replace the development value with a random secret.
- `MAX_UPLOAD_MB`: maximum request/upload size in MB (default 5).

## Safety and limitations

- Generated files are written only inside `workspaces/<chat-id>/`.
- Uploads are limited by extension and request size. Do not upload secrets, passwords, private keys, or personal data.
- CodeAI does **not** run generated programs, install packages, use a terminal, or make arbitrary changes to your computer.
- Review generated code before running it. AI output can be incorrect or insecure.
- This is a local development app, not a production deployment. Do not expose Flask's development server directly to the internet.
- The interface loads Markdown and syntax-highlighting libraries from public CDNs. An internet connection is needed for those browser assets.
- OpenRouter usage may incur costs depending on the selected model and account terms.


## Automation connectors

The workspace panel supports a user-selected local folder through the browser File System Access API. CodeAI can import supported text/code/CSV files into the active chat and sync generated project files back to the selected folder after confirmation. Folder access is granted by the browser and is not available to the Render server itself.

Excel/CSV import is available in the browser. XLSX files are converted to CSV text for AI context; this does not preserve workbook formulas, formatting, charts, or macros.

Google Drive, Google Sheets, YouTube, Vercel, and Render are shown as integration targets but are **not connected** by this foundation release. Real account access requires OAuth/API credentials, a secure user identity/session layer, scoped permissions, and server-side token storage. Do not paste API tokens into chat or browser code. VS Code can use the synced folder, but direct editor/terminal control requires a separately installed and authenticated desktop bridge.
