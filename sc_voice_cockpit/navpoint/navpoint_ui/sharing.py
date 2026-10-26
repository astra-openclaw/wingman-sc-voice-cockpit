"""Dashboard sharing, using Accountant's chat image format and QR library."""
import base64
import io
import logging
import secrets
import socket
from pathlib import Path


def get_lan_ip() -> str:
    """Find the outbound interface without sending application data."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('8.8.8.8', 80))
            address = sock.getsockname()[0]
            return address if not address.startswith('127.') else ''
    except OSError:
        return ''


def dashboard_token(directory) -> str:
    """The phone link's key, kept so a scanned QR code keeps working."""
    path = Path(directory) / 'dashboard_token'
    try:
        token = path.read_text(encoding='utf-8').strip()
        if len(token) >= 24:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(18)
    try:
        path.write_text(token, encoding='utf-8')
    except OSError:
        logging.getLogger(__name__).warning('NavPoint dashboard token not saved', exc_info=True)
    return token


def qr_png_base64(url: str) -> str | None:
    """Generate the PNG attachment understood by Wingman's chat renderer."""
    try:
        from PIL import Image, ImageDraw
        from .qrcodegen import QrCode
        qr = QrCode.encode_text(url, QrCode.Ecc.LOW)
        size, scale, border = qr.get_size(), 3, 4
        image = Image.new('RGB', ((size + 2 * border) * scale,) * 2, 'white')
        draw = ImageDraw.Draw(image)
        for y in range(size):
            for x in range(size):
                if qr.get_module(x, y):
                    left, top = (x + border) * scale, (y + border) * scale
                    draw.rectangle((left, top, left + scale - 1, top + scale - 1), fill='black')
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        return base64.b64encode(buffer.getvalue()).decode('ascii')
    except Exception:
        logging.getLogger(__name__).warning('NavPoint QR generation failed', exc_info=True)
        return None
