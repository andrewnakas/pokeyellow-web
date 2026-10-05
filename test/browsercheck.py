#!/usr/bin/env python3
"""Headless browser check for the Pokemon Yellow browser target.

Audio is off twice over: Chrome runs with --mute-audio and the page is loaded with
?mute=1, which sets EJS_volume to 0 and calls setVolume(0) once the core starts.

Four things this harness refuses to take on trust, each a real failure mode seen on
this box by one agent or another:

1. THE TAB IS MINE. Ten agents share this machine. Chrome will happily attach to an
   instance already listening on a fixed debug port and /json/list then hands you
   SOMEBODY ELSE'S page -- one agent graded another agent's game as its own. So
   cdp.Chrome only ever uses --remote-debugging-port=0 and reads the real port back
   out of its own private profile. On top of that the URL carries a nonce the
   attached target must echo, the core must identify as gambatte, and the sha1 the
   PAGE computes over the ROM must equal the sha1 of the file on disk.

2. THE PICTURE IS REAL. The page cannot measure its own canvas: EmulatorJS's WebGL
   context has no preserveDrawingBuffer, so drawImage() into a 2D context reports
   mean luma 0.00 while the game is plainly running. The ground truth is the
   screenshot the DRIVER takes, decoded out here, cropped to the canvas rect. The
   canvas is also asserted non-zero: emulator.js:352 replaces the player element's
   whole inline style attribute, so a mis-styled page renders at 60fps into nothing
   while reporting started:true and a climbing frame counter.

3. THE SAVE IS REALLY ON DISK. gameManager.getSaveFile() calls saveSaveFiles()
   first, which writes the core's CURRENT SRAM over the file -- call it too soon
   after a reload and it overwrites the restored save with blanks, so the test
   destroys the thing it is measuring. Persistence is therefore checked by reading
   emscripten's IDBFS IndexedDB store directly, on a fresh page load, BEFORE any ROM
   is picked and before a core exists to clobber it. The database is enumerated, not
   guessed: indexedDB.open() CREATES a missing database, so a wrong name invents an
   empty one and then confidently reports "the save was lost".

4. IT WAS PLAYED, NOT WATCHED. Yellow's opening is a long scripted sequence -- the
   Pikachu intro, Oak's speech, two naming screens -- that is screenshot-identical
   to play. The gate is the game's own save data, decoded with the symbol table of
   the build under test (test/gen1save.py). Gen 1 is stricter than Gen 2 here: the
   save is gated on CalcCheckSum over sGameData..sGameDataEnd matching
   sMainDataCheckSum, not on two magic bytes. We also check the main menu's own
   "is there a save" test, CheckForPlayerNameInSRAM, and the player name we typed.

Usage: browsercheck.py [--rom PATH] [--stage all|boot|play|save]
"""

import argparse
import base64
import hashlib
import json
import os
import shutil
import socket
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cdp        # noqa: E402
import serve      # noqa: E402
import gen1save   # noqa: E402
import pngstat    # noqa: E402

OUT = os.path.join(HERE, "out")
UPSTREAM = os.path.normpath(os.path.join(HERE, "..", "upstream"))

# EmulatorJS / libretro joypad ids for the "gb" control scheme
A, B, SELECT, START, UP, DOWN, LEFT, RIGHT = 8, 0, 2, 3, 4, 5, 6, 7

# Without EVENT_GOT_POKEDEX the start menu is, per engine/menus/draw_start_menu.asm:
#   0 #MON   1 ITEM   2 <player name>   3 SAVE   4 OPTION   5 EXIT
# and at the point this test saves, the player has no Pokedex and no Pokemon.
SAVE_MENU_INDEX = 3

# EmulatorJS pings this on localhost only (emulator.js:207 gates it on
# `this.debug || localhost`), so it fires in the test and NOT on a deployed page.
# The page's egress guard refuses it; that is the guard working, not a leak.
EXPECTED_REFUSALS = {"https://cdn.emulatorjs.org/stable/data/version.json"}

# There are no expected 404s: data/cores/reports/gambatte.json is vendored here,
# unlike in the sibling pokegold tree where it was left as a whitelisted 404.
EXPECTED_404S = ()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def banner(msg):
    print("\n=== %s" % msg, flush=True)


def attach(ch):
    ch.call("Runtime.enable")
    ch.call("Log.enable")
    ch.call("Page.enable")
    ch.call("Network.enable")


# ----------------------------------------------------------------- identity --

def assert_my_tab(ch, nonce):
    href = ch.eval("location.href")
    title = ch.eval("document.title")
    if nonce not in (href or ""):
        raise RuntimeError("attached to someone else's tab: %r (expected nonce %s)"
                           % (href, nonce))
    print("    tab identity ok: nonce echoed, title %r" % title)
    return {"href": href, "title": title}


