"use strict";

const menuToggle = document.getElementById("menuToggle");
const siteNav = document.getElementById("siteNav");
const form = document.getElementById("contactForm");
const status = document.getElementById("formStatus");
const submitButton = document.getElementById("submitButton");

document.getElementById("year").textContent = new Date().getFullYear();

menuToggle.addEventListener("click", () => {
  const open = siteNav.classList.toggle("open");
  menuToggle.setAttribute("aria-expanded", String(open));
  menuToggle.textContent = open ? "✕" : "☰";
});

siteNav.querySelectorAll("a").forEach(link => {
  link.addEventListener("click", () => {
    siteNav.classList.remove("open");
    menuToggle.setAttribute("aria-expanded", "false");
    menuToggle.textContent = "☰";
  });
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (!form.reportValidity()) return;

  const payload = {
    name: document.getElementById("name").value.trim(),
    email: document.getElementById("email").value.trim(),
    message: document.getElementById("message").value.trim(),
    website: document.getElementById("website").value.trim()
  };

  submitButton.disabled = true;
  submitButton.textContent = "Sending...";
  status.textContent = "";

  try {
    const response = await fetch("/api/contact", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Unable to send your message.");
    status.textContent = data.message || "Your message was received.";
    form.reset();
  } catch (error) {
    status.textContent = error.message || "Network error. Please try again.";
  } finally {
    submitButton.disabled = false;
    submitButton.innerHTML = 'Send message <span>↗</span>';
  }
});
