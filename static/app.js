let activeChatId = null;
let selectedUploads = [];

const $ = (id) => document.getElementById(id);
const messagesEl = $("messages");
const promptEl = $("prompt");
const scrollBtn = $("scrollBottom");

/* ---------- ChatGPT-style scroll management ---------- */
let autoFollow = true; // true while user is pinned near the bottom

function isNearBottom() {
  return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 80;
}

function scrollToBottom(smooth = false) {
  messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: smooth ? "smooth" : "auto" });
}

function updateScrollButton() {
  autoFollow = isNearBottom();          // resume following when back at bottom
  scrollBtn.hidden = autoFollow;        // show button only when scrolled up
}

messagesEl.addEventListener("scroll", updateScrollButton, { passive: true });

scrollBtn.addEventListener("click", () => {
  scrollToBottom(true);                 // smooth scroll to latest message
  autoFollow = true;
  scrollBtn.hidden = true;
});

function followOutput() {
  if (autoFollow) scrollToBottom();     // never yanks the page while user reads older messages
}

window.addEventListener("resize", () => { if (autoFollow) scrollToBottom(); });

/* ---------- core helpers ---------- */
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2600);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}

function renderMarkdown(target, markdown) {
  const raw = window.marked
    ? marked.parse(markdown, { breaks: true, gfm: true })
    : `<p>${escapeHtml(markdown)}</p>`;
  target.innerHTML = window.DOMPurify ? DOMPurify.sanitize(raw) : escapeHtml(markdown);
  if (window.hljs) {
    target.querySelectorAll("pre code").forEach(block => hljs.highlightElement(block));
  }
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    const label = button.querySelector(".copy-label");
    const original = label ? label.textContent : "Copy";
    if (label) label.textContent = "Copied!";
    else button.textContent = "Copied!";
    setTimeout(() => {
      if (label) label.textContent = original;
      else button.textContent = original;
    }, 1500);
  } catch (err) {
    toast("Could not copy: " + err.message);
  }
}

function makeCopyButton(text, title = "Copy") {
  const button = document.createElement("button");
  button.className = "copy-btn";
  button.title = title;
  button.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span class="copy-label">Copy</span>`;
  button.onclick = () => copyText(text, button);
  return button;
}

function addCodeCopyButtons(bubble) {
  bubble.querySelectorAll("pre").forEach(pre => {
    if (pre.querySelector(".code-copy")) return;
    const wrapper = document.createElement("div");
    wrapper.className = "code-block-wrap";
    pre.replaceWith(wrapper);
    wrapper.append(pre);
    const btn = document.createElement("button");
    btn.className = "copy-btn code-copy";
    btn.title = "Copy code";
    btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span class="copy-label">Copy</span>`;
    const codeEl = pre.querySelector("code");
    btn.onclick = () => copyText(codeEl ? codeEl.innerText : pre.innerText, btn);
    wrapper.append(btn);
  });
}

function addMessage(role, content) {
  const row = document.createElement("div");
  row.className = `message ${role}`;
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "U" : "⌘";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (role === "assistant") renderMarkdown(bubble, content);
  else bubble.textContent = content;
  row.append(avatar, bubble);

  if (role === "assistant") {
    addCodeCopyButtons(bubble);
    const actions = document.createElement("div");
    actions.className = "message-actions";
    actions.append(makeCopyButton(content, "Copy AI response"));
    row.append(actions);
  } else {
    const actions = document.createElement("div");
    actions.className = "message-actions";
    actions.append(makeCopyButton(content, "Copy message"));
    row.append(actions);
  }

  messagesEl.appendChild(row);
  followOutput();
}

