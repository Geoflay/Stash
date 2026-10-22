"""Links sent from a phone to the computer, on the same Wi-Fi (Settings > General), and files sent from the
computer to a phone (the Library's "Send to a phone").

The computer runs a small web server while this is turned on. Its address (shown as a QR code) holds a secret
key: without it, nothing is accepted. Opened in the phone's browser, it is a page where a link is pasted and
sent; the Android app sends the links shared to it there too (send_link). Each link received is analyzed in the
Download tab. A file sent to a phone gets an address of its own (another secret), which downloads it.
"""

import http.server
import json
import os
import secrets
import shutil
import socket
import threading
import time
import urllib.request
from html import escape
from pathlib import Path
from urllib.parse import quote

from core.links import all_links

PORT = 8790  # the first free one from here is used
PORT_TRIES = 10
DISCOVERY_PORT = 8789  # where the devices running the app answer those looking for them (UDP)
HELLO = "video-downloader"
LOOK_SECONDS = 1.5  # how long answers are waited for

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Send to Video downloader</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#f6f6fb;color:#1b1b1f}
@media (prefers-color-scheme:dark){body{background:#131318;color:#e5e1e9}textarea,input{background:#1f1f25;color:#e5e1e9}}
h1{font-size:20px}textarea{width:100%%;box-sizing:border-box;height:120px;font-size:16px;padding:8px;
border-radius:8px;border:1px solid #888}button{font-size:16px;padding:12px 20px;border:0;border-radius:24px;
background:#4f46e5;color:#fff;margin-top:12px}#status{margin-top:12px}small{display:block;margin-top:24px;opacity:.7}
input{width:100%%;box-sizing:border-box;font-size:14px;padding:8px;border-radius:8px;border:1px solid #888}
</style></head><body>
<h1>Send to %(name)s</h1>
<p>Paste one link or several: they are added to the computer's download queue.</p>
<textarea id="links" placeholder="https://…"></textarea>
<button onclick="send()">Send to the computer</button>
<div id="status"></div>
<small>Android app: paste this address in Settings &gt; Send links to a computer, then use Share &gt; Video
downloader in any app.<br><input id="address" readonly value="%(address)s" onclick="this.select();
document.execCommand('copy')"></small>
<script>
async function send(){
  const box=document.getElementById('links'),status=document.getElementById('status');
  try{
    const r=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:box.value})});
    const d=await r.json();
    status.textContent=d.added?d.added+' link'+(d.added>1?'s':'')+' sent.':'No link found in the text.';
    if(d.added)box.value='';
  }catch(e){status.textContent='The computer did not answer: is the app open, on the same Wi-Fi?';}
}
</script></body></html>"""


def new_key():
    return secrets.token_urlsafe(9)


def local_address():
    """This computer's address on the Wi-Fi (nothing is sent: a UDP socket only picks the way out)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


class LinkServer:
    """`on_link(url)` is called (from the server's thread) for every link received with the key;
    `on_offer(url, name, device_id)` for those sent by a device that found this one on the Wi-Fi (the user
    accepts them, or trusts the device)."""

    def __init__(self, key, on_link, name="Video downloader", on_offer=None):
        self.key, self.on_link, self.name, self.on_offer = key, on_link, name, on_offer
        self.server = None
        self.files = {}  # secret -> path of a file sent to a phone

    def share_file(self, path):
        """The address downloading `path` (to show as a QR code); the server must be started."""
        token = next((t for t, p in self.files.items() if p == str(path)), None) or new_key()
        self.files[token] = str(path)
        return "%s/file/%s/%s" % (self.address, token, quote(Path(path).name))

    @property
    def address(self):
        return "http://%s:%d/%s" % (local_address(), self.server.server_port, self.key) if self.server else ""

    def start(self, port=PORT):
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body, kind):
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def allowed(self):
                if self.path.split("?")[0].strip("/") == owner.key:
                    return True
                self.reply(404, "Not found", "text/plain; charset=utf-8")
                return False

            def send_file(self, path):
                try:
                    size = os.path.getsize(path)
                    source = open(path, "rb")
                except OSError:
                    self.reply(404, "This file is not there any more.", "text/plain; charset=utf-8")
                    return
                with source:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(size))
                    self.send_header("Content-Disposition", "attachment; filename*=UTF-8''%s"
                                     % quote(Path(path).name))
                    self.end_headers()
                    try:
                        shutil.copyfileobj(source, self.wfile, 1024 * 1024)
                    except OSError:  # the phone stopped the download
                        pass

            def do_GET(self):
                parts = self.path.split("?")[0].strip("/").split("/")
                if len(parts) >= 3 and parts[0] == owner.key and parts[1] == "file" and parts[2] in owner.files:
                    self.send_file(owner.files[parts[2]])
                elif self.allowed():
                    self.reply(200, PAGE % {"name": escape(owner.name), "address": escape(owner.address)},
                               "text/html; charset=utf-8")

            def do_POST(self):
                offer = self.path.split("?")[0].strip("/") == "offer" and owner.on_offer is not None
                if not offer and not self.allowed():
                    return
                length = min(int(self.headers.get("Content-Length") or 0), 100_000)
                body = self.rfile.read(length).decode("utf-8", "replace")
                try:
                    data = json.loads(body)
                    text = str(data.get("text") or "")
                except (ValueError, AttributeError):
                    data, text = {}, body
                links = all_links(text)
                for link in links:
                    if offer:
                        owner.on_offer(link, str(data.get("name") or "?")[:60], str(data.get("id") or ""))
                    else:
                        owner.on_link(link)
                self.reply(200, json.dumps({"added": len(links)}), "application/json")

        for tried in range(PORT_TRIES):
            try:
                self.server = http.server.ThreadingHTTPServer(("0.0.0.0", port + tried), Handler)
                break
            except OSError:
                if tried == PORT_TRIES - 1:
                    raise
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.address

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


