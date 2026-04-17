import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def default_python_executable() -> str:
    return shutil.which("python3.11") or sys.executable


def build_backend_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("TOKENIZERS_PARALLELISM", "false")

    warnings = env.get("PYTHONWARNINGS", "")
    extra = "ignore:resource_tracker:UserWarning"
    if extra not in warnings:
        env["PYTHONWARNINGS"] = ",".join(filter(None, [warnings, extra]))
    return env


def install_dependencies(repo_root: Path, python_executable: str) -> None:
    subprocess.check_call(
        [
            python_executable,
            "-m",
            "pip",
            "install",
            "-r",
            "requirements.txt",
        ],
        cwd=str(repo_root),
    )


def start_backend(
    repo_root: Path,
    python_executable: str,
    host: str,
    port: int,
) -> subprocess.Popen:
    if not (repo_root / "src").exists():
        raise RuntimeError(f"{repo_root} does not look like the A2X repo root")

    return subprocess.Popen(
        [
            python_executable,
            "-m",
            "src.backend",
            "--host",
            host,
            "--port",
            str(port),
        ],
        cwd=str(repo_root),
        env=build_backend_env(),
    )


def stop_backend(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent

    parser = argparse.ArgumentParser(
        description="Install dependencies and start the A2X backend server."
    )
    parser.add_argument("--repo-root", default=str(repo_root))
    parser.add_argument("--python", default=default_python_executable())
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install requirements.txt before starting the backend.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()

    if args.install:
        install_dependencies(repo_root, args.python)

    print("Starting A2X backend")
    print(f"  Repo:   {repo_root}")
    print(f"  Python: {args.python}")
    print(f"  URL:    http://{args.host}:{args.port}")
    print("  Press Ctrl+C to stop")

    proc: subprocess.Popen | None = None
    try:
        proc = start_backend(
            repo_root=repo_root,
            python_executable=args.python,
            host=args.host,
            port=args.port,
        )
        proc.wait()
    except KeyboardInterrupt:
        print("\nStopping A2X backend...")
    finally:
        stop_backend(proc)


if __name__ == "__main__":
    main()
