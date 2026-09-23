import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import identity  # noqa: E402


def test_luhn_known_imei():
    # نمونه‌ی مرجع رایج برای الگوریتم Luhn روی IMEI
    assert identity.luhn_check_digit("49015420323751") == 8
    assert identity.luhn_valid("490154203237518")
    assert not identity.luhn_valid("490154203237517")


def test_imei_format_and_luhn():
    for _ in range(500):
        imei = identity.gen_imei()
        assert re.fullmatch(r"\d{15}", imei)
        assert identity.luhn_valid(imei)


def test_identities_are_unique():
    idents = [identity.gen_identity() for _ in range(300)]
    for key in ("imei", "serial", "mac"):
        values = [i[key] for i in idents]
        assert len(set(values)) == len(values), key


def test_mac_is_local_unicast():
    for _ in range(200):
        mac = identity.gen_mac()
        assert re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", mac)
        first = int(mac[:2], 16)
        assert first & 0x02 and not first & 0x01


def test_serial_format():
    assert re.fullmatch(r"R[A-Z0-9]{10}", identity.gen_serial())


def test_model_selection():
    ident = identity.gen_identity("pixel 7")
    assert ident["model"] == "Pixel 7" and ident["device"] == "panther"
    assert identity.gen_identity("dm1q")["model"] == "SM-S911B"
    with pytest.raises(ValueError):
        identity.gen_identity("iphone")


def test_boot_args_carry_identity():
    ident = identity.gen_identity()
    args = identity.boot_args(ident)
    assert f"ro.serialno={ident['serial']}" in args
    assert f"ro.ril.oem.imei={ident['imei']}" in args
    # init در redroid روی فاصله می‌شکند؛ هیچ آرگومانی نباید فاصله داشته باشد
    assert all("=" in a and " " not in a for a in args)


def test_build_prop_keeps_spaces():
    ident = identity.gen_identity("moto g54 5G")
    prop = identity.build_prop(ident).splitlines()
    assert "ro.product.model=moto g54 5G" in prop
    assert "ro.product.system.model=moto g54 5G" in prop
    assert "ro.product.vendor.manufacturer=motorola" in prop
    assert all(re.fullmatch(r"ro\.[a-z.]+=[^\n=]+", line) for line in prop)
