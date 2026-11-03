(() => {
  const film = document.getElementById("film");
  const timeline = document.getElementById("timeline");
  const playhead = document.getElementById("playhead");
  const said = document.getElementById("said");
  const box = document.getElementById("captions");
  const rows = box ? [...box.children] : [];
  const duration = () => (Number.isFinite(film.duration) && film.duration) || Number(timeline.dataset.duration);
  const stamp = (at) => `${String(Math.floor(at / 60)).padStart(2, "0")}:${(at % 60).toFixed(2).padStart(5, "0")}`;
  let lit = null;

  function draw() {
    const at = film.currentTime;
    playhead.style.left = `${Math.min(at / duration(), 1) * 100}%`;
    const row = rows.find((item) => at >= Number(item.querySelector("[data-at]").dataset.at) && at < Number(item.dataset.end)) || null;
    if (row === lit) return;
    lit?.classList.remove("now");
    row?.classList.add("now");
    lit = row;
    if (row) box.scrollTop = row.offsetTop - box.offsetTop - box.clientHeight / 2;
  }

  function jump(at, text) {
    film.currentTime = Math.min(Math.max(at, 0), duration());
    said.textContent = text ? `${text}. Jumped to ${stamp(at)}.` : `Jumped to ${stamp(at)}.`;
    draw();
  }

  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-at]");
    if (target) {
      jump(Number(target.dataset.at), target.dataset.say);
    } else if (timeline.contains(event.target)) {
      const lanes = timeline.querySelector(".lanes").getBoundingClientRect();
      jump(((event.clientX - lanes.left) / lanes.width) * duration(), "");
    }
  });

  film.addEventListener("timeupdate", draw);
  film.addEventListener("seeked", draw);
  film.addEventListener("error", () => { said.textContent = "This browser could not play the film. It is at out/final.mp4."; }, true);

  const tick = () => {
    if (!film.paused) {
      draw();
      requestAnimationFrame(tick);
    }
  };
  film.addEventListener("play", tick);
})();
