"""تست پنل وب و بکاپ/بازگردانی با docker و adb ساختگی."""
import json
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

import pytest

from test_droid import ROOT, droid, env, log  # noqa: F401 — env فیکسچر است


@pytest.fixture
def panel(env):  # noqa: F811
    tmp, e = env
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    p = subprocess.Popen([sys.executable, str(ROOT / "lib/panel.py"), "--port", str(port)],
                         env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/api/state", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    yield tmp, e, base
    p.kill()


def call(base, path, body=None, header=True, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=data)
    if header:
        req.add_header("X-Farm", "1")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ctype = r.headers.get("Content-Type", "")
            b = r.read()
            return r.status, (json.loads(b) if "json" in ctype else b)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait_job(base, jid):
    for _ in range(200):
        _, st = call(base, "/api/state")
        j = next(j for j in st["jobs"] if j["id"] == jid)
        if j["state"] in ("ok", "failed"):
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_panel_profiles_and_devices(panel):
    tmp, e, base = panel
    code, page = call(base, "/")
    assert code == 200 and b"Android Farm" in page

    code, j = call(base, "/api/do/profile_new", {"count": "2", "model": "Pixel 8"})
    assert code == 200 and wait_job(base, j["id"])["state"] == "ok"
    _, st = call(base, "/api/state")
    assert [p["name"] for p in st["profiles"]] == ["p1", "p2"]
    assert all(p["model"].endswith("Pixel 8") for p in st["profiles"])
    assert any(m["value"] == "Pixel 7" for m in st["models"])

    code, j = call(base, "/api/do/device_new", {"name": "acc1", "profile": "p2", "proxy": "1.2.3.4:80"})
    assert wait_job(base, j["id"])["state"] == "ok", j
    _, st = call(base, "/api/state")
    dev = st["devices"][0]
    assert dev["name"] == "acc1" and dev["status"] == "running" and dev["profile"] == "p2"
    assert next(p for p in st["profiles"] if p["name"] == "p2")["device"] == "acc1"

    # همان پروفایل برای گوشی دوم رد می‌شود و خطای droid در خروجی کار دیده می‌شود
    _, j = call(base, "/api/do/device_new", {"name": "acc2", "profile": "p2"})
    j = wait_job(base, j["id"])
    assert j["state"] == "failed" and "acc1" in j["output"]

    _, j = call(base, "/api/do/device_stop", {"name": "acc1"})
    assert wait_job(base, j["id"])["state"] == "ok"
    _, st = call(base, "/api/state")
    assert st["devices"][0]["status"] == "exited"


def test_panel_rejects(panel):
    tmp, e, base = panel
    assert call(base, "/api/do/device_stop", {"name": "acc1"}, header=False)[0] == 403, "CSRF guard"
    assert call(base, "/api/do/nope", {"name": "x"})[0] == 400
    assert call(base, "/api/do/device_new", {"name": "--purge"})[0] == 400
    assert call(base, "/api/do/device_new", {"name": "a", "proxy": "-x"})[0] == 400
    assert call(base, "/api/do/backup_restore", {"file": "../../etc/passwd.tar.gz"})[0] == 400
    assert call(base, "/api/backup/..%2Fprofile.json")[0] in (400, 404)
    assert call(base, "/api/upload?filename=x.tar.gz", raw=b"not gzip data")[0] == 400
    assert "docker run" not in log(tmp)


def test_backup_restore_cli(env):  # noqa: F811
    tmp, e = env
    assert droid(e, "new", "acc1", "--model", "Pixel 7").returncode == 0
    orig = json.loads((tmp / "farm/acc1/profile.json").read_text())
    (tmp / "farm/acc1/data/app").mkdir(parents=True)
    (tmp / "farm/acc1/data/app/account.db").write_text("session")

    out = tmp / "b.tar.gz"
    r = droid(e, "backup", "acc1", "-o", str(out))
    assert r.returncode == 0, r.stderr
    lg = log(tmp)
    assert lg.index("docker stop -t 20 droid-acc1") < lg.rindex("docker start droid-acc1"), \
        "running device is stopped for a consistent copy, then started again"
    with tarfile.open(out) as t:
        names = t.getnames()
    assert "data/app/account.db" in names and "profile.json" in names

    # هویت تکراری روی همین سرور رد می‌شود
    r = droid(e, "restore", str(out), "--name", "copy")
    assert r.returncode != 0 and "acc1" in r.stderr

    droid(e, "rm", "acc1", "--purge", "-y")
    r = droid(e, "restore", str(out), "--name", "back")
    assert r.returncode == 0, r.stderr
    new = json.loads((tmp / "farm/back/profile.json").read_text())
    assert new["identity"] == orig["identity"] and new["port"] == 6000
    assert (tmp / "farm/back/data/app/account.db").read_text() == "session"
    assert "--mac-address " + orig["identity"]["mac"] in log(tmp)
    assert not list((tmp / "farm").glob(".restore/*")), "staging dir cleaned"
    assert droid(e, "restore", str(out), "--name", "back").returncode != 0, "name taken"


def test_restore_brings_back_profile_link(env):  # noqa: F811
    tmp, e = env
    droid(e, "profile", "new", "work")
    assert droid(e, "new", "acc1", "--profile", "work").returncode == 0
    out = tmp / "w.tar.gz"
    assert droid(e, "backup", "acc1", "-o", str(out)).returncode == 0
    droid(e, "rm", "acc1", "--purge", "-y")
    droid(e, "profile", "rm", "work")
    r = droid(e, "restore", str(out))
    assert r.returncode == 0, r.stderr
    assert (tmp / "farm/.identities/work.json").exists(), "saved profile comes back"
    assert json.loads((tmp / "farm/acc1/profile.json").read_text())["from_profile"] == "work"


def test_restore_rejects_foreign_archives(env, tmp_path):  # noqa: F811
    tmp, e = env
    evil = tmp_path / "evil.tar.gz"
    (tmp_path / "x").write_text("x")
    with tarfile.open(evil, "w:gz") as t:
        t.add(tmp_path / "x", arcname="../escape")
    r = droid(e, "restore", str(evil))
    assert r.returncode != 0 and "docker run" not in log(tmp)
    plain = tmp_path / "plain.tar.gz"
    with tarfile.open(plain, "w:gz") as t:
        t.add(tmp_path / "x", arcname="data/x")
    assert droid(e, "restore", str(plain)).returncode != 0


def test_panel_upload_download(panel):
    tmp, e, base = panel
    assert droid(e, "new", "acc1").returncode == 0
    _, j = call(base, "/api/do/device_backup", {"name": "acc1"})
    assert wait_job(base, j["id"])["state"] == "ok"
    _, st = call(base, "/api/state")
    f = st["backups"][0]["file"]
    assert f.startswith("acc1-") and st["backups"][0]["size"] > 0
    code, blob = call(base, "/api/backup/" + f)
    assert code == 200 and blob[:2] == b"\x1f\x8b"

    code, up = call(base, "/api/upload?filename=My%20Phone.tar.gz", raw=blob)
    assert code == 200 and up["file"] == "my-phone.tar.gz"
    code, up2 = call(base, "/api/upload?filename=My%20Phone.tar.gz", raw=blob)
    assert up2["file"] == "my-phone-2.tar.gz", "never overwrites"

    droid(e, "rm", "acc1", "--purge", "-y")
    _, j = call(base, "/api/do/backup_restore", {"file": up["file"], "name": "acc9"})
    assert wait_job(base, j["id"])["state"] == "ok"
    _, st = call(base, "/api/state")
    assert [d["name"] for d in st["devices"]] == ["acc9"]
    _, j = call(base, "/api/do/backup_rm", {"file": up2["file"]})
    wait_job(base, j["id"])
    assert up2["file"] not in [b["file"] for b in call(base, "/api/state")[1]["backups"]]
