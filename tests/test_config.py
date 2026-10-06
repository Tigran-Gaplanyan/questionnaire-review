import os
import subprocess
import sys

from qra import config


def test_relative_db_setting_is_read_from_the_project_folder():
    assert config._project_path("var/x.sqlite") == config.ROOT / "var" / "x.sqlite"


def test_cli_works_in_a_fresh_process_with_a_relative_QRA_DB(tmp_path):
    # Regression: QRA_DB=var/qra.sqlite (as in .env.example) used to crash every new process,
    # which broke the evaluator's restart check whenever a .env file existed.
    env = {**os.environ, "QRA_DB": "var/qra.sqlite"}
    result = subprocess.run([sys.executable, "-m", "qra", "--db", str(tmp_path / "t.sqlite"), "export-state"],
                            capture_output=True, text=True, env=env, cwd=config.ROOT, timeout=60)
    assert result.returncode == 0, result.stderr


def test_dotenv_accepts_plain_and_export_lines(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nQRA_TEST_A=1\nexport QRA_TEST_B='2'\n")
    monkeypatch.delenv("QRA_TEST_A", raising=False)
    monkeypatch.delenv("QRA_TEST_B", raising=False)
    config.load_dotenv(env_file)
    assert os.environ["QRA_TEST_A"] == "1" and os.environ["QRA_TEST_B"] == "2"
