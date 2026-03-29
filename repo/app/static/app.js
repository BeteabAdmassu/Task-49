const offlineBanner = document.getElementById("offline-banner");
const screenName = window.location.pathname || "unknown";
const csrfToken = document.querySelector("meta[name='csrf-token']")?.content || "";

let lastSuccess = null;

if (csrfToken && typeof window.fetch === "function") {
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const method = (init.method || "GET").toUpperCase();
    const needsCsrf = method !== "GET" && method !== "HEAD" && method !== "OPTIONS";
    if (needsCsrf) {
      const headers = new Headers(init.headers || {});
      if (!headers.has("X-CSRF-Token")) {
        headers.set("X-CSRF-Token", csrfToken);
      }
      init.headers = headers;
    }
    return nativeFetch(input, init);
  };
}

function setOffline(isOffline) {
  if (!offlineBanner) return;
  offlineBanner.classList.toggle("hidden", !isOffline);
  syncPollingState();
}

function syncPollingState() {
  const pollingTargets = document.querySelectorAll('[hx-trigger*="every 10s"]');
  const offline = offlineBanner && !offlineBanner.classList.contains("hidden");
  pollingTargets.forEach((element) => {
    if (offline) {
      element.setAttribute("hx-disable", "true");
    } else {
      element.removeAttribute("hx-disable");
    }
  });
}

async function heartbeat() {
  try {
    const response = await fetch(`/api/heartbeat?screen=${encodeURIComponent(screenName)}`, {
      headers: { "HX-Request": "true" },
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) throw new Error("heartbeat failed");
    await response.json();
    lastSuccess = Date.now();
    setOffline(false);
  } catch (_err) {
    setOffline(true);
  }
}

heartbeat();
setInterval(heartbeat, 10000);

document.body.addEventListener("htmx:responseError", () => setOffline(true));
document.body.addEventListener("htmx:sendError", () => setOffline(true));
document.body.addEventListener("htmx:timeout", () => setOffline(true));
document.body.addEventListener("htmx:configRequest", (event) => {
  if (csrfToken) {
    event.detail.headers["X-CSRF-Token"] = csrfToken;
  }
});
document.body.addEventListener("htmx:afterSwap", () => {
  if (lastSuccess) {
    setOffline(false);
  }
});

async function emitRecommendationTelemetry(eventType, widgetKey, variantLabel) {
  if (!widgetKey || !variantLabel) return;
  await fetch("/api/analytics/recommendation-event", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      event_type: eventType,
      widget_key: widgetKey,
      variant_label: variantLabel,
    }),
    credentials: "same-origin",
    keepalive: true,
  });
}

async function loadExperimentPresentation(widgetKey) {
  const labelNode = document.getElementById("exp-label");
  const badgeNode = document.getElementById("exp-badge");
  const kioskBadgeNode = document.getElementById("kiosk-exp-badge");
  const kioskLabelNode = document.getElementById("kiosk-exp-label");
  try {
    const response = await fetch(`/api/experiments/assign/${encodeURIComponent(widgetKey)}`, {
      credentials: "same-origin",
      cache: "no-store",
    });
    const data = await response.json();
    const label = data.label || "Version A";
    const variantCode = data.variant || (label.includes("B") ? "B" : "A");
    if (labelNode) labelNode.textContent = label;
    if (kioskLabelNode) kioskLabelNode.textContent = label;
    if (badgeNode) badgeNode.textContent = `v${variantCode}`;
    if (kioskBadgeNode) kioskBadgeNode.textContent = `v${variantCode}`;
    return { label, variantCode };
  } catch (_err) {
    if (labelNode) labelNode.textContent = "Version A";
    if (kioskLabelNode) kioskLabelNode.textContent = "Version A";
    if (badgeNode) badgeNode.textContent = "vA";
    if (kioskBadgeNode) kioskBadgeNode.textContent = "vA";
    return { label: "Version A", variantCode: "A" };
  }
}

