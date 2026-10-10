from pathlib import Path


def test_legal_agent_repository_has_expected_structure() -> None:
    root = Path(__file__).resolve().parents[1]

    assert (root / "src" / "chinese_legal_agent").is_dir()