function showWelcome() {
  messagesEl.innerHTML = `
    <div class="welcome">
      <div class="welcome-mark">⌘</div>
      <h1>What are we building?</h1>
      <p>Describe a coding task, paste an error, or ask for a complete project.</p>
      <div class="suggestions">
        <button data-prompt="Build a Python calculator with a simple graphical interface.">Build a Python app</button>
        <button data-prompt="Explain and debug this code: ">Debug code</button>
        <button data-prompt="Create a responsive portfolio website with HTML, CSS and JavaScript.">Create a website</button>
        <button data-prompt="Explain recursion with a simple example.">Explain a concept</button>
      </div>
    </div>`;
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const raw = await response.text();

  let data;
  if (contentType.includes("application/json")) {
    try {
      data = JSON.parse(raw);
    } catch {
      throw new Error(`Server returned malformed JSON (HTTP ${response.status}).`);
    }
  } else {
    const detail = raw.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 180);
    throw new Error(
      `Server returned a non-JSON response (HTTP ${response.status}). ${detail || "Check the backend logs."}`
    );
  }

  if (!response.ok) throw new Error(data.error || `Request failed (HTTP ${response.status})`);
  return data;
}

async function loadChats() {
  const chats = await api("/api/chats");
  const list = $("chatList");
  list.innerHTML = "";
  chats.forEach(chat => {
    const button = document.createElement("button");
    button.className = "chat-item" + (chat.id === activeChatId ? " active" : "");
    button.textContent = chat.title;
    button.title = chat.title;
    button.onclick = () => selectChat(chat.id);
    list.appendChild(button);
  });
}

async function newChat() {
  const chat = await api("/api/chats", { method: "POST" });
  await selectChat(chat.id);
  await loadChats();
}

async function selectChat(id) {
  activeChatId = id;
  const history = await api(`/api/chats/${id}/messages`);
  messagesEl.innerHTML = "";
  if (!history.length) showWelcome();
  else history.forEach(item => addMessage(item.role, item.content));
  scrollToBottom();                      // always land on newest when switching chats
  selectedUploads = [];
  $("uploadList").innerHTML = "";
  await refreshFiles();
  await loadAutomationMode();
  $("sidebar").classList.remove("open");
  await loadChats();
}

async function refreshFiles() {
  if (!activeChatId) return;
  const files = await api(`/api/chats/${activeChatId}/files`);
  $("fileCount").textContent = files.length;
  $("fileTree").innerHTML = "";
  if (!files.length) {
    $("fileTree").innerHTML = '<p class="empty-files">Generated project files will appear here.</p>';
    $("downloadZip").disabled = true;
    return;
  }
  $("downloadZip").disabled = false;
  files.forEach(path => {
    const row = document.createElement("div");
    row.className = "file-entry";

    const previewButton = document.createElement("button");
    previewButton.className = "file-button";
    previewButton.textContent = "▤  " + path;
    previewButton.onclick = async () => {
      try {
        const result = await api(`/api/chats/${activeChatId}/files/${path.split("/").map(encodeURIComponent).join("/")}`);
        $("filePreview").textContent = result.content;
      } catch (err) { toast(err.message); }
    };

    const downloadLink = document.createElement("a");
    downloadLink.className = "file-download-button";
    downloadLink.textContent = "↓ DOWNLOAD";
    downloadLink.href = `/api/chats/${encodeURIComponent(activeChatId)}/file-download?path=${encodeURIComponent(path)}`;
    downloadLink.setAttribute("download", path.split("/").pop());
    downloadLink.addEventListener("click", () => toast("Starting download..."));

    row.append(previewButton, downloadLink);
    $("fileTree").appendChild(row);
  });
}

function requestedFilename(message) {
  const match = message.match(/\b(?:[a-zA-Z0-9_-]+\/)*[a-zA-Z0-9_-]+\.(?:html?|css|js|jsx|tsx|ts|py|json|md|txt|java|cpp|c|h|cs|php|go|rs|sql|ya?ml|toml|xml|sh|csv)\b/i);
  return match ? match[0].split("/").pop() : null;
}

function isFileCreationRequest(message) {
  return /\b(create|make|generate|write|save)\b/i.test(message)
    && /\b(file|index\.html|\w+\.(html?|css|js|py|json|md|txt))\b/i.test(message)
    && Boolean(requestedFilename(message));
}

