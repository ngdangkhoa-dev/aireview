"""Core logic for AIReview.

Reads a git diff (working tree + staged vs HEAD, or vs a chosen ref),
summarizes it in plain language, groups changes by kind, and raises risk
flags for the things a human should look at twice: deletions, secrets,
config changes, dependency changes, huge deletions, binary files.

Strictly read-only: AIReview never writes, stages, or commits anything in
the user's repo. The only file it creates is its own report.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


class GitError(Exception):
    """A friendly, user-facing git problem."""


@dataclass
class FileChange:
    path: str
    status: str = "M"  # 'A' added, 'M' modified, 'D' deleted
    added: int = 0
    deleted: int = 0
    binary: bool = False


@dataclass
class Flag:
    path: str
    reason: str


@dataclass
class Review:
    base: str
    changes: list = field(default_factory=list)      # list[FileChange]
    flags: list = field(default_factory=list)        # list[Flag]
    verdict: str = "BÌNH THƯỜNG"
    by_category: dict = field(default_factory=dict)  # name -> [files, added, deleted]

    @property
    def total_added(self) -> int:
        return sum(c.added for c in self.changes)

    @property
    def total_deleted(self) -> int:
        return sum(c.deleted for c in self.changes)

    @property
    def count_added(self) -> int:
        return sum(1 for c in self.changes if c.status == "A")

    @property
    def count_modified(self) -> int:
        return sum(1 for c in self.changes if c.status == "M")

    @property
    def count_deleted(self) -> int:
        return sum(1 for c in self.changes if c.status == "D")


# --------------------------------------------------------------------------
# git plumbing (read-only)
# --------------------------------------------------------------------------

def _git(cwd: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True
        )
    except FileNotFoundError:
        raise GitError(
            "Không tìm thấy lệnh `git` trên máy này. "
            "AIReview cần git để đọc thay đổi — hãy cài git rồi chạy lại."
        )
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} thất bại")
    return proc.stdout


def ensure_repo(cwd: Path, since: str | None = None) -> str:
    """Return the ref to diff against, or raise a friendly GitError."""
    try:
        inside = _git(cwd, "rev-parse", "--is-inside-work-tree").strip()
    except GitError:
        inside = "false"
    if inside != "true":
        raise GitError(
            "Thư mục hiện tại không nằm trong git repo nào. "
            "Hãy chạy `aireview` bên trong thư mục dự án của bạn (nơi có thư mục .git)."
        )
    if since:
        try:
            _git(cwd, "rev-parse", "--verify", "--quiet", since + "^{commit}")
        except GitError:
            raise GitError(
                f"Không tìm thấy ref '{since}'. "
                "Kiểm tra lại tên branch / tag / mã commit rồi chạy lại."
            )
        return since
    try:
        _git(cwd, "rev-parse", "--verify", "--quiet", "HEAD")
    except GitError:
        raise GitError(
            "Repo này chưa có commit nào nên chưa có mốc để so sánh. "
            "Hãy commit ít nhất một lần, để AI sửa, rồi chạy `aireview`."
        )
    return "HEAD"


def _count_lines(path: Path) -> tuple[int, bool]:
    """(line_count, is_binary) for an untracked file, read-only."""
    try:
        data = path.read_bytes()
    except OSError:
        return 0, False
    if b"\0" in data[:8192]:
        return 0, True
    if not data:
        return 0, False
    lines = data.count(b"\n")
    if not data.endswith(b"\n"):
        lines += 1
    return lines, False


def collect_changes(cwd: Path, base: str) -> list[FileChange]:
    changes: dict[str, FileChange] = {}

    # Line counts per file. --no-renames: a rename shows as delete + add,
    # which is exactly how a reviewer should see it anyway.
    out = _git(cwd, "diff", "--numstat", "--no-renames", base)
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d, path = parts[0], parts[1], parts[2]
        fc = FileChange(path=path)
        if a == "-" or d == "-":
            fc.binary = True
        else:
            fc.added, fc.deleted = int(a), int(d)
        changes[path] = fc

    # Added / deleted status per file.
    out = _git(cwd, "diff", "--name-status", "--no-renames", base)
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        fc = changes.get(path)
        if fc is None:
            fc = FileChange(path=path)
            changes[path] = fc
        if status:
            fc.status = status[0]

    # Untracked files never appear in `git diff` — pick them up from status.
    out = _git(cwd, "status", "--porcelain=v1", "--untracked-files=all")
    for line in out.splitlines():
        if line.startswith("?? "):
            path = line[3:].strip().strip('"')
            if path in changes:
                continue
            fc = FileChange(path=path, status="A")
            fc.added, fc.binary = _count_lines(cwd / path)
            changes[path] = fc

    return sorted(changes.values(), key=lambda c: c.path)


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cc", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".kts", ".gd",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".sql", ".html", ".css", ".scss",
    ".vue", ".lua", ".pl", ".r", ".cs", ".scala", ".clj", ".ex", ".exs",
    ".erl", ".hs", ".ml", ".dart",
}
CONFIG_EXTS = {".toml", ".yaml", ".yml", ".ini", ".cfg", ".conf"}
DOC_EXTS = {".md", ".rst", ".adoc", ".txt"}
CATEGORIES = ["Code", "Cấu hình", "Tài liệu", "Test", "Khác"]


def classify(path: str) -> str:
    p = Path(path)
    parts = [part.lower() for part in p.parts]
    lname = p.name.lower()
    ext = p.suffix.lower()

    # Tests first: test files are code, but reviewers want them grouped apart.
    if (
        any(part in {"test", "tests", "__tests__", "spec", "specs"} for part in parts)
        or lname.startswith("test_")
        or lname.endswith("_test.py")
        or ".test." in lname
        or ".spec." in lname
    ):
        return "Test"
    if lname.startswith("requirements") and ext == ".txt":
        return "Cấu hình"
    if ext in DOC_EXTS or (parts and parts[0] == "docs"):
        return "Tài liệu"
    if (
        ext in CONFIG_EXTS
        or lname in {"dockerfile", "package.json", "pyproject.toml", "tsconfig.json", "composer.json"}
        or lname.startswith("dockerfile.")
        or ".github" in parts
    ):
        return "Cấu hình"
    if ext in CODE_EXTS:
        return "Code"
    return "Khác"


# --------------------------------------------------------------------------
# risk flags
# --------------------------------------------------------------------------

DEPENDENCY_FILES = {
    "pyproject.toml", "package.json", "pipfile", "pipfile.lock",
    "poetry.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "cargo.toml", "cargo.lock", "go.mod", "go.sum", "gemfile",
    "gemfile.lock", "composer.json", "composer.lock",
}
PRIVATE_KEY_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
SENSITIVE_EXTS = {".pem", ".key", ".p12", ".pfx", ".keystore"}
ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")

LARGE_FILE_DELETION = 100   # lines removed from a single file
LARGE_TOTAL_DELETION = 500  # lines removed across the whole change


def is_sensitive(path: str) -> bool:
    p = Path(path)
    lname = p.name.lower()
    lowered = path.lower()
    if lname == ".env":
        return True
    if lname.startswith(".env.") and not lname.endswith(ENV_TEMPLATE_SUFFIXES):
        return True
    if "secret" in lowered or "credential" in lowered:
        return True
    if p.suffix.lower() in SENSITIVE_EXTS:
        return True
    if lname in PRIVATE_KEY_NAMES:
        return True
    return False


def is_dependency_file(path: str) -> bool:
    lname = Path(path).name.lower()
    if lname.startswith("requirements") and lname.endswith(".txt"):
        return True
    return lname in DEPENDENCY_FILES


def detect_flags(changes: list[FileChange]) -> list[Flag]:
    flags: list[Flag] = []
    for c in changes:
        if c.status == "D":
            flags.append(Flag(
                c.path,
                "File bị XOÁ — kiểm tra chắc chắn file này không còn cần nữa",
            ))
        if is_sensitive(c.path):
            flags.append(Flag(
                c.path,
                "File nhạy cảm bị đụng tới — có thể chứa mật khẩu / khóa bí mật",
            ))
        if classify(c.path) == "Cấu hình":
            flags.append(Flag(
                c.path,
                "File cấu hình thay đổi — hành vi khi chạy thật có thể khác đi",
            ))
        if is_dependency_file(c.path):
            flags.append(Flag(
                c.path,
                "File dependency thay đổi — kiểm tra thư viện được thêm / bớt / đổi phiên bản",
            ))
        if c.binary:
            flags.append(Flag(
                c.path,
                "File nhị phân thay đổi — không xem được nội dung diff theo dòng",
            ))
        if c.deleted > LARGE_FILE_DELETION:
            flags.append(Flag(
                c.path,
                f"Một file mất rất nhiều dòng ({c.deleted} dòng bị xoá)",
            ))
    total_deleted = sum(c.deleted for c in changes)
    if total_deleted > LARGE_TOTAL_DELETION:
        flags.append(Flag(
            "(toàn bộ thay đổi)",
            f"Tổng số dòng bị xoá rất lớn ({total_deleted} dòng)",
        ))
    return flags


def verdict_for(flags: list[Flag]) -> str:
    n = len(flags)
    if n == 0:
        return "BÌNH THƯỜNG"
    if n <= 2:
        return "CẦN XEM LẠI"
    return "RỦI RO CAO"


# --------------------------------------------------------------------------
# review assembly + rendering
# --------------------------------------------------------------------------

def build_review(cwd: Path, since: str | None = None) -> Review:
    base = ensure_repo(cwd, since)
    changes = collect_changes(cwd, base)
    flags = detect_flags(changes)
    by_category: dict[str, list] = {}
    for c in changes:
        cat = classify(c.path)
        bucket = by_category.setdefault(cat, [0, 0, 0])
        bucket[0] += 1
        bucket[1] += c.added
        bucket[2] += c.deleted
    return Review(
        base=base,
        changes=changes,
        flags=flags,
        verdict=verdict_for(flags),
        by_category=by_category,
    )


def _summary_lines(review: Review) -> list[str]:
    return [
        f"File thay đổi: {len(review.changes)} "
        f"(thêm {review.count_added} · sửa {review.count_modified} · xoá {review.count_deleted})",
        f"Dòng thay đổi: +{review.total_added} / -{review.total_deleted}",
        f"Kết luận: {review.verdict} ({len(review.flags)} cờ rủi ro)",
    ]


def render_text(review: Review) -> str:
    lines = ["=== AIReview — AI vừa sửa gì? ==="]
    lines.append(f"So sánh: working tree ↔ {review.base}")
    lines += _summary_lines(review)
    if review.by_category:
        lines.append("")
        lines.append("Theo nhóm:")
        for cat in CATEGORIES:
            if cat in review.by_category:
                n, a, d = review.by_category[cat]
                lines.append(f"  {cat:<10} {n} file   +{a} / -{d}")
    if review.flags:
        lines.append("")
        lines.append("🚩 Cờ rủi ro:")
        for f in review.flags:
            lines.append(f"  - {f.path} — {f.reason}")
    if review.changes:
        lines.append("")
        lines.append("Tất cả file đã đổi:")
        for c in review.changes:
            mark = {"A": "+", "M": "~", "D": "−"}.get(c.status, "?")
            stat = "nhị phân" if c.binary else f"+{c.added} / -{c.deleted}"
            lines.append(f"  [{mark}] {c.path}  ({stat}, {classify(c.path)})")
    else:
        lines.append("")
        lines.append("Không có thay đổi nào so với mốc so sánh. Repo đang sạch.")
    return "\n".join(lines)


def render_markdown(review: Review) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# AIReview — AI vừa sửa gì trong repo này?",
        "",
        f"- Thời điểm: {now}",
        f"- So sánh: working tree ↔ `{review.base}`",
        f"- **Kết luận: {review.verdict}** ({len(review.flags)} cờ rủi ro)",
        "",
        "## Tổng quan",
        "",
    ]
    for s in _summary_lines(review):
        lines.append(f"- {s}")
    lines += ["", "## Theo nhóm", "", "| Nhóm | File | Dòng + | Dòng − |", "|---|---:|---:|---:|"]
    for cat in CATEGORIES:
        if cat in review.by_category:
            n, a, d = review.by_category[cat]
            lines.append(f"| {cat} | {n} | {a} | {d} |")
    lines += ["", "## 🚩 Cờ rủi ro", ""]
    if review.flags:
        for f in review.flags:
            lines.append(f"- `{f.path}` — {f.reason}")
    else:
        lines.append("Không có cờ rủi ro nào. 🎉")
    lines += ["", "## Tất cả file đã đổi", ""]
    if review.changes:
        lines += ["| File | Trạng thái | Dòng + | Dòng − | Nhóm |", "|---|---|---:|---:|---|"]
        status_name = {"A": "Thêm", "M": "Sửa", "D": "Xoá"}
        for c in review.changes:
            a = "—" if c.binary else str(c.added)
            d = "—" if c.binary else str(c.deleted)
            lines.append(
                f"| `{c.path}` | {status_name.get(c.status, c.status)} | {a} | {d} | {classify(c.path)} |"
            )
    else:
        lines.append("Không có thay đổi nào so với mốc so sánh. Repo đang sạch.")
    lines += [
        "",
        "---",
        "Báo cáo do AIReview tạo — chỉ đọc, không sửa gì trong repo của bạn.",
        "",
    ]
    return "\n".join(lines)
