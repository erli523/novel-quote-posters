"""Run repository checks and retain each attempt's private evidence without overwriting."""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from novel_quote_posters.storage import digest_file, save_json


def main():
    identity = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = Path("evidence") / identity
    evidence.mkdir(parents=True)
    environment = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONIOENCODING": "utf-8"}
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                           check=True, capture_output=True, encoding="utf-8").stdout.splitlines()
    snapshots = {name: digest_file(Path(name)) for name in files if Path(name).is_file()}
    steps = [[sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"],
             [sys.executable, "-m", "pytest", "-q", "--tb=short",
              f"--junitxml={evidence / 'tests.xml'}"]]
    results = []
    for index, command in enumerate(steps):
        result = subprocess.run(command, check=False, env=environment, capture_output=True,
                                encoding="utf-8")
        (evidence / f"step{index}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        results.append({"command": command, "exit_code": result.returncode,
                        "log": f"step{index}.log"})
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
    save_json(evidence / "report.json", {"schema_version": 1, "run_id": identity,
                                        "input_hashes": snapshots, "steps": results})
    print(f"Evidence: {evidence}")
    return int(any(row["exit_code"] != 0 for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