async function generateRequestedFile(message) {
  const expectedName = requestedFilename(message);
  const modeState = await api(`/api/automation/mode?chat_id=${encodeURIComponent(activeChatId)}`);
  const restoreAutomatic = modeState.mode === "automatic";
  if (restoreAutomatic) {
    await api("/api/automation/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: activeChatId, mode: "approval" })
    });
  }

  let plan;
  try {
    plan = await api("/api/automation/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: activeChatId, task:
        "Create only the requested file. The exact required filename is " + expectedName +
        ". Create this file in the current project workspace. Do not substitute another filename.\n\nUser request:\n" + message
      })
    });
  } finally {
    if (restoreAutomatic) {
      await api("/api/automation/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: activeChatId, mode: "automatic" })
      });
    }
  }

  if (plan.applied) {
    if (!plan.files.some(path => path.split("/").pop() === expectedName)) {
      addMessage("assistant", "**File generation stopped:** the planner did not create the exact requested filename.");
      return;
    }
    addMessage("assistant", "**File created successfully:** `" + expectedName +
      "`\n\nSaved in the Project Files panel. Select it to preview, or use Export ZIP to download it.");
    await refreshFiles();
    await loadChats();
    toast(expectedName + " created");
    return;
  }

  if (!Array.isArray(plan.actions) || !plan.actions.some(item => item.path.split("/").pop() === expectedName)) {
    await api("/api/automation/tasks/" + plan.task_id + "/reject", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: activeChatId })
    });
    addMessage("assistant", "**File generation stopped:** the AI did not return the exact filename `" + expectedName + "`. No file was saved.");
    return;
  }

  const preview = plan.actions.map(item =>
    "### " + item.path + "\n" + (item.reason || "Generated file") +
    "\n\n```text\n" + (item.preview || "") +
    (item.truncated ? "\n[Preview shortened]" : "") + "\n```"
  ).join("\n\n");
  addMessage("assistant", "**File ready for approval**\n\n" + plan.summary + "\n\n" + preview);
  const approved = window.confirm(
    "Approve file generation?\n\n" + plan.actions.map(item => item.path).join("\n") +
    "\n\nIf a file already exists, approving may replace its contents."
  );
  const endpoint = approved
    ? "/api/automation/tasks/" + plan.task_id + "/approve"
    : "/api/automation/tasks/" + plan.task_id + "/reject";
  const result = await api(endpoint, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: activeChatId })
  });
  if (approved) {
    addMessage("assistant", "**File generated:** " + result.files.map(path => "`" + path + "`").join(", ") +
      "\n\nYou can preview it in Project Files or download the project ZIP.");
    await refreshFiles();
    await loadChats();
    toast(expectedName + " saved");
  } else {
    addMessage("assistant", "File generation cancelled. No files were changed.");
  }
}

async function sendMessage(message) {
  if (!activeChatId) await newChat();
  autoFollow = true;
  addMessage("user", message);
  scrollToBottom(true);
  $("sendButton").disabled = true;
  try {
    if (isFileCreationRequest(message)) {
      await generateRequestedFile(message);
    } else {
      const result = await api("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: activeChatId, message })
      });
      addMessage("assistant", result.reply);
      await loadChats();
    }
  } catch (err) {
    addMessage("assistant", `**Error:** ${err.message}`);
  } finally {
    $("sendButton").disabled = false;
    promptEl.focus();
  }
}

// Enter sends the prompt; Shift+Enter inserts a new line.
promptEl.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!("requestSubmit" in $("composer"))) {
      $("sendButton").click();
    } else {
      $("composer").requestSubmit();
    }
  }
});

$("composer").addEventListener("submit", async event => {
  event.preventDefault();
  const message = promptEl.value.trim();
  if (!message) return;
  promptEl.value = "";
  await sendMessage(message);
});

$("newChat").onclick = newChat;
$("mobileMenu").onclick = () => $("sidebar").classList.toggle("open");

document.addEventListener("click", event => {
  const suggestion = event.target.closest("[data-prompt]");
  if (suggestion) {
    promptEl.value = suggestion.dataset.prompt;
    promptEl.focus();
  }
});

