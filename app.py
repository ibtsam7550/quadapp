"""Quadratic solver backend (Milestone 01).

Local test : python3 app.py            -> http://localhost:5000
On the VMs : gunicorn --bind 0.0.0.0:80 app:app   (see the systemd unit in the guide)
"""
import math
import random
import socket

from flask import Flask, jsonify

app = Flask(__name__, static_folder="static", static_url_path="/static")


def server_ip():
    """IP of the interface used to reach the network (sends no packets)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "unknown"
    finally:
        s.close()


def solve_quadratic(a, b, c):
    """Return (discriminant, kind, roots) for ax^2 + bx + c = 0, a != 0."""
    d = b * b - 4 * a * c
    if d > 0:
        r = math.sqrt(d)
        return d, "two real solutions", [(-b + r) / (2 * a), (-b - r) / (2 * a)]
    if d == 0:
        return d, "one real solution", [-b / (2 * a)]
    return d, "no real solutions", []


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/solve")
def solve():
    a = random.choice([n for n in range(-10, 11) if n != 0])  # a must never be 0
    b = random.randint(-20, 20)
    c = random.randint(-20, 20)
    d, kind, roots = solve_quadratic(a, b, c)
    resp = jsonify(
        a=a, b=b, c=c, discriminant=d, kind=kind,
        roots=[round(x, 4) + 0.0 for x in roots],  # "+ 0.0" turns -0.0 into 0.0
        hostname=socket.gethostname(), ip=server_ip(),
    )
    resp.headers["Cache-Control"] = "no-store"  # every request must reach a backend
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