def assert_core(ch, expect="gambatte"):
    core = ch.eval("window.EJS_emulator ? window.EJS_emulator.getCore() : null")
    system = ch.eval("window.EJS_emulator ? window.EJS_emulator.config.system : null")
    if core != expect:
        raise RuntimeError("wrong core: %r (expected %r) -- wrong page?" % (core, expect))
    print("    core: %s (EJS_core=%s)" % (core, system))
    return core


def canvas_rect(ch):
    """Backing size plus the on-screen rect, so screenshots can be cropped to it.

    Also the explicit non-zero assertion from trap 6: emulator.js:352 replaces the
    player element's entire inline style attribute, so anything set with
    element.style.* is erased and the core can render at 60fps into a 0x0 canvas
    while reporting started:true. Measure it; do not trust the self-report.
    """
    r = ch.eval("""(() => { const c = document.querySelector('#game canvas');
        if (!c) return null;
        const b = c.getBoundingClientRect();
        return {w: c.width, h: c.height,
                x: b.left, y: b.top, cssW: b.width, cssH: b.height,
                vw: window.innerWidth, vh: window.innerHeight,
                dpr: window.devicePixelRatio};
      })()""")
    if not r:
        raise RuntimeError("no canvas in #game")
    if min(r["w"], r["h"], r["cssW"], r["cssH"]) <= 0:
        raise RuntimeError("canvas has a zero dimension: %r" % r)
    return r


def frac_box(rect):
    """CSS rect -> the fractional (left, top, right, bottom) box pngstat.luma wants.

    Fractions of the viewport, so this is independent of devicePixelRatio -- a NaN
    or surprising dpr has produced silently wrong crops on this box before. Clamped,
    because a rect can extend past the viewport.
    """
    vw = rect.get("vw") or 1
    vh = rect.get("vh") or 1

    def clamp(v):
        return min(1.0, max(0.0, v))

    bx = (clamp(rect["x"] / vw), clamp(rect["y"] / vh),
          clamp((rect["x"] + rect["cssW"]) / vw), clamp((rect["y"] + rect["cssH"]) / vh))
    if bx[2] - bx[0] <= 0.01 or bx[3] - bx[1] <= 0.01:
        raise RuntimeError("canvas crop is degenerate: %r from %r" % (bx, rect))
    return bx


# --------------------------------------------------------------------- page --

def load_rom_via_file_input(ch, url, rom_bytes_url):
    """Go through the page's real file-input change handler.

    A File is built in the page, put into the input's FileList through DataTransfer,
    and a change event dispatched: the same path a human click on the picker takes,
    minus the OS dialog. The file name is deliberately unhelpful and has the wrong
    extension for what upstream calls the ROM -- the page must identify it by its
    bytes, not by what it is called.
    """
    ch.call("Page.navigate", url=url)
    ch.pump(2.0)
    return ch.eval(
        """(async () => {
             const r = await fetch(%s);
             const buf = await r.arrayBuffer();
             const dt = new DataTransfer();
             dt.items.add(new File([buf], "some cartridge dump.bin",
                                   {type: "application/octet-stream"}));
             const input = document.getElementById("file");
             input.files = dt.files;
             input.dispatchEvent(new Event("change", {bubbles: true}));
             return {bytes: buf.byteLength, files: input.files.length};
           })()""" % json.dumps(rom_bytes_url),
        await_promise=True, timeout=60)


def goto(ch, url):
    """Navigate and wait for the NEW document.

    After Page.navigate, Runtime.evaluate still runs against the OLD document for a
    short window, so "navigate then poll until my thing exists" returns true against
    the page you were trying to leave. Poll for the nonce in location.search instead.
    """
    nonce = "nav" + uuid.uuid4().hex[:8]
    sep = "&" if "?" in url else "?"
    ch.call("Page.navigate", url=url + sep + "_n=" + nonce)
    end = time.time() + 30
    while time.time() < end:
        ch.pump(0.25)
        try:
            if ch.eval("location.search.indexOf(%s) !== -1 && document.readyState !== 'loading'"
                       % json.dumps(nonce)):
                return True
        except Exception:
            pass
    raise RuntimeError("navigation to %s never landed" % url)


def wait_started(ch, limit=90):
    end = time.time() + limit
    while time.time() < end:
        ch.pump(1.0)
        if ch.eval("!!(window.EJS_emulator && window.EJS_emulator.started)"):
            return True
    return False


def hold(ch, bid, seconds):
    """Hold a button across real running frames.

    Deliberately NOT window.__press(): that releases on a page setTimeout, and a
    timer that fires while Page.captureScreenshot has the page stalled can land the
    press and the release in the same task batch, so the core polls neither edge --
    the button looks pressed in the log and the game never saw it. Pumping CDP events
    between the two edges keeps the page running across the hold.
    """
    ch.eval("window.EJS_emulator.gameManager.simulateInput(0,%d,1)" % bid)
    ch.pump(seconds)
    ch.eval("window.EJS_emulator.gameManager.simulateInput(0,%d,0)" % bid)
    ch.pump(0.08)


