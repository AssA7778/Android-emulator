#!/usr/bin/env python3
"""پنل وب android-farm: ساخت و انتخاب پروفایل، ساخت و مدیریت گوشی‌ها از مرورگر.

فقط روی 127.0.0.1 گوش می‌دهد؛ از بیرون فقط از راه Caddy (رمز + HTTPS) در دسترس است.
هر کاری با همان CLI «droid» انجام می‌شود تا همه‌ی قانون‌ها (یک پروفایل = یک گوشی،
اعتبارسنجی نام و پروکسی، …) یک جا باشند.

  python3 panel.py [--host 127.0.0.1] [--port 8100]
"""
import argparse
import importlib.machinery
import importlib.util
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(os.path.realpath(__file__)).parent
APP = HERE.parent
DROID = APP / "droid"
INDEX = APP / "web" / "index.html"

# ماژول droid را برای خواندن وضعیت import می‌کنیم (فایل پسوند .py ندارد)
_loader = importlib.machinery.SourceFileLoader("droid_cli", str(DROID))
_spec = importlib.util.spec_from_loader("droid_cli", _loader)
droid = importlib.util.module_from_spec(_spec)
_loader.exec_module(droid)

MAX_JOBS = 40
# ساخت گوشی و پروفایل پشت سر هم اجرا می‌شوند تا دو کار همزمان یک پورت یا نام را نگیرند
alloc_lock = threading.Lock()
jobs: dict[int, dict] = {}
jobs_lock = threading.Lock()
job_ids = itertools.count(1)
BACKUP_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,120}\.tar\.gz$")


def backups_dir() -> Path:
    return droid.backups_dir()


def backup_file(fname: str) -> Path:
    if not BACKUP_RE.match(fname or "") or ".." in fname:
        raise ValueError("نام فایل بکاپ نامعتبر")
    return backups_dir() / fname


def list_backups() -> list[dict]:
    d = backups_dir()
    if not d.exists():
        return []
    items = [p for p in d.glob("*.tar.gz") if p.is_file()]
    items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"file": p.name, "size": p.stat().st_size,
             "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))} for p in items]


def screen_url() -> str:
    f = droid.CONF / "screen-url"
    return f.read_text().strip() if f.exists() else ""


def state() -> dict:
    devices = []
    for p in droid.all_profiles():
        i = p["identity"]
        devices.append({
            "name": p["name"], "status": droid.status(p["name"]), "adb": droid.serial(p),
            "model": f"{i['manufacturer']} {i['model']}", "imei": i["imei"],
            "serial": i["serial"], "proxy": p.get("proxy"), "mem": p["mem"],
            "cpus": p["cpus"], "profile": p.get("from_profile"), "created": p.get("created"),
        })
    used = {d["profile"]: d["name"] for d in devices if d["profile"]}
    profiles = [{
        "name": n, "model": f"{i['manufacturer']} {i['model']}", "imei": i["imei"],
        "serial": i["serial"], "mac": i["mac"], "screen": f"{i['width']}x{i['height']}",
        "created": i.get("created"), "device": used.get(n),
    } for n, i in droid.saved_identities()]
    with jobs_lock:
        js = sorted(jobs.values(), key=lambda j: -j["id"])
    # value همان نامی است که droid --model می‌پذیرد
    models = [{"value": m[2], "label": f"{m[2]} ({m[0]})"} for m in droid.identity.PROFILES]
    return {"devices": devices, "profiles": profiles, "models": models,
            "jobs": js, "screen": screen_url(), "backups": list_backups()}


def _s(body: dict, key: str) -> str:
    v = body.get(key)
    v = "" if v is None else str(v).strip()
    # هیچ مقداری نباید مثل یک گزینه‌ی خط فرمان خوانده شود
    if v.startswith("-"):
        raise ValueError(f"مقدار نامعتبر برای {key}")
    return v


def build_args(action: str, body: dict) -> tuple[list[str], str]:
    """(آرگومان‌های droid، عنوان کار) — ValueError برای ورودی بد."""
    name = _s(body, "name")
    if action == "device_new":
        if not name:
            raise ValueError("نام گوشی را بنویس")
        args = ["new", name]
        if prof := _s(body, "profile"):
            args += ["--profile", prof]
        elif model := _s(body, "model"):
            args += ["--model", model]
        if proxy := _s(body, "proxy"):
            args += ["--proxy", proxy]
        if mem := _s(body, "mem"):
            args += ["--mem", mem]
        if cpus := _s(body, "cpus"):
            args += ["--cpus", cpus]
        return args, f"ساخت گوشی {name}"
    if action == "backup_restore":
        path = backup_file(_s(body, "file"))
        if not path.exists():
            raise ValueError("فایل بکاپ نیست")
        args = ["restore", str(path)]
        if name:
            args += ["--name", name]
        return args, f"بازگردانی {name or path.name}"
    if action == "backup_rm":
        path = backup_file(_s(body, "file"))
        path.unlink(missing_ok=True)
        return [], f"حذف بکاپ {path.name}"
    if action == "profile_new":
        args = ["profile", "new"]
        if name:
            args.append(name)
        if model := _s(body, "model"):
            args += ["--model", model]
        count = _s(body, "count") or "1"
        if not count.isdigit():
            raise ValueError("تعداد باید عدد باشد")
        args += ["--count", count]
        return args, f"ساخت پروفایل {name or '×' + count}"
    if not name:
        raise ValueError("نام لازم است")
    simple = {
        "device_start": (["start", name], f"روشن کردن {name}"),
        "device_stop": (["stop", name], f"خاموش کردن {name}"),
        "device_rm": (["rm", name], f"حذف کانتینر {name}"),
        "device_purge": (["rm", name, "--purge", "-y"], f"حذف کامل {name}"),
        "device_info": (["info", name], f"مشخصات {name}"),
        "device_gsf": (["gsf", name], f"شناسه‌ی GSF {name}"),
        "device_backup": (["backup", name], f"بکاپ {name}"),
        "profile_show": (["profile", "show", name], f"پروفایل {name}"),
        "profile_rm": (["profile", "rm", name], f"حذف پروفایل {name}"),
    }
    if action == "device_proxy":
        return ["proxy", name, _s(body, "proxy") or "off"], f"پروکسی {name}"
    if action not in simple:
        raise ValueError("کار ناشناخته")
    return simple[action]


