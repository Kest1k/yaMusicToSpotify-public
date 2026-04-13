import json
import os
import webbrowser
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 8765
SELECTED_FILE = os.path.join(BASE_DIR, "selected_potential_artists.json")


class DashboardHandler(SimpleHTTPRequestHandler):
    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/selected-artists":
            if os.path.exists(SELECTED_FILE):
                with open(SELECTED_FILE, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            else:
                payload = {"updated_at": None, "selected": []}
            return self._send_json(payload)
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/selected-artists":
            return self._send_json({"error": "Not found"}, status=404)

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._send_json({"error": "Invalid JSON"}, status=400)

        selected = payload.get("selected", [])
        data = {
            "updated_at": datetime.now().isoformat(),
            "selected_count": len(selected),
            "selected": selected,
        }
        with open(SELECTED_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        return self._send_json({"ok": True, "selected_count": len(selected), "path": SELECTED_FILE})


def main():
    os.chdir(BASE_DIR)
    url = f"http://127.0.0.1:{PORT}/dashboard/index.html"
    server = ThreadingHTTPServer(("127.0.0.1", PORT), DashboardHandler)
    print(f"Serving {BASE_DIR}")
    print(f"Open: {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    server.serve_forever()


if __name__ == "__main__":
    main()
