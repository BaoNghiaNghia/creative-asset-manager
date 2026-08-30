import os
import sys

from deploy.tools.production_env import main


def _environment_file(tmp_path):
    path = tmp_path / "production.env"
    path.write_text(
        "\n".join(
            (
                "APP_ENV=production",
                "GEMINI_MODEL_LIMITS='{\"gemini-test\":{\"rpm\":1,\"tpm\":2,\"rpd\":3}}'",
                "OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx1g",
                "TEST_SECRET=secret-value",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_run_redacted_preserves_values_without_shell_evaluation(tmp_path, capsys):
    path = _environment_file(tmp_path)
    code = (
        "import json,os;"
        "print(json.loads(os.environ['GEMINI_MODEL_LIMITS'])['gemini-test']['rpd']);"
        "print(len(os.environ['OPENSEARCH_JAVA_OPTS'].split()));"
        "print(os.environ['TEST_SECRET'])"
    )

    result = main(
        [
            "run-redacted",
            "--env-file",
            str(path),
            "--expected-owner-uid",
            str(os.getuid()),
            "--",
            sys.executable,
            "-c",
            code,
        ]
    )

    output = capsys.readouterr()
    assert result == 0
    assert output.out.splitlines() == ["3", "2", "[redacted]"]
    assert "secret-value" not in output.out


def test_run_quiet_keeps_success_output_hidden(tmp_path, capsys):
    path = _environment_file(tmp_path)

    result = main(
        [
            "run-quiet",
            "--env-file",
            str(path),
            "--expected-owner-uid",
            str(os.getuid()),
            "--",
            sys.executable,
            "-c",
            "print('hidden')",
        ]
    )

    output = capsys.readouterr()
    assert result == 0
    assert output.out == ""
    assert output.err == ""