$("attachButton").onclick = () => $("fileInput").click();
$("fileInput").addEventListener("change", async event => {
  const files = Array.from(event.target.files || []);
  if (!activeChatId) await newChat();
  for (const file of files) {
    const form = new FormData();
    form.append("file", file);
    try {
      const result = await api(`/api/chats/${activeChatId}/upload`, { method: "POST", body: form });
      const chip = document.createElement("span");
      chip.className = "upload-chip";
      chip.textContent = "✓ " + result.filename;
      $("uploadList").appendChild(chip);
      toast(`${result.filename} uploaded`);
    } catch (err) { toast(err.message); }
  }
  event.target.value = "";
});

$("generateProject").onclick = async () => {
  const prompt = promptEl.value.trim();
  if (!prompt) {
    toast("Describe the project in the message box first.");
    promptEl.focus();
    return;
  }
  if (!activeChatId) await newChat();
  $("generateProject").disabled = true;
  $("generateProject").textContent = "Generating...";
  try {
    const result = await api("/api/projects/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: activeChatId, prompt })
    });
    addMessage("assistant", `**${result.project_name} generated.**\n\n${result.description}\n\nCreated ${result.files.length} files. Review them in the Project files panel, then download the ZIP.`);
    promptEl.value = "";
    await refreshFiles();
    await loadChats();
    toast("Project generated");
  } catch (err) {
    toast(err.message);
  } finally {
    $("generateProject").disabled = false;
    $("generateProject").textContent = "✦ Generate multi-file project";
  }
};

$("downloadZip").onclick = () => {
  if (activeChatId) window.location.href = `/api/chats/${activeChatId}/download`;
};

(async function init() {
  try {
    const chats = await api("/api/chats");
    if (chats.length) await selectChat(chats[0].id);
    else await newChat();
  } catch (err) {
    toast(err.message);
  }
})();



/* ---------- bounded project automation ---------- */
async function loadAutomationMode() {
  if (!activeChatId) return;
  try {
    const result = await api(`/api/automation/mode?chat_id=${encodeURIComponent(activeChatId)}`);
    $("automationMode").value = result.mode;
  } catch (err) {
    toast(err.message);
  }
}

$("automationMode").addEventListener("change", async () => {
  if (!activeChatId) return;
  try {
    const result = await api("/api/automation/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: activeChatId, mode: $("automationMode").value })
    });
    toast(result.mode === "automatic"
      ? "Automatic mode enabled: new files only"
      : "Approval mode enabled");
  } catch (err) {
    toast(err.message);
    await loadAutomationMode();
  }
});

$("automationButton").addEventListener("click", async () => {
  const task = promptEl.value.trim();
  if (!task) {
    toast("Describe the task in the message box first.");
    promptEl.focus();
    return;
  }
  if (!activeChatId) await newChat();
  const button = $("automationButton");
  button.disabled = true;
  button.textContent = "PLANNING...";
  try {
    const plan = await api("/api/automation/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: activeChatId, task })
    });

    if (plan.applied) {
      addMessage("assistant", `**Automation completed.** ${plan.summary}\n\nCreated files:\n${plan.files.map(path => "- `" + path + "`").join("\n")}\n\nAutomatic mode only creates new files; it does not run commands or overwrite existing files.`);
      promptEl.value = "";
      await refreshFiles();
      toast("Automation completed");
      return;
    }

    const preview = plan.actions.map(item => {
      const suffix = item.truncated ? "\n\n[Preview shortened. The complete file will be written only if approved.]" : "";
      return "### " + item.path + "\n" + (item.reason || "Planned file change") + "\n\n```text\n" + (item.preview || "") + suffix + "\n```";
    }).join("\n\n");
    addMessage("assistant", "**Automation plan ready for review**\n\n" + plan.summary + "\n\n" + preview);
    const details = plan.actions.map(item => "• " + item.path + " — " + (item.reason || "planned change")).join("\n");
    const approved = window.confirm(
      "Review the proposed files shown in the chat.\n\n" + plan.summary + "\n\n" + details + "\n\nApply these file changes?"
    );
    const endpoint = approved
      ? `/api/automation/tasks/${plan.task_id}/approve`
      : `/api/automation/tasks/${plan.task_id}/reject`;
    const result = await api(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: activeChatId })
    });
    if (approved) {
      addMessage("assistant", `**Approved and applied.**\n\n${plan.summary}\n\nUpdated files:\n${result.files.map(path => "- `" + path + "`").join("\n")}`);
      promptEl.value = "";
      await refreshFiles();
      toast("Approved changes applied");
    } else {
      toast("Automation plan rejected; no files changed");
    }
  } catch (err) {
    toast(err.message);
  } finally {
    button.disabled = false;
    button.textContent = "PLAN & RUN TASK";
  }
});