function initRecommendationWidgetTelemetry() {
  const widget = document.getElementById("recommendation-widget");
  if (!widget) return;

  const widgetKey = widget.dataset.widgetKey || "suggested-times";
  let variantLabel = "Version A";
  let impressionSent = false;

  const sendImpressionOnce = () => {
    if (impressionSent) return;
    impressionSent = true;
    void emitRecommendationTelemetry("rec_impression", widgetKey, variantLabel);
  };

  loadExperimentPresentation(widgetKey)
    .then(({ label }) => {
      variantLabel = label;
    })
    .finally(() => {
      if (typeof window.IntersectionObserver === "function") {
        const observer = new IntersectionObserver((entries) => {
          if (entries.some((entry) => entry.isIntersecting)) {
            sendImpressionOnce();
            observer.disconnect();
          }
        });
        observer.observe(widget);
      } else {
        sendImpressionOnce();
      }
    });

  widget.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const actionable = target.closest("a,button,[data-rec-action]");
    if (!actionable) return;
    void emitRecommendationTelemetry("rec_click", widgetKey, variantLabel);
  });
}

initRecommendationWidgetTelemetry();
void loadExperimentPresentation("suggested-times");

async function submitSocialAction(targetUserId, relation) {
  const response = await fetch("/api/social/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      target_user_id: targetUserId,
      relation,
    }),
    credentials: "same-origin",
  });

  let payload = {};
  try {
    payload = await response.json();
  } catch (_err) {
    payload = {};
  }
  return { response, payload };
}

function initSocialActionPanel() {
  const panel = document.getElementById("social-actions-panel");
  if (!panel) return;

  const targetInput = document.getElementById("social-target-user-id");
  const relationInput = document.getElementById("social-relation");
  const button = document.getElementById("social-action-button");
  const status = document.getElementById("social-action-status");
  if (!targetInput || !relationInput || !button || !status) return;

  button.addEventListener("click", async () => {
    const targetUserId = Number(targetInput.value);
    const relation = relationInput.value;
    if (!targetUserId) {
      status.textContent = "Target User ID is required.";
      return;
    }

    button.disabled = true;
    status.textContent = "Submitting social action...";
    try {
      const { response, payload } = await submitSocialAction(targetUserId, relation);
      if (response.ok) {
        status.textContent = `Action '${relation}' saved for user ${targetUserId}.`;
      } else {
        status.textContent = payload.error || "Social action failed.";
      }
    } catch (_err) {
      status.textContent = "Network error while submitting social action.";
    } finally {
      button.disabled = false;
    }
  });
}

initSocialActionPanel();

function setProfileSocialButtonLabel(button, relation) {
  if (!button) return;
  if (relation === "follow") button.textContent = "Unfollow";
  if (relation === "unfollow") button.textContent = "Follow";
  if (relation === "favorite") button.textContent = "Favorited";
  if (relation === "like") button.textContent = "Liked";
  if (relation === "block") button.textContent = "Blocked";
  if (relation === "report") button.textContent = "Reported";
}

function initProfileSocialActions() {
  const panel = document.getElementById("profile-social-actions");
  if (!panel) return;

  const status = document.getElementById("profile-social-status");
  const targetUserId = Number(panel.dataset.targetUserId || "0");
  const buttons = Array.from(panel.querySelectorAll("[data-social-profile-action]"));
  if (!status || !targetUserId || !buttons.length) return;

  let submitting = false;
  buttons.forEach((button) => {
    button.addEventListener("click", async () => {
      if (submitting) return;
      const relation = button.dataset.relation || "";
      if (!relation) return;

      submitting = true;
      buttons.forEach((b) => {
        b.disabled = true;
      });
      status.textContent = `Submitting '${relation}'...`;
      try {
        const { response, payload } = await submitSocialAction(targetUserId, relation);
        if (!response.ok) {
          status.textContent = payload.error || "Social action failed.";
          return;
        }
        setProfileSocialButtonLabel(button, relation);
        if (relation === "follow") {
          button.dataset.relation = "unfollow";
        } else if (relation === "unfollow") {
          button.dataset.relation = "follow";
        }
        status.textContent = `Action '${relation}' saved.`;
      } catch (_err) {
        status.textContent = "Network error while submitting social action.";
      } finally {
        submitting = false;
        buttons.forEach((b) => {
          b.disabled = false;
        });
      }
    });
  });
}

initProfileSocialActions();
