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
    const button = document.createElement("button");
    button.className = "file-button";
    button.textContent = "▤  " + path;
    button.onclick = async () => {
      try {
        const result = await api(`/api/chats/${activeChatId}/files/${path.split("/").map(encodeURIComponent).join("/")}`);
        $("filePreview").textContent = result.content;
      } catch (err) { toast(err.message); }
    };
    $("fileTree").appendChild(button);
  });
}

async function sendMessage(message) {
  if (!activeChatId) await newChat();
  autoFollow = true;                     // sending always re-engages following
  addMessage("user", message);
  scrollToBottom(true);
  $("sendButton").disabled = true;
  try {
    const result = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: activeChatId, message })
    });
    addMessage("assistant", result.reply);
    await loadChats();
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

    const details = plan.actions.map(item => `• ${item.path} — ${item.reason || "planned change"}`).join("\n");
    const approved = window.confirm(
      `Review automation plan\n\n${plan.summary}\n\n${details}\n\nApply these file changes?`
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
