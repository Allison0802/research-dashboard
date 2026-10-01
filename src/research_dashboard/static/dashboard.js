(() => {
  const parameter = "filter";
  const filters = [...document.querySelectorAll("[data-dashboard-filter]")];
  const items = [...document.querySelectorAll("[data-dashboard-item]")];
  const setFilter = (filter) => {
    filters.forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.dashboardFilter === filter));
    });
    items.forEach((item) => {
      const kinds = item.dataset.dashboardKinds.split(" ").filter(Boolean);
      item.hidden = filter !== "all" && !kinds.includes(filter);
    });
    const empty = document.querySelector("[data-dashboard-empty]");
    if (empty) empty.hidden = items.some((item) => !item.hidden);
    const url = new URL(window.location.href);
    if (filter === "all") url.searchParams.delete(parameter);
    else url.searchParams.set(parameter, filter);
    window.history.replaceState({}, "", url);
    try {
      window.sessionStorage.setItem("research-dashboard-filter", filter);
    } catch (_) {
      // The URL still preserves the selected filter when storage is unavailable.
    }
  };

  if (filters.length) {
    const requested = new URLSearchParams(window.location.search).get(parameter);
    let saved = null;
    try {
      saved = window.sessionStorage.getItem("research-dashboard-filter");
    } catch (_) {
      // Private browsing and strict storage policies may reject access.
    }
    const supported = (filter) => filters.some((button) => button.dataset.dashboardFilter === filter);
    setFilter(supported(requested) ? requested : supported(saved) ? saved : "all");
    filters.forEach((button) => button.addEventListener("click", () => setFilter(button.dataset.dashboardFilter)));
  }

  document.querySelectorAll("[data-project-switcher]").forEach((switcher) => {
    switcher.addEventListener("change", () => { window.location.assign(switcher.value); });
  });

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy-next-step]");
    if (!button) return;
    const value = button.dataset.copyNextStep;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(value);
      button.textContent = "Copied";
    } catch (_) {
      button.textContent = "Copy unavailable";
    }
  });

  const revealHashTarget = () => {
    const target = document.getElementById(window.location.hash.slice(1));
    if (!target) return;
    for (let parent = target.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
    target.querySelector("details")?.setAttribute("open", "");
    target.scrollIntoView();
  };
  revealHashTarget();
  window.addEventListener("hashchange", revealHashTarget);
})();
