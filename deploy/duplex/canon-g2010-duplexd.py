#!/usr/bin/env python3
"""canon-g2010-duplexd — tiny local HTTP confirm service for server-side manual
duplex (no client installs; open it in any browser on any device).

Endpoints
---------
  GET  /                 HTML page: pending duplex jobs + "Flip & Continue" button
  GET  /status           JSON: {jobs: [...]}  (jobid, user, title, fronts/backs)
  POST /confirm/<jobid>  Submit the flip confirmation (requires the job's token)
  GET  /healthz          "ok"

Confirm-time printing
---------------------
On confirm the daemon delivers the job's BACKS stream to the native sink
(TCP 9100) — i.e. straight to /dev/usb/lp0 — or, in dry-run mode, writes it to a
file. The confirmation is bound to a per-job token so a stray click cannot print
the wrong job, and it is one-shot/idempotent (the record is marked done).

Safety: --dry-run never opens the device or the sink; it writes to a directory.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import pathlib
import socket
import sys
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE_DIR = pathlib.Path(os.environ.get("CANON_G2010_DUPLEX_STATE",
                                        "/var/tmp/canon-g2010-duplex"))
TIMEOUT = float(os.environ.get("CANON_G2010_DUPLEX_TIMEOUT", "0"))  # 0 = never expire


def _jobs():
    out = []
    if not STATE_DIR.is_dir():
        return out
    for jf in sorted(STATE_DIR.glob("*.json")):
        try:
            rec = json.loads(jf.read_text())
        except Exception:
            continue
        if TIMEOUT and rec.get("state") == "awaiting_confirm" \
                and time.time() - rec.get("created", 0) > TIMEOUT:
            rec["state"] = "expired"
            jf.write_text(json.dumps(rec, indent=2))
        prn = jf.with_suffix(".prn")
        rec["has_backs"] = prn.exists()
        out.append(rec)
    return out


def _deliver(rec, dry_dir):
    # Variant A: backs are a CUPS raster; turn them into colour BJRaster3 with the
    # stock Gutenprint filter (using the queue PPD), then deliver to the sink.
    backs_ras = rec.get("backs_raster")
    if backs_ras and os.path.exists(backs_ras):
        sb = os.environ.get("CUPS_SERVERBIN", "/usr/lib/cups")
        ppd = os.environ.get("PPD", "/usr/local/lib/canon-g2010/canon-g2010-color.ppd")
        opts = ""
        try:
            import re
            txt = open(ppd).read()
            opts = " ".join(f"{k}={v.strip().strip(chr(34))}"
                            for k, v in re.findall(r'^\*Default([A-Za-z0-9_]+):\s*(.+)$',
                                                   txt, re.M))
        except OSError:
            pass
        p = subprocess.run([f"{sb}/filter/rastertogutenprint.5.3",
                            "duplex-backs", "cups", "backs", "1", opts, backs_ras],
                           capture_output=True, timeout=1800)
        if p.returncode:
            raise RuntimeError(p.stderr.decode(errors="replace")[:300])
        data = p.stdout
        if dry_dir:
            os.makedirs(dry_dir, exist_ok=True)
            out = os.path.join(dry_dir, f"backs-{rec['jobid']}-{int(time.time())}.prn")
            with open(out, "wb") as fh:
                fh.write(data)
            return f"dry-run wrote {len(data)} B -> {out}"
        host = rec.get("sink_host", "127.0.0.1")
        port = int(rec.get("sink_port", 9100))
        with socket.create_connection((host, port), timeout=30) as s:
            s.sendall(data)
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            time.sleep(0.5)
        return f"sent {len(data)} colour BJRaster3 B to sink {host}:{port}"
    # Variant A alternate: backs are a PDF; submit them to the CUPS queue so the
    # stock Gutenprint chain turns them into COLOUR BJRaster3 -> sink.
    backs_pdf = rec.get("backs_pdf")
    prn = STATE_DIR / f"{rec['jobid']}.prn"
    if backs_pdf and os.path.exists(backs_pdf):
        if dry_dir:
            os.makedirs(dry_dir, exist_ok=True)
            out = os.path.join(dry_dir, f"backs-{rec['jobid']}-{int(time.time())}.pdf")
            with open(out, "wb") as fh:
                fh.write(open(backs_pdf, "rb").read())
            return f"dry-run copied backs PDF -> {out}"
        queue = rec.get("queue", "G2010-series")
        p = subprocess.run(["lp", "-d", queue, "-o", "canon-duplex-pass=backs",
                            backs_pdf], capture_output=True, timeout=120)
        if p.returncode:
            raise RuntimeError(p.stderr.decode(errors="replace"))
        return f"submitted backs to queue {queue}: {p.stdout.decode().strip()}"
    # legacy path: a raw .prn backs stream delivered straight to the sink
    data = prn.read_bytes()
    if dry_dir:
        os.makedirs(dry_dir, exist_ok=True)
        out = os.path.join(dry_dir, f"backs-{rec['jobid']}-{int(time.time())}.prn")
        with open(out, "wb") as fh:
            fh.write(data)
        return f"dry-run wrote {len(data)} B -> {out}"
    host = rec.get("sink_host", "127.0.0.1")
    port = int(rec.get("sink_port", 9100))
    with socket.create_connection((host, port), timeout=30) as s:
        s.sendall(data)
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        time.sleep(0.5)
    return f"sent {len(data)} B to sink {host}:{port}"


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Canon G2010 — Manual Duplex</title>
<style>
 body{{font-family:system-ui,Arial,sans-serif;margin:0;background:#f4f5f7;color:#1c1e21}}
 .wrap{{max-width:640px;margin:0 auto;padding:24px}}
 h1{{font-size:22px}} .job{{background:#fff;border:1px solid #dcdfe3;border-radius:10px;
  padding:16px;margin:14px 0}}
 .meta{{color:#57606a;font-size:14px;margin-bottom:10px}}
 button{{font-size:18px;padding:14px 22px;border:0;border-radius:10px;
  background:#1a7f37;color:#fff;width:100%;cursor:pointer}}
 button:active{{background:#146c2e}} .done{{color:#1a7f37;font-weight:600}}
 .empty{{color:#57606a}} code{{background:#eef1f4;padding:2px 6px;border-radius:4px}}
</style></head><body><div class="wrap">
<h1>Canon G2010 — ручная двусторонняя печать</h1>
<p class="meta">Лицевые страницы напечатаны. <b>Переверни листы</b> в лотке и нажми
кнопку, чтобы напечатать обратные.</p>
{body}
<p class="meta">Обнови страницу, если задание ещё не появилось.</p>
</div></body></html>"""


