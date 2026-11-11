(() => {
  // Text from a person or a model only ever reaches this page through textContent. Nothing here builds markup from a string.
  const data = JSON.parse(document.getElementById("data").textContent);
  const el = (id) => document.getElementById(id);
  const byId = new Map(data.versions.map((version) => [version.id, version]));
  const films = { a: el("film-a"), b: el("film-b") };
  const screens = { a: el("screen-a"), b: el("screen-b") };
  const captions = { a: el("cap-a"), b: el("cap-b") };
  const picks = { a: el("pick-a"), b: el("pick-b") };
  const ink = el("ink");
  const pen = ink.getContext("2d");
  const scrub = el("scrub");
  const served = document.querySelector('meta[name="altocue-serve"]');
  const token = served ? served.content : "";
  const storeKey = `altocue-versions-notes:${location.pathname}`;
  const MOST_STROKES = 60;
  const MOST_POINTS = 2000;
  const state = { now: 0, playing: false, last: 0, shift: 0, lead: "a", drawing: false, strokes: [], stroke: null, shown: null };
  let kept = Array.isArray(data.notes) ? data.notes.slice() : [];
  let mine = [];

  const stamp = (at) => {
    const tenths = Math.round(Math.max(Number(at) || 0, 0) * 10);
    return `${Math.floor(tenths / 600)}:${((tenths % 600) / 10).toFixed(1).padStart(4, "0")}`;
  };
  const say = (text) => { el("said").textContent = text; };
  const chosen = (side) => byId.get(picks[side].value) || null;
  const active = () => (chosen("b") ? ["a", "b"] : ["a"]);
  const loaded = (side) => Boolean(films[side].getAttribute("src"));
  const lengthOf = (side) => {
    const version = chosen(side);
    return (Number.isFinite(films[side].duration) && films[side].duration) || (version && version.duration) || 0;
  };
  const shiftOf = (side) => (side === "b" ? state.shift : 0);
  const total = () => Math.max(0, ...active().map((side) => lengthOf(side) - shiftOf(side)));

  // Two films, one clock. The film whose sound is on leads while it plays, and the other is held to it.

  function load(side) {
    const version = chosen(side);
    const film = films[side];
    screens[side].hidden = !version;
    if (!version) {
      film.pause();
      film.removeAttribute("src");
      film.dataset.version = "";
      film.load();
      return;
    }
    screens[side].style.setProperty("--shape", String(version.shape || 0.5625));
    captions[side].textContent = version.film ? `${version.id}, ${stamp(version.duration)} long` : `${version.id} kept no film`;
    if (film.dataset.version === version.id) return;
    film.dataset.version = version.id;
    if (version.film) film.src = version.film;
    else film.removeAttribute("src");
    film.load();
  }

  function sync(side) {
    const film = films[side];
    if (!chosen(side) || !loaded(side)) return;
    const end = lengthOf(side);
    const want = state.now + shiftOf(side);
    if (want >= 0 && want < end - 0.03) {
      if (Math.abs(film.currentTime - want) > (state.playing ? 0.3 : 0.01)) film.currentTime = want;
      if (state.playing && film.paused) film.play().catch(() => {});
    } else {
      if (!film.paused) film.pause();
      const hold = Math.min(Math.max(want, 0), Math.max(end - 0.03, 0));
      if (!state.playing && Math.abs(film.currentTime - hold) > 0.01) film.currentTime = hold;
    }
    if (!state.playing && !film.paused) film.pause();
  }

  function show() {
    const end = total();
    scrub.max = end.toFixed(2);
    scrub.value = String(state.now);
    el("clock").textContent = `${stamp(state.now)} / ${stamp(end)}`;
    el("note-time").textContent = stamp(state.now);
    el("note-version").textContent = picks.a.value;
    el("play").textContent = state.playing ? "Pause" : "Play";
  }

  function sound() {
    if (!chosen(state.lead)) state.lead = "a";
    films.a.muted = state.lead !== "a";
    films.b.muted = state.lead !== "b";
    el("sound").hidden = !chosen("b");
    el("sound").textContent = `Sound: ${picks[state.lead].value}`;
  }

  function pause() {
    state.playing = false;
    active().forEach(sync);
    show();
  }

  function tick(time) {
    if (!state.playing) return;
    const lead = films[state.lead];
    const held = lead.paused || lead.ended || !loaded(state.lead);
    if (!held && lead.readyState > 2 && !lead.seeking) state.now = lead.currentTime - shiftOf(state.lead);
    else if (held) state.now += (time - state.last) / 1000;
    state.last = time;
    if (state.now >= total() - 0.03) {
      state.now = total();
      pause();
      return;
    }
    active().forEach(sync);
    show();
    requestAnimationFrame(tick);
  }

  function play() {
    if (!active().some(loaded)) {
      say("This version kept no film to play.");
      return;
    }
    if (state.now >= total() - 0.05) state.now = 0;
    state.playing = true;
    state.shown = null;
    state.last = performance.now();
    paint();
    active().forEach(sync);
    show();
    requestAnimationFrame(tick);
  }

  function seek(at, text) {
    state.now = Math.min(Math.max(Number(at) || 0, 0), total() || Number(at) || 0);
    state.shown = null;
    active().forEach(sync);
    show();
    paint();
    if (text) say(text);
  }

  function choose(a, b) {
    if (a !== undefined && byId.has(a)) picks.a.value = a;
    if (b !== undefined) picks.b.value = b && byId.has(b) ? b : "";
    if (picks.b.value === picks.a.value) picks.b.value = "";
    if (!chosen("b")) state.shift = 0;
    load("a");
    load("b");
    document.body.classList.toggle("comparing", Boolean(chosen("b")));
    sound();
    fit();
    seek(state.now);
  }

  // Drawing on the frame. Points are kept as shares of the picture, so a stroke means the same on any screen.

  function fit() {
    const box = ink.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    ink.width = Math.max(1, Math.round(box.width * ratio));
    ink.height = Math.max(1, Math.round(box.height * ratio));
    paint();
  }

  function trace(strokes) {
    const wide = ink.width;
    const tall = ink.height;
    const weight = Math.max(3, Math.max(wide, tall) / 160);
    pen.lineCap = "round";
    pen.lineJoin = "round";
    for (const [colour, extra] of [["#080d13", 4], ["#ffb454", 0]]) {
      pen.strokeStyle = colour;
      pen.lineWidth = weight + extra;
      for (const stroke of strokes) {
        if (!Array.isArray(stroke) || !stroke.length) continue;
        pen.beginPath();
        stroke.forEach((point, index) => {
          if (index) pen.lineTo(point[0] * wide, point[1] * tall);
          else pen.moveTo(point[0] * wide, point[1] * tall);
        });
        if (stroke.length === 1) pen.lineTo(stroke[0][0] * wide + 0.1, stroke[0][1] * tall);
        pen.stroke();
      }
    }
  }

  function paint() {
    pen.clearRect(0, 0, ink.width, ink.height);
    if (Array.isArray(state.shown)) trace(state.shown);
    trace(state.stroke ? [...state.strokes, state.stroke] : state.strokes);
    el("undo").hidden = !state.strokes.length;
  }

  function drawing(on) {
    state.drawing = on;
    document.body.classList.toggle("drawing", on);
    el("draw").setAttribute("aria-pressed", String(on));
    if (on) {
      if (state.playing) pause();
      state.shown = null;
      paint();
      say("Draw on the left film. Each line is kept with the note.");
    }
  }

  function point(event) {
    const box = ink.getBoundingClientRect();
    const share = (value) => Math.round(Math.min(Math.max(value, 0), 1) * 10000) / 10000;
    return [share((event.clientX - box.left) / box.width), share((event.clientY - box.top) / box.height)];
  }

  function lift() {
    if (!state.stroke) return;
    state.strokes.push(state.stroke);
    state.stroke = null;
    paint();
  }

  ink.addEventListener("pointerdown", (event) => {
    if (!state.drawing || state.strokes.length >= MOST_STROKES) return;
    event.preventDefault();
    ink.setPointerCapture(event.pointerId);
    state.stroke = [point(event)];
    paint();
  });
  ink.addEventListener("pointermove", (event) => {
    if (!state.stroke || state.stroke.length >= MOST_POINTS) return;
    const next = point(event);
    const last = state.stroke[state.stroke.length - 1];
    if (Math.hypot(next[0] - last[0], next[1] - last[1]) < 0.004) return;
    state.stroke.push(next);
    paint();
  });
  ink.addEventListener("pointerup", lift);
  ink.addEventListener("pointercancel", lift);

  // Notes. Those in the project came with the page. Those made here wait in this browser until they are handed on.

  const fits = (note) =>
    note && typeof note === "object" && typeof note.version === "string" && Number.isFinite(note.at) && typeof note.text === "string" && Array.isArray(note.strokes);
  const same = (one, other) => one.version === other.version && Math.abs(one.at - other.at) < 0.0015 && one.text === other.text && one.created === other.created;

  function readMine() {
    try {
      const found = JSON.parse(localStorage.getItem(storeKey) || "[]");
      return Array.isArray(found) ? found.filter(fits).filter((note) => !kept.some((other) => same(other, note))) : [];
    } catch (error) {
      return [];
    }
  }

  function writeMine() {
    try {
      localStorage.setItem(storeKey, JSON.stringify(mine));
    } catch (error) {
      say("This browser would not keep the note. Copy or download your notes before you close the page.");
    }
  }

  function openNote(note) {
    choose(note.version);
    state.shift = 0;
    seek(note.at, `Note at ${stamp(note.at)} on ${note.version}.`);
    state.shown = note.strokes;
    paint();
  }

  function noteRow(note, here) {
    const row = document.createElement("li");
    row.className = note.resolved_in ? "note done" : "note";
    const go = document.createElement("button");
    go.type = "button";
    go.className = "jump";
    go.textContent = stamp(note.at);
    go.addEventListener("click", () => openNote(note));
    const body = document.createElement("div");
    const meta = document.createElement("p");
    meta.className = "meta";
    const where = here ? "only in this browser so far" : note.resolved_in ? `resolved in ${note.resolved_in}` : "open";
    meta.textContent = [note.id, note.version, where, note.strokes.length ? "with a drawing" : ""].filter(Boolean).join(", ");
    const words = document.createElement("p");
    words.className = "words";
    words.textContent = note.text || "A drawing with no words.";
    body.append(meta, words);
    if (here) {
      const drop = document.createElement("button");
      drop.type = "button";
      drop.className = "act";
      drop.textContent = "Remove";
      drop.addEventListener("click", () => {
        mine = mine.filter((other) => other !== note);
        writeMine();
        listNotes();
      });
      body.append(drop);
    }
    row.append(go, body);
    return row;
  }

  function listNotes() {
    const order = (one, other) => other.version.localeCompare(one.version) || one.at - other.at;
    const rows = [...kept.filter(fits).sort(order).map((note) => noteRow(note, false)), ...mine.slice().sort(order).map((note) => noteRow(note, true))];
    el("note-list").replaceChildren(...rows);
    el("no-notes").hidden = rows.length > 0;
  }

  function send(notes) {
    return fetch("notes", { method: "POST", headers: { "Content-Type": "application/json", "X-Altocue-Token": token }, body: JSON.stringify({ notes }) })
      .then((answer) => answer.json().then((body) => ({ ok: answer.ok, body })))
      .then(({ ok, body }) => {
        if (!ok) throw new Error(body.error || "The project refused the note.");
        kept = body.notes;
        listNotes();
        say(body.added.length ? `Saved in the project as ${body.added.join(", ")}.` : "The project already had that note.");
      })
      .catch((error) => {
        mine.push(...notes);
        writeMine();
        listNotes();
        say(`${error.message} The note is kept in this browser for now.`);
      });
  }

  el("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = el("note-text").value.trim();
    if (!chosen("a")) return;
    if (!text && !state.strokes.length) {
      say("Type a note or draw on the frame first.");
      return;
    }
    if (state.playing) pause();
    const note = { version: picks.a.value, at: Math.round(state.now * 1000) / 1000, end: null, text, strokes: state.strokes, author: "user", created: new Date().toISOString() };
    state.strokes = [];
    el("note-text").value = "";
    drawing(false);
    paint();
    if (token) {
      send([note]);
      return;
    }
    mine.push(note);
    writeMine();
    listNotes();
    say(`Note added at ${stamp(note.at)} on ${note.version}. Copy or download your notes to hand them on.`);
  });

  const payload = () => JSON.stringify({ altocue_notes: 1, project: data.project, notes: mine }, null, 2);
  const counted = () => `${mine.length} note${mine.length === 1 ? "" : "s"}`;

  el("copy").addEventListener("click", () => {
    if (!mine.length) {
      say("No notes are waiting in this browser.");
      return;
    }
    const text = payload();
    const done = () => say(`Copied ${counted()}. Paste them to your agent, or into a file for altocue versions notes --ingest.`);
    const byHand = () => {
      const box = el("export");
      box.hidden = false;
      box.value = text;
      box.focus();
      box.select();
      let copied = false;
      try {
        copied = document.execCommand("copy");
      } catch (error) {
        copied = false;
      }
      if (copied) {
        box.hidden = true;
        done();
      } else say("This browser would not copy for you. The notes are in the box under the buttons: copy them by hand.");
    };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, byHand);
    else byHand();
  });

  el("download").addEventListener("click", () => {
    if (!mine.length) {
      say("No notes are waiting in this browser.");
      return;
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([payload()], { type: "application/json" }));
    link.download = "altocue-notes.json";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    say(`Downloaded ${counted()} as altocue-notes.json. Give the file to your agent, or run altocue versions notes --ingest with it.`);
  });

  // The controls, and every change in the lists that carries a time.

  el("play").addEventListener("click", () => (state.playing ? pause() : play()));
  el("sound").addEventListener("click", () => {
    state.lead = state.lead === "a" && chosen("b") ? "b" : "a";
    sound();
  });
  el("draw").addEventListener("click", () => drawing(!state.drawing));
  el("undo").addEventListener("click", () => {
    state.strokes.pop();
    paint();
  });
  scrub.addEventListener("input", () => {
    state.shift = 0;
    seek(Number(scrub.value));
  });
  picks.a.addEventListener("change", () => {
    state.shift = 0;
    choose();
  });
  picks.b.addEventListener("change", () => {
    state.shift = 0;
    choose();
  });

  document.addEventListener("click", (event) => {
    const go = event.target.closest("button.go");
    if (!go) return;
    const version = byId.get(go.dataset.version);
    if (!version) return;
    const at = go.dataset.at === undefined ? null : Number(go.dataset.at);
    const was = go.dataset.was === undefined ? null : Number(go.dataset.was);
    if (at === null && was === null) {
      state.shift = 0;
      choose(version.id);
      say(`Showing ${version.id}.`);
    } else if (at !== null) {
      choose(version.id);
      const beside = chosen("b") && picks.b.value === version.previous && was !== null;
      state.shift = beside ? was - at : 0;
      seek(at, beside ? `${go.dataset.say}. ${version.id} is at ${stamp(at)} and ${version.previous} at ${stamp(was)}.` : `${go.dataset.say}. Jumped to ${stamp(at)} in ${version.id}.`);
    } else {
      const before = byId.get(version.previous);
      state.shift = 0;
      choose(version.id, before && before.film ? before.id : undefined);
      seek(was, `${go.dataset.say}. It was at ${stamp(was)} in ${version.previous}.`);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.target.closest("input, textarea, select, button") || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === " ") {
      event.preventDefault();
      if (state.playing) pause();
      else play();
    } else if (event.key === "ArrowLeft") seek(state.now - 1);
    else if (event.key === "ArrowRight") seek(state.now + 1);
  });

  for (const side of ["a", "b"]) {
    films[side].addEventListener("loadedmetadata", () => {
      fit();
      sync(side);
      show();
    });
    films[side].addEventListener("error", () => {
      const version = chosen(side);
      if (version && version.film) say(`This browser could not play ${version.id}. Its film is at versions/${version.film}.`);
    });
  }
  window.addEventListener("resize", fit);

  mine = readMine();
  if (token) {
    el("hand-on").hidden = true;
    el("how").textContent = "This page is served from your machine, so each note is saved straight into the project.";
    if (mine.length) {
      const waiting = mine;
      mine = [];
      writeMine();
      send(waiting);
    }
  }
  choose(data.first || (data.versions.length ? data.versions[data.versions.length - 1].id : undefined), "");
  listNotes();
})();
