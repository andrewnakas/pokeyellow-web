#!/usr/bin/env python3
"""Does EmulatorJS's beforeunload handler empty the battery-save store? No race.

This is the DETERMINISTIC detector for trap 10, and it exists because the obvious
experiment is a weak one. "Navigate away, come back, look" measures the
user-visible outcome, but whether EmulatorJS's teardown beats the page dying is a
race: `pokegold` saw 3 failures in 5 runs, `pokefirered` saw 0 failures in 8 on a
page that was provably broken. A soak test that passes 8/8 on a broken page is not
evidence of anything.

So: fire the event by hand and do not navigate at all.

    window.dispatchEvent(new Event("beforeunload"))

EmulatorJS's listener (emulator.js:1092) fires its `exit` event; GameManager's exit
handler (GameManager.js:44) ends with FS.unmount("/data/saves"), and the IndexedDB
store behind that mount comes back EMPTY. With no actual unload there is nothing to
race, so the answer is the same every run.

The clearest single signal needs no storage read at all: `autoPersist` on the
/data/saves mount flips from true to false, because the IDBFS mount has been torn
down and replaced.

Two arms, one query parameter apart:

    (default)         index.html patches window.addEventListener and drops it
    ?stockunload=1    index.html leaves EmulatorJS's listener in place

AND THE CONTROL IS CHECKED. If the parameter silently did nothing, both arms would
be the same page and "they behaved identically" would mean nothing. The page logs
which arm it is in, and this script asserts that the "dropped a listener" line is
present in exactly one arm before it believes any comparison between them.

Usage: save_mechanism.py [--rom PATH]
"""

import argparse
import hashlib
import json
import os
import socket
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cdp           # noqa: E402
import serve         # noqa: E402
import browsercheck as bc   # noqa: E402  (reuse idb_saves / load_rom_via_file_input)

OUT = os.path.join(HERE, "out")
UPSTREAM = os.path.normpath(os.path.join(HERE, "..", "upstream"))

MOUNT_STATE = """(function () {
  try {
    const n = window.EJS_emulator.gameManager.FS.lookupPath('/data/saves').node;
    return {mounted: !!n.mount,
            autoPersist: !!(n.mount && n.mount.opts && n.mount.opts.autoPersist),
            type: (n.mount && n.mount.type && n.mount.type.name) || null};
  } catch (e) { return {error: String(e)}; }
})()"""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def files_of(idb):
    if not idb or "files" not in idb:
        return {}
    return {k: v["len"] for k, v in idb["files"].items()}