/* ---------- local folder + spreadsheet bridge ---------- */
let selectedDirectoryHandle = null;
const LOCAL_TEXT_EXTENSIONS = new Set([".py",".js",".ts",".jsx",".tsx",".html",".css",".json",".md",".txt",".java",".cpp",".c",".h",".cs",".php",".go",".rs",".sql",".yaml",".yml",".toml",".xml",".sh",".csv"]);

function fileExtension(name) {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

async function uploadTextToChat(name, text) {
  if (!activeChatId) await newChat();
  const file = new File([text], name, { type: name.toLowerCase().endsWith(".csv") ? "text/csv" : "text/plain" });
  const form = new FormData();
  form.append("file", file);
  const result = await api(`/api/chats/${activeChatId}/upload`, { method: "POST", body: form });
  const chip = document.createElement("span");
  chip.className = "upload-chip";
  chip.textContent = "✓ " + result.filename;
  $("uploadList").appendChild(chip);
}

$("chooseFolder").addEventListener("click", async () => {
  if (!window.showDirectoryPicker) {
    toast("Folder access needs a supported Chromium browser over HTTPS or localhost.");
    return;
  }
  try {
    selectedDirectoryHandle = await window.showDirectoryPicker({ mode: "readwrite" });
    $("folderStatus").textContent = "Selected: " + selectedDirectoryHandle.name;
    $("syncFolder").disabled = false;
    toast("Folder permission granted for this session");
    const importFolder = window.confirm("Import supported text/code/CSV files from this folder into the current chat?");
    if (!importFolder) return;
    if (!activeChatId) await newChat();
    let imported = 0;
    let skipped = 0;
    async function walk(directory, prefix = "") {
      for await (const entry of directory.values()) {
        if (imported >= 25) { skipped++; continue; }
        if (entry.kind === "directory") {
          await walk(entry, prefix + entry.name + "/");
        } else if (LOCAL_TEXT_EXTENSIONS.has(fileExtension(entry.name))) {
          const file = await entry.getFile();
          if (file.size > 1024 * 1024) { skipped++; continue; }
          const content = await file.text();
          await uploadTextToChat(prefix + entry.name, content);
          imported++;
        }
      }
    }
    await walk(selectedDirectoryHandle);
    toast(`Imported ${imported} files${skipped ? "; some files skipped" : ""}`);
  } catch (err) {
    if (err.name !== "AbortError") toast("Folder access failed: " + err.message);
  }
});

$("importSpreadsheet").addEventListener("click", () => $("spreadsheetInput").click());
$("spreadsheetInput").addEventListener("change", async event => {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  try {
    if (!activeChatId) await newChat();
    let csv;
    if (fileExtension(file.name) === ".csv") {
      csv = await file.text();
    } else {
      if (!window.XLSX) throw new Error("Spreadsheet parser did not load. Refresh and try again.");
      const workbook = XLSX.read(await file.arrayBuffer(), { type: "array" });
      csv = workbook.SheetNames.map(name =>
        "# Sheet: " + name + "\n" + XLSX.utils.sheet_to_csv(workbook.Sheets[name])
      ).join("\n\n");
    }
    if (csv.length > 120000) throw new Error("Spreadsheet is too large to import (120,000 characters maximum).");
    await uploadTextToChat(file.name.replace(/\.(xlsx|xls)$/i, ".csv"), csv);
    toast("Spreadsheet imported into chat context");
  } catch (err) {
    toast("Spreadsheet import failed: " + err.message);
  } finally {
    event.target.value = "";
  }
});

async function getOrCreateDirectory(parent, name) {
  return parent.getDirectoryHandle(name, { create: true });
}

$("syncFolder").addEventListener("click", async () => {
  if (!selectedDirectoryHandle || !activeChatId) {
    toast("Select a local folder and an active chat first.");
    return;
  }
  if (!window.confirm("Sync generated project files into the selected folder? Existing files with matching names may be overwritten.")) return;
  const button = $("syncFolder");
  button.disabled = true;
  button.textContent = "SYNCING...";
  try {
    const paths = await api(`/api/chats/${activeChatId}/files`);
    let written = 0;
    for (const path of paths) {
      const result = await api(`/api/chats/${activeChatId}/files/${path.split("/").map(encodeURIComponent).join("/")}`);
      if (typeof result.content !== "string" || result.content.length > 200000) continue;
      const parts = path.split("/");
      const filename = parts.pop();
      let directory = selectedDirectoryHandle;
      for (const part of parts) directory = await getOrCreateDirectory(directory, part);
      const handle = await directory.getFileHandle(filename, { create: true });
      const writable = await handle.createWritable();
      await writable.write(result.content);
      await writable.close();
      written++;
    }
    toast(`Synced ${written} project files to ${selectedDirectoryHandle.name}`);
  } catch (err) {
    toast("Folder sync failed: " + err.message);
  } finally {
    button.disabled = !selectedDirectoryHandle;
    button.textContent = "SYNC PROJECT TO FOLDER";
  }
});


/* ---------- plugin manager: GitHub + Render ---------- */
const pluginManagerEl = $("pluginManager");

function openPluginManager() {
  pluginManagerEl.hidden = false;
  document.body.classList.add("plugin-open");
  loadPluginStatuses();
}

function closePluginManager() {
  pluginManagerEl.hidden = true;
  document.body.classList.remove("plugin-open");
}

$("pluginManagerOpen").addEventListener("click", openPluginManager);
$("pluginManagerClose").addEventListener("click", closePluginManager);
pluginManagerEl.addEventListener("click", event => {
  if (event.target === pluginManagerEl) closePluginManager();
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !pluginManagerEl.hidden) closePluginManager();
});

