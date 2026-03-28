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

function initRecommendationWidgetTelemetry() {
  const widget = document.getElementById("recommendation-widget");
  const labelNode = document.getElementById("exp-label");
  if (!widget || !labelNode) return;

  const widgetKey = widget.dataset.widgetKey || "suggested-times";
  let variantLabel = "Version A";
  let impressionSent = false;

  const sendImpressionOnce = () => {
    if (impressionSent) return;
    impressionSent = true;
    void emitRecommendationTelemetry("rec_impression", widgetKey, variantLabel);
  };

  fetch(`/api/experiments/assign/${encodeURIComponent(widgetKey)}`, {
    credentials: "same-origin",
    cache: "no-store",
  })
    .then((response) => response.json())
    .then((data) => {
      variantLabel = data.label || "Version A";
      labelNode.textContent = variantLabel;
    })
    .catch(() => {
      variantLabel = "Version A";
      labelNode.textContent = variantLabel;
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
