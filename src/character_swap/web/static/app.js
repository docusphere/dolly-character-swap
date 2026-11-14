// Character Swap app: the before/after wipe player, live progress and the elapsed timer.
// No inline scripts anywhere (the CSP allows only files from /static).
(() => {
  "use strict";
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const DRIFT = 0.1; // seconds the "before" video may drift before it is put back in step

  // 1. The wipe player: two videos in one box, the top one clipped at a draggable divider.
  function setupWipe(root) {
    const stage = root.querySelector(".wipe-stage");
    const before = root.querySelector(".wipe-before");
    const after = root.querySelector(".wipe-after"); // leads: it may carry the sound
    const play = root.querySelector(".wipe-play");
    const seek = root.querySelector(".wipe-seek");
    const divider = root.querySelector(".wipe-divider");
    const sound = root.querySelector(".wipe-sound");

    function split(percent) {
      const value = Math.min(100, Math.max(0, percent));
      stage.style.setProperty("--split", `${value}%`); // CSSOM: allowed by the CSP
      divider.value = String(Math.round(value));
    }
    divider.addEventListener("input", () => split(Number(divider.value)));
    let dragging = false;
    function fromPointer(event) {
      const box = stage.getBoundingClientRect();
      if (box.width > 0) split(((event.clientX - box.left) / box.width) * 100);
    }
    stage.addEventListener("pointerdown", (event) => {
      if (reduceMotion) return; // side by side: there is no divider to drag
      dragging = true;
      stage.setPointerCapture(event.pointerId);
      fromPointer(event);
    });
    stage.addEventListener("pointermove", (event) => {
      if (dragging) fromPointer(event);
    });
    ["pointerup", "pointercancel"].forEach((name) =>
      stage.addEventListener(name, () => {
        dragging = false;
      })
    );

    function inStep() {
      if (Math.abs(before.currentTime - after.currentTime) > DRIFT) {
        before.currentTime = after.currentTime;
      }
    }
    function showState() {
      play.textContent = after.paused ? "Play" : "Pause";
    }
    play.addEventListener("click", () => {
      if (after.paused) {
        if (after.ended) after.currentTime = 0;
        inStep();
        after.play().catch(() => {});
        before.play().catch(() => {});
      } else {
        after.pause();
        before.pause();
      }
    });
    after.addEventListener("play", showState);
    after.addEventListener("pause", showState);
    after.addEventListener("ended", () => {
      before.pause();
      showState();
      root.dispatchEvent(new CustomEvent("wipe:ended", { bubbles: true }));
    });
    after.addEventListener("timeupdate", () => {
      inStep();
      if (after.duration) {
        seek.value = String(Math.round((after.currentTime / after.duration) * 1000));
      }
    });
    seek.addEventListener("input", () => {
      if (!after.duration) return;
      after.currentTime = (Number(seek.value) / 1000) * after.duration;
      before.currentTime = after.currentTime;
    });
    if (sound) {
      sound.addEventListener("click", () => {
        after.muted = !after.muted; // the draft and final carry the original sound
        sound.setAttribute("aria-pressed", String(!after.muted)); // the label stays "Sound"
      });
    }
    split(50);
  }
  document.querySelectorAll("[data-wipe]").forEach(setupWipe);

  // 2. How long a running draft or final has been going.
  document.querySelectorAll(".elapsed[data-started]").forEach((node) => {
    const started = Date.parse(node.dataset.started);
    if (Number.isNaN(started)) return;
    function tick() {
      const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
      const minutes = Math.floor(seconds / 60);
      node.textContent = `· ${minutes}:${String(seconds % 60).padStart(2, "0")}`;
    }
    tick();
    window.setInterval(tick, 1000);
  });

  // 3. Live progress: poll every 2 s while work runs, slowly while the job waits for you.
  const main = document.querySelector("main[data-job-id]");
  if (!main) return;
  const POLL_MS = { fast: 2000, slow: 10000 };
  let mode = main.dataset.poll;
  if (!Object.hasOwn(POLL_MS, mode)) return;
  const shown = JSON.parse(main.dataset.progress);
  const url = `/api/jobs/${main.dataset.jobId}`;
  const refresh = document.getElementById("refresh-button");
  if (refresh) refresh.addEventListener("click", () => location.reload());

  function playing() {
    // Paused part-way counts too: a reload would lose the viewer's place.
    return [...document.querySelectorAll("video, audio")].some(
      (m) => !m.ended && (!m.paused || m.currentTime > 0)
    );
  }
  function changed(data) {
    // Only the keys the page was rendered with are compared (the server picks them).
    return Object.keys(shown).some((key) => JSON.stringify(data[key]) !== JSON.stringify(shown[key]));
  }
  async function poll() {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok) {
        const data = await response.json();
        if (changed(data)) {
          if (!playing()) {
            location.reload();
            return;
          }
          document.getElementById("new-results").hidden = false; // never interrupt playback
        }
        mode = data.poll === "off" ? "slow" : data.poll;
        if (!Object.hasOwn(POLL_MS, mode)) mode = "slow";
      }
    } catch (error) {
      // the app may be restarting; keep trying
    }
    window.setTimeout(poll, POLL_MS[mode]);
  }
  window.setTimeout(poll, POLL_MS[mode]);
})();