def press(ch, bid, dur=0.15):
    hold(ch, bid, dur)


def frame(ch):
    try:
        return ch.eval("window.EJS_emulator.gameManager.getFrameNum()")
    except Exception:
        return None


# ------------------------------------------------------------------- saves --

def idb_saves(ch):
    """Read emscripten's IDBFS store directly.

    Non-destructive, and it works with no core loaded at all -- which is the only
    honest way to see what actually survived a reload.

    The database is enumerated rather than guessed: this emscripten names the IDBFS
    database after the MOUNT POINT ("/data/saves"), not the old 'EM_FS_'+pathname.
    Guessing also has a nasty side effect -- indexedDB.open() on a name that does not
    exist CREATES it, so a wrong guess silently litters and then reports "empty".
    """
    return ch.eval(
        """(async () => {
             const dbs = await indexedDB.databases();
             const hit = dbs.find(d => String(d.name).indexOf('saves') !== -1);
             if (!hit) return {error: 'no saves database', databases: dbs};
             return await new Promise(resolve => {
               const req = indexedDB.open(hit.name);
               req.onerror = () => resolve({error: 'open failed', db: hit.name});
               req.onsuccess = () => {
                 const d = req.result;
                 const stores = Array.from(d.objectStoreNames);
                 const store = stores.includes('FILE_DATA') ? 'FILE_DATA' : stores[0];
                 if (!store) return resolve({error: 'no object store', db: hit.name});
                 const tx = d.transaction([store], 'readonly').objectStore(store);
                 const keys = tx.getAllKeys();
                 keys.onerror = () => resolve({error: 'getAllKeys failed', db: hit.name});
                 keys.onsuccess = () => {
                   const out = {db: hit.name, store: store,
                                keys: keys.result.map(String), files: {}};
                   let left = keys.result.length;
                   if (!left) return resolve(out);
                   keys.result.forEach(k => {
                     const g = tx.get(k);
                     const done = () => { if (--left === 0) resolve(out); };
                     g.onerror = done;
                     g.onsuccess = () => {
                       const v = g.result, c = v && v.contents;
                       if (c && (c.byteLength !== undefined || c.buffer)) {
                         const u = new Uint8Array(c.buffer ? c.buffer : c);
                         let s = '';
                         for (let i = 0; i < u.length; i++) s += String.fromCharCode(u[i]);
                         out.files[String(k)] = {len: u.length, b64: btoa(s)};
                       }
                       done();
                     };
                   });
                 };
               };
             });
           })()""", await_promise=True, timeout=30)


def pick_srm(idb):
    """The one 32 KiB battery save out of whatever IDBFS is holding.

    Yellow's header says MBC5+RAM+BATTERY with RAM size 0x03, i.e. four 8 KiB SRAM
    banks = 32768 bytes, which is also what gen1save's offsets assume.
    """
    if not idb or "files" not in idb:
        return None, None, idb
    for k, v in sorted(idb["files"].items()):
        if v["len"] == 32768:
            raw = base64.b64decode(v["b64"])
            return k, raw, idb
    return None, None, idb


def egress(ch):
    return ch.eval("window.__egress ? {blocked: window.__egress.blocked, "
                   "bodies: window.__egress.bodies} : null")


def check_egress(ch):
    eg = egress(ch) or {"blocked": [], "bodies": []}
    unexpected = [b for b in eg["blocked"] if b["url"] not in EXPECTED_REFUSALS]
    return {"no_outbound_bodies": not eg["bodies"],
            "no_unexpected_refusals": not unexpected,
            "refused": sorted({b["url"] for b in eg["blocked"]}),
            "bodies": eg["bodies"]}


def http_failures(ch):
    out = set()
    for e in ch.events:
        if e.get("method") == "Network.responseReceived":
            r = e["params"]["response"]
            if r.get("status", 200) >= 400:
                out.add("%s %s" % (r["status"], r["url"]))
        elif e.get("method") == "Network.loadingFailed":
            if not e["params"].get("canceled"):
                out.add("failed %s" % e["params"].get("errorText"))
    return sorted(out)


def unexpected_http_failures(ch):
    return [f for f in http_failures(ch)
            if not any(p in f for p in EXPECTED_404S)]


def network_hosts(ch):
    """Every host the browser was actually asked to contact, from CDP, not the page.

    A page claiming it sent nothing is not evidence; this is the network layer.
    """
    hosts = set()
    for e in ch.events:
        if e.get("method") == "Network.requestWillBeSent":
            u = e["params"]["request"]["url"]
            if u.startswith("http"):
                hosts.add(u.split("/")[2])
    return sorted(hosts)


