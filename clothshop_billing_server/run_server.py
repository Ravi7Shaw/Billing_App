"""
run_server.py
--------------
Double-click start_server.bat (Windows) -- or run `python run_server.py`
on any OS -- to start the billing server.

What it does:
1. Works out this machine's LAN IP address (the address other devices on
   the same Wi-Fi use to reach it).
2. Prints the URL, and a scannable QR code of that URL, right in the
   console -- point a phone's camera at it to open the billing app in
   its browser, no typing required. The same QR is also saved as a PNG
   so it can be printed and kept at the billing counter.
3. Sets up logging to backend/logs/server.log (rotates automatically).
4. Starts the FastAPI app with uvicorn, listening on all network
   interfaces so phones/laptops on the same Wi-Fi can reach it.

Everything stays on the local network -- nothing here talks to the
internet, and the server refuses to be useful to anyone not on the same
Wi-Fi (it's just not reachable from outside it).
"""

import os
import socket
import sys

PORT = int(os.environ.get("BILLING_PORT", "5000"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
sys.path.insert(0, BACKEND_DIR)


def get_lan_ip() -> str:
    """Ask the OS which local network interface it would use to reach the
    outside world, without actually sending any packets. This is the
    standard trick for finding 'my LAN IP' cross-platform, and it works
    even with no real internet connection."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def show_qr(url: str, save_path: str):
    try:
        import qrcode
    except ImportError:
        print("  (Install `qrcode[pil]` to also get a QR code: pip install -r backend/requirements.txt)")
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make()
    qr.print_ascii(invert=True)
    try:
        img = qr.make_image()
        img.save(save_path)
        print(f"  QR code image also saved to: {save_path}")
        print("  (print it and keep it at the billing counter)")
    except Exception as e:
        print(f"  (could not save QR image: {e})")


def main():
    # main.py (in backend/) sets up logging and creates the `app` object
    # and the logs/ directory as soon as it's imported.
    from main import app, LOG_PATH  # noqa: E402

    ip = get_lan_ip()
    url = f"http://{ip}:{PORT}"
    qr_path = os.path.join(BACKEND_DIR, "logs", "billing_qr.png")

    print("=" * 62)
    print("  Cloth Shop Billing Server")
    print("=" * 62)
    print(f"  Log file : {LOG_PATH}")
    print(f"  Database : {os.path.join(BACKEND_DIR, 'data', 'cloth_shop.db')}")
    print(f"  Backups  : {os.path.join(BACKEND_DIR, 'data', 'backups')}")
    print()
    print("  On this computer, open:")
    print(f"    {url}")
    print()
    print("  On a PHONE (same Wi-Fi as this computer), either:")
    print(f"    - type {url} into the browser, or")
    print("    - scan this QR code:")
    print()
    show_qr(url, qr_path)
    print()
    print("  Keep this window open while billing.")
    print("  Press Ctrl+C here to stop the server.")
    print("=" * 62)

    import logging
    logging.getLogger("server").info(f"Server ready at {url}")

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_config=None)


if __name__ == "__main__":
    main()
