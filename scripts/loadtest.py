"""Навантажувальний тест: реєструє кількох користувачів і одночасно ставить важкі задачі,
щоб показати балансування, чергу з ETA та автоскейлінг.
Запуск:  pip install requests && python scripts/loadtest.py --users 6 --per-user 3"""
import argparse
import collections
import random
import time

import requests
import urllib3

urllib3.disable_warnings()
ap = argparse.ArgumentParser()
ap.add_argument("--url", default="https://localhost:8443")
ap.add_argument("--users", type=int, default=6)
ap.add_argument("--per-user", type=int, default=3)
a = ap.parse_args()

heavy = [("mandelbrot", {"width": 1200, "height": 900, "max_iter": 300}),
         ("linear_system", {"n": 2000}), ("fem_poisson", {"m": 250, "f": 1})]
ids, tag = [], random.randint(1000, 9999)
for i in range(a.users):
    creds = {"username": f"load{tag}_{i}", "password": "password123"}
    tok = requests.post(f"{a.url}/api/register", json=creds, verify=False).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    for _ in range(a.per_user):
        t, p = random.choice(heavy)
        r = requests.post(f"{a.url}/api/tasks", json={"type": t, "params": p}, headers=h, verify=False)
        print(r.status_code, t, r.headers.get("X-Served-By"), r.json().get("detail", ""))
        if r.ok:
            ids.append((r.json()["id"], h))
while True:
    st = collections.Counter(); servers = collections.Counter()
    for tid, h in ids:
        t = requests.get(f"{a.url}/api/tasks/{tid}", headers=h, verify=False).json()
        st[t["status"]] += 1
        if t.get("server_id"):
            servers[t["server_id"]] += 1
    print(dict(st), "виконавці:", dict(servers))
    if not st["queued"] and not st["running"]:
        break
    time.sleep(3)
