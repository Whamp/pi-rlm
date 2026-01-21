"""Shared test fixtures for RLM tests."""
import subprocess
import pytest
from pathlib import Path
import sys
import os

# Add scripts dir to path for imports
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def pytest_addoption(parser):
    """Add custom CLI options for pytest."""
    parser.addoption(
        "--slow",
        action="store_true",
        default=False,
        help="Run slow integration tests that spawn real pi subprocesses"
    )


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "slow: mark test as slow (requires --slow to run)"
    )


def pytest_collection_modifyitems(config, items):
    """Skip slow tests unless --slow flag is given."""
    if config.getoption("--slow"):
        return
    
    skip_slow = pytest.mark.skip(reason="need --slow option to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


@pytest.fixture
def rlm_repl_path():
    """Path to the rlm_repl.py script."""
    return SCRIPTS_DIR / "rlm_repl.py"


@pytest.fixture
def init_session(tmp_path, rlm_repl_path):
    """Factory fixture to create initialized sessions.
    
    Returns a function that:
    - Takes content string and optional init args
    - Creates a test file with that content
    - Runs rlm_repl.py init
    - Returns the state.pkl path
    """
    created_paths = []
    
    def _init(content: str, **kwargs) -> Path:
        test_file = tmp_path / "test_context.txt"
        test_file.write_text(content)
        
        cmd = ["python3", str(rlm_repl_path), "init", str(test_file)]
        for key, value in kwargs.items():
            cmd.extend([f"--{key.replace('_', '-')}", str(value)])
        
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
        assert result.returncode == 0, f"Init failed: {result.stderr}"
        
        # Extract state path from output
        for line in result.stdout.splitlines():
            if "Session path:" in line:
                raw_path = line.split(":", 1)[1].strip()
                # Path is relative to tmp_path (cwd), make it absolute
                state_path = (tmp_path / raw_path).resolve()
                created_paths.append(state_path)
                return state_path
        
        raise ValueError(f"Could not find state path in init output: {result.stdout}")
    
    yield _init
    
    # Cleanup is automatic with tmp_path


@pytest.fixture
def run_exec(rlm_repl_path):
    """Factory fixture to run exec commands on a session.
    
    Returns a function that takes state_path and code, runs exec, and
    returns (stdout, stderr, returncode).
    """
    def _exec(state_path: Path, code: str, **kwargs) -> tuple:
        cmd = [
            "python3", str(rlm_repl_path),
            "--state", str(state_path),
            "exec", "-c", code
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout, result.stderr, result.returncode
    
    return _exec