def run_job(job: dict, args: list[str]):
    if not args:  # کار بدون droid (مثل حذف بکاپ) همان لحظه انجام شده
        job["state"], job["ended"] = "ok", time.time()
        return
    needs_lock = args[0] in ("new", "restore") or args[:2] == ["profile", "new"]
    if needs_lock:
        alloc_lock.acquire()
    try:
        job["state"] = "running"
        p = subprocess.Popen([sys.executable, str(DROID), *args], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True)
        for line in p.stdout:
            job["output"] += line
        p.wait()
        job["state"] = "ok" if p.returncode == 0 else "failed"
    except Exception as e:  # noqa: BLE001 — خطا باید در خود کار دیده شود نه در لاگ سرور
        job["output"] += f"\n✗ {e}"
        job["state"] = "failed"
    finally:
        job["ended"] = time.time()
        if needs_lock:
            alloc_lock.release()


def start_job(action: str, body: dict) -> dict:
    args, title = build_args(action, body)
    job = {"id": next(job_ids), "title": title, "state": "queued", "output": "",
           "started": time.time(), "ended": None}
    with jobs_lock:
        jobs[job["id"]] = job
        for old in sorted(jobs)[:-MAX_JOBS]:
            if jobs[old]["ended"]:
                del jobs[old]
    threading.Thread(target=run_job, args=(job, args), daemon=True).start()
    return job


class Handler(BaseHTTPRequestHandler):
    server_version = "android-farm-panel"

    def log_message(self, fmt, *args):  # لاگ هر درخواست لازم نیست
        pass

    def send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def json(self, code: int, data):
        self.send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self.send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/state":
            self.json(200, state())
        elif path.startswith("/api/backup/"):
            self.download(path[len("/api/backup/"):])
        else:
            self.json(404, {"error": "not found"})

    def download(self, fname: str):
        try:
            f = backup_file(fname)
        except ValueError:
            return self.json(400, {"error": "bad name"})
        if not f.is_file():
            return self.json(404, {"error": "not found"})
        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Length", str(f.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{f.name}"')
        self.end_headers()
        with f.open("rb") as fh:
            shutil.copyfileobj(fh, self.wfile, 1 << 20)

    def upload(self):
        """بدنه‌ی خام درخواست = فایل بکاپ؛ تکه‌تکه روی دیسک نوشته می‌شود، نه در رم."""
        qs = parse_qs(urlsplit(self.path).query)
        orig = (qs.get("filename") or ["backup.tar.gz"])[0]
        stem = re.sub(r"[^a-z0-9._-]", "-", orig.lower().removesuffix(".tar.gz"))[:80].strip(".-") or "backup"
        fname = f"{stem}.tar.gz"
        d = backups_dir()
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        dest = d / fname
        n = 2
        while dest.exists():
            dest = d / f"{stem}-{n}.tar.gz"
            n += 1
        try:
            left = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            left = -1
        if left <= 0:
            return self.json(411, {"error": "حجم فایل مشخص نیست"})
        part = dest.with_name(dest.name + ".part")
        try:
            with part.open("wb") as out:
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        raise ConnectionError("آپلود نیمه‌کاره قطع شد")
                    out.write(chunk)
                    left -= len(chunk)
            with part.open("rb") as fh:
                if fh.read(2) != b"\x1f\x8b":
                    raise ValueError("این فایل tar.gz نیست")
            os.chmod(part, 0o600)
            part.replace(dest)
        except (OSError, ValueError, ConnectionError) as e:
            part.unlink(missing_ok=True)
            return self.json(400, {"error": str(e)})
        self.json(200, {"file": dest.name})

    def do_POST(self):
        # هدر سفارشی یعنی درخواست از خود پنل آمده؛ سایت دیگری بدون CORS نمی‌تواند آن را بفرستد
        # (رمز basic_auth را مرورگر خودکار می‌فرستد، پس این جلوی CSRF را می‌گیرد)
        if self.headers.get("X-Farm") != "1":
            return self.json(403, {"error": "forbidden"})
        path = self.path.split("?", 1)[0]
        if path == "/api/upload":
            return self.upload()
        if not path.startswith("/api/do/"):
            return self.json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("بدنه‌ی نامعتبر")
            job = start_job(path[len("/api/do/"):], body)
        except (ValueError, json.JSONDecodeError) as e:
            return self.json(400, {"error": str(e)})
        self.json(200, job)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("ANDROID_FARM_PANEL_PORT", 8100)))
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"پنل: http://{a.host}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