# ----------------------------------------------------------------- pictures --

SHOTS = []


def shot(ch, name, box=None):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, name + ".png")
    ch.screenshot(p)
    lum = None
    try:
        lum = pngstat.luma(p, box)
    except Exception as e:
        lum = {"error": str(e)}
    SHOTS.append({"name": name, "path": "test/out/%s.png" % name, "luma": lum})
    print("    shot -> test/out/%s.png  luma=%s" % (name, _fmt_luma(lum)), flush=True)
    return p


def _fmt_luma(lum):
    if isinstance(lum, dict) and "mean" in lum:
        return "%.1f" % lum["mean"]
    if isinstance(lum, (int, float)):
        return "%.1f" % lum
    return str(lum)


def luma_mean(lum):
    if isinstance(lum, dict) and "mean" in lum:
        return lum["mean"]
    if isinstance(lum, (int, float)):
        return float(lum)
    return None


def pixels_differ(p1, p2):
    from PIL import Image, ImageChops
    if not (os.path.exists(p1) and os.path.exists(p2)):
        return None
    a, b = Image.open(p1).convert("RGB"), Image.open(p2).convert("RGB")
    if a.size != b.size:
        return 1.0
    diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 12 else 0)
    return diff.histogram()[255] / float(a.size[0] * a.size[1])


def console_errors(ch):
    bad = []
    for lvl, t in ch.console():
        if lvl in ("error", "exception") or "Uncaught" in t:
            if "no-phone-home" in t or "cdn.emulatorjs.org" in t:
                continue
            if "Content Security Policy" in t or "violates the following" in t:
                continue       # the CSP refusing the CDN ping IS the deliverable
            if "404" in t:
                continue       # covered by unexpected_http_failures, with the URL
            bad.append((lvl, t[:200]))
    return bad


def page_logs(ch, needle):
    return [t for _, t in ch.console() if needle in t]


# ------------------------------------------------- getting out of a text box --
#
# These two are state-verified rather than count-based, because the count-based
# version was genuinely flaky and produced a confident, wrong "FAIL" run.
#
# What happened: the A-mashing that drives the opening keeps going after the player
# is standing in the bedroom, where A talks to the SNES ("<PLAYER> is playing the
# SNES! ...Okay! It's time to go!"). Whether the mash window happens to end with
# that text box open is a coin flip. In Gen 1 a text box swallows START, so the
# save sequence then pressed START, DOWN, DOWN, DOWN, A, A into a dialogue, never
# opened the menu, never saved -- and the test correctly reported an all-0xff save,
# having never actually exercised saving. Four fixed B presses at 0.5 s did not
# clear it either: input is ignored while a box is still typing, so a press on too
# short a timer is simply dropped.

# Measured on this page, canvas-crop mean luma: the bedroom with a white text box
# open reads 153-155; the same room with no box reads 87-110. A wide, clean gap.
TEXTBOX_LUMA = 130.0


def probe(ch, box):
    """Screenshot to a scratch file and measure it, without adding to the report."""
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "_probe.png")
    ch.screenshot(p)
    try:
        return luma_mean(pngstat.luma(p, box)), p
    except Exception:
        return None, p


def clear_text(ch, box, tries=14):
    """Press B until no text box is on screen.

    B advances Gen 1 text exactly like A does, but unlike A it never starts a new
    interaction -- so it can only ever close a box, never re-open one. Each press
    gets a full second of running time, because a box that is still typing ignores
    input.
    """
    for i in range(tries):
        lum, _ = probe(ch, box)
        if lum is not None and lum < TEXTBOX_LUMA:
            print("    no text box on screen after %d B presses (luma %.1f)" % (i, lum))
            return i
        press(ch, B)
        ch.pump(1.1)
    lum, _ = probe(ch, box)
    print("    WARNING: still looks like a text box after %d B presses (luma %s)"
          % (tries, lum))
    return tries


def open_start_menu(ch, box, tries=5):
    """Press START until the start menu is actually on screen.

    Verified by pixel delta rather than assumed: the menu is a box over the right
    of the screen, so opening it moves well over 3% of the pixels. If it did not
    open, something is still holding input -- press B and try again.
    """
    for i in range(tries):
        _, p0 = probe(ch, box)
        snap = os.path.join(OUT, "_probe_before.png")
        shutil.copyfile(p0, snap)
        press(ch, START)
        ch.pump(1.8)
        _, p1 = probe(ch, box)
        d = pixels_differ(snap, p1)
        if d is not None and d > 0.03:
            print("    start menu opened on press %d (screen moved %.1f%%)"
                  % (i + 1, 100 * d))
            return i + 1
        print("    START did nothing (screen moved %.1f%%) -- clearing text and retrying"
              % (100 * (d or 0)))
        press(ch, B)
        ch.pump(1.2)
    raise RuntimeError("could not open the start menu in %d attempts -- the game is "
                       "probably still in a dialogue" % tries)


