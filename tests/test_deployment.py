"""Step 13 deployment-contract tests.

The Docker image is local-first infrastructure, not a claim that the MVP is a
safe hosted service. These checks keep sensitive evidence out of the image and
ensure the application actually writes to the declared mounted volume.
"""

from pathlib import Path

from agents.orchestrator import Orchestrator

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_dockerfile_has_the_required_runtime_and_mounted_evidence_path():
    dockerfile = _read("Dockerfile")

    assert dockerfile.startswith("FROM python:3.11-slim\n")
    for package in (
        "libpango-1.0-0",
        "libpangoft2-1.0-0",
        "libpangocairo-1.0-0",
        "libgdk-pixbuf2.0-0",
        "libffi-dev",
        "shared-mime-info",
    ):
        assert package in dockerfile
    assert "COPY requirements.txt ." in dockerfile
    assert "pip install --no-cache-dir -r requirements.txt" in dockerfile
    assert "COPY . ." in dockerfile
    assert "ENV AUDIT_DB_PATH=/data/audit.db" in dockerfile
    assert 'VOLUME ["/data"]' in dockerfile
    assert "EXPOSE 8501" in dockerfile
    assert (
        'CMD ["streamlit", "run", "app.py", "--server.port=8501", '
        '"--server.address=0.0.0.0"]'
    ) in dockerfile


def test_sensitive_local_files_are_excluded_from_the_image_context():
    ignored = set(_read(".dockerignore").splitlines())

    assert {".env", ".streamlit/secrets.toml", "audit.db", "*.db", ".local-data/"} <= ignored


def test_audit_db_environment_path_is_shared_with_snapshot_storage(monkeypatch, tmp_path):
    mounted_db = tmp_path / "mounted-data" / "audit.db"
    mounted_db.parent.mkdir()
    monkeypatch.setenv("AUDIT_DB_PATH", str(mounted_db))

    orchestrator = Orchestrator(code_version="step-13-test")

    assert Path(orchestrator._audit_log.db_path) == mounted_db
    assert Path(orchestrator._state_store.db_path) == mounted_db
    assert mounted_db.exists()


def test_blank_audit_db_environment_value_preserves_the_local_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUDIT_DB_PATH", "   ")

    orchestrator = Orchestrator(code_version="step-13-test")

    assert orchestrator._audit_log.db_path == "audit.db"
    assert (tmp_path / "audit.db").exists()


def test_secret_example_contains_the_required_password_warning():
    example = _read(".streamlit/secrets.toml.example")

    assert (
        "Never commit the real secrets.toml. This key grants access to send data to the "
        "Anthropic API on your behalf — treat it like a password."
    ) in example
    assert 'ANTHROPIC_API_KEY = "sk-ant-your-key-here"' in example


def test_readme_leads_with_local_first_posture_and_plain_limitations():
    readme = _read("README.md")
    lower_readme = readme.lower()
    headings = [line for line in readme.splitlines() if line.startswith("## ")]

    assert "## Deployment Posture" in headings
    assert "## Five-Minute Demonstration" in headings
    assert headings.index("## Deployment Posture") < headings.index(
        "## Five-Minute Demonstration"
    )
    assert "Default and recommended: run locally" in readme
    assert (
        "Hosted deployment is not recommended without additional access control."
        in readme
    )
    assert "reachable by anyone with the URL" in readme
    assert "-p 127.0.0.1:8501:8501" in readme
    assert "named approval record" in lower_readme
    assert "No independent reviewer enforced" in readme
    assert "No application-level authentication." in readme
    assert "tamper-evident, not tamper-proof" in readme
    assert "Data minimization is informal" in readme
    assert "not a certified privacy or regulatory control" in readme
