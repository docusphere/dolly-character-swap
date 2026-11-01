// Job page behaviour: click-to-scroll navigator, scroll-spy, live progress polling.
(() => {
  "use strict";
  const layout = document.querySelector(".layout[data-job-id]");
  if (!layout) return; // the jobs list needs no script

  const POLL_MS = 3000;
  const STALL_MS = 10 * 60 * 1000; // no change for 10 minutes: stop and say so
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const links = new Map(
    [...layout.querySelectorAll(".steps a[href^='#']")].map((a) => [a.hash.slice(1), a])
  );

  function setCurrent(id) {
    links.forEach((link, key) => {
      if (key === id) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    });
  }

  // 1. Click a step: smooth-scroll to its section and move keyboard focus there.
  links.forEach((link, id) => {
    link.addEventListener("click", (event) => {
      const target = document.getElementById(id);
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
      target.focus({ preventScroll: true });
      history.replaceState(null, "", `#${id}`);
      setCurrent(id);
    });
  });

  // 2. Scroll-spy: highlight the first section crossing the top part of the screen.
  const visible = new Set();
  const spy = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) visible.add(entry.target.id);
        else visible.delete(entry.target.id);
      });
      const atBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 2;
      const ids = [...links.keys()];
      const current = atBottom ? ids[ids.length - 1] : ids.find((id) => visible.has(id));
      if (current) setCurrent(current);
    },
    { rootMargin: "-80px 0px -60% 0px" }
  );
  links.forEach((_, id) => {
    const section = document.getElementById(id);
    if (section) spy.observe(section);
  });
  window.addEventListener(
    "scroll",
    () => {
      if (window.innerHeight + window.scrollY >= document.body.scrollHeight - 2) {
        setCurrent([...links.keys()].pop());
      }
    },
    { passive: true }
  );
  const linked = location.hash ? document.getElementById(location.hash.slice(1)) : null;
  if (linked && links.has(linked.id)) {
    linked.scrollIntoView({ block: "start" }); // a shared #section link opens on that step
    setCurrent(linked.id);
  } else {
    setCurrent(links.keys().next().value);
  }

  // 3. Live progress: poll while the job is active; reload when any stage changes.
  if (layout.dataset.active !== "true") return;
  const url = `/api/jobs/${layout.dataset.jobId}`;
  const shown = JSON.stringify(JSON.parse(layout.dataset.stages));
  const startedWaiting = Date.now();

  function stalled() {
    document.getElementById("stalled").hidden = false;
  }

  async function tick() {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok) {
        const data = await response.json();
        if (JSON.stringify(data.stages) !== shown) {
          location.reload();
          return;
        }
        if (!data.active) return; // finished or failed: nothing more to watch
      }
    } catch (error) {
      // the viewer may be restarting; keep trying until the stall timer runs out
    }
    if (Date.now() - startedWaiting > STALL_MS) {
      stalled();
      return;
    }
    window.setTimeout(tick, POLL_MS);
  }
  window.setTimeout(tick, POLL_MS);
})();
