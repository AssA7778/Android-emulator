"""هویت جدای هر دستگاه مجازی: مدل، سریال، MAC، IMEI.

IMEI فرمت درست ۱۵ رقمی با رقم کنترل Luhn دارد ولی کاملاً تصادفی است و
از هیچ دستگاه واقعی کپی نمی‌شود. Redroid مودم (RIL) ندارد و اپ‌های معمولی
از اندروید ۱۰ به بعد اجازه‌ی خواندن IMEI را ندارند؛ پس IMEI فقط در پروفایل
ذخیره و به‌عنوان prop ست می‌شود.
"""
import json
import secrets
import string
import sys

# (brand, manufacturer, model, device, name) — فقط مشخصات ظاهری
PROFILES = [
    ("samsung", "samsung", "SM-S911B", "dm1q", "dm1qxxx"),
    ("samsung", "samsung", "SM-A546E", "a54x", "a54xnsxx"),
    ("samsung", "samsung", "SM-A155F", "a15", "a15nsxx"),
    ("google", "Google", "Pixel 7", "panther", "panther"),
    ("google", "Google", "Pixel 8", "shiba", "shiba"),
    ("Redmi", "Xiaomi", "23021RAAEG", "tapas", "tapas_global"),
    ("POCO", "Xiaomi", "23049PCD8G", "marble", "marble_global"),
    ("OnePlus", "OnePlus", "CPH2449", "OP5961L1", "CPH2449EEA"),
    ("motorola", "motorola", "moto g54 5G", "cancunf", "cancunf_g"),
    ("nothing", "Nothing", "A065", "Pong", "Pong"),
]

# عرض ۷۲۰: با رندر نرم‌افزاری، تصویر و لمس در نمایشگر وب روان می‌ماند
SCREENS = [(720, 1600, 320), (720, 1560, 320), (720, 1520, 300)]


def luhn_check_digit(body: str) -> int:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:  # از راست، رقم‌های جایگاه زوجِ بدنه دو برابر می‌شوند
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10


def luhn_valid(number: str) -> bool:
    return number.isdigit() and luhn_check_digit(number[:-1]) == int(number[-1])


def gen_imei() -> str:
    body = "35" + "".join(secrets.choice(string.digits) for _ in range(12))
    return body + str(luhn_check_digit(body))


def gen_serial() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "R" + "".join(secrets.choice(alphabet) for _ in range(10))


def gen_mac() -> str:
    # بیت locally-administered روشن، unicast
    first = (secrets.randbits(8) | 0x02) & 0xFE
    rest = [secrets.randbits(8) for _ in range(5)]
    return ":".join(f"{b:02x}" for b in [first, *rest])


def gen_identity(model: str | None = None) -> dict:
    if model:
        matches = [p for p in PROFILES if model.lower() in (p[2].lower(), p[3].lower())]
        if not matches:
            raise ValueError(f"unknown model: {model}")
        profile = matches[0]
    else:
        profile = secrets.choice(PROFILES)
    brand, manufacturer, model_name, device, name = profile
    width, height, dpi = secrets.choice(SCREENS)
    return {
        "brand": brand,
        "manufacturer": manufacturer,
        "model": model_name,
        "device": device,
        "name": name,
        "serial": gen_serial(),
        "mac": gen_mac(),
        "imei": gen_imei(),
        "width": width,
        "height": height,
        "dpi": dpi,
    }


def boot_args(ident: dict) -> list[str]:
    """آرگومان‌هایی که به کانتینر redroid داده می‌شوند.

    init در redroid آرگومان‌ها را روی فاصله می‌شکند (نقل‌قول هم کمکی نمی‌کند)،
    پس فقط مقدارهای بدون فاصله اینجا می‌آیند؛ مدل و برند در build_prop هستند.
    """
    return [
        f"androidboot.redroid_width={ident['width']}",
        f"androidboot.redroid_height={ident['height']}",
        f"androidboot.redroid_dpi={ident['dpi']}",
        "androidboot.redroid_gpu_mode=guest",
        f"androidboot.serialno={ident['serial']}",
        f"ro.serialno={ident['serial']}",
        f"ro.ril.oem.imei={ident['imei']}",
    ]


# جایی که در ایمیج خالی است ولی اندروید ۱۲/۱۳ آن را بعد از system و vendor می‌خواند
PROP_MOUNT = "/odm_dlkm/etc/build.prop"


def build_prop(ident: dict) -> str:
    """فایل prop هویت؛ مقدارهای فاصله‌دار مثل «Pixel 7» را سالم می‌رساند."""
    lines = []
    for part in ("product", "product.system", "product.vendor"):
        for key in ("brand", "manufacturer", "model", "device", "name"):
            lines.append(f"ro.{part}.{key}={ident[key]}")
    return "\n".join(lines) + "\n"


def list_models() -> list[str]:
    return [f"{p[2]} ({p[0]} / {p[3]})" for p in PROFILES]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "models":
        print("\n".join(list_models()))
    else:
        print(json.dumps(gen_identity(sys.argv[1] if len(sys.argv) > 1 else None), indent=2))
