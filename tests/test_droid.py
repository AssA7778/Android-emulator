"""تست جریان کامل CLI با docker و adb ساختگی (بدون اجرای اندروید واقعی)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FAKE_DOCKER = r"""#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
state="$FAKE_STATE"
case "$1" in
  run)    for a in "$@"; do [[ $a == droid-* ]] && touch "$state/$a"; done; echo cid ;;
  inspect) n="${@: -1}"; [[ -e $state/$n ]] && { cat "$state/$n" 2>/dev/null | grep -q stopped && echo exited || echo running; } || exit 1 ;;
  stop)   echo stopped > "$state/${@: -1}" ;;
  start)  : > "$state/${@: -1}" ;;
  rm)     rm -f "$state/${@: -1}" ;;
esac
"""

FAKE_ADB = r"""#!/usr/bin/env bash
echo "adb $*" >> "$FAKE_LOG"
case "$*" in
  *"getprop sys.boot_completed"*) echo 1 ;;
  *"getprop ro.product.model"*) echo FAKEMODEL ;;
  *"settings get secure android_id"*) echo abcdef0123456789 ;;
  *"pm list packages com.android.vending"*) [[ -n $FAKE_PLAY ]] && echo package:com.android.vending ;;
  *"sqlite3 "*"gservices.db \"select value from main where name='android_id'\""*) echo 3952247128601331777 ;;
  connect*) echo "connected to ${2}" ;;
