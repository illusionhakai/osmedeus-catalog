(() => {
  "use strict";
  const buildId = "__CATALOG_BUILD_ID__";
  let checking = false;
  let reloading = false;
  async function check() {
    if (checking || reloading || document.hidden) return;
    checking = true;
    try {
      const response = await fetch("/catalog-release.json", { cache: "no-store" });
      if (!response.ok) return;
      const release = await response.json();
      if (typeof release.build_id === "string" && /^[a-f0-9]{64}$/.test(release.build_id) && release.build_id !== buildId) {
        reloading = true;
        window.location.reload();
      }
    } catch (_) {
      // Keep the current page usable while the gateway restarts.
    } finally {
      checking = false;
    }
  }
  window.setInterval(check, 5000);
  document.addEventListener("visibilitychange", check);
  void check();
})();
