"""Launch the A2X Registry Demo UI.

Two modes (selected automatically based on whether dist/ exists):

  Dev mode   — no dist/ present: starts Vite dev server, open http://localhost:5173
  Prod mode  — dist/ present:    serves static files,    open http://localhost:8000

To build frontend for prod mode:
    python -m src.frontend

Options:
    python -m src.ui --port 8000      # custom backend port
    python -m src.ui --no-frontend    # backend only
    python -m src.ui --reload         # enable uvicorn auto-reload
"""

import argparse
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

# Ensure project root is on path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


def _get_local_ip() -> str | None:
    """Return the machine's LAN IP address, or None if unavailable."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def _kill_proc_tree(proc: subprocess.Popen):
    """Kill a process and all its children (Windows-safe)."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            proc.kill()


def _start_frontend_dev(backend_port: int) -> subprocess.Popen | None:
    """Start Vite dev server as a child process. Returns None if using dist/."""
    if (FRONTEND_DIR / "dist").exists():
        print(f"  Frontend: http://127.0.0.1:{backend_port}  (serving from dist/)")
        return None

    if not (FRONTEND_DIR / "node_modules").exists():
        print("  [info] Installing frontend dependencies (first time)...")
        subprocess.run(
            "npm install",
            cwd=str(FRONTEND_DIR),
            shell=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    return subprocess.Popen(
        "npm run dev -- --clearScreen false",
        cwd=str(FRONTEND_DIR),
        shell=True,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def main():
    parser = argparse.ArgumentParser(description="A2X Registry Demo UI")
    parser.add_argument("--port", type=int, default=8000, help="Backend port (default: 8000)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--no-frontend", action="store_true", help="Skip starting frontend dev server")
    parser.add_argument("--reload", action="store_true", default=False, help="Enable auto-reload")
    args = parser.parse_args()

    lan_ip = _get_local_ip()
    display_host = lan_ip if args.host in ("0.0.0.0", "::") else args.host

    print(f"\n  A2X Registry Demo UI")
    print(f"  Local:    http://localhost:{args.port}")
    if display_host and display_host not in ("localhost", "127.0.0.1"):
        print(f"  Network:  http://{display_host}:{args.port}")

    vite_proc = None
    if not args.no_frontend:
        vite_proc = _start_frontend_dev(args.port)
        if vite_proc:
            print(f"  Frontend: http://localhost:5173  (Vite dev server)")
            if display_host and display_host not in ("localhost", "127.0.0.1"):
                print(f"  Frontend: http://{display_host}:5173  (remote access)")
            print()
        else:
            print()
    else:
        print()

    import uvicorn

    try:
        uvicorn.run(
            "src.backend.app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
    finally:
        if vite_proc:
            _kill_proc_tree(vite_proc)


if __name__ == "__main__":
    main()
