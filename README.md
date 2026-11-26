# AIReview 🔍

**Your AI pair-programmer just edited 40 files — here's what actually happened.**

You come back from coffee and the AI says "Done!" The diff is 600 lines across
a dozen files. Somewhere in there it deleted a helper, rewrote your `.env`,
and quietly added a dependency. Reading the raw diff line by line is nobody's
job — so most people just commit and pray.

AIReview reads the diff for you and reports back in plain language: what
changed, grouped by kind (code / config / docs / tests), and — most
importantly — **risk flags** on the things a human should look at twice.

```console
$ aireview
=== AIReview — AI vừa sửa gì? ===
So sánh: working tree ↔ HEAD
File thay đổi: 4 (thêm 1 · sửa 2 · xoá 1)
Dòng thay đổi: +18 / -132
Kết luận: RỦI RO CAO (5 cờ rủi ro)

🚩 Cờ rủi ro:
  - .env — File nhạy cảm bị đụng tới — có thể chứa mật khẩu / khóa bí mật
  - helper.py — File bị XOÁ — kiểm tra chắc chắn file này không còn cần nữa
  - pyproject.toml — File dependency thay đổi — ...
```

*(The summary UI is in Vietnamese for now — an English UI is on the roadmap.
The verdicts: BÌNH THƯỜNG = all clear, CẦN XEM LẠI = take a look,
RỦI RO CAO = stop and review before you commit.)*

## Install

```bash
pip install git+https://github.com/ngdangkhoa-dev/aireview.git
```

Or from a clone:

```bash
git clone https://github.com/ngdangkhoa-dev/aireview.git
cd aireview
pip install .
```

Requires Python 3.9+ and `git` on your PATH. No third-party dependencies —
standard library only.

## Usage

Run it inside any git repo, after your AI (or anyone, really) has been
editing:

```bash
aireview                  # working tree + staged changes vs HEAD
aireview --since main     # compare a branch / tag / commit to the working tree
aireview --no-write       # print only, don't write the report file
aireview --output notes.md
```

Every run prints a summary to the terminal **and** writes `aireview-report.md`
in the current directory (unless `--no-write`).

AIReview is **strictly read-only**: it never stages, commits, or modifies
anything in your repo. The only file it creates is its own report.

## Risk flags

| Flag | Triggers when |
|---|---|
| 🗑️ **File deleted** | Any tracked file is deleted |
| 🔑 **Sensitive file touched** | `.env` (templates like `.env.example` excluded), paths containing `secret` or `credential`, `*.pem` / `*.key` / `*.p12` / `*.pfx`, private keys (`id_rsa`, `id_ed25519`, …) |
| ⚙️ **Config changed** | `*.toml` / `*.yaml` / `*.ini` / `*.cfg`, `Dockerfile`, `package.json`, `pyproject.toml`, anything under `.github/` |
| 📦 **Dependency change** | `requirements*.txt`, `pyproject.toml`, `package.json`, `Pipfile`, `Cargo.toml` / `go.mod` / `Gemfile` / `composer.json`, and lock files (`poetry.lock`, `package-lock.json`, `yarn.lock`, `Cargo.lock`, …) |
| ✂️ **Huge deletion** | One file loses more than 100 lines, or the change deletes more than 500 lines in total |
| 💾 **Binary changed** | A binary file changed (no line diff exists to eyeball) |

The overall verdict is a simple count of flags:

- **0 flags → BÌNH THƯỜNG** (all clear)
- **1–2 flags → CẦN XEM LẠI** (take a look)
- **3+ flags → RỦI RO CAO** (stop and review before committing)

Files are also grouped for the summary: **Code**, **Cấu hình** (config),
**Tài liệu** (docs), **Test**, **Khác** (other) — based on path and extension.

## How it works

Nothing clever, on purpose: AIReview shells out to `git diff --numstat`,
`git diff --name-status`, and `git status` (to catch untracked files, which
never show up in a diff), then classifies each path and applies the flag
rules above. Renames are intentionally shown as a delete plus an add — that
is how a reviewer experiences them anyway.

## Honest limitations

- **Heuristics, not understanding.** AIReview reads file *names, paths, and
  line counts* — it does not parse your code or judge whether a change is
  correct. A green verdict means "nothing structurally risky", not "the code
  is right". Pair it with your test suite and, for visual work, a screenshot
  gate like [BossEye](https://github.com/ngdangkhoa-dev/bosseyes).
- **Dependency flags are file-level.** If `pyproject.toml` changed, you get a
  dependency flag even if the edit only touched a comment. Checking *which*
  dependency lines changed is on the roadmap.
- **Untracked binary detection is shallow** (first 8 KB sniffed for NUL
  bytes) — good enough for a flag, not a file-type oracle.
- **The summary UI is Vietnamese-first.** The verdict labels and group names
  are Vietnamese; an English UI switch is planned.

## The Boss series

AIReview is part of a small family of tools born from real vibe-coding pain,
built by [K-tech](https://github.com/ngdangkhoa-dev):

- **[BossEye](https://github.com/ngdangkhoa-dev/bosseyes)** 👁️ — a visual
  acceptance gate: your app must match the screenshots the boss approved
  before anyone is allowed to say "done".

## License

MIT © 2026 Nguyen Dang Khoa / K-tech

---

## Tiếng Việt (tóm tắt)

**AIReview — AI vừa sửa gì trong repo của bạn?**

Sau mỗi phiên để AI sửa code, chạy `aireview` ngay trong thư mục dự án. Tool
sẽ so working tree với HEAD (hoặc với ref bạn chỉ định qua `--since`), rồi tóm
tắt bằng tiếng người: bao nhiêu file thêm/sửa/xoá, bao nhiêu dòng +/−, nhóm
theo Code / Cấu hình / Tài liệu / Test — và quan trọng nhất là **cờ rủi ro**:
file bị xoá, file nhạy cảm (`.env`, khóa riêng, secret), cấu hình thay đổi,
dependency thay đổi, xoá quá nhiều dòng, file nhị phân thay đổi.

Kết quả in ra terminal và ghi vào `aireview-report.md`. Kết luận tổng thể:
**BÌNH THƯỜNG** (0 cờ) · **CẦN XEM LẠI** (1–2 cờ) · **RỦI RO CAO** (3 cờ trở
lên). Tool chỉ đọc — không sửa, không commit bất cứ thứ gì trong repo của bạn.