def send_link(address, link, timeout=10):
    """From the phone: send `link` to the computer at `address` (shown under its QR code); returns how many
    links it took (0: none found). Raises OSError when the computer cannot be reached."""
    request = urllib.request.Request(address.strip(), data=json.dumps({"text": link}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return int(json.loads(response.read().decode("utf-8")).get("added") or 0)


class Beacon:
    """Answers the devices looking for this one on the Wi-Fi (find_devices) with its name and where its
    LinkServer listens."""

    def __init__(self, name, device_id, port):
        self.reply = json.dumps({"app": HELLO, "name": name, "id": device_id, "port": port}).encode("utf-8")
        self.device_id = device_id
        self.socket = None

    def start(self, port=DISCOVERY_PORT):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        self.socket = sock
        threading.Thread(target=self.answer, args=(sock,), daemon=True).start()

    def answer(self, sock):
        while self.socket is sock:
            try:
                data, sender = sock.recvfrom(2048)
                question = json.loads(data.decode("utf-8"))
            except (OSError, ValueError):
                if self.socket is not sock:
                    return
                continue
            if isinstance(question, dict) and question.get("app") == HELLO and question.get("id") != self.device_id:
                try:
                    sock.sendto(self.reply, sender)
                except OSError:
                    pass

    def stop(self):
        sock, self.socket = self.socket, None
        if sock is not None:
            sock.close()


def find_devices(device_id, port=DISCOVERY_PORT, wait=LOOK_SECONDS):
    """The other devices running the app on this Wi-Fi with "Receive links" on: [{"name", "id", "address"}]."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.2)
    question = json.dumps({"app": HELLO, "id": device_id}).encode("utf-8")
    own = local_address()
    targets = {"255.255.255.255", own.rsplit(".", 1)[0] + ".255", "127.0.0.1"}
    found = {}
    try:
        for target in targets:
            try:
                sock.sendto(question, (target, port))
            except OSError:
                pass
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                data, (host, _) = sock.recvfrom(2048)
                answer = json.loads(data.decode("utf-8"))
            except socket.timeout:
                continue
            except (OSError, ValueError):
                continue
            if isinstance(answer, dict) and answer.get("app") == HELLO and answer.get("id") not in (None, device_id):
                found[answer["id"]] = {"name": str(answer.get("name") or host), "id": answer["id"],
                                       "address": "http://%s:%d" % (host, int(answer.get("port") or PORT))}
    finally:
        sock.close()
    return sorted(found.values(), key=lambda device: device["name"].casefold())


def offer_link(device, link, name, device_id, timeout=10):
    """Send `link` to a device found by find_devices (it asks its user unless it trusts this one)."""
    request = urllib.request.Request(
        device["address"] + "/offer", data=json.dumps({"text": link, "name": name, "id": device_id}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return int(json.loads(response.read().decode("utf-8")).get("added") or 0)


def qr_png(text, scale=6):
    """The QR code of `text`, as PNG bytes (segno)."""
    import io
    import segno
    out = io.BytesIO()
    segno.make(text, error="m").save(out, kind="png", scale=scale, border=2)
    return out.getvalue()
