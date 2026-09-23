#!/usr/bin/env bash
# نصب کامل android-farm روی Ubuntu 22.04/24.04 (x86_64)
#   sudo bash install.sh                 نصب کامل
#   sudo bash install.sh --check         فقط بررسی پیش‌نیازها، بدون نصب
#   sudo bash install.sh --domain d.com  پنل وب با HTTPS روی دامنه
#   sudo bash install.sh --no-web        بدون پنل وب و نمایشگر صفحه
#   sudo bash install.sh --no-gapps      اندروید ۱۳ خام، بدون Google Play
set -euo pipefail

APP_DIR=/opt/android-farm
DATA_DIR=/var/lib/android-farm
CONF_DIR=/etc/android-farm
IMAGE=redroid/redroid:13.0.0-latest
# پیش‌فرض: اندروید ۱۲ + Google Play (MindTheGapps) + اجرای اپ‌های ARM (libndk).
# libndk فقط روی ۱۱/۱۲ کار می‌کند و بیشتر اپ‌های فروشگاه فقط نسخه‌ی ARM دارند.
GAPPS_IMAGE=redroid/redroid:12.0.0_mindthegapps_ndk
REDROID_SCRIPT_REF=a4951b7  # ayasa520/redroid-script 2026-09-13 — تست‌شده
GAPPS=1
WS_SCRCPY_REF=8855ad11  # master 2026-08-24: fitToScreen (تصویر کامل در صفحه‌ی موبایل) — تست‌شده
MODE=install
DOMAIN=""
WEB=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE=check ;;
    --domain) DOMAIN="${2:?}"; shift ;;
    --no-web) WEB=0 ;;
    --no-gapps) GAPPS=0 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "گزینه‌ی ناشناخته: $1" >&2; exit 1 ;;
  esac
  shift
done

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FATAL=0

# ---------------------------------------------------------------- بررسی
check_host() {
  step "بررسی سرور"
  local arch virt cores mem_gb disk_gb
  arch=$(uname -m)
  if [[ $arch == x86_64 ]]; then ok "معماری: $arch"; else bad "معماری $arch — ایمیج redroid اینجا فقط x86_64 است"; FATAL=1; fi

  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    if [[ ${ID:-} == ubuntu ]]; then ok "سیستم‌عامل: $PRETTY_NAME"; else warn "سیستم‌عامل $PRETTY_NAME تست نشده؛ Ubuntu 22.04/24.04 پیشنهاد می‌شود"; fi
  fi

  virt=$(systemd-detect-virt 2>/dev/null || true)
  case "$virt" in
    openvz|lxc|lxc-libvirt|docker|podman|wsl)
      bad "مجازی‌سازی «$virt» اجازه‌ی لود ماژول کرنل نمی‌دهد → Redroid اجرا نمی‌شود. یک VPS از نوع KVM بگیر."
      FATAL=1 ;;
    ""|none) ok "سرور فیزیکی" ;;
    *) ok "مجازی‌سازی: $virt (VM کامل — مناسب)" ;;
  esac

  if [[ -e /dev/kvm ]]; then ok "/dev/kvm هست (برای Redroid لازم نیست، ولی حالت اضطراری AVD سریع می‌شود)"
  else ok "/dev/kvm نیست — مشکلی نیست، Redroid به KVM نیاز ندارد"; fi

  cores=$(nproc)
  mem_gb=$(awk '/MemTotal/ {printf "%.1f", $2/1048576}' /proc/meminfo)
  disk_gb=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
  echo "  • منابع: ${cores} هسته، ${mem_gb}GB رم، ${disk_gb}GB فضای خالی"
  local max_by_mem max_by_cpu max
  max_by_mem=$(awk -v m="$mem_gb" 'BEGIN {n=int((m-1)/1.5); print (n<0?0:n)}')
  max_by_cpu=$(( cores ))
  max=$(( max_by_mem < max_by_cpu ? max_by_mem : max_by_cpu ))
  if (( max >= 1 )); then ok "ظرفیت تقریبی: ${max} دستگاه همزمان"
  else warn "منابع خیلی کم است؛ حتی یک دستگاه هم کند خواهد بود (حداقل ۲ هسته و ۴GB)"; fi
  if (( disk_gb < 15 )); then warn "فضای دیسک کم است (هر دستگاه ~۸GB)"; fi
}

check_binder() {
  step "بررسی binder (قلب Redroid)"
  if lsmod | grep -q '^binder_linux'; then
    ok "binder از قبل فعال است"; return 0
  fi
  if modinfo binder_linux >/dev/null 2>&1; then
    ok "ماژول binder_linux در کرنل $(uname -r) موجود است"; return 0
  fi
  if [[ $MODE == install ]] && apt-cache show "linux-modules-extra-$(uname -r)" >/dev/null 2>&1; then
    warn "binder_linux در linux-modules-extra است؛ هنگام نصب اضافه می‌شود"; return 0
  fi
  if grep -qE '^CONFIG_ANDROID_BINDER(FS)?=y' "/boot/config-$(uname -r)" 2>/dev/null; then
    ok "binder داخل خود کرنل کامپایل شده"; return 0
  fi
  bad "کرنل $(uname -r) binder ندارد."
  echo "     راه‌حل‌ها: ۱) VPS با کرنل استاندارد Ubuntu (generic)  ۲) نصب کرنل generic و ریبوت"
  echo "     ۳) حالت اضطراری AVD (خیلی کند) — بخش «بدون binder» در README"
  FATAL=1
}

