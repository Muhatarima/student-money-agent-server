// Browser side of the passkey pages. Buttons say what they do with data-action, so the
// page needs no inline script (the Content-Security-Policy forbids it).
// SimpleWebAuthnBrowser comes from the pinned unpkg script in base.html.

async function postJSON(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Something went wrong. Please try again.");
  return data;
}

function showError(message) {
  const box = document.getElementById("error");
  box.textContent = message;
  box.hidden = false;
}

// Link page: create a passkey, then the account is linked
async function register(button) {
  const t = button.dataset.token;
  const optionsJSON = await postJSON("/passkeys/register/options", { t });
  const credential = await SimpleWebAuthnBrowser.startRegistration({ optionsJSON });
  const result = await postJSON("/passkeys/register/verify", { t, credential });
  window.location.href = result.redirect;
}

// Approval page: sign the challenge (derived from what's shown) with the passkey
async function approve(button) {
  const { id, token: t } = button.dataset;
  const optionsJSON = await postJSON(`/approve/${id}/options`, { t });
  const credential = await SimpleWebAuthnBrowser.startAuthentication({ optionsJSON });
  const result = await postJSON(`/approve/${id}/verify`, { t, credential });
  window.location.href = result.redirect;
}

async function cancel(button) {
  const { id, token: t } = button.dataset;
  const result = await postJSON(`/approve/${id}/cancel`, { t });
  window.location.href = result.redirect;
}

// Upload page: prove it's you before the form appears
async function uploadLogin(button) {
  const { id, token: t } = button.dataset;
  const optionsJSON = await postJSON(`/upload/${id}/options`, { t });
  const credential = await SimpleWebAuthnBrowser.startAuthentication({ optionsJSON });
  const result = await postJSON(`/upload/${id}/verify`, { t, credential });
  window.location.href = result.redirect;
}

// Approval page: live countdown to the expiry time
function startCountdown(element) {
  const expiresAt = new Date(element.dataset.expiresAt).getTime();
  const tick = () => {
    const seconds = Math.max(0, Math.round((expiresAt - Date.now()) / 1000));
    element.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    if (seconds === 0) {
      document.querySelectorAll("button").forEach((b) => (b.disabled = true));
      showError("This request expired. Ask again in WhatsApp.");
      return;
    }
    setTimeout(tick, 1000);
  };
  tick();
}

const ACTIONS = { register, approve, cancel, uploadLogin };

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await ACTIONS[button.dataset.action](button);
      } catch (error) {
        showError(error.message);
        button.disabled = false;
      }
    });
  });
  document.querySelectorAll("[data-expires-at]").forEach(startCountdown);
});
