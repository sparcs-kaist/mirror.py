import pytest
from pathlib import Path
import mirror
import os
import shutil

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """
    This autouse fixture runs once per session to set up the test environment.
    It modifies the default RUN_PATH and STATE_PATH to use a temporary
    test-specific directory, preventing tests from interfering with the
    actual production environment or each other.
    """
    # Define temporary paths within the project's test environment directory
    test_run_path = Path("test_env/run")
    test_state_path = Path("test_env/state")

    # Clean up old test directories if they exist
    if test_run_path.exists():
        shutil.rmtree(test_run_path)
    if test_state_path.exists():
        shutil.rmtree(test_state_path)

    # Create the temporary directories
    test_run_path.mkdir(parents=True, exist_ok=True)
    test_state_path.mkdir(parents=True, exist_ok=True)

    # Monkeypatch the paths in the mirror module
    mirror.RUN_PATH = test_run_path
    mirror.STATE_PATH = test_state_path

    print(f"Redirected mirror.RUN_PATH to {test_run_path}")
    print(f"Redirected mirror.STATE_PATH to {test_state_path}")

    # Yield control to the test session
    yield

    # Teardown: Clean up the temporary directories after the test session
    print("Cleaning up test environment...")
    shutil.rmtree(test_run_path)
    shutil.rmtree(test_state_path)