def run_arm(rom, stock):
    """One arm. Returns a dict; never navigates away from the page under test."""
    label = "stock EmulatorJS" if stock else "this page (listener dropped)"
    print("\n--- arm: %s" % label, flush=True)

    port = free_port()
    srv = serve.start(port, rom_path=rom)
    nonce = uuid.uuid4().hex
    url = ("http://127.0.0.1:%d/index.html?mute=1&nonce=%s%s"
           % (port, nonce, "&stockunload=1" if stock else ""))
    ch = None
    r = {"arm": "stock" if stock else "patched", "url_has_stockunload": stock}
    try:
        # Ephemeral debug port in a private profile, always. A fixed port attaches
        # to whatever another agent already has running on this box.
        ch = cdp.Chrome()
        bc.attach(ch)
        bc.load_rom_via_file_input(ch, url, "/_test/rom.gb")
        bc.assert_my_tab(ch, nonce)
        if not bc.wait_started(ch, 90):
            raise RuntimeError("core never started")
        bc.assert_core(ch, "gambatte")

        # The control. Must differ between the arms, or the comparison is void.
        logs = bc.page_logs(ch, "unload-patch")
        r["unload_logs"] = logs
        r["listener_dropped"] = any("dropped" in t for t in logs)
        r["arm_declared"] = ("DISABLED" if any("DISABLED" in t for t in logs)
                             else "ENABLED" if any("ENABLED" in t for t in logs)
                             else None)
        print("    page says: %s   (dropped a listener: %s)"
              % (r["arm_declared"], r["listener_dropped"]))

        # gambatte writes a .srm shortly after boot, so no gameplay is needed to
        # reproduce this -- which is the point: it is not about losing progress you
        # made, it is about the store being emptied on the way out.
        deadline = time.time() + 90
        while time.time() < deadline:
            ch.pump(2.0)
            if files_of(bc.idb_saves(ch)):
                break
        try:
            ch.eval("window.__flushSave && window.__flushSave('mechanism', true)",
                    await_promise=True)
        except Exception:
            pass
        ch.pump(2.0)

        r["before"] = files_of(bc.idb_saves(ch))
        r["mount_before"] = ch.eval(MOUNT_STATE)
        print("    after boot         files=%s" % r["before"])
        print("                       mount=%s" % r["mount_before"])
        if not r["before"]:
            raise RuntimeError("no save in IndexedDB before the test even started")

        print("    dispatching beforeunload (no actual unload, so no race) ...")
        ch.eval("window.dispatchEvent(new Event('beforeunload'))")
        ch.pump(5.0)

        r["after"] = files_of(bc.idb_saves(ch))
        r["mount_after"] = ch.eval(MOUNT_STATE)
        print("    after beforeunload files=%s" % r["after"])
        print("                       mount=%s" % r["mount_after"])

        r["destroyed"] = bool(r["before"]) and not r["after"]
        r["unmounted"] = bool(r["mount_before"].get("autoPersist")
                              and not r["mount_after"].get("autoPersist"))
        print("    -> save destroyed by beforeunload: %s"
              % ("YES" if r["destroyed"] else "no"))
        print("    -> /data/saves mount torn down   : %s"
              % ("YES" if r["unmounted"] else "no"))
        return r
    finally:
        if ch:
            ch.kill()
        srv.shutdown()
        srv.server_close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", default=os.path.join(UPSTREAM, "pokeyellow.gbc"))
    args = ap.parse_args()
    if not os.path.exists(args.rom):
        sys.exit("no ROM at %s -- run ./build.sh first" % args.rom)
    os.makedirs(OUT, exist_ok=True)

    print("ROM  %s" % args.rom)
    print("sha1 %s" % hashlib.sha1(open(args.rom, "rb").read()).hexdigest())

    stock = run_arm(args.rom, stock=True)
    patched = run_arm(args.rom, stock=False)

    # Verify the control BEFORE believing the comparison.
    control_ok = (stock["listener_dropped"] is False
                  and patched["listener_dropped"] is True
                  and stock["arm_declared"] == "DISABLED"
                  and patched["arm_declared"] == "ENABLED")

    res = {"stock": stock, "patched": patched, "control_ok": control_ok}
    res["PASS"] = bool(control_ok
                       and stock["destroyed"] and stock["unmounted"]
                       and not patched["destroyed"] and not patched["unmounted"])

    print("\n=== result")
    print("  %-24s %-28s %s" % ("", "stock EmulatorJS", "this page"))
    print("  %-24s %-28s %s" % ("files before", stock["before"], patched["before"]))
    print("  %-24s %-28s %s" % ("files after beforeunload", stock["after"], patched["after"]))
    print("  %-24s %-28s %s" % ("autoPersist before",
                                stock["mount_before"].get("autoPersist"),
                                patched["mount_before"].get("autoPersist")))
    print("  %-24s %-28s %s" % ("autoPersist after",
                                stock["mount_after"].get("autoPersist"),
                                patched["mount_after"].get("autoPersist")))
    print("\n  control (arms really differ): %s" % ("ok" if control_ok else "BROKEN"))
    if not control_ok:
        print("  !! the two arms may be the same page; the comparison above means nothing")
    print("  PASS" if res["PASS"] else "  FAIL")

    with open(os.path.join(OUT, "save_mechanism.json"), "w") as f:
        json.dump(res, f, indent=2)
    print("\nwritten to test/out/save_mechanism.json")
    return 0 if res["PASS"] else 1


if __name__ == "__main__":
    sys.exit(main())