# ---------------------------------------------------------------- نصب
install_packages() {
  step "نصب بسته‌ها"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl git python3 adb iptables >/dev/null
  apt-get install -y -qq "linux-modules-extra-$(uname -r)" >/dev/null 2>&1 \
    || warn "linux-modules-extra پیدا نشد (اگر binder داخل کرنل باشد مشکلی نیست)"
  if ! command -v docker >/dev/null; then
    curl -fsSL https://get.docker.com | sh >/dev/null
  fi
  systemctl enable --now docker >/dev/null
  ok "docker $(docker --version | awk '{print $3}' | tr -d ,) و adb نصب شد"
}

setup_binder() {
  step "فعال‌سازی binder"
  echo 'options binder_linux devices="binder,hwbinder,vndbinder"' > /etc/modprobe.d/android-farm.conf
  echo binder_linux > /etc/modules-load.d/android-farm.conf
  if ! lsmod | grep -q '^binder_linux'; then
    modprobe binder_linux devices="binder,hwbinder,vndbinder" || {
      grep -qE '^CONFIG_ANDROID_BINDER(FS)?=y' "/boot/config-$(uname -r)" 2>/dev/null \
        || { bad "لود binder_linux شکست خورد"; exit 1; }
    }
  fi
  ok "binder فعال است و بعد از ریبوت هم می‌ماند"
}

install_app() {
  step "نصب ابزار droid"
  mkdir -p "$APP_DIR/lib" "$DATA_DIR" "$CONF_DIR"
  chmod 700 "$DATA_DIR"
  install -m 755 "$SRC_DIR/droid" "$APP_DIR/droid"
  install -m 644 "$SRC_DIR/lib/identity.py" "$APP_DIR/lib/identity.py"
  install -m 644 "$SRC_DIR/lib/panel.py" "$APP_DIR/lib/panel.py"
  install -D -m 644 "$SRC_DIR/web/index.html" "$APP_DIR/web/index.html"
  ln -sf "$APP_DIR/droid" /usr/local/bin/droid

  cat > /etc/systemd/system/android-farm-reconnect.service <<EOF
[Unit]
Description=Reconnect adb to android-farm devices
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStartPre=/bin/sleep 20
ExecStart=/usr/local/bin/droid reconnect

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable android-farm-reconnect.service >/dev/null
  ok "droid در /usr/local/bin نصب شد"

  if (( GAPPS )); then build_gapps_image; else
    step "دانلود ایمیج اندروید ($IMAGE)"
    docker pull -q "$IMAGE" >/dev/null
    ok "ایمیج آماده است"
  fi
  echo "$IMAGE" > "$CONF_DIR/image"
}

build_gapps_image() {
  IMAGE=$GAPPS_IMAGE
  step "ساخت ایمیج اندروید با Google Play ($IMAGE)"
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then ok "ایمیج از قبل ساخته شده"; return; fi
  apt-get install -y -qq lzip python3-requests python3-tqdm >/dev/null
  local src=$APP_DIR/redroid-script
  if [[ ! -d $src/.git ]]; then
    rm -rf "$src"
    git clone -q https://github.com/ayasa520/redroid-script.git "$src"
  fi
  git -C "$src" -c advice.detachedHead=false checkout -q "$REDROID_SCRIPT_REF"
  echo "  • دانلود GApps و libndk و ساخت ایمیج (۳ تا ۱۰ دقیقه)…"
  ( cd "$src" && python3 redroid.py -a 12.0.0 -mtg -n ) >/tmp/android-farm-gapps.log 2>&1 \
    && docker image inspect "$IMAGE" >/dev/null 2>&1 \
    || { bad "ساخت ایمیج شکست خورد — لاگ: /tmp/android-farm-gapps.log (بدون Play: --no-gapps)"; exit 1; }
  docker image prune -f >/dev/null
  ok "ایمیج با Google Play و پشتیبانی ARM آماده است"
}

