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
