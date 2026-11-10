"""The glass kit: an optional example look for `altocue shorts`, built on Shorts Studio.

A light canvas with a brand row, a Geist headline top-left, frosted glass cards and generated glass objects in the
top band, the caption centred between the graphics and the presenter, the speaker in a rounded aperture in the bottom
half, a full light-canvas graphic over every moment the speaker looks off the lens, and an end card that asks for a
comment keyword. No grain.

It is one studio's house look, shipped as an example. It is never the default: a project's own brand kit is. Copy
this folder, make house.json yours (brand name, logo, colours, sounds), then point `[shorts] kit` at your copy.

Run it from the run folder, after Shorts Studio's measure step and first scaffold, with a build.py such as:

    from glass_kit import Reel
    r = Reel("Reel name", "KEYWORD")
    r.require()                      # every look-away from altocue-shorts.json must end up under a full graphic
    r.beat("b1", 0.4, 3.0, '<div class="hl">One idea per card</div>')
    r.hide("h1", r.w("covered"), r.w("covered") + 2, '<div class="hl big">A full graphic</div>' + r.ob("check", "", "left:380px;top:700px;width:320px;height:320px"))
    r.end_card(r.END - 3)
    r.write()

Set ALTOCUE_KIT_LINT=1 while you build, and the composition reports layout problems through console.error: text
outside x 56 to 1000, text under y 1500 in a full graphic, a card outside x 40 to 1010, or a glass object over more
than 15% of any text. Leave it unset for the final render.
"""
import json
import os
import re
from pathlib import Path

FPS = 30
COL_L, COL_R = 72, 882          # content column, clear of the platform's right-hand buttons
COL_C = (COL_L + COL_R) // 2
AP_TOP, AP_L, AP_R, AP_RAD = 1092, 30, 1050, 90
EPS = 0.0004                    # starts sit a hair before their frame, so no frame falls between two scenes

COLOURS = {"canvas": "#F3F7FA", "ink": "#142027", "soft": "#64716D", "green": "#267052", "mint": "#B5F4D9", "edge": "#D7E1DE"}
TYPING = [["keyboard-short-01", 0.6], ["keyboard-01", 2.1], ["keyboard-02", 2.3], ["keyboard-04", 5.2]]


def fr(s):
    return round(s * FPS) / FPS


def read(name, default=None):
    path = Path(name)
    return json.loads(path.read_text()) if path.exists() else default