install_web() {
  step "پنل وب + نمایشگر صفحه (ws-scrcpy) پشت Caddy با رمز"
  if ! command -v node >/dev/null || (( $(node -v | tr -dc 0-9. | cut -d. -f1) < 18 )); then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
    apt-get install -y -qq nodejs build-essential >/dev/null
  fi
  apt-get install -y -qq build-essential python3 >/dev/null
  if [[ ! -d $APP_DIR/ws-scrcpy-src/.git ]]; then
    rm -rf "$APP_DIR/ws-scrcpy-src"
    git clone -q https://github.com/NetrisTV/ws-scrcpy.git "$APP_DIR/ws-scrcpy-src"
  fi
  git -C "$APP_DIR/ws-scrcpy-src" -c advice.detachedHead=false checkout -q "$WS_SCRCPY_REF"
  ( cd "$APP_DIR/ws-scrcpy-src" && npm install --silent >/dev/null 2>&1 && npm run dist >/dev/null 2>&1 )
  ( cd "$APP_DIR/ws-scrcpy-src/dist" && npm install --omit=dev --silent >/dev/null 2>&1 )

  cat > /etc/systemd/system/ws-scrcpy.service <<EOF
[Unit]
Description=ws-scrcpy web viewer
After=network.target android-farm-reconnect.service

[Service]
WorkingDirectory=$APP_DIR/ws-scrcpy-src/dist
# پورت 8000 فقط از داخل سرور؛ از بیرون فقط از راه Caddy با رمز
ExecStartPre=-/usr/sbin/iptables -D INPUT -p tcp --dport 8000 ! -i lo -j DROP
ExecStartPre=/usr/sbin/iptables -I INPUT -p tcp --dport 8000 ! -i lo -j DROP
ExecStart=/usr/bin/node index.js
Restart=always

[Install]
WantedBy=multi-user.target
EOF

  # پنل مدیریت (پروفایل، گوشی، بکاپ)؛ خودش فقط روی 127.0.0.1 گوش می‌دهد
  cat > /etc/systemd/system/android-farm-panel.service <<EOF
[Unit]
Description=android-farm web panel
After=docker.service
Wants=docker.service

[Service]
ExecStart=/usr/bin/python3 $APP_DIR/lib/panel.py --host 127.0.0.1 --port 8100
Restart=always

[Install]
WantedBy=multi-user.target
EOF

  if ! command -v caddy >/dev/null; then
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq && apt-get install -y -qq caddy >/dev/null
  fi

  local pass hash site url screen ip
  pass=$(python3 -c 'import secrets; print(secrets.token_urlsafe(15))')
  hash=$(caddy hash-password --plaintext "$pass")
  ip=$(curl -fsS4 --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')
  local tls_line=""
  # پنل روی آدرس اصلی، صفحه‌ی گوشی‌ها روی پورت 8444 (ws-scrcpy زیر مسیر فرعی کار نمی‌کند)
  if [[ -n $DOMAIN ]]; then site="$DOMAIN"; url="https://$DOMAIN"; screen="https://$DOMAIN:8444"
  else site="https://$ip:8443"; url="https://$ip:8443"; screen="https://$ip:8444"; tls_line="	tls internal"; fi

  cat > /etc/caddy/Caddyfile <<EOF
(farm) {
$tls_line
	basic_auth {
		admin $hash
	}
}

$site {
	import farm
	reverse_proxy 127.0.0.1:8100
}

$screen {
	import farm
	reverse_proxy 127.0.0.1:8000
}
EOF
  echo "$url" > "$CONF_DIR/web-url"
  echo "$screen" > "$CONF_DIR/screen-url"
  printf 'user: admin\npass: %s\npanel:  %s\nscreen: %s\n' "$pass" "$url" "$screen" > "$CONF_DIR/web-credentials"
  chmod 600 "$CONF_DIR/web-credentials"

  systemctl daemon-reload
  systemctl enable --now ws-scrcpy >/dev/null
  systemctl enable android-farm-panel >/dev/null
  systemctl restart android-farm-panel
  systemctl restart caddy
  ok "پنل وب: $url  (user: admin — رمز در $CONF_DIR/web-credentials)"
  ok "صفحه‌ی گوشی‌ها: $screen  (از پنل با «باز کردن صفحه» هم باز می‌شود)"
  if [[ -z $DOMAIN ]]; then warn "بدون دامنه، گواهی self-signed است؛ مرورگر یک هشدار می‌دهد که باید قبولش کنی"; fi
}

# ---------------------------------------------------------------- اجرا
[[ $EUID -eq 0 ]] || { echo "با sudo/root اجرا کن" >&2; exit 1; }

check_host
check_binder

if [[ $MODE == check ]]; then
  echo
  if (( FATAL )); then bad "این سرور برای Redroid مناسب نیست (موارد ✗ بالا)"; exit 2; fi
  ok "این سرور آماده‌ی نصب است: sudo bash install.sh"
  exit 0
fi
if (( FATAL )); then bad "نصب متوقف شد (موارد ✗ بالا)"; exit 2; fi

install_packages
setup_binder
install_app
if (( WEB )); then install_web; fi

step "تمام شد"
cat <<'EOF'
  droid new acc1                 ساخت دستگاه با هویت تصادفی
  droid new acc2 --model "Pixel 7" --proxy 1.2.3.4:8080
  droid list                     لیست دستگاه‌ها
  droid info acc1                هویت + مقادیر زنده
  droid view acc1                راه دیدن صفحه
  droid models                   مدل‌های قابل انتخاب
  droid gsf acc1                 شناسه‌ی ثبت در گوگل (اگر Play گفت «دستگاه تأییدنشده»)
  droid backup acc1              بکاپ کامل (هویت + داده‌ها)
  droid restore file.tar.gz      بازگردانی از بکاپ
EOF
