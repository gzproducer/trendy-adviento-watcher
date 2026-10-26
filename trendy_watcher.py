#!/usr/bin/env python3
"""Avisa cuando Trendy publique el calendario de adviento nuevo.

La tienda (maquillajetrendyshop.com) es VTEX. Cualquier URL responde 200, asi que
no sirve adivinar rutas; en cambio se vigilan 4 senales y se avisa cuando aparece
algo NUEVO respecto a la primera corrida (la "linea base"):

  1. sitemaps          -> URLs nuevas con "adviento"/"calendario"
  2. buscador interno  -> productos nuevos al buscar "adviento"/"calendario"
  3. home              -> enlaces/bloques nuevos con "adviento"
  4. pagina del 2025   -> cambia el texto de /calendario-de-adviento-nick-90s

Uso:
  python trendy_watcher.py                 # revisa cada 10 min, para siempre
  python trendy_watcher.py --once          # una sola revision (para cron / Actions)
  python trendy_watcher.py --test-notify   # manda una notificacion de prueba

Notificaciones (opcional, por variables de entorno):
  NTFY_TOPIC=<tema-secreto>                # push al celular con la app ntfy
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID    # mensaje por Telegram
Sin ninguna, solo imprime en consola y suena un beep.
"""
import argparse
import gzip
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

BASE = "https://www.maquillajetrendyshop.com"
KEYWORDS = re.compile(r"adviento|advent|calendario", re.I)
SEARCH_TERMS = ("adviento", "calendario de adviento", "advent")
KNOWN_PAGE = "/calendario-de-adviento-nick-90s"  # edicion 2025, ya publicada
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trendy_state.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def fetch(url, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 - red inestable, reintentar
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def visible_text(page):
    page = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page))).strip()


# ---- senales: cada una devuelve un set de strings; lo nuevo dispara el aviso ----

def sig_sitemaps():
    found = set()
    index = fetch(f"{BASE}/sitemap.xml")
    for sm in re.findall(r"<loc>([^<]+)</loc>", index):
        for url in re.findall(r"<loc>([^<]+)</loc>", fetch(sm)):
            if KEYWORDS.search(url):
                found.add(url)
    return found


def sig_search():
    found = set()
    for term in SEARCH_TERMS:
        q = urllib.parse.quote(term)
        data = json.loads(fetch(f"{BASE}/api/catalog_system/pub/products/search?ft={q}&_from=0&_to=49"))
        for p in data:
            found.add(f"{p.get('productName')} -> {p.get('link')}")
    return found


def sig_home():
    page = fetch(f"{BASE}/")
    return set(re.findall(r"[\w\-/#.:]*(?:adviento|advent)[\w\-/#.]*", page, re.I))


def sig_page():
    text = visible_text(fetch(BASE + KNOWN_PAGE))
    return {"texto-pagina-2025:" + hashlib.sha1(text.encode()).hexdigest()[:12]}


SIGNALS = {"sitemap": sig_sitemaps, "buscador": sig_search, "home": sig_home, "pagina-2025": sig_page}


# ---- notificaciones ----

def notify(title, body, url=BASE):
    log(f"NOTIFICACION: {title} | {body}")
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(1000, 300)
    except Exception:  # noqa: BLE001 - no es Windows
        pass
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
            headers={"Title": title, "Click": url, "Priority": "urgent", "Tags": "gift,tada"},
        )
        urllib.request.urlopen(req, timeout=20).read()
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        data = urllib.parse.urlencode({"chat_id": chat, "text": f"{title}\n{body}\n{url}"}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20).read()


# ---- logica principal ----

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({k: sorted(v) for k, v in state.items()}, f, ensure_ascii=False, indent=1)


def check_once():
    saved = load_state()
    state = {k: set(v) for k, v in (saved or {}).items()}
    alerts = []
    for name, fn in SIGNALS.items():
        try:
            current = fn()
        except Exception as e:  # noqa: BLE001 - una senal caida no frena las demas
            log(f"[{name}] fallo (se reintenta luego): {e}")
            continue
        if saved is None or name not in state:
            state[name] = current
            continue
        new = current - state[name]
        if new:
            alerts.append((name, sorted(new)))
        state[name] |= current
    if saved is None:
        save_state(state)
        log("Primera corrida: linea base guardada, no se avisa nada. Desde ahora solo avisa lo nuevo.")
        return
    if alerts:
        body = "\n".join(f"[{n}] " + " ; ".join(items)[:300] for n, items in alerts)
        # Si notify() falla, no se guarda el estado: el aviso se reintenta en la proxima revision.
        notify("Trendy: posible calendario de adviento NUEVO", body)
    else:
        log("Sin novedades.")
    save_state(state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=600, help="segundos entre revisiones (def. 600)")
    ap.add_argument("--duration", type=int, help="revisar cada --interval durante N segundos y salir (para Actions)")
    ap.add_argument("--test-notify", action="store_true")
    args = ap.parse_args()
    if args.test_notify:
        notify("Trendy bot: prueba", "Si ves esto, las notificaciones funcionan.")
        return
    if args.once:
        check_once()
        return
    if args.duration:
        end = time.time() + args.duration
        while True:
            check_once()
            if time.time() + args.interval >= end:
                return
            time.sleep(args.interval)
    log(f"Vigilando {BASE} cada {args.interval}s. Ctrl+C para parar.")
    while True:
        check_once()
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