# -------------------------------------------------------------------- intro --

def run_intro(ch, mash_until, box, shots=None):
    """Title -> NEW GAME -> Oak -> name -> rival name -> the bedroom, by mashing A.

    The whole Yellow opening is A-advanceable. Both naming screens default to
    "NEW NAME", which opens the keyboard with the cursor on "A"; mashing A types A
    until the field is full, at which point naming_screen.asm forces the cursor onto
    "ED" (see .checkEmptySpaces), so the next A confirms. The player therefore ends
    up named AAAAAAA -- PLAYER_NAME_LENGTH - 1 characters, read from the game's own
    constants, not assumed. That is the point: it is a name WE chose, and we read it
    back out of the save later. No scripted intro can produce it.
    """
    shots = dict(shots or {})
    pending = sorted(shots)
    t0 = time.time()

    def elapsed():
        return time.time() - t0

    def advance_to(target):
        while pending and pending[0] <= target:
            s = pending.pop(0)
            ch.pump(max(0.0, s - elapsed()))
            shot(ch, shots[s], box)
        ch.pump(max(0.0, target - elapsed()))

    # The opening animation (GAME FREAK, then Pikachu running) is skippable with
    # START, which lands on the title screen.
    advance_to(10.0)
    press(ch, START)
    print("    t=%.0fs START -> title screen" % elapsed(), flush=True)
    advance_to(13.0)
    shot(ch, "03-title", box)

    # A on the title opens the main menu. Capture it DELIBERATELY: once mashing
    # starts it is on screen for about a second, and the post-reload comparison
    # needs a trustworthy "before" picture of a menu with no CONTINUE on it.
    press(ch, A)
    advance_to(16.5)
    shot(ch, "04-main-menu-no-continue", box)
    print("    t=%.0fs main menu captured, mashing A from here" % elapsed(), flush=True)

    while elapsed() < mash_until:
        press(ch, A)
        advance_to(min(mash_until, elapsed() + 0.35))
    for s in list(pending):
        advance_to(s)
    return elapsed()


