#!/usr/bin/env -S uv run --with playwright --script
"""Save spaces, end to end in the page (web/PLATFORM.md, "Your files"), with
synthesized data only: a `pak1.pak` made the way verify_content.py makes it
(id's `gfx/pop.lmp` from common.c's table, the shareware e1m1 as e2m1), a
second one that differs by a file, and a mission pack's `hipnotic/pak0.pak`.

1. A fresh browser plays in the default space: a save is kept under its
   plain path, as every save was before spaces.
2. With the player's own pak1 the page starts in that pak's space: the
   shareware save is not listed, and a save made here is kept under the
   space's key only.
3. A different pak1 is a different space; the first pak1 again finds its
   own saves again.
4. Removing the files plays the default space again — its save loads, the
   others are not listed but still kept.
5. A mission pack with the same pak1 is a space of its own.
6. A browser that kept its own pak1 before spaces existed (its saves under
   the plain paths, no marker) finds its saves in the pak's space at its
   first start with spaces, and they stay in the default space too; a start
   that could not hash the paks before that copies nothing and leaves no
   marker, so the adoption still happens.

Usage: verify_save_spaces.py [deploydir]   (PLATFORM.md: index.html, wasi.js,
quake.wasm, id1/pak0.pak)."""
import base64, os, sys, time
from playwright.sync_api import sync_playwright
import isolated
from isolated import POP_LMP, pak_file, write_pak

WEB = isolated.webdir()
PORT = isolated.port(8181)
PAK0 = os.path.join(WEB, "id1", "pak0.pak")
E1M1 = pak_file(PAK0, "maps/e1m1.bsp")
PAK1 = write_pak([("gfx/pop.lmp", POP_LMP), ("maps/e2m1.bsp", E1M1)])
PAK1_OTHER = write_pak([("gfx/pop.lmp", POP_LMP), ("maps/e2m1.bsp", E1M1), ("maps/e2m2.bsp", E1M1)])
HIP_PAK0 = write_pak([("maps/hip1m1.bsp", E1M1)])

ADD = """async (items) => quake.content.add(items.map(([name, path, b64]) =>
    ({ name, path, bytes: Uint8Array.from(atob(b64), c => c.charCodeAt(0)) })))"""
RAW_KEYS = """() => new Promise((res, rej) => { const r = indexedDB.open('quake-rs');
    r.onsuccess = () => { const q = r.result.transaction('files').objectStore('files').getAllKeys();
      q.onsuccess = () => { res(q.result.map(String)); r.result.close(); }; q.onerror = () => rej(q.error); };
    r.onerror = () => rej(r.error); })"""
# A browser from before spaces: its own pak1 and a save under the plain
# path, kept the way that page kept them (database version 2, its two
# stores), and no marker.
SEED = """([pak, save]) => new Promise((res, rej) => {
    const r = indexedDB.open('quake-rs', 2);
    r.onupgradeneeded = () => { for (const n of ['files', 'music']) r.result.createObjectStore(n); };
    r.onsuccess = () => {
      const tx = r.result.transaction('files', 'readwrite'), st = tx.objectStore('files');
      st.put(Uint8Array.from(atob(pak), c => c.charCodeAt(0)), 'id1/pak1.pak');
      st.put(new TextEncoder().encode(save), 'id1/s7.sav');
      tx.oncomplete = () => { r.result.close(); res(); };
      tx.onerror = () => rej(tx.error);
    };
    r.onerror = () => rej(r.error);
  })"""

httpd = isolated.serve(WEB, PORT)
fails = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fails.append(what)


def b64(b):
    return base64.b64encode(b).decode()


def ready(pg):
    """The page is up and has the program's word on its files (polled: a
    reload the page makes itself is waited out too)."""
    until = time.time() + 180
    while time.time() < until:
        try:
            if pg.evaluate("!!(window.quake && quake.ready && quake.contentPath !== undefined)"):
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise SystemExit("the page did not come up")


def line(pg, cmd):
    return pg.evaluate(f"quake.callLine({('exec ' + cmd)!r})")


def last_lines(pg, n=3):
    return pg.evaluate("quake.text('console_text')").rstrip().splitlines()[-n:]


def go_map(pg, name):
    line(pg, f"map {name}")
    isolated.wait_until(pg, f"quake.text('map_name').then(m => m === 'maps/{name}.bsp')", 20)


def save(pg, slot, gamedir="id1"):
    line(pg, f"save s{slot}")
    isolated.wait_until(pg, f"quake.kept('{gamedir}/s{slot}.sav').then(t => t !== null)", 10)


def loads(pg, slot):
    """`load s<slot>`: true if it loaded, false if the program found no such
    file (id's "ERROR: couldn't open.")."""
    line(pg, f"load s{slot}")
    time.sleep(0.8)
    return not any("couldn't open" in l for l in last_lines(pg))


def add(pg, items):
    try:
        pg.evaluate(ADD, [[name, path, b64(data)] for name, path, data in items])
    except Exception:
        pass                      # the page reloads under the call
    time.sleep(1.5)
    ready(pg)


def space(pg):
    return pg.evaluate("quake.saveSpace()")