def _render():
    jobs = _jobs()
    pending = [j for j in jobs if j.get("state") == "awaiting_confirm" and j.get("has_backs")]
    done = [j for j in jobs if j.get("state") == "done"]
    parts = []
    for j in pending:
        parts.append(
            f'<div class="job"><div class="meta">Задание <code>{html.escape(str(j["jobid"]))}</code>'
            f' · {html.escape(j.get("user",""))} · {html.escape(j.get("title",""))}<br>'
            f'Лицевые: {j.get("fronts_pages")} → Обратные: {j.get("backs_pages")}</div>'
            f'<form method="post" action="/confirm/{html.escape(str(j["jobid"]))}">'
            f'<input type="hidden" name="token" value="{html.escape(j["token"])}">'
            f'<button type="submit">Перевернул — Печатать обратные</button></form></div>')
    for j in done[-5:]:
        parts.append(f'<div class="job done">✅ Задание <code>{html.escape(str(j["jobid"]))}</code> завершено.</div>')
    if not pending and not done:
        parts.append('<div class="job empty">Нет заданий, ожидающих подтверждения.</div>')
    return PAGE.format(body="\n".join(parts))


class Handler(BaseHTTPRequestHandler):
    dry_dir = None

    def log_message(self, *a):
        sys.stderr.write("[duplexd] " + (a[0] % a[1:]) + "\n")

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/healthz"):
            return self._send(200, "ok", "text/plain")
        if self.path.startswith("/status"):
            return self._send(200, json.dumps({"jobs": _jobs()}), "application/json")
        return self._send(200, _render())

    def do_POST(self):
        if not self.path.startswith("/confirm/"):
            return self._send(404, "not found", "text/plain")
        jobid = self.path.split("/confirm/", 1)[1].split("?", 1)[0]
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode(errors="replace") if length else ""
        token = ""
        for pair in body.split("&"):
            if pair.startswith("token="):
                token = pair.split("=", 1)[1]
        jf = STATE_DIR / f"{jobid}.json"
        if not jf.exists():
            return self._send(404, _render())
        rec = json.loads(jf.read_text())
        if TIMEOUT and rec.get("state") == "awaiting_confirm" \
                and time.time() - rec.get("created", 0) > TIMEOUT:
            rec["state"] = "expired"
            jf.write_text(json.dumps(rec, indent=2))
        if rec.get("token") != token:
            return self._send(403, "<h1>403</h1><p>Неверный токен.</p>")
        if rec.get("state") == "done":
            return self._send(200, _render())          # idempotent
        if rec.get("state") == "expired":
            return self._send(410, "<h1>410</h1><p>Задание истекло.</p>")
        try:
            msg = _deliver(rec, self.dry_dir)
            rec["state"] = "done"
            rec["delivered"] = msg
            rec["delivered_at"] = time.time()
            jf.write_text(json.dumps(rec, indent=2))
            sys.stderr.write(f"[duplexd] confirmed {jobid}: {msg}\n")
        except Exception as e:
            return self._send(500, f"<h1>500</h1><pre>{html.escape(str(e))}</pre>")
        return self._send(200, _render())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--dry-run", action="store_true",
                    help="write backs to a file instead of the sink")
    ap.add_argument("--dry-run-dir", default="/var/tmp/canon-g2010-duplex-dryrun")
    a = ap.parse_args()
    Handler.dry_dir = a.dry_run_dir if a.dry_run else None
    srv = ThreadingHTTPServer((a.listen, a.port), Handler)
    mode = "DRY-RUN -> " + a.dry_run_dir if a.dry_run else "SINK delivery"
    sys.stderr.write(f"[duplexd] listening {a.listen}:{a.port} ({mode}), "
                     f"state {STATE_DIR}\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