# --------------------------------------------------------------------- main --

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", default=None)
    ap.add_argument("--stage", default="all", choices=["all", "boot", "play", "save"])
    ap.add_argument("--mash", type=float, default=120.0,
                    help="seconds of A-mashing through the opening")
    args = ap.parse_args()

    rom = args.rom or os.path.join(UPSTREAM, "pokeyellow.gbc")
    if not os.path.exists(rom):
        sys.exit("no ROM at %s -- run ./build.sh first" % rom)
    rom_bytes = open(rom, "rb").read()
    rom_sha1 = hashlib.sha1(rom_bytes).hexdigest()
    syms = gen1save.Symbols(UPSTREAM)
    expect_name = "A" * syms.player_name_length

    print("ROM        %s  (%d bytes)" % (rom, len(rom_bytes)))
    print("ROM sha1   %s  (on disk)" % rom_sha1)
    print("symbols    pokeyellow.sym: sGameData=%02x:%04x  sMainDataCheckSum=%02x:%04x"
          % (syms.sym["sGameData"][0], syms.sym["sGameData"][1],
             syms.sym["sMainDataCheckSum"][0], syms.sym["sMainDataCheckSum"][1]))
    print("           checksum over .srm[0x%04x:0x%04x], stored at .srm+0x%04x"
          % (syms.srm_offset("sGameData"), syms.srm_offset("sGameDataEnd"),
             syms.srm_offset("sMainDataCheckSum")))
    print("           player name at .srm+0x%04x, %d chars (PLAYER_NAME_LENGTH-1) -> expect %r"
          % (syms.srm_offset("sPlayerName"), syms.player_name_length, expect_name))

    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)

    port = free_port()                      # one port for the whole run: IndexedDB
    srv = serve.start(port, rom_path=rom)    # is keyed by origin, port included
    nonce = uuid.uuid4().hex
    base = "http://127.0.0.1:%d/index.html?mute=1&hb" % port
    url = base + "&nonce=" + nonce
    romurl = "/_test/rom.gb"
    R = {"rom": os.path.basename(rom), "rom_bytes": len(rom_bytes),
         "rom_sha1_on_disk": rom_sha1, "nonce": nonce,
         "expected_player_name": expect_name,
         "symbols": {k: "0x%04x" % syms.srm_offset(k) for k in
                     ("sGameData", "sGameDataEnd", "sPlayerName", "sMainDataCheckSum")}}

    ch = None
    try:
        ch = cdp.Chrome()
        print("devtools   ephemeral port %d, profile %s" % (ch.port, ch.profile))
        attach(ch)

        # ------------------------------------------------------------ boot --
        banner("boot: hand the ROM to the page's own file input")
        info = load_rom_via_file_input(ch, url, romurl)
        R["identity"] = assert_my_tab(ch, nonce)
        print("    input accepted %s bytes as 'some cartridge dump.bin'" % info.get("bytes"))
        R["started"] = bool(wait_started(ch, 90))
        print("    core started: %s" % R["started"])
        if not R["started"]:
            shot(ch, "00-failed"); sys.exit("core never started")

        R["core"] = assert_core(ch, "gambatte")
        R["canvas"] = canvas_rect(ch)
        box = frac_box(R["canvas"])
        R["canvas_crop_fractions"] = [round(v, 4) for v in box]
        print("    canvas: %sx%s backing, %.0fx%.0f on screen, dpr=%s"
              % (R["canvas"]["w"], R["canvas"]["h"], R["canvas"]["cssW"],
                 R["canvas"]["cssH"], R["canvas"]["dpr"]))
        print("    screenshot crop (fractions of viewport): %s"
              % (R["canvas_crop_fractions"],))

        R["unload_patch_log"] = page_logs(ch, "unload-patch")
        print("    unload patch: %s" % (R["unload_patch_log"] or "NOT LOGGED"))
        R["unload_patch_active"] = any("ENABLED" in t or "dropped" in t
                                       for t in R["unload_patch_log"])

        page_sha1 = ch.eval(
            "(document.getElementById('b-sha').innerText.match(/[0-9a-f]{40}/) || [null])[0]")
        R["rom_sha1_in_page"] = page_sha1
        R["rom_sha1_matches"] = (page_sha1 == rom_sha1)
        if not R["rom_sha1_matches"]:
            raise RuntimeError("page computed sha1 %s but the file on disk is %s"
                               % (page_sha1, rom_sha1))
        print("    page-computed ROM sha1 matches the file on disk")

        ch.pump(2.0); f0 = frame(ch)
        ch.pump(3.0); f1 = frame(ch)
        R["fps_approx"] = round((f1 - f0) / 3.0, 1) if (f0 and f1) else None
        R["frames_advance"] = bool(f0 and f1 and f1 > f0)
        print("    frames %s -> %s  (~%s fps)" % (f0, f1, R["fps_approx"]))
        R["muted"] = ch.eval("window.EJS_emulator.volume === 0 || window.EJS_volume === 0")
        print("    muted: %s" % R["muted"])
        R["save_file_path"] = ch.eval("window.EJS_emulator.gameManager.getSaveFilePath()")
        print("    core save path: %s" % R["save_file_path"])
        R["mount_at_boot"] = ch.eval("window.__saveMountState ? window.__saveMountState() : null")
        print("    /data/saves mount: %s" % R["mount_at_boot"])
        R["egress_boot"] = check_egress(ch)
        R["network_hosts"] = network_hosts(ch)
        print("    hosts contacted (per CDP): %s" % R["network_hosts"])
        if args.stage == "boot":
            R["shots"] = SHOTS
            print(json.dumps(R, indent=2)); return

        # ------------------------------------------------------------ play --
        banner("play: drive the opening")
        run_intro(ch, args.mash, box,
                  shots={2.0: "01-gamefreak", 7.0: "02-pikachu-intro",
                         30.0: "05-oak-speech", 50.0: "06-intro-50s",
                         70.0: "07-intro-70s", 90.0: "08-intro-90s",
                         110.0: "09-intro-110s",
                         args.mash - 1.0: "10-after-intro"})

        R["text_cleared_after"] = clear_text(ch, box)
        before = shot(ch, "11-before-walk", box)
        hold(ch, DOWN, 0.8); ch.pump(0.4)
        hold(ch, LEFT, 0.8); ch.pump(0.6)
        after = shot(ch, "12-after-walk", box)
        R["walk_pixel_delta"] = pixels_differ(before, after)
        R["walked"] = bool(R["walk_pixel_delta"] and R["walk_pixel_delta"] > 0.01)
        print("    screen changed by %.1f%% after walking down+left"
              % (100 * (R["walk_pixel_delta"] or 0)))
        R["frames_after_intro"] = frame(ch)

        # The driver's own screenshot is the only trustworthy brightness reading.
        lumas = [luma_mean(s["luma"]) for s in SHOTS if luma_mean(s["luma"]) is not None]
        R["canvas_luma_mean_max"] = max(lumas) if lumas else None
        R["canvas_drew_something"] = bool(R["canvas_luma_mean_max"]
                                         and R["canvas_luma_mean_max"] > 8.0)
        print("    brightest canvas crop across all shots: %s" % R["canvas_luma_mean_max"])
        if args.stage == "play":
            R["shots"] = SHOTS
            print(json.dumps(R, indent=2)); return

        # ------------------------------------------------------------ save --
        banner("save: in-game START -> SAVE -> YES")
        k0, srm0, idb0 = pick_srm(idb_saves(ch))
        R["idb_before_save"] = ({k: v["len"] for k, v in idb0["files"].items()}
                                if idb0 and "files" in idb0 else None)
        print("    IDBFS before saving: %s" % R["idb_before_save"])

        R["start_menu_presses"] = open_start_menu(ch, box)
        shot(ch, "13-start-menu", box)
        for i in range(SAVE_MENU_INDEX):
            press(ch, DOWN); ch.pump(0.45)
        shot(ch, "14-cursor-on-save", box)
        press(ch, A);    ch.pump(2.0); shot(ch, "15-save-prompt-yes-no", box)
        press(ch, A)                                   # YES
        ch.pump(7.0)
        shot(ch, "16-saving", box)
        press(ch, A);    ch.pump(2.5); shot(ch, "17-saved", box)

        R["flush"] = ch.eval("window.__flushSave('test', true)", await_promise=True)
        ch.pump(2.5)
        key1, srm1, idb1 = pick_srm(idb_saves(ch))
        R["idb_db"] = idb1.get("db") if idb1 else None
        R["idb_files"] = ({k: v["len"] for k, v in idb1["files"].items()}
                          if idb1 and "files" in idb1 else None)
        R["srm_key"] = key1
        if srm1 is None:
            raise RuntimeError("no 32 KiB save in IDBFS after saving: %r" % idb1)
        R["srm_after_save"] = {"sha1": hashlib.sha1(srm1).hexdigest(), "len": len(srm1)}
        R["save_decoded"] = syms.read_save(srm1)
        print("    IDBFS after saving: %s sha1=%s" % (key1, R["srm_after_save"]["sha1"]))
        print("    decoded with this build's symbols:")
        print("      " + json.dumps(R["save_decoded"]))
        with open(os.path.join(OUT, "save-after-save.srm"), "wb") as f:
            f.write(srm1)

        # ---------------------------------------------------------- reload --
        banner("reload: fresh page, NO rom picked -- read IDBFS before a core exists")
        nonce2 = uuid.uuid4().hex
        url2 = base + "&nonce=" + nonce2
        # Navigate away IN THE SAME TAB. Closing the tab over CDP can skip
        # beforeunload entirely, which is exactly the thing being tested.
        goto(ch, "http://127.0.0.1:%d/index.html?blank=1" % port)
        ch.pump(1.5)
        goto(ch, url2)
        ch.pump(1.5)
        assert_my_tab(ch, nonce2)
        key2, srm2, idb2 = pick_srm(idb_saves(ch))
        R["srm_after_reload"] = ({"sha1": hashlib.sha1(srm2).hexdigest(), "len": len(srm2)}
                                 if srm2 is not None else None)
        R["save_persisted"] = bool(
            srm2 is not None
            and hashlib.sha1(srm2).hexdigest() == R["srm_after_save"]["sha1"])
        R["idb_files_after_reload"] = ({k: v["len"] for k, v in idb2["files"].items()}
                                       if idb2 and "files" in idb2 else None)
        print("    IDBFS on a fresh load: db=%s files=%s"
              % (idb2.get("db") if idb2 else None, R["idb_files_after_reload"]))
        print("    picked %s -> %s" % (key2, R["srm_after_reload"]))
        print("    save persisted across reload: %s" % R["save_persisted"])
        R["save_decoded_after_reload"] = (
            syms.read_save(srm2) if srm2 is not None
            else {"checksum_ok": False, "player_name": None,
                  "player_name_terminated": False,
                  "error": "no .srm in IDBFS after reload"})
        print("    decoded after reload:")
        print("      " + json.dumps(R["save_decoded_after_reload"]))

        # ground truth a human can see: CONTINUE, then the saved player back in the room
        banner("resume: load the ROM again and continue the saved game")
        load_rom_via_file_input(ch, url2, romurl)
        R["restarted"] = bool(wait_started(ch, 90))
        assert_core(ch, "gambatte")
        box2 = frac_box(canvas_rect(ch))

        # Step forward with A until the picture is the bedroom again, rather than
        # pressing on a fixed schedule. A fixed schedule drifts: the first run's
        # clock started after the boot measurements, so the same absolute times land
        # on different screens here, and an earlier version of this block ended up
        # photographing the title screen and calling it the main menu.
        #
        # The sequence being walked is: opening animation -> title -> main menu
        # (now with CONTINUE) -> the save's status panel -> the bedroom. Every step
        # is photographed, in order, so the sequence is auditable by eye.
        # Reference for "the player is back where the game was saved" is the frame
        # taken straight after walking, NOT 17-saved.png. 17-saved can still have
        # the "<PLAYER> saved the game!" box up -- whether that message has cleared
        # by the time the shot is taken varies run to run, and it moves 37% of the
        # pixels, which is enough to fail a comparison against a perfectly correct
        # resume. 12-after-walk is the same tile, same camera, no text box.
        saved_png = os.path.join(OUT, "12-after-walk.png")
        R["resume_steps"] = []
        resumed, delta = None, None
        ch.pump(8.0)
        for i in range(10):
            press(ch, START if i == 0 else A)
            ch.pump(3.5)
            p = shot(ch, "18-resume-step%d" % i, box2)
            d = pixels_differ(saved_png, p)
            R["resume_steps"].append({"step": i, "delta_vs_saved": d})
            print("    step %d: %.1f%% away from the spot the game was saved at"
                  % (i, 100 * (d or 1.0)))
            if d is not None and d < 0.15:
                resumed, delta = p, d
                break
        R["resumed_vs_saved_delta"] = delta
        R["resume_press_count"] = len(R["resume_steps"])
        if resumed:
            shutil.copyfile(resumed, os.path.join(OUT, "19-resumed.png"))
            print("    back in the bedroom after %d presses, %.1f%% from the spot "
                  "the game was saved at -> test/out/19-resumed.png"
                  % (R["resume_press_count"], 100 * delta))
        else:
            print("    never got back to the overworld in %d presses"
                  % R["resume_press_count"])

        # NOTE: there is deliberately NO pixel-diff gate on "the main menu gained a
        # CONTINUE entry". Measured on this page, the main menu with CONTINUE sits
        # 3.4% away from the one without it, while the Pikachu opening animation
        # sits 4.1% away -- both are mostly-white screens, so the two are not
        # separable by that metric and a threshold between them would be a
        # coin-flip dressed up as a test. The automated gate for "the game can see
        # a save file" is the decoded save's `player_name_terminated`, which is
        # literally the condition engine/menus/main_menu.asm's
        # CheckForPlayerNameInSRAM tests before it offers CONTINUE. The screenshots
        # are human-readable corroboration, not the gate.
        R["main_menu_closest_delta"] = min(
            (pixels_differ(os.path.join(OUT, "04-main-menu-no-continue.png"), s)
             for s in [os.path.join(OUT, "18-resume-step%d.png" % i)
                       for i in range(R["resume_press_count"])]),
            default=None)

        R["egress_final"] = check_egress(ch)
        R["network_hosts"] = network_hosts(ch)
        R["console_errors"] = console_errors(ch)
        R["http_failures"] = http_failures(ch)
        R["unexpected_http_failures"] = unexpected_http_failures(ch)
        R["shots"] = SHOTS

        banner("summary")
        sd, sd2 = R["save_decoded"], R["save_decoded_after_reload"]
        checks = {
            "core started": R["started"],
            "core restarted after reload": R["restarted"],
            "frames advance": R["frames_advance"],
            "muted": R["muted"],
            "page sha1 == on-disk sha1": R["rom_sha1_matches"],
            "core is gambatte": R["core"] == "gambatte",
            "canvas non-zero": True,
            "canvas actually drew": R["canvas_drew_something"],
            "unload patch active": R["unload_patch_active"],
            "walked": R["walked"],
            "save checksum valid (game's own CalcCheckSum)": sd.get("checksum_ok"),
            "player name == %r" % expect_name: sd.get("player_name") == expect_name,
            "main menu would see a save file": sd.get("player_name_terminated"),
            "resumed the save, back at the saved spot":
                bool(R["resumed_vs_saved_delta"] is not None
                     and R["resumed_vs_saved_delta"] < 0.15),
            "save persisted across reload": R["save_persisted"],
            "checksum still valid after reload": sd2.get("checksum_ok"),
            "name still %r after reload" % expect_name: sd2.get("player_name") == expect_name,
            "no outbound request bodies": R["egress_final"]["no_outbound_bodies"],
            "no unexpected refusals": R["egress_final"]["no_unexpected_refusals"],
            "no unexpected http failures": not R["unexpected_http_failures"],
            "no console errors": not R["console_errors"],
        }
        R["checks"] = {k: bool(v) for k, v in checks.items()}
        R["PASS"] = all(checks.values())
        for k, v in checks.items():
            print("  %-48s %s" % (k, "ok" if v else "FAIL"))
        with open(os.path.join(OUT, "result.json"), "w") as f:
            json.dump(R, f, indent=2)
        print("\nfull result -> test/out/result.json")
        print("PASS" if R["PASS"] else "FAIL")
        sys.exit(0 if R["PASS"] else 1)

    finally:
        if ch:
            ch.kill()
        srv.shutdown()
        srv.server_close()
        print("server stopped, browser killed")


if __name__ == "__main__":
    main()
