// Job page behaviour: click-to-scroll navigator, scroll-spy, live progress polling.
(() => {
  "use strict";
  const layout = document.querySelector(".layout[data-job-id]");
  if (!layout) return; // the jobs list needs no script

  const STALL_MS = 10 * 60 * 1000; // no change for 10 minutes: stop and say so
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const links = new Map(
    [...layout.querySelectorAll(".steps a[href^='#']")].map((a) => [a.hash.slice(1), a])
  );

  function setCurrent(id) {
    links.forEach((link, key) => {
      if (key === id) {
        link.setAttribute("aria-current", "true");
        // keep the highlighted step visible in the (phone) step bar
        link.scrollIntoView({
          block: "nearest",
          inline: "nearest",
          behavior: reduceMotion ? "auto" : "smooth",
        });
      } else link.removeAttribute("aria-current");
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

  // 3. Live progress. "fast" while a step runs, "slow" while the job can still change
  //    (failed, waiting for review or generation), "off" once the final video exists.
  const POLL_MS = { fast: 3000, slow: 10000 };
  const pollMode = layout.dataset.poll;
  if (!Object.hasOwn(POLL_MS, pollMode)) return;
  const url = `/api/jobs/${layout.dataset.jobId}`;
  const initial = JSON.parse(layout.dataset.stages);
  const shown = JSON.stringify(initial);
  const startedWaiting = Date.now();
  // A running generation (Wan's 720p final can take over 10 minutes) is never "stalled",
  // and the stall hint below only fits preparation, where `resume` is the right command.
  let generating = Object.values(initial.generation || {}).includes("running");

  document.getElementById("refresh-button").addEventListener("click", () => location.reload());

  function playing() {
    return [...document.querySelectorAll("video, audio")].some((m) => !m.paused && !m.ended);
  }

  function stalled() {
    document.getElementById("stalled").textContent =
      "No progress for 10 minutes. The job may have stopped. Run " +
      `charswap resume ${layout.dataset.jobId}.`;
  }

  async function tick() {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok) {
        const data = await response.json();
        generating = Object.values(data.generation || {}).includes("running");
        const now = JSON.stringify({ stages: data.stages, generation: data.generation });
        if (now !== shown) {
          if (!playing()) {
            location.reload();
            return;
          }
          // never interrupt playback: offer a refresh and keep polling
          document.getElementById("new-results").hidden = false;
        }
        if (data.poll === "off") return; // final video exists: nothing more to watch
      }
    } catch (error) {
      // the viewer may be restarting; keep trying
    }
    if (pollMode === "fast" && !generating && Date.now() - startedWaiting > STALL_MS) {
      stalled();
      return;
    }
    window.setTimeout(tick, POLL_MS[pollMode]);
  }
  window.setTimeout(tick, POLL_MS[pollMode]);
})();
