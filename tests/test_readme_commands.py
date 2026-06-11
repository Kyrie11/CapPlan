from pathlib import Path


def test_readme_contains_required_commands():
    text = Path("README.md").read_text()
    required = [
        "python -m venv .venv",
        "source .venv/bin/activate",
        "python -m pip install --upgrade pip",
        "pip install -r requirements.txt",
        "pip install -e .",
        "pytest -q",
        "python scripts/build_dataset.py \\",
        "--scene_source synthetic",
        "python scripts/validate_dataset.py \\",
        "--scene_source nuplan",
        "python scripts/train_casa.py \\",
        "python scripts/run_closed_loop_eval.py \\",
        "python scripts/run_ablations.py \\",
    ]
    for cmd in required:
        assert cmd in text


def test_readme_does_not_claim_mock_strict_is_nuplan_closed_loop():
    text = Path("README.md").read_text().lower()
    assert "not a substitute for nuplan paper-level closed-loop results" in text
