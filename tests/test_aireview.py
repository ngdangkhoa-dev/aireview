"""Tests for AIReview. Each test builds a real throwaway git repo."""
import shutil
import subprocess
from pathlib import Path

import pytest

from aireview.cli import main
from aireview.core import GitError, build_review

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git is not available on this machine — skipping git-dependent tests",
)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test User")
    git(tmp_path, "config", "commit.gpgsign", "false")
    return tmp_path


def commit_all(cwd: Path, msg: str = "initial commit") -> None:
    git(cwd, "add", "-A")
    git(cwd, "commit", "-m", msg)


def write(cwd: Path, name: str, content: str) -> Path:
    p = cwd / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def reasons(review, needle: str) -> list:
    return [f for f in review.flags if needle in f.reason]


def test_clean_repo_is_normal(repo):
    write(repo, "app.py", "print('hello')\n")
    commit_all(repo)
    review = build_review(repo)
    assert review.changes == []
    assert review.flags == []
    assert review.verdict == "BÌNH THƯỜNG"


def test_code_change_counts_are_right(repo):
    write(repo, "app.py", "a\nb\nc\n")
    commit_all(repo)
    # The "AI" appends two lines, touches nothing else.
    write(repo, "app.py", "a\nb\nc\nd\ne\n")
    review = build_review(repo)
    assert len(review.changes) == 1
    change = review.changes[0]
    assert change.path == "app.py"
    assert change.status == "M"
    assert change.added == 2
    assert change.deleted == 0
    assert review.total_added == 2
    assert review.total_deleted == 0
    assert review.by_category["Code"][0] == 1


def test_deleted_file_is_flagged(repo):
    write(repo, "app.py", "x = 1\n")
    write(repo, "helper.py", "y = 2\n")
    commit_all(repo)
    (repo / "helper.py").unlink()
    review = build_review(repo)
    assert review.count_deleted == 1
    hits = [f for f in review.flags if f.path == "helper.py" and "XOÁ" in f.reason]
    assert hits, "deleting a file must raise a deletion flag"


def test_env_file_is_flagged_sensitive(repo):
    write(repo, ".env", "API_KEY=one\n")
    commit_all(repo)
    write(repo, ".env", "API_KEY=two\n")
    review = build_review(repo)
    hits = [f for f in review.flags if f.path == ".env" and "nhạy cảm" in f.reason]
    assert hits, "touching .env must raise a sensitive-file flag"


def test_dependency_change_in_pyproject_is_flagged(repo):
    write(
        repo,
        "pyproject.toml",
        '[project]\nname = "demo"\ndependencies = ["requests"]\n',
    )
    commit_all(repo)
    write(
        repo,
        "pyproject.toml",
        '[project]\nname = "demo"\ndependencies = ["requests", "flask"]\n',
    )
    review = build_review(repo)
    hits = [f for f in review.flags if f.path == "pyproject.toml" and "dependency" in f.reason]
    assert hits, "changing pyproject.toml must raise a dependency flag"


def test_not_a_git_repo_is_a_friendly_error(tmp_path):
    with pytest.raises(GitError) as excinfo:
        build_review(tmp_path)
    assert "git repo" in str(excinfo.value)


def test_cli_outside_repo_returns_friendly_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = main([])
    assert code == 2
    err = capsys.readouterr().err
    assert "git repo" in err


def test_repo_without_commits_is_a_friendly_error(repo):
    with pytest.raises(GitError) as excinfo:
        build_review(repo)
    assert "chưa có commit" in str(excinfo.value)


def test_cli_writes_report_file(repo, monkeypatch, capsys):
    write(repo, "app.py", "print(1)\n")
    commit_all(repo)
    write(repo, "app.py", "print(2)\n")
    monkeypatch.chdir(repo)
    code = main([])
    assert code == 0
    report = repo / "aireview-report.md"
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    assert "AIReview" in text
    assert "app.py" in text