with sync_playwright() as p:
    br = isolated.launch(p, ["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
    pg = br.new_page(viewport={"width": 900, "height": 700})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
    pg.goto(f"http://127.0.0.1:{PORT}/index.html", wait_until="load")
    ready(pg)
    pg.mouse.click(450, 300)

    # --- 1. The default space.
    check(space(pg) == "", "a fresh browser plays in the default space")
    go_map(pg, "e1m1")
    save(pg, 0)
    keys = pg.evaluate(RAW_KEYS)
    check("id1/s0.sav" in keys and "ns:-:id1" in keys, f"s0 kept under its plain path; the marker left ({keys})")
    s0_text = pg.evaluate("quake.kept('id1/s0.sav')")

    # --- 2. The player's own pak1: its space.
    add(pg, [("pak1.pak", "id1/pak1.pak", PAK1)])
    note = pg.inner_text("#fileNote") if pg.is_visible("#fileNote") else ""
    check("earlier saves stay with the shareware game" in note, f"the line under click to start says where the earlier saves are ({note!r})")
    a = space(pg)
    check(len(a) == 16 and all(c in "0123456789abcdef" for c in a), f"pak1 makes a space ({a!r})")
    check(not loads(pg, 0), "the shareware save is not listed there")
    go_map(pg, "e2m1")
    save(pg, 1)
    keys = pg.evaluate(RAW_KEYS)
    check(f"ns:{a}:id1/s1.sav" in keys and "id1/s1.sav" not in keys, "s1 kept under the space's key only")

    # --- 3. Another pak1, then the first again.
    add(pg, [("pak1.pak", "id1/pak1.pak", PAK1_OTHER)])
    note = pg.inner_text("#fileNote") if pg.is_visible("#fileNote") else ""
    check("earlier saves stay with the files before" in note, f"another pak1: the line says the saves stay with the files before ({note!r})")
    b = space(pg)
    check(len(b) == 16 and b != a, f"a different pak1 is a different space ({b!r})")
    check(not loads(pg, 1), "the first pak1's save is not listed there")
    go_map(pg, "e2m1")
    save(pg, 2)
    add(pg, [("pak1.pak", "id1/pak1.pak", PAK1)])
    check(space(pg) == a, "the first pak1 again: its space again")
    check(loads(pg, 1) and pg.evaluate("quake.text('map_name')") == "maps/e2m1.bsp", "and its save loads")
    check(not loads(pg, 2), "the other pak1's save is not listed")

    # --- 4. Removing the files: the default space.
    try:
        pg.evaluate("quake.content.remove()")
    except Exception:
        pass
    time.sleep(1.5)
    ready(pg)
    check(space(pg) == "", "removing the files plays the default space")
    check(loads(pg, 0), "s0 loads again")
    check(not loads(pg, 1) and not loads(pg, 2), "the spaces' saves are not listed")
    keys = pg.evaluate(RAW_KEYS)
    check(f"ns:{a}:id1/s1.sav" in keys and f"ns:{b}:id1/s2.sav" in keys, "but still kept")

    # --- 5. A mission pack: a space of its own.
    add(pg, [("pak1.pak", "id1/pak1.pak", PAK1), ("pak0.pak", "hipnotic/pak0.pak", HIP_PAK0)])
    check(space(pg) == a, "Quake with the pack kept: still pak1's space (the pack is not in its search path)")
    add(pg, [("pak0.pak", "hipnotic/pak0.pak", HIP_PAK0)])
    note = pg.inner_text("#fileNote") if pg.is_visible("#fileNote") else ""
    check(space(pg) == a and "added hipnotic/pak0.pak" in note and "earlier saves" not in note,
          f"adding a file that leaves the space as it was says nothing about saves ({note!r})")
    pg.goto(f"http://127.0.0.1:{PORT}/index.html?game=hipnotic", wait_until="load")
    ready(pg)
    h = space(pg)
    check(len(h) == 16 and h not in (a, b), f"?game=hipnotic: a space of its own ({h!r})")
    go_map(pg, "hip1m1")
    save(pg, 3, "hipnotic")
    keys = pg.evaluate(RAW_KEYS)
    check(f"ns:{h}:hipnotic/s3.sav" in keys and "ns:-:hipnotic" in keys, "its save under its key; its marker left")

    # --- 6. A browser from before spaces.
    ctx = br.new_context(viewport={"width": 900, "height": 700})
    old = ctx.new_page()
    old.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
    old.goto(f"http://127.0.0.1:{PORT}/icons/icon-192.png")   # the origin, before the page has run here (a page every browser shows)
    old.evaluate(SEED, [b64(PAK1), s0_text])
    # Its first start with spaces cannot hash the paks: the default space,
    # nothing copied, no marker — so the next start still adopts.
    broken = ctx.new_page()
    broken.add_init_script("SubtleCrypto.prototype.digest = function () { return Promise.reject(new Error('no digest here')); };")
    broken.goto(f"http://127.0.0.1:{PORT}/index.html", wait_until="load")
    ready(broken)
    check(space(broken) == "", "a start that cannot hash the paks keeps to the default space")
    keys = broken.evaluate(RAW_KEYS)
    check(not any(k.startswith("ns:") for k in keys), f"and copies nothing, leaves no marker ({keys})")
    broken.close()
    old.goto(f"http://127.0.0.1:{PORT}/index.html", wait_until="load")
    ready(old)
    check(space(old) == a, "its pak1 makes the same space as anywhere")
    old.mouse.click(450, 300)
    check(loads(old, 7), "its old save is listed in the pak's space")
    keys = old.evaluate(RAW_KEYS)
    check(f"ns:{a}:id1/s7.sav" in keys and "id1/s7.sav" in keys and "ns:-:id1" in keys,
          "copied there once, kept in the default space too, the marker left")
    ctx.close()

    check(not errs, f"no console errors ({errs[:5]})")
    br.close()
httpd.shutdown()
if fails:
    print(f"FAILED: {len(fails)}")
    sys.exit(1)
print("done: save spaces verified (default space unchanged, per-pak spaces, back again, removal, a mission pack, a pre-spaces browser)")
