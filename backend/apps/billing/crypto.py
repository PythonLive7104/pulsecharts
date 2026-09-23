"""Wallet configuration + QR rendering for manual crypto payments."""

from __future__ import annotations

import io

from django.conf import settings

# (asset, settings key, network key). Order is display order at checkout.
_ASSETS = (
    ("USDT", "CRYPTO_WALLET_USDT", "CRYPTO_WALLET_USDT_NETWORK"),
    ("BTC", "CRYPTO_WALLET_BTC", "CRYPTO_WALLET_BTC_NETWORK"),
    ("LTC", "CRYPTO_WALLET_LTC", "CRYPTO_WALLET_LTC_NETWORK"),
)


def _qr_svg(data: str) -> str:
    """Inline SVG QR for `data`, or "" if the library is unavailable.

    SVG rather than PNG so there is no Pillow dependency and no native build, and the
    result scales cleanly at any size. Returned as markup for the API to embed, so
    the frontend needs no QR library of its own.

    Fails soft: a missing library must degrade to "copy the address", never break
    checkout — the address text is the thing that actually matters.
    """
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return ""
    try:
        img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue().decode()
    except Exception:
        return ""


def wallets() -> list[dict]:
    """Configured wallets, newest config each call so a rotation needs no restart.

    An asset with no address is OMITTED rather than shown empty: a checkout page
    offering an asset that receives nothing is worse than one offering fewer options.
    """
    out = []
    for asset, addr_key, net_key in _ASSETS:
        address = (getattr(settings, addr_key, "") or "").strip()
        if not address:
            continue
        out.append({
            "asset": asset,
            "address": address,
            # Displayed with every address: USDT exists on several chains and paying
            # on the wrong one loses the funds irrecoverably.
            "network": (getattr(settings, net_key, "") or "").strip(),
            "qr_svg": _qr_svg(address),
        })
    return out


def address_for(asset: str) -> str:
    for a in wallets():
        if a["asset"] == asset:
            return a["address"]
    return ""
