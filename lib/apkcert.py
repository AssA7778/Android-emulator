#!/usr/bin/env python3
"""اثرانگشت SHA-256 گواهی امضای APK (طرح v2/v3) — بدون نیاز به apksigner.

  python3 apkcert.py file.apk            چاپ اثرانگشت‌ها
  python3 apkcert.py file.apk <sha256>   خروج 0 فقط اگر همه‌ی امضاها همین گواهی باشند

فقط گواهی را بیرون می‌کشد؛ درستی خود امضا را اندروید موقع نصب/اسکن بررسی می‌کند.
پس ترکیب «گواهی درست» + «اندروید قبولش کرد» یعنی فایل دست‌نخورده‌ی خود ناشر است.
"""
import hashlib
import struct
import sys

SCHEMES = (0x7109871A, 0xF05368C0)  # v2, v3


def _lp(buf: bytes, off: int) -> tuple[bytes, int]:
    n = struct.unpack_from("<I", buf, off)[0]
    return buf[off + 4:off + 4 + n], off + 4 + n


def cert_digests(path: str) -> list[str]:
    with open(path, "rb") as f:
        data = f.read()
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ValueError("فایل zip/APK نیست")
    cd_off = struct.unpack_from("<I", data, eocd + 16)[0]
    if data[cd_off - 16:cd_off] != b"APK Sig Block 42":
        return []
    size = struct.unpack_from("<Q", data, cd_off - 24)[0]
    block = data[cd_off - size:cd_off - 24]  # بعد از فیلد اندازه‌ی ابتدایی
    out, i = [], 0
    while i + 12 <= len(block):
        ln, bid = struct.unpack_from("<QI", block, i)
        val = block[i + 12:i + 8 + ln]
        i += 8 + ln
        if bid not in SCHEMES:
            continue
        signers, _ = _lp(val, 0)
        off = 0
        while off < len(signers):
            signer, off = _lp(signers, off)
            signed, _ = _lp(signer, 0)
            _, o = _lp(signed, 0)        # digests
            certs, _ = _lp(signed, o)
            cert, _ = _lp(certs, 0)
            out.append(hashlib.sha256(cert).hexdigest())
    return out


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    digests = cert_digests(argv[0])
    if len(argv) == 1:
        print("\n".join(digests))
        return 0 if digests else 1
    want = argv[1].lower().replace(":", "")
    return 0 if digests and all(d == want for d in digests) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
