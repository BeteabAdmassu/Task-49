const offlineBanner = document.getElementById("offline-banner");
const screenName = window.location.pathname || "unknown";

let lastSuccess = null;

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
document.body.addEventListener("htmx:afterSwap", () => {
  if (lastSuccess) {
    setOffline(false);
  }
});