esac
"""


@pytest.fixture
def env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("adb", FAKE_ADB)):
        f = bindir / name
        f.write_text(body)
        f.chmod(0o755)
    (tmp_path / "state").mkdir()
    e = dict(os.environ)
    e.update(PATH=f"{bindir}:{e['PATH']}", ANDROID_FARM_HOME=str(tmp_path / "farm"),
             FAKE_LOG=str(tmp_path / "log"), FAKE_STATE=str(tmp_path / "state"),
             ANDROID_FARM_CONF=str(tmp_path / "conf"))
    e.pop("ANDROID_FARM_IMAGE", None)
    return tmp_path, e


def droid(e, *args, input=None):
    return subprocess.run([sys.executable, str(ROOT / "droid"), *args], env=e,
                          text=True, capture_output=True, input=input, timeout=60)


def log(tmp):
    return (tmp / "log").read_text() if (tmp / "log").exists() else ""


def test_full_lifecycle(env):
    tmp, e = env
    r = droid(e, "new", "acc1", "--model", "Pixel 7", "--proxy", "10.0.0.1:8080")
    assert r.returncode == 0, r.stderr
    prof = json.loads((tmp / "farm/acc1/profile.json").read_text())
    assert prof["port"] == 6000 and prof["identity"]["model"] == "Pixel 7"
    assert oct((tmp / "farm/acc1/profile.json").stat().st_mode)[-3:] == "600"
    lg = log(tmp)
    assert "--mac-address " + prof["identity"]["mac"] in lg
    assert "127.0.0.1:6000:5555" in lg, "adb must only bind to localhost"
    assert "ro.product.model=Pixel 7" in (tmp / "farm/acc1/identity.prop").read_text()
    assert "identity.prop:/odm_dlkm/etc/build.prop:ro" in lg
    assert "http_proxy 10.0.0.1:8080" in lg
    assert "svc power stayon true" in lg and "locksettings set-disabled true" in lg

    r = droid(e, "new", "acc2")
    assert r.returncode == 0, r.stderr
    p2 = json.loads((tmp / "farm/acc2/profile.json").read_text())
    assert p2["port"] == 6001
    assert p2["identity"]["imei"] != prof["identity"]["imei"]

    r = droid(e, "list")
    assert "acc1" in r.stdout and "acc2" in r.stdout and "running" in r.stdout

    r = droid(e, "info", "acc1")
    assert "FAKEMODEL" in r.stdout and "android_id" in r.stdout

    r = droid(e, "proxy", "acc1", "off")
    assert r.returncode == 0
    assert json.loads((tmp / "farm/acc1/profile.json").read_text())["proxy"] is None
    assert "settings delete global http_proxy" in log(tmp)

    r = droid(e, "stop", "acc1")
    assert r.returncode == 0 and "exited" in droid(e, "list").stdout

    r = droid(e, "start", "acc1")
    assert r.returncode == 0, r.stderr

    r = droid(e, "rm", "acc2")
    assert (tmp / "farm/acc2/profile.json").exists(), "rm without --purge keeps data"
    r = droid(e, "rm", "acc2", "--purge", input="no\n")
    assert (tmp / "farm/acc2").exists(), "answering no must keep data"
    r = droid(e, "rm", "acc2", "--purge", "-y")
    assert not (tmp / "farm/acc2").exists()
    # بعد از حذف acc2 پورتش دوباره قابل استفاده است
    assert droid(e, "new", "acc3").returncode == 0
    assert json.loads((tmp / "farm/acc3/profile.json").read_text())["port"] == 6001


@pytest.mark.parametrize("args", [
    ["new", "Bad_Name"],
    ["new", "x;rm"],
    ["new", "ok", "--proxy", "not a proxy"],
    ["new", "ok", "--model", "iphone"],
    ["info", "ghost"],
])
def test_rejects_bad_input(env, args):
    tmp, e = env
    r = droid(e, *args)
    assert r.returncode != 0
    assert "docker run" not in log(tmp)


def test_duplicate_name(env):
    _, e = env
    assert droid(e, "new", "dup").returncode == 0
    assert droid(e, "new", "dup").returncode != 0


def test_models_needs_no_docker(tmp_path):
    e = dict(os.environ, PATH="/usr/bin:/bin", ANDROID_FARM_HOME=str(tmp_path))
    r = droid(e, "models")
    assert r.returncode == 0 and "Pixel 7" in r.stdout


def test_default_image_comes_from_install_conf(env):
    tmp, e = env
    assert droid(e, "new", "raw").returncode == 0
    assert json.loads((tmp / "farm/raw/profile.json").read_text())["image"] == "redroid/redroid:13.0.0-latest"
    (tmp / "conf").mkdir()
    (tmp / "conf/image").write_text("redroid/redroid:12.0.0_mindthegapps_ndk\n")
    assert droid(e, "new", "play").returncode == 0
    assert json.loads((tmp / "farm/play/profile.json").read_text())["image"] == "redroid/redroid:12.0.0_mindthegapps_ndk"
    assert "redroid/redroid:12.0.0_mindthegapps_ndk androidboot" in log(tmp)


def test_gsf(env):
    tmp, e = env
    assert droid(e, "new", "g").returncode == 0
    r = droid(e, "gsf", "g")
    assert r.returncode != 0 and "Google Play" in r.stderr, "no Play → clear error"
    r = droid(dict(e, FAKE_PLAY="1"), "gsf", "g")
    assert r.returncode == 0, r.stderr
    assert "3952247128601331777" in r.stdout and "google.com/android/uncertified" in r.stdout
    assert "adb -s 127.0.0.1:6000 root" in log(tmp)
    droid(e, "stop", "g")
    assert droid(dict(e, FAKE_PLAY="1"), "gsf", "g").returncode != 0


def test_saved_profiles(env):
    tmp, e = env
    r = droid(e, "profile", "new", "--count", "3")
    assert r.returncode == 0, r.stderr
    saved = {n: json.loads((tmp / f"farm/.identities/{n}.json").read_text()) for n in ("p1", "p2", "p3")}
    assert len({i["imei"] for i in saved.values()}) == 3, "every profile gets a fresh IMEI"
    assert len({i["serial"] for i in saved.values()}) == 3
    assert oct((tmp / "farm/.identities/p1.json").stat().st_mode)[-3:] == "600"
    assert droid(e, "profile", "new", "work", "--model", "Pixel 8").returncode == 0
    assert droid(e, "profile", "new", "work").returncode != 0, "duplicate profile name"
    assert "Pixel 8" in droid(e, "profile", "list").stdout
    # پروفایل‌ها در لیست دستگاه‌ها نمی‌آیند
    assert "هیچ دستگاهی" in droid(e, "list").stdout

    r = droid(e, "new", "acc1", "--profile", "p2")
    assert r.returncode == 0, r.stderr
    dev = json.loads((tmp / "farm/acc1/profile.json").read_text())
    assert dev["identity"]["imei"] == saved["p2"]["imei"] and dev["from_profile"] == "p2"
    assert "--mac-address " + saved["p2"]["mac"] in log(tmp)
    assert "acc1" in droid(e, "profile", "list").stdout

    assert droid(e, "new", "acc2", "--profile", "p2").returncode != 0, "one identity, one device"
    assert droid(e, "new", "acc2", "--profile", "ghost").returncode != 0
    assert droid(e, "new", "acc2", "--profile", "p1", "--model", "Pixel 7").returncode != 0
    assert droid(e, "profile", "rm", "p2").returncode != 0, "in use"
    assert droid(e, "profile", "rm", "p3").returncode == 0
    assert not (tmp / "farm/.identities/p3.json").exists()
    droid(e, "rm", "acc1", "--purge", "-y")
    assert droid(e, "profile", "rm", "p2").returncode == 0
