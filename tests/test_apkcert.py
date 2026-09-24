import hashlib
import struct
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import apkcert  # noqa: E402


def lp(b: bytes) -> bytes:
    return struct.pack("<I", len(b)) + b


def make_apk(path: Path, certs: list[bytes], scheme: int = 0x7109871A) -> None:
    """یک zip واقعی که جلوی central directory یک APK Signing Block ساختگی دارد."""
    zpath = path.with_suffix(".zip")
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("AndroidManifest.xml", b"x")
    data = zpath.read_bytes()
    eocd = data.rfind(b"PK\x05\x06")
    cd_off = struct.unpack_from("<I", data, eocd + 16)[0]

    signers = b""
    for c in certs:
        signed = lp(b"") + lp(lp(c))  # digests, certificates
        signers += lp(lp(signed))
    pair_val = lp(signers)
    pair = struct.pack("<QI", len(pair_val) + 4, scheme) + pair_val
    size = len(pair) + 8 + 16
    block = struct.pack("<Q", size) + pair + struct.pack("<Q", size) + b"APK Sig Block 42"

    new_cd = cd_off + len(block)
    tail = bytearray(data[cd_off:])
    struct.pack_into("<I", tail, eocd - cd_off + 16, new_cd)
    path.write_bytes(data[:cd_off] + block + bytes(tail))


def test_reads_cert_digest(tmp_path):
    apk = tmp_path / "a.apk"
    make_apk(apk, [b"google-cert"])
    want = hashlib.sha256(b"google-cert").hexdigest()
    assert apkcert.cert_digests(str(apk)) == [want]
    assert apkcert.main([str(apk), want]) == 0
    assert apkcert.main([str(apk), want.upper()]) == 0


def test_rejects_other_or_mixed_signers(tmp_path):
    want = hashlib.sha256(b"google-cert").hexdigest()
    other = tmp_path / "o.apk"
    make_apk(other, [b"evil-cert"])
    assert apkcert.main([str(other), want]) == 1
    mixed = tmp_path / "m.apk"
    make_apk(mixed, [b"google-cert", b"evil-cert"])
    assert apkcert.main([str(mixed), want]) == 1


def test_unsigned_or_v1_only_is_rejected(tmp_path):
    apk = tmp_path / "u.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("AndroidManifest.xml", b"x")
    assert apkcert.cert_digests(str(apk)) == []
    assert apkcert.main([str(apk), "00" * 32]) == 1
