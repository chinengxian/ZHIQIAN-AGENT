import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from tests.test_docling_adapter import minimal_text_pdf
from tests.test_ingestion_e2e_integration import docx_bytes

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
DATA = ROOT / "data" / "browser-knowledge-e2e"
FIXTURES = DATA / "fixtures"
LOGS = DATA / "logs"
NODE = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"


def wait_port(port: int, process: subprocess.Popen[bytes], timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"service_exited:{port}:{process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError(f"service_start_timeout:{port}")


def wait_health(process: subprocess.Popen[bytes], timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"api_exited:{process.returncode}")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    raise RuntimeError("api_start_timeout")


def main() -> int:
    if os.getenv("RUN_KNOWLEDGE_INTEGRATION") != "1":
        raise RuntimeError("set RUN_KNOWLEDGE_INTEGRATION=1")
    FIXTURES.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "orbital.pdf").write_bytes(minimal_text_pdf("OrbitalPDF evidence"))
    (FIXTURES / "integration.docx").write_bytes(docx_bytes())
    (FIXTURES / "markdown.md").write_bytes(b"# Manual\nMarkdownToken evidence")
    (FIXTURES / "plain.txt").write_bytes(b"PlainToken evidence")
    env = os.environ.copy()
    env.update(
        {
            "AGENT_MODEL_PROVIDER": "openai",
            "AGENT_OPENAI_BASE_URL": "https://example.test/v1",
            "AGENT_OPENAI_API_KEY": "local-test-key",
            "AGENT_OPENAI_MODEL": "deterministic-knowledge-test",
            "AGENT_KNOWLEDGE_ENABLED": "true",
            "AGENT_DATABASE_URL": "postgresql+asyncpg://agent:agent@127.0.0.1:5432/agent",
            "AGENT_REDIS_URL": "redis://127.0.0.1:6379/0",
            "AGENT_MILVUS_URI": "http://127.0.0.1:19530",
            "AGENT_STORAGE_ROOT": str(DATA / "uploads"),
            "AGENT_EMBEDDING_BASE_URL": "http://127.0.0.1:8011/v1",
            "AGENT_EMBEDDING_API_KEY": "local-embedding-key",
            "AGENT_EMBEDDING_MODEL": "text-embedding-3-small",
            "AGENT_EMBEDDING_DIMENSION": "1536",
            "RUN_KNOWLEDGE_INTEGRATION": "1",
            "KNOWLEDGE_FIXTURE_DIR": str(FIXTURES),
            "KNOWLEDGE_WEB_BASE_URL": "http://127.0.0.1:4175",
            "PATH": f"{NODE.parent};{env['PATH']}",
        }
    )
    commands = [
        ("embedding", [sys.executable, "-m", "tests.integration.embedding_stub"], ROOT),
        (
            "worker",
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "agent_api.knowledge.worker.celery_app:app",
                "worker",
                "--pool=solo",
                "-Q",
                "knowledge",
                "--loglevel=WARNING",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
            ],
            ROOT,
        ),
        (
            "beat",
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "agent_api.knowledge.worker.celery_app:app",
                "beat",
                "--loglevel=WARNING",
            ],
            ROOT,
        ),
        (
            "api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "tests.integration.e2e_knowledge_app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            ROOT,
        ),
        (
            "web",
            [
                str(NODE),
                str(WEB / "node_modules/vite/bin/vite.js"),
                "--host",
                "127.0.0.1",
                "--port",
                "4175",
                "--strictPort",
            ],
            WEB,
        ),
    ]
    processes: list[subprocess.Popen[bytes]] = []
    logs = []
    try:
        for name, command, cwd in commands:
            log = (LOGS / f"{name}.log").open("wb")
            logs.append(log)
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            processes.append(process)
            if name == "embedding":
                wait_port(8011, process)
            elif name == "api":
                wait_health(process)
            elif name == "web":
                wait_port(4175, process)
        result = subprocess.run(
            [
                str(NODE),
                str(WEB / "node_modules/@playwright/test/cli.js"),
                "test",
                "--config=playwright.real.config.ts",
                "--workers=1",
            ],
            cwd=WEB,
            env=env,
            check=False,
        )
        return result.returncode
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