async function loadPluginStatuses() {
  const cards = $("pluginCards");
  try {
    const result = await api("/api/plugins");
    result.plugins.forEach(plugin => {
      const button = cards.querySelector('[data-test-plugin="' + plugin.id + '"]');
      const card = button && button.closest(".plugin-card");
      if (!card) return;
      const status = card.querySelector(".plugin-status");
      const detail = card.querySelector(".plugin-result");
      status.textContent = plugin.configured ? "CONFIGURED" : "SETUP REQUIRED";
      status.classList.toggle("is-configured", plugin.configured);
      detail.textContent = plugin.configured
        ? "Server credential found. Test the connection to verify it."
        : "Add " + (plugin.id === "github" ? "GITHUB_TOKEN" : "RENDER_API_KEY") + " to server environment variables.";
    });
  } catch (err) {
    toast("Could not load plugins: " + err.message);
  }
}

$("refreshPlugins").addEventListener("click", loadPluginStatuses);

$("pluginCards").addEventListener("click", async event => {
  const button = event.target.closest("[data-test-plugin]");
  if (!button) return;
  const pluginId = button.dataset.testPlugin;
  const resultEl = $(pluginId + "PluginResult");
  button.disabled = true;
  button.textContent = "TESTING...";
  resultEl.textContent = "Checking provider connection…";
  resultEl.classList.remove("plugin-success");
  try {
    const result = await api("/api/plugins/" + pluginId + "/test", { method: "POST" });
    resultEl.textContent = result.account
      ? "Connected as " + result.account + ". " + result.message
      : result.message;
    resultEl.classList.add("plugin-success");
    toast(pluginId + " connected");
  } catch (err) {
    resultEl.textContent = err.message;
    toast(pluginId + " connection failed");
  } finally {
    button.disabled = false;
    button.textContent = "TEST CONNECTION";
    await loadPluginStatuses();
  }
});