class Reel:
    def __init__(self, reel_name, keyword, subline="Get the setup guide", shift=None):
        self.house = read("house.json", {})
        brand = self.house.get("brand", {})
        self.brand_name = brand.get("name", "")
        self.logo = brand.get("logo", "")
        self.colours = {**COLOURS, **self.house.get("colors", {})}
        self.typing_sounds = self.house.get("typing_sounds", TYPING)
        # shift: how far the cutout moves down, so the top of the head stays below the caption line (y 842 to 918)
        self.shift = shift if shift is not None else int(self.house.get("frame", {}).get("presenter", {}).get("plate_shift_y", 300))
        self.T = json.load(open("timeline.json"))
        self.END = self.T["master_frames"] / FPS
        self.words = self.T["caption_words"]
        self.takes = self.T["takes"]
        self.record_in = read("altocue-shorts.json", {})
        # where each take starts in the cut, so a cut second maps onto the reel. plate-map.json, from a studio's own
        # prep, can say the same in plate seconds.
        self.plan = {t["id"]: {"plate_from": float(t.get("from", 0.0))} for t in self.record_in.get("takes", [])}
        self.plan.update({p["id"]: p for p in read("plate-map.json", [])})
        self.sound_files = {s["name"]: s["file"] for s in self.record_in.get("sounds", [])}
        self.name, self.keyword, self.subline = reel_name, keyword, subline
        self.html, self.sfx, self.hidden, self.beats, self.js = [], [], [], [], []
        self.required, self.blinks = [], []
        self.punches = []
        self.sfx_dur = {}
        self.scene_cls = {}

    # time ------------------------------------------------------------------
    def w(self, text, n=0):
        """Reel second of the nth time a word is spoken."""
        hits = [x for x in self.words if x["word"].strip(".,!?:;\"").lower() == text.lower()]
        if len(hits) <= n:
            raise SystemExit(f'"{text}" is spoken {len(hits)} times, so there is no number {n + 1}')
        return hits[n]["start"]

    def _from(self, tk):
        return self.plan.get(tk["id"], {}).get("plate_from", 0.0) + tk.get("source_from", 0.0)

    def master(self, plate_t):
        """Cut seconds to reel seconds (None if that moment was trimmed out)."""
        for tk in self.takes:
            src0 = self._from(tk)
            if src0 - 1e-6 <= plate_t <= src0 + tk["timeline_duration"] + 1e-6:
                return tk["timeline_start"] + (plate_t - src0)
        return None

    def master_or_next(self, plate_t):
        """Cut seconds to reel seconds; a moment that was trimmed out maps to the start of the next take."""
        m = self.master(plate_t)
        if m is not None:
            return m
        later = [tk["timeline_start"] for tk in self.takes if self._from(tk) >= plate_t - 1e-6]
        return min(later) if later else None

    def plate(self, t):
        """Reel seconds back to cut seconds, as altocue-shorts.json keeps its covers."""
        for tk in self.takes:
            if tk["timeline_start"] - 1e-6 <= t <= tk["timeline_start"] + tk["timeline_duration"] + 1e-6:
                return self._from(tk) + (t - tk["timeline_start"])
        return None

    def cover_span(self, a, b):
        """A look-away in cut seconds, mapped onto the reel and padded; clipped to what survived the trim."""
        pts = [self.master(a + k * (b - a) / 20) for k in range(21)]
        pts = [p for p in pts if p is not None]
        if not pts:
            return None
        return (max(0.0, min(pts) - 0.12), max(pts) + 0.12)

    # building ----------------------------------------------------------------
    def sound(self, t, name, vol=0.2, dur=1.6):
        """A sound from the run's pack (sfx-pack.json), by name, at reel second t."""
        self.sfx.append((fr(t), name, vol))
        self.sfx_dur[(fr(t), name)] = dur

    def typing(self, t, text, cps=26, vol=0.13):
        """Key clicks under a typed line: from reel second t, for as long as text takes at cps characters a second.
        Use the same t and cps as the k.typed() call that draws it. Every typing animation gets typing sounds."""
        seconds = max(0.35, len(text) / cps)
        name, longest = next(((n, s) for n, s in self.typing_sounds if seconds <= s), self.typing_sounds[-1])
        self.sound(t, name, vol, round(min(seconds + 0.05, longest), 3))

    def hide(self, sid, a, b, inner, cls=""):
        """A full light-canvas graphic with no presenter, reel seconds. Held at least max(1.2 s, 0.3 s + words/5)."""
        txt = re.sub(r'<(div|span)[^>]*class="(?:dl|ds|tag[^"]*|lock|label[^"]*|cap-k|cdl)"[^>]*>[^<]*</\1>', " ", inner)
        txt = re.sub(r"&\w+;", " ", txt)   # labels on diagrams and arrows are not read, so they do not lengthen the hold
        n = len(re.sub(r"<[^>]+>", " ", txt).split())
        b = max(b, a + max(1.2, 0.3 + n / 5))
        self.scene_cls[sid] = cls
        self.hidden.append((sid, fr(a), fr(min(b, self.END)), inner))

    def beat(self, bid, a, b, inner, js=""):
        """A top-band graphic, reel seconds. Shows above the presenter in cutout scenes."""
        if b - a < 0.3:
            return
        self.beats.append((bid, fr(a), fr(b), inner))
        if js:
            self.js.append(f"k.beat('{bid}', (t, h) => {{ {js} }});")

    def _presenter_scenes(self):
        """Presenter scenes fill every moment no full graphic owns; each holds the takes it overlaps. With a cutout
        (media/<take>-cutout.webm with alpha) the head breaks above the aperture; without one the take fills it."""
        cuts = sorted([(a, b) for _, a, b, _ in self.hidden])
        free, t = [], 0.0
        for a, b in cuts:
            if a > t + 1e-3:
                free.append((t, a))
            t = max(t, b)
        if t < self.END - 1e-3:
            free.append((t, self.END))
        out = []
        for i, (a, b) in enumerate(free):
            imgs = []
            for tk in self.takes:
                ta, tb = tk["timeline_start"], tk["timeline_start"] + tk["timeline_duration"]
                lo, hi = max(a, ta), min(b, tb)
                if hi - lo < 1 / FPS:
                    continue
                s, d = lo - ta, hi - lo
                attrs = f'data-take="{tk["id"]}" data-take-start="{s:.4f}" data-take-duration="{d:.4f}"'
                picture = f'media/{tk["id"]}-picture.mp4' if Path(f'composition/media/{tk["id"]}-picture.mp4').exists() else f'media/takes/{tk["id"]}.mp4'
                imgs.append(f'<div class="roomwrap"><img class="room" alt="" data-video="{picture}" {attrs}></div>')
                if Path(f'composition/media/{tk["id"]}-cutout.webm').exists():
                    imgs.append(f'<div class="subjwrap"><img class="subj" alt="" data-video="media/{tk["id"]}-cutout.webm" {attrs}></div>')
            out.append((f"me{i:02d}", fr(a), fr(b), "".join(imgs)))
        return out

    def end_card(self, a):
        mark = f'<div class="endmark"><img alt="" src="{self.logo}"></div>' if self.logo else ""
        self.hide("end", a, self.END,
                  '<div class="pillname">' + self.name + '</div>' + mark +
                  f'<div class="endcard"><div class="e1">Comment</div><div class="e2">{self.keyword}</div><div class="e3">{self.subline}</div></div>')
        self.js.append("""k.beat('s-end', (t, h) => {
  const m = $(h, '.endmark'); if (m) { const p = k.settle(t, 0.05, 0.7); m.style.opacity = k.clamp(t / 0.15); m.style.transform = `translateY(${(1 - p) * 40}px) scale(${0.86 + 0.14 * p})`; }
  const c = $(h, '.endcard'); const q = k.span(t, 0.35, 0.5, k.ease.expo); c.style.opacity = q; c.style.transform = `translateY(${(1 - q) * 30}px)`;
  $(h, '.pillname').style.opacity = k.clamp(t / 0.2);
});""")

    def punch(self, t, scale=1.12, ease=0.22):
        """Push in on the speaker at reel second t (a word that matters), held until the next take or punch."""
        self.punches.append((fr(t), scale, ease))

    def camera_keys(self):
        """The speaker's framing over time: [reel second, scale, ease seconds]. Each take gets its own size, so
        neighbours differ clearly and every join reads as a punch-in. plate-cuts.json, from a studio's own prep, can
        name joins inside a take. A punch from the build pushes in a little more on a word and holds to the next join."""
        cuts = []
        pc = read("plate-cuts.json")
        if pc:
            for b, sc in zip([0.0] + pc["cuts"], pc["scale"], strict=False):
                m = 0.0 if b == 0 else self.master_or_next(b + 1e-3)
                if m is not None:
                    cuts.append((round(max(0.0, m - 1e-3), 4), sc))
            if not cuts or cuts[0][0] > 0:
                cuts.insert(0, (0.0, pc["scale"][0]))
        if len(cuts) < 2:
            cuts = [(round(tk["timeline_start"], 4), 1.09 if n % 2 else 1.0) for n, tk in enumerate(self.takes)]
        events = sorted([(t, 0, sc, 0) for t, sc in cuts] + [(t, 1, sc, e) for t, sc, e in self.punches])
        keys, base = [], 1.0
        for t, kind, sc, e in events:
            if kind == 0:
                base = sc
                keys.append((t, sc, 0))
            else:
                keys.append((t, round(min(1.24, base * (1 + (sc - 1) * 0.5)), 4), e))
        return keys

    def ob(self, name, cls, style):
        """A glass object from the set (media/glass/<name>.webp), placed by its own style."""
        return f'<div class="ob {cls}" style="position:absolute;{style}"><img alt="" src="media/glass/{name}.webp"></div>'

    def span(self, a, b):
        """Grow a full-graphic span so every look-away it touches sits wholly inside it, and land its edges on joins."""
        for _ in range(3):
            for ra, rb in self.required:
                if ra < b and rb > a:
                    a, b = min(a, ra - 0.05), max(b, rb + 0.05)
        # A full graphic starts and ends on a join in the footage when one is near, or a new line shows for a few
        # frames before the graphic lands, or the speaker comes back mid-line...
        joins = self.joins()
        # ...but never onto a join inside another look-away: that edge would cut the moment in two, and no single
        # graphic would hold it.
        def inside(t):
            return any(ra + 0.02 < t < rb - 0.02 or abs(t - ra) <= 0.02 for ra, rb in self.required if not (a <= ra and rb <= b))

        before = [j for j in joins if a - 0.7 <= j <= a + 0.02]
        if before and not inside(max(before)):
            a = max(before)
        after = [j for j in joins if b - 0.02 <= j <= b + 0.7]
        if after and min(after) < self.END - 1.0 and not inside(min(after)):
            b = min(after)
        for ba, bb in self.blinks:
            if b - 0.05 <= ba <= b + 0.25:      # back with the eyes still down: hold until they are up
                b = max(b, bb + 0.07)
            if a - 0.25 <= bb <= a + 0.05:      # the eyes drop just before the graphic: the graphic gets there first
                a = min(a, ba - 0.05)
        return max(0.0, a), min(self.END, b)

    def joins(self):
        """Reel seconds of the joins in the footage: where one take hands over to the next."""
        pc = read("plate-cuts.json")
        if not pc:
            return [round(tk["timeline_start"], 4) for tk in self.takes[1:]]
        out = []
        for c in pc["cuts"]:
            m = self.master_or_next(c + 1e-3)
            if m is not None:
                out.append(round(max(0.0, m - 1e-3), 4))
        return out

    def tiles(self, items, sel=None):
        """Five (or fewer) glass tiles, each with its own glass object, a number and a label. items: [(label, object)]."""
        w = min(150, (810 - 14 * (len(items) - 1)) // len(items))
        out = ""
        for i, (label, name) in enumerate(items):
            out += (f'<div class="card gtile{" sel" if sel == i else ""}" style="left:{i * (w + 14)}px;top:20px;width:{w}px;height:250px">'
                    f'<div class="ob"><img alt="" src="media/glass/{name}.webp"></div><div class="cap-k">0{i + 1}</div><div class="tl">{label}</div></div>')
        return out

    def deck(self, items, blur_last=True):
        """Five big glass cards for a full graphic: three above, two below, the last one frosted and out of focus."""
        pos = [(0, 0), (312, 0), (624, 0), (156, 440), (468, 440)]
        out = '<div class="deck">'
        for i, (label, name) in enumerate(items):
            x, y = pos[i]
            last = blur_last and i == len(items) - 1
            out += (f'<div class="card dcard{" last" if last else ""}" style="left:{x}px;top:{y}px"><div class="ob"><img alt="" src="media/glass/{name}.webp"></div>'
                    f'<div class="cap-k">0{i + 1}</div><div class="tl">{label}</div>{"<i class=frost></i>" if last else ""}</div>')
        return out + '</div>'

    def require(self, spans=None):
        """The look-aways every full graphic must hold, in cut seconds. By default the covers `altocue shorts prepare`
        wrote to altocue-shorts.json, from `altocue shorts gaze`. Blinks come from gaze.json, in the run or the project:
        a blink is no look-away, but one at the edge of a graphic is the speaker looking up from notes, so the graphic
        holds until the eyes are up."""
        spans = [(float(s["start"]), float(s["end"])) for s in self.record_in.get("covers", [])] if spans is None else [tuple(x) for x in spans]
        project = self.record_in.get("project", "")
        gaze = read("gaze.json") or (read(str(Path(project) / "gaze.json")) if project else None) or {}
        self.blinks = [s for s in (self.cover_span(a, b) for a, b in gaze.get("blinks", [])) if s]
        spans.sort()
        merged = []
        for a, b in spans:
            if merged and a <= merged[-1][1] + 0.15:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        self.required = [s for s in (self.cover_span(a, b) for a, b in merged) if s]

    def _cover_rest(self):
        for i, (a, b) in enumerate(self.required):
            if any(ha - 0.02 <= a and b <= hb + 0.02 for _, ha, hb, _ in self.hidden) or self._through(self.hidden, a, b):
                continue
            beat = next((x for x in self.beats if x[1] <= a < x[2]), None)
            m = re.search(r'<div class="hl">(.*?)</div>', beat[3]) if beat else None
            head = m.group(1) if m else ""
            self.hidden.append((f"cover{i}", fr(a - 0.1), fr(b + 0.1), f'<div class="hl big">{head}</div>'))
            print("look-away cover added", round(a, 2), round(b, 2))

    def record(self):
        """Write the covers back to altocue-shorts.json, so `altocue shorts check` holds this run to the house rules.
        A look-away that runs through two graphics in a row is kept as one piece per graphic, in cut seconds."""
        if not Path("altocue-shorts.json").exists():
            return
        covers = []
        for a, b in getattr(self, "cover_pieces", self.required):
            pa, pb = self.plate(a + 1e-4), self.plate(b - 1e-4)
            if pa is not None and pb is not None and pb - pa > 0.02:
                covers.append({"start": round(pa, 3), "end": round(pb, 3), "reason": "look-away"})
        Path("altocue-shorts.json").write_text(json.dumps({**self.record_in, "covers": covers}, indent=1))

    def _through(self, scenes, ra, rb):
        """The pieces of a look-away inside each full graphic, if graphics that follow each other hold all of it."""
        t, pieces = ra, []
        for _, a, b, _ in sorted(scenes, key=lambda h: h[1]):
            if a - 0.03 <= t < b:
                pieces.append((max(ra, a), min(rb, b)))
                t = b
            if t >= rb - 0.03:
                return pieces
        return None

    def write(self):
        self._cover_rest()
        # close gaps under a second between full graphics, so the speaker never flashes on screen
        self.hidden.sort(key=lambda h: h[1])
        for i in range(len(self.hidden) - 1):
            sid, a, b, inner = self.hidden[i]
            na = self.hidden[i + 1][1]
            if 0 < na - b < 1.0:
                self.hidden[i] = (sid, a, na, inner)
        # no overlapping full graphics. The earlier one hands over where the later starts, unless that would bare a
        # look-away the earlier one holds; then the later one starts where the earlier ends.
        self.hidden.sort(key=lambda h: h[1])
        req = self.required
        def own(a, b):
            return [(ra, rb) for ra, rb in req if a - 0.02 <= ra and rb <= b + 0.02]

        fixed = []
        for sid, a, b, inner in self.hidden:
            if fixed and a < fixed[-1][2]:
                psid, pa, pb, pin = fixed[-1]
                if all(rb <= a + 0.02 for _, rb in own(pa, pb)) and a - pa >= 1.2:
                    fixed[-1] = (psid, pa, fr(a), pin)
                else:
                    a = pb
                    if b - a < 1.2:
                        b = a + 1.2
            fixed.append((sid, fr(a), fr(min(b, self.END)), inner))
        # every look-away sits wholly inside one full graphic (the check counts nothing less)
        for ra, rb in req:
            if any(a - 0.02 <= ra and rb <= b + 0.02 for _, a, b, _ in fixed) or self._through(fixed, ra, rb):
                continue
            i = next((i for i, (_, a, b, _) in enumerate(fixed) if a - 0.02 <= ra < b), None)
            if i is None:
                raise SystemExit(f"look-away {ra:.2f} to {rb:.2f} s has no full graphic")
            sid, a, b, inner = fixed[i]
            fixed[i] = (sid, a, fr(rb + 0.05), inner)
            for j in range(i + 1, len(fixed)):
                s2, a2, b2, in2 = fixed[j]
                if a2 < fixed[j - 1][2]:
                    fixed[j] = (s2, fixed[j - 1][2], max(b2, fixed[j - 1][2] + 1.2), in2)
        self.hidden = fixed
        # the check wants each cover inside one full graphic: a look-away that runs through several is recorded piece by piece
        self.cover_pieces = []
        for ra, rb in req:
            self.cover_pieces += self._through(fixed, ra, rb) or [(ra, rb)]
        self.record()
        share = sum(b - a for _, a, b, _ in fixed) / self.END
        print("required", [(round(a, 2), round(b, 2)) for a, b in req])
        print("hidden", [(s, round(a, 2), round(b, 2)) for s, a, b, _ in fixed], f"share {share:.0%}")
        for sid, a, b, _ in fixed:   # a bad word lookup shows up as an empty or runaway full graphic: stop here, not in review
            if sid != "end" and not (0.5 <= b - a <= 10.0):
                raise SystemExit(f"full graphic {sid} runs {b - a:.2f} s ({a:.2f} to {b:.2f}); check its word lookups")
            if sid == "end" and b - a < 1.5:
                raise SystemExit(f"end card only {b - a:.2f} s; something before it runs too long")
        # at most two sounds in any one second
        kept = []
        for t, n, v in sorted(self.sfx):
            if sum(1 for u, _, _ in kept if t - u < 1.0) < 2:
                kept.append((t, n, v))
        self.sfx = kept
        scenes = [(sid, "hidden", a, b, inner) for sid, a, b, inner in self.hidden] + \
                 [(sid, "cutout", a, b, inner) for sid, a, b, inner in self._presenter_scenes()]
        scenes.sort(key=lambda s: s[2])
        html = []
        for sid, lay, a, b, inner in scenes:
            bg = '<div class="canvas"></div>' if lay == "hidden" else ""
            if lay == "hidden" and sid != "end":
                inner = f'<div class="hmove">{inner}</div>'
            html.append(f'<section id="s-{sid}" class="scene {lay} {self.scene_cls.get(sid, "")}" data-layout="{lay}" data-start="{max(0, a - EPS):.5f}" data-duration="{b - a:.5f}">{bg}{inner}</section>\n')
        for bid, a, b, inner in self.beats:
            html.append(f'<div id="{bid}" class="beat" data-start="{max(0, a - EPS):.5f}" data-duration="{b - a:.5f}">{inner}</div>\n')
        caps = [[a, b] for sid, lay, a, b, _ in scenes if lay == "cutout"]
        logo = f'<img alt="" src="{self.logo}">' if self.logo else ""
        brand = (f'<div id="brand" class="brand" data-start="0" data-duration="{self.END:.4f}">{logo}'
                 f'<b>{self.brand_name}</b><span class="rname">{self.name}</span></div>\n')
        cap = f'<div id="captions" class="caps" data-start="0" data-duration="{self.END:.4f}"><div class="capline"></div></div>\n'
        missing = sorted({n for _, n, _ in self.sfx if n not in self.sound_files})
        if missing and self.sound_files:
            raise SystemExit(f"no sound named {', '.join(missing)} in the run's pack (sfx-pack.json)")
        audio = "".join(f'<audio id="sfx-{i:02d}" src="{self.sound_files.get(n, f"media/sfx/{n}.wav")}" data-start="{t:.4f}" data-duration="{self.sfx_dur.get((t, n), 1.6)}" data-media-start="0" data-volume="{v}"></audio>\n'
                        for i, (t, n, v) in enumerate(self.sfx))
        idx = Path("composition/index.html").read_text()
        base_end = "<!-- reels:base:end -->"
        head, _ = idx.split(base_end, 1)
        if "/* glass kit */" in head:
            a, b = head.index("<style>/* glass kit */"), head.index("</head>")
            head = head[:a] + head[b:]
        s = self.shift
        plate = (f"<style>.subjwrap{{top:{s}px;height:{1920 - s}px}}.room{{top:{s - AP_TOP}px}}</style>\n")
        c = self.colours
        palette = f"<style>:root{{--bg:{c['canvas']};--ink:{c['ink']};--soft:{c['soft']};--green:{c['green']};--mint:{c['mint']};--edge:{c['edge']}}}</style>\n"
        head = head.replace("</head>", CSS + palette + plate + "</head>", 1)
        js = (JS_HEAD.replace("__ORIGIN__", str(AP_TOP - self.shift + 230)).replace("__KEYS__", json.dumps(self.camera_keys())) + "\n".join(self.js)
              + JS_TAIL.replace("__CAPS__", json.dumps(caps)).replace("__LINT__", "true" if os.environ.get("ALTOCUE_KIT_LINT") else "false"))
        glow = f'<div id="glow" class="glowbg" data-start="0" data-duration="{self.END:.4f}"><i></i></div>\n'
        Path("composition/index.html").write_text(head + base_end + "\n<!-- motion -->\n" + glow + "".join(html) + brand + cap + audio +
                                                 "</div>\n" + js + "</body>\n</html>\n")
        print(len(scenes), "scenes,", len(self.beats), "beats,", len(self.sfx), "sounds,", round(self.END, 2), "s")



CSS = r"""<style>/* glass kit */
#root{background:var(--bg)!important}
#root .scene{position:absolute;inset:0;overflow:hidden;font-family:'Geist',sans-serif;color:var(--ink)}
#root .scene.cutout{z-index:10}
#root .scene.hidden{z-index:30}
#root .beat{position:absolute;inset:0;z-index:20;font-family:'Geist',sans-serif;color:var(--ink);pointer-events:none}
.canvas{position:absolute;inset:0;background:var(--bg)}
.hmove{position:absolute;inset:0}
.ph{position:absolute;inset:0;transform-origin:540px 900px}
/* film mode: a full graphic in the dark Altocue look (r.hide(..., cls="film")), for a brand film inside a Reel */
.scene.film{color:#f3f7fa}
.scene.film .canvas{background:radial-gradient(950px 760px at 72% 20%,rgba(181,244,217,.17),rgba(8,13,19,0) 70%),#080d13!important}
.scene.film .canvas:after{content:'';position:absolute;inset:0;background-image:linear-gradient(90deg,rgba(181,244,217,.055) 1px,transparent 1px);background-size:180px 100%}
.film .card{background:linear-gradient(150deg,rgba(32,50,60,.97),rgba(13,22,30,.97));border-color:rgba(211,238,252,.16);outline:none;color:#f3f7fa;
  box-shadow:0 40px 80px rgba(0,0,0,.55),inset 0 1px 0 rgba(255,255,255,.10)}
.film .card::after{background:linear-gradient(100deg,rgba(255,255,255,0),rgba(181,244,217,.20),rgba(255,255,255,0))}
.film .ob{filter:drop-shadow(0 30px 40px rgba(0,0,0,.55)) drop-shadow(0 0 46px rgba(181,244,217,.16))}
.fh{position:absolute;left:72px;top:330px;width:900px;font-weight:600;font-size:104px;line-height:1.0;letter-spacing:-.035em;color:#f3f7fa}
.fh em{font-style:normal;color:#8a9aa8}.fh b{font-weight:600;color:#b5f4d9}
.fh .wd{display:inline-block}
.hl .n{font-family:'Geist Mono',monospace;font-weight:500;font-size:40px;color:var(--soft);margin-right:14px;vertical-align:middle}
.hl.big.at{top:660px}
.bign{position:absolute;left:72px;top:400px;font-family:'Geist Mono',monospace;font-weight:500;font-size:200px;line-height:1;color:var(--green);transform-origin:0 80%}
.ck{display:inline-block;width:30px;height:30px;border-radius:50%;background:var(--green);position:relative;flex:none}
.ck:after{content:'';position:absolute;left:10px;top:5px;width:8px;height:15px;border:solid #fff;border-width:0 4px 4px 0;transform:rotate(45deg)}
.ck.big{width:76px;height:76px}.ck.big:after{left:27px;top:14px;width:18px;height:36px;border-width:0 9px 9px 0}
.chip s{text-decoration-thickness:4px;text-decoration-color:var(--green)}
.vt{position:relative}.vt i{position:absolute;left:-6px;right:-6px;top:52%;height:7px;border-radius:4px;background:var(--green);transform-origin:0 50%;transform:scaleX(0)}
.src{font-family:'Geist Mono',monospace;font-size:22px;letter-spacing:.06em;text-transform:uppercase;color:var(--soft)}
.hl.big{font-size:72px;top:420px}
.roomwrap{position:absolute;left:30px;top:1092px;width:1020px;height:900px;overflow:hidden;border-radius:90px 90px 0 0}
.room{position:absolute;left:-30px;top:-792px;width:1080px;height:1920px;object-fit:cover}
.subjwrap{position:absolute;left:0;top:300px;width:1080px;height:1620px;overflow:hidden}
.subj{position:absolute;left:0;top:0;width:1080px;height:1920px;object-fit:cover}
.brand{position:absolute;left:0;right:0;top:0;height:400px;z-index:60;font-family:'Geist',sans-serif;pointer-events:none}
.brand img{position:absolute;left:72px;top:329px;width:44px;height:44px;object-fit:contain}
.brand b{position:absolute;left:132px;top:351px;transform:translateY(-50%);font-weight:600;font-size:27px;color:var(--ink);letter-spacing:-.01em}
.brand .rname{position:absolute;right:198px;top:351px;transform:translateY(-50%);font-family:'Geist Mono',monospace;font-size:25px;color:var(--soft)}
.hl{position:absolute;left:72px;top:400px;width:810px;font-weight:600;font-size:63px;line-height:1.08;letter-spacing:-.025em;color:var(--ink)}
.hl em{font-style:normal;color:var(--green);background-image:linear-gradient(var(--mint),var(--mint));background-repeat:no-repeat;background-position:0 92%;background-size:0 0.3em}
.hl .wd,.hl .n{display:inline-block}
.glowbg{position:absolute;inset:0;z-index:5;pointer-events:none;overflow:hidden}
.glowbg i{position:absolute;left:180px;top:180px;width:900px;height:900px;border-radius:50%;background:radial-gradient(closest-side,rgba(181,244,217,.42),rgba(181,244,217,0))}
.canvas{background:radial-gradient(1000px 760px at 72% 26%,rgba(181,244,217,.30),rgba(181,244,217,0) 70%),var(--bg)!important}
.band{position:absolute;left:72px;width:810px;top:500px;height:340px}
.card{position:absolute;border-radius:40px;border:2px solid rgba(255,255,255,.95);outline:1.5px solid rgba(38,112,82,.10);
  background:linear-gradient(150deg,rgba(255,255,255,.98) 0%,rgba(255,255,255,.90) 45%,rgba(226,247,238,.92) 100%);
  box-shadow:0 34px 60px rgba(31,57,48,.16),0 6px 14px rgba(31,57,48,.07),inset 0 2px 0 #fff,inset 0 -26px 44px rgba(181,244,217,.26)}
.card::after{content:'';position:absolute;top:-30%;bottom:-30%;width:34%;left:calc(var(--sh,-70) * 1%);border-radius:inherit;pointer-events:none;
  background:linear-gradient(100deg,rgba(255,255,255,0),rgba(255,255,255,.85),rgba(255,255,255,0));transform:skewX(-18deg)}
.card{overflow:hidden;box-sizing:border-box}
.card.sel{outline-color:rgba(38,112,82,.45);background:linear-gradient(150deg,#fff 0%,#DDF7EA 100%);box-shadow:0 34px 60px rgba(38,112,82,.24),0 0 0 6px rgba(181,244,217,.55),inset 0 2px 0 #fff}
.ob{position:relative;filter:drop-shadow(0 22px 26px rgba(31,57,48,.24))}.ob img{display:block;width:100%;height:100%;object-fit:contain}
.gtile{padding:0}.gtile .ob{position:absolute;left:8%;top:10px;width:84%;height:126px}
.gtile .cap-k{position:absolute;left:16px;top:146px;font-size:20px;color:var(--green)}
.gtile .tl{position:absolute;left:14px;right:6px;top:170px;font-weight:600;font-size:30px;line-height:1.06;letter-spacing:-.02em}
.deck{position:absolute;left:66px;top:520px;width:914px;height:850px}
.dcard{width:290px;height:410px}.dcard .ob{position:absolute;left:10%;top:28px;width:80%;height:230px}
.dcard .cap-k{position:absolute;left:28px;top:272px;font-size:26px;color:var(--green)}
.dcard .tl{position:absolute;left:28px;right:16px;top:306px;font-weight:600;font-size:40px;line-height:1.05;letter-spacing:-.02em}
.dcard.last .ob,.dcard.last .tl{filter:blur(13px)}
.dcard.last{box-shadow:0 34px 70px rgba(38,112,82,.30),0 0 0 8px rgba(181,244,217,.6),inset 0 2px 0 #fff}
.dcard.last .cap-k{font-size:54px;top:26px;left:26px;z-index:3;font-weight:500}
.dcard .frost{position:absolute;inset:0;border-radius:inherit;background:linear-gradient(160deg,rgba(255,255,255,.55),rgba(181,244,217,.35))}
.cap-k{font-family:'Geist Mono',monospace;font-size:24px;color:var(--soft)}
.chip{display:inline-flex;align-items:center;gap:14px;padding:16px 30px;border-radius:40px;border:2px solid rgba(255,255,255,.95);outline:1.5px solid rgba(38,112,82,.10);
  background:linear-gradient(150deg,#fff 0%,rgba(236,249,243,.96) 100%);font-weight:600;font-size:36px;color:var(--ink);box-shadow:0 20px 34px rgba(31,57,48,.13),inset 0 2px 0 #fff}
.brand .rname,.pillname{display:none!important}
.chip.on{background:linear-gradient(150deg,#CFF8E6 0%,var(--mint) 100%);outline-color:rgba(38,112,82,.28);box-shadow:0 20px 34px rgba(38,112,82,.22),inset 0 2px 0 rgba(255,255,255,.8)}
.label{position:absolute;font-family:'Geist Mono',monospace;font-size:22px;letter-spacing:.08em;text-transform:uppercase;color:var(--soft)}
.caps{position:absolute;left:72px;width:810px;top:842px;height:76px;z-index:50;display:flex;justify-content:center;align-items:center;pointer-events:none}
.capline{font-family:'Geist',sans-serif;font-weight:600;font-size:57px;letter-spacing:-.02em;color:var(--ink);white-space:nowrap}
.capline b{color:var(--green);font-weight:600}
.pillname{position:absolute;right:186px;top:327px;padding:8px 22px;border:2px solid var(--edge);border-radius:30px;background:#fff;font-weight:500;font-size:27px;color:var(--ink);z-index:70}
.endmark{position:absolute;left:0;right:0;top:500px;height:340px;display:flex;justify-content:center;align-items:center}
.endmark img{width:300px;height:300px;object-fit:contain;filter:drop-shadow(0 30px 40px rgba(38,112,82,.18))}
.endcard{position:absolute;left:151px;right:151px;top:896px;height:389px;background:#fff;border:2px solid var(--edge);border-radius:40px;box-shadow:0 24px 50px rgba(31,57,48,.12);text-align:center}
.endcard .e1{margin-top:52px;font-weight:500;font-size:64px;color:var(--ink)}
.endcard .e2{margin-top:6px;font-weight:600;font-size:125px;line-height:1.05;letter-spacing:-.02em;color:var(--green)}
.endcard .e3{margin-top:18px;font-weight:400;font-size:34px;color:var(--soft)}
</style>
"""

JS_HEAD = r"""<script>
(() => {
const K0 = window.kit, R = K0.R;
const LEAD = 0.1;   // a scene's own clock starts a few frames in, so its first frame is never an empty canvas
const k = Object.assign({}, K0, { beat: (id, fn) => K0.beat(id, id.startsWith('s-') ? (t, h) => fn(t + LEAD, h) : fn) });
const KEYS = __KEYS__;
const $ = (h, s) => h.querySelector(s), $$ = (h, s) => [...h.querySelectorAll(s)];
const S0 = (h) => Number(h.dataset.start);
const W = (txt, n = 0) => { const h = R.caption_words.filter(x => x.word.replace(/[.,!?:;"]/g, '').toLowerCase() === txt.toLowerCase()); return h[n].start; };
// Motion vocabulary from Shorts Studio, kept calm enough to read: depth entrances that land out of a soft blur, spring
// pops, word-by-word headlines with a highlighter sweep, slow drift. One idea at a time; nothing cuts faster than before.
const up = (el, t, from, len = 0.4, dist = 40) => { if (!el) return; const p = k.span(t, from, len, k.ease.expo);
  el.style.opacity = p; el.style.transform = `translateY(${(1 - p) * dist}px) scale(${0.965 + 0.035 * p})`; el.style.filter = p < 0.999 ? `blur(${(1 - p) * 7}px)` : 'none'; };
const pop = (el, t, from, len = 0.45) => { if (!el) return; const s = k.settle(t, from, len), o = k.clamp((t - from) / 0.14);
  el.style.opacity = o; el.style.transform = `scale(${0.78 + 0.22 * s})`; el.style.filter = o < 0.999 ? `blur(${(1 - o) * 5}px)` : 'none'; };
// a glass object arrives out of depth and soft focus, overshoots a little, then floats
const obj = (el, t, from, o = {}) => { if (!el) return; const a = k.arrive(t, from, o.len || 0.6), live = Math.max(0, t - from), sd = o.seed || 1;
  el.style.opacity = k.clamp((t - from) / 0.14);
  el.style.transform = `translateY(${(1 - a.p) * (o.dy ?? 80) + k.drift(live, 7, 3.6, sd)}px) rotate(${(1 - a.p) * (o.rot ?? -16) + k.drift(live, 1.8, 5, sd + 2)}deg) scale(${(o.s0 ?? 0.5) + (1 - (o.s0 ?? 0.5)) * a.p})`;
  el.style.filter = a.blur > 0.3 ? `blur(${a.blur}px)` : 'none'; };
// a glass card lands from in front of the frame: tilted, out of focus, settling flat with a spring
const land = (el, t, from, o = {}) => { if (!el) return; const sp = k.settle(t, from, o.len || 0.75), a = k.clamp((t - from) / 0.18);
  el.style.opacity = a;
  el.style.transform = `perspective(1300px) translate3d(0, ${(1 - sp) * (o.dy ?? -150)}px, ${(1 - sp) * 320}px) rotateX(${(1 - sp) * (o.rx ?? 42)}deg) rotateZ(${(1 - sp) * (o.rz ?? -7)}deg)`;
  el.style.filter = a < 0.999 ? `blur(${(1 - a) * 9}px)` : 'none'; };
// a band of light crossing a glass card
const sheen = (el, t, from, len = 0.9) => { if (el) el.style.setProperty('--sh', -70 + 240 * k.span(t, from, len, k.ease.inOut)); };
// a short camera hit: 0 before and after, up to amt in between
const hit = (t, at, amt = 0.06, len = 0.5) => { const x = (t - at) / len; return x < 0 || x > 1 ? 0 : amt * Math.sin(Math.PI * Math.sqrt(x)); };
const tilesIn = (h, t, from = 0.1, gap = 0.09) => $$(h, '.gtile').forEach((el, i) => { land(el, t, from + i * gap, { dy: -90, rx: 30, len: 0.65 }); obj($(el, '.ob img'), t, from + 0.12 + i * gap, { dy: 30, seed: i + 1 }); sheen(el, t, from + 0.5 + i * gap, 0.8); });
const deckIn = (h, t, from = 0.05, gap = 0.16) => $$(h, '.dcard').forEach((el, i) => { land(el, t, from + i * gap, { rz: i % 2 ? 8 : -8 }); obj($(el, '.ob img'), t, from + 0.15 + i * gap, { dy: 40, seed: i + 3 }); sheen(el, t, from + 0.7 + i * gap, 0.9);
  if (el.classList.contains('last')) el.style.transform += ` scale(${1 + 0.05 * Math.sin(Math.max(0, t - from - 1) * 2.4)})`; });
// phases inside one full graphic: each arrives from a little back and leaves by pushing through the lens, so the
// picture changes without a cut
const phase = (el, t, a, b) => { if (!el) return; const i = k.span(t, a, 0.34, k.ease.expo), o = b == null ? 0 : k.span(t, b - 0.24, 0.24, k.ease.inOut);
  el.style.opacity = i * (1 - o); el.style.transform = `scale(${(0.9 + 0.1 * i) * (1 + 0.24 * o)})`;
  el.style.filter = (i < 0.999 || o > 0.001) ? `blur(${(1 - i) * 8 + o * 10}px)` : 'none'; el.style.visibility = (i <= 0 || o >= 1) ? 'hidden' : 'visible'; };
const words = (el) => { if (el.dataset.split) return; el.dataset.split = '1';
  const walk = (node) => [...node.childNodes].forEach(c => {
    if (c.nodeType === 3) { const f = document.createDocumentFragment();
      c.textContent.split(/(\s+)/).forEach(p => { if (!p) return; if (/^\s+$/.test(p)) { f.appendChild(document.createTextNode(p)); return; }
        const s = document.createElement('span'); s.className = 'wd'; s.textContent = p; f.appendChild(s); });
      c.replaceWith(f); }
    else if (c.nodeType === 1 && !c.classList.contains('n')) walk(c); });
  walk(el); };
const kinetic = (el, t, from = 0.02) => { if (!el) return; words(el); el.style.opacity = 1; el.style.filter = 'none';
  el.style.transform = `translateY(${k.drift(t, 2.5, 5, 1)}px)`;
  const units = [...el.querySelectorAll('.wd, .n')];
  units.forEach((u, i) => { const a = k.span(t, from + i * 0.045, 0.42, k.ease.expo);
    u.style.opacity = a; u.style.transform = `translateY(${(1 - a) * 0.5}em)`; u.style.filter = a < 0.999 ? `blur(${(1 - a) * 6}px)` : 'none'; });
  const em = el.querySelector('em'); if (em) em.style.backgroundSize = `${100 * k.span(t, from + units.length * 0.045 + 0.12, 0.5, k.ease.inOut)}% 0.3em`; };
// A top-band headline keeps to one line so it never crowds the cards below it: shrink it (to 44 px at least) until it fits.
const fit = (el) => { if (!el || el.classList.contains('big') || el.dataset.fit || !el.offsetHeight) return; let s = 63; el.style.fontSize = s + 'px';
  while (el.offsetHeight > s * 1.6 && s > 44) { s -= 2; el.style.fontSize = s + 'px'; } el.dataset.fit = '1'; };
// A full graphic's cards settle in the open canvas under the headline (not crammed under it with the lower half empty),
// grow a little when there is room, and push in slowly so the frame never sits still. Measured once, from layout boxes.
const settle = (sec, t) => {
  const mv = sec.querySelector(':scope > .hmove'); if (!mv) return;
  const a = Number(sec.dataset.start), d = Number(sec.dataset.duration);
  if (t < a || t >= a + d || !mv.offsetHeight) return;
  if (!mv.dataset.fit && mv.querySelector(':scope > .ph')) mv.dataset.fit = 'none';   // a phased scene places its own pieces
  if (!mv.dataset.fit) {
    const heads = [...mv.querySelectorAll(':scope > .hl')];
    const hb = heads.reduce((m, e) => Math.max(m, e.offsetTop + e.offsetHeight), 0);
    const kids = [...mv.children].filter(e => !e.classList.contains('hl') && (e.offsetWidth || e.offsetHeight));
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    kids.forEach(e => { x0 = Math.min(x0, e.offsetLeft); y0 = Math.min(y0, e.offsetTop); x1 = Math.max(x1, e.offsetLeft + e.offsetWidth); y1 = Math.max(y1, e.offsetTop + e.offsetHeight); });
    if (!kids.length || y0 < hb - 4 || y1 - y0 < 40) { mv.dataset.fit = 'none'; }
    else {
      const top = Math.max(560, hb + 60), bot = 1600;
      const ox0 = (x0 + x1) / 2;   // grown, pushed in 4% and still inside the safe area on both sides
      const s = Math.max(1, Math.min(1.25, (bot - top) / (y1 - y0), 860 / (x1 - x0), (ox0 - 60) / ((ox0 - x0) * 1.04), (1000 - ox0) / ((x1 - ox0) * 1.04)));
      const cy = (y0 + y1) / 2, target = Math.max(top + (y1 - y0) * s / 2, top + (bot - top) * 0.42);
      Object.assign(mv.dataset, { fit: '1', s, ty: target - cy, ox: (x0 + x1) / 2, oy: cy });
      heads.forEach(e => sec.appendChild(e));   // the headline keeps the house position
    }
  }
  if (mv.dataset.fit === 'none') { const arr = k.ease.expo(k.clamp((t - a + 0.1) / 0.5)); mv.style.transformOrigin = '540px 900px';
    mv.style.transform = `scale(${(1 + 0.035 * (t - a) / d) * (1.16 - 0.16 * arr)})`; mv.style.filter = arr < 0.995 ? `blur(${(1 - arr) * 12}px)` : 'none'; }
  if (mv.dataset.fit === '1') {
    const arr = k.ease.expo(k.clamp((t - a + 0.1) / 0.5));
    const s = Number(mv.dataset.s) * (1 + 0.04 * (t - a) / d) * (1.16 - 0.16 * arr);
    mv.style.filter = arr < 0.995 ? `blur(${(1 - arr) * 12}px)` : 'none';
    mv.style.transformOrigin = `${mv.dataset.ox}px ${mv.dataset.oy}px`;
    mv.style.transform = `translateY(${Number(mv.dataset.ty) + k.drift(t, 4, 6, 3)}px) scale(${s})`;
  }
};
const hl = (h, t, from = 0.02) => { const el = $(h, '.hl'); if (el) { fit(el); kinetic(el, t, from); } };
// slow life under everything: the band floats a few pixels, the mint glow drifts behind the presenter scenes
const ambient = (t) => {
  document.querySelectorAll('.beat .band').forEach((b, i) => { b.style.translate = `0 ${k.drift(t, 3, 4.5, i)}px`; });
  const g = document.querySelector('#glow i'); if (g) g.style.transform = `translate(${k.drift(t, 70, 9, 2)}px, ${k.drift(t, 40, 11, 5)}px) scale(${1 + 0.06 * Math.sin(t * 0.5)})`;
  // a slow push-in through each stretch on camera, and every other take a touch closer, so the joins where pauses
  // were trimmed read as punch-ins instead of jumps. Scaled about the aperture's top centre.
  document.querySelectorAll('section.scene.cutout').forEach(sec => {
    const a = Number(sec.dataset.start), d = Number(sec.dataset.duration); if (t < a || t >= a + d) return;
    const p = (t - a) / d;
    let cur = 1, prev = 1, at = 0, ez = 0;
    for (const [kt, ks, ke] of KEYS) { if (kt <= t + 1e-4) { prev = cur; cur = ks; at = kt; ez = ke; } else break; }
    // at a join the new framing arrives a touch closer and eases out, so the cut lands instead of snapping
    const cam = ez ? prev + (cur - prev) * k.ease.expo(k.clamp((t - at) / ez)) : cur * (1 + 0.03 * (1 - k.ease.expo(k.clamp((t - at) / 0.45))));
    const landing = a > 0.01 ? 0.06 * (1 - k.ease.expo(k.clamp((t - a) / 0.5))) : 0;   // comes back a touch close, settles out
    sec.querySelectorAll('img.room, img.subj').forEach(img => {
      const n = parseInt((img.dataset.take || '0').replace(/\D/g, ''), 10) || 0;
      img.style.transformOrigin = `540px ${__ORIGIN__}px`; img.style.transform = `scale(${cam + landing + 0.03 * p})`; });
  });
};
"""

JS_TAIL = r"""
const CAPS = __CAPS__;
// Layout lint (pre-render check only): text outside the safe area, a card off the edge, a glass object over text.
// Judged on settled elements only, and reported once each through console.error, which the snapshot lists.
const LINT = __LINT__, seen = new Set();
const lint = (t) => {
  const root = document.getElementById('root').getBoundingClientRect(), sc = root.width / 1080;
  const box = (el) => { const r = el.getBoundingClientRect(); return { l: (r.left - root.left) / sc, t: (r.top - root.top) / sc, r: (r.right - root.left) / sc, b: (r.bottom - root.top) / sc }; };
  const settled = (el) => { for (let e = el; e && e.id !== 'root'; e = e.parentElement) { const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) < 0.9 || (cs.filter && cs.filter !== 'none' && cs.filter.includes('blur'))) return false; } return true; };
  const say = (key, msg) => { if (!seen.has(key)) { seen.add(key); console.error('LINT ' + t.toFixed(2) + ' s: ' + msg); } };
  const hosts = [...document.querySelectorAll('section.scene, .beat')].filter(h => { const a = Number(h.dataset.start), d = Number(h.dataset.duration); return t >= a + 0.9 && t < a + d - 0.3; });
  hosts.forEach(h => {
    const texts = [...h.querySelectorAll('.hl, .fh, .fline, .fname, .chip, .tl, .dl, .cn, .pq, .qt, .cv, .fv, .li, .ck2, .ck3, .src, .cap-k, .e1, .e2, .e3, .doc b, .fn, .saved, .cl, .row b')].filter(e => e.textContent.trim() && settled(e));
    texts.forEach(e => { const b = box(e); if (b.r - b.l < 4) return; const name = (e.className || e.tagName) + ' "' + e.textContent.trim().slice(0, 24) + '"';
      if (b.l < 56 || b.r > 1000) say(h.id + name + 'x', `text ${name} in ${h.id} runs outside the safe area (x ${Math.round(b.l)} to ${Math.round(b.r)}; keep 56 to 1000)`);
      if (b.b > 1500 && h.classList.contains('hidden')) say(h.id + name + 'y', `text ${name} in ${h.id} sits under the platform's caption area (bottom ${Math.round(b.b)}; keep above 1500)`); });
    [...h.querySelectorAll('.card')].filter(settled).forEach(e => { const b = box(e); if (b.l < 40 || b.r > 1010) say(h.id + e.className + 'c', `card ${e.className} in ${h.id} runs off the edge (x ${Math.round(b.l)} to ${Math.round(b.r)}; keep 40 to 1010)`); });
    [...h.querySelectorAll('.ob')].filter(settled).forEach(o => { const ob = box(o), mx = (ob.r - ob.l) * 0.2, my = (ob.b - ob.t) * 0.2;
      const core = { l: ob.l + mx, r: ob.r - mx, t: ob.t + my, b: ob.b - my };
      texts.forEach(e => { if (o.closest('.gtile, .dcard') && o.closest('.gtile, .dcard') === e.closest('.gtile, .dcard')) return;
        const b = box(e), w = Math.min(b.r, core.r) - Math.max(b.l, core.l), hh = Math.min(b.b, core.b) - Math.max(b.t, core.t);
        if (w > 0 && hh > 0 && w * hh > 0.15 * (b.r - b.l) * (b.b - b.t)) say(h.id + o.className + e.className, `object ${o.className} in ${h.id} covers the text "${e.textContent.trim().slice(0, 24)}"`); }); });
  });
};
k.beat('captions', (t, h) => {
  document.querySelectorAll('section.scene.hidden').forEach(sec => settle(sec, t)); ambient(t); if (LINT) lint(t);
  const line = $(h, '.capline'); const on = CAPS.some(([a, b]) => t >= a && t < b - 1e-3);
  const c = on ? k.caption(t) : null;
  if (!c) { line.innerHTML = ''; return; }
  line.innerHTML = c.words.map((x, i) => i === c.index ? `<b>${x.word}</b>` : x.word).join(' ');
});
})();
</script>
"""
