#!/usr/bin/env python3
from __future__ import annotations

import difflib
import html
import os
import subprocess  # nosec
import sys
from pathlib import Path

DOCS_PREFIX = "docs/"
OUT_DIR = Path("site/pr-diff")
STATUS_LABEL = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
RENDERABLE = {"A", "M", "R"}


def git(*args: str) -> str:
    return subprocess.run(  # nosec
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def changed_entries(base: str) -> list[tuple[str, str, str]]:
    out = git("diff", "--name-status", f"{base}..HEAD", "--", "docs/")
    entries: list[tuple[str, str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0][0]
        if status == "R":
            old_path, path = parts[1], parts[2]
        else:
            old_path = path = parts[-1]
        if path.startswith(DOCS_PREFIX) and path.endswith(".md"):
            entries.append((status, path, old_path))
    return sorted(entries, key=lambda e: e[1])


def other_changed(base: str) -> list[str]:
    out = git("diff", "--name-only", f"{base}..HEAD")
    return [
        p
        for p in out.splitlines()
        if p.strip() and not (p.startswith(DOCS_PREFIX) and p.endswith(".md"))
    ]


def file_lines(ref: str, path: str) -> list[str]:
    try:
        return git("show", f"{ref}:{path}").splitlines(keepends=True)
    except subprocess.CalledProcessError:
        return []


def slug_for(path: str) -> str:
    rel = path[len(DOCS_PREFIX):]
    if rel.endswith(".md"):
        rel = rel[:-3]
    if rel == "index":
        rel = "home"
    return rel.replace("/", "-")


def render_url(path: str) -> str:
    rel = path[len(DOCS_PREFIX):]
    if rel.endswith(".md"):
        rel = rel[:-3]
    if rel == "index":
        return ""
    if rel.endswith("/index"):
        rel = rel[: -len("/index")]
    return rel + "/"


DIFF_CSS = """
        body{margin:0;background:#f6f8fa;color:#1f2328;font:14px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
        .diff-shell{max-width:1600px;margin:0 auto;padding:16px}
        table.diff[rules='groups']{table-layout:fixed;width:100%;border-collapse:collapse;border:1px solid #d1d9e0;border-radius:6px;background:#fff;margin:12px 0;font-size:12px;line-height:1.6}
        table.diff[rules='groups'] colgroup:nth-of-type(1),table.diff[rules='groups'] colgroup:nth-of-type(4){width:14px}
        table.diff[rules='groups'] colgroup:nth-of-type(2),table.diff[rules='groups'] colgroup:nth-of-type(5){width:52px}
        table.diff th,table.diff td{vertical-align:top;overflow-wrap:anywhere}
        table.diff td[nowrap]{white-space:pre-wrap;padding:1px 8px}
        table.diff th.diff_header{text-align:left;padding:8px 10px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:13px;font-weight:600;border-bottom:1px solid #d1d9e0;background:#f6f8fa;color:#1f2328}
        table.diff thead th{position:sticky;top:0;z-index:1}
        td.diff_header{padding:1px 6px;text-align:right;color:#59636e;background:#f6f8fa;user-select:none}
        td.diff_next{text-align:center;font-size:10px;line-height:1.4;background-color:#f6f8fa}
        .diff_add{background-color:#dafbe1}
        .diff_chg{background-color:#fff8c5}
        .diff_sub{background-color:#ffebe9}
        a{color:#0969da}
        @media (prefers-color-scheme:dark){
          body{background:#0d1117;color:#e6edf3}
          table.diff[rules='groups']{background:#161b22;border-color:#30363d}
          table.diff th.diff_header{border-bottom:1px solid #30363d;background:#1c2129;color:#e6edf3}
          td.diff_header{color:#9198a1;background:#161b22}
          td.diff_next{background-color:#21262d}
          .diff_add{background-color:rgba(46,160,67,.25)}
          .diff_chg{background-color:rgba(187,128,9,.25)}
          .diff_sub{background-color:rgba(248,81,73,.22)}
          a{color:#58a6ff}
        }
"""


def fit_diff_page(page: str, title: str) -> str:
    page = page.replace("<title></title>", f"<title>{html.escape(title)}</title>", 1)
    page = page.replace("&nbsp;", " ")
    page = page.replace("</style>", f"</style>\n<style>{DIFF_CSS}</style>", 1)
    page = page.replace("<body>", '<body>\n<div class="diff-shell">', 1)
    return page.replace("</body>", "</div>\n</body>", 1)


def write_diff_page(status: str, path: str, old_path: str, base: str) -> str:
    slug = slug_for(path)
    old = [] if status == "A" else file_lines(base, old_path)
    new = [] if status == "D" else file_lines("HEAD", path)
    if status == "A":
        fromdesc = f"{path} (new file)"
    elif status == "R":
        fromdesc = f"{old_path} @ main"
    else:
        fromdesc = f"{path} @ main"
    todesc = f"{path} (deleted)" if status == "D" else f"{path} @ PR head"
    page = difflib.HtmlDiff().make_file(
        old, new, fromdesc=fromdesc, todesc=todesc, context=True, numlines=4
    )
    (OUT_DIR / f"{slug}.html").write_text(
        fit_diff_page(page, f"{path} – docs diff"), encoding="utf-8"
    )
    return slug


def write_index(
    rows: list[tuple[str, str, str, str]],
    others: list[str],
    pr_number: str,
) -> None:
    badge_css = (
        ".badge{padding:2px 8px;border-radius:4px;font-size:12px;"
        "font-weight:600;color:#fff}"
        ".a{background:#1a7f37}.m{background:#0969da}"
        ".d{background:#cf222e}.r{background:#8250df}"
        "body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;"
        "margin:2rem auto;max-width:900px;color:#1f2328}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{padding:6px 10px;text-align:left;border-bottom:1px solid #d0d7de}"
        "code{background:#f6f8fa;padding:1px 4px;border-radius:3px}"
        ".note{background:#fff8c5;border:1px solid #e3c841;padding:10px 14px;"
        "border-radius:6px;margin-top:1rem}"
    )
    pr_label = f" for PR #{pr_number}" if pr_number else ""
    body_parts = [
        f"<h1>Documentation diff{html.escape(pr_label)}</h1>",
        "<p>Side-by-side source diffs of the docs pages this PR changes. "
        'Each row links to the <em>source diff</em> and, where the page still '
        'exists, to the <em>rendered</em> page in the full preview.</p>',
    ]
    if rows:
        body_parts.append("<table><thead><tr>"
                          "<th>Change</th><th>Page</th>"
                          "<th>Source diff</th><th>Rendered</th>"
                          "</tr></thead><tbody>")
        for status, path, slug, url in rows:
            label = STATUS_LABEL.get(status, status)
            cls = status.lower()
            rendered = (
                f'<a href="../{url}">view</a>'
                if status in RENDERABLE
                else "&mdash;"
            )
            body_parts.append(
                f'<tr><td><span class="badge {cls}">{label}</span></td>'
                f"<td><code>{html.escape(path)}</code></td>"
                f'<td><a href="{slug}.html">source diff</a></td>'
                f"<td>{rendered}</td></tr>"
            )
        body_parts.append("</tbody></table>")
    else:
        body_parts.append(
            "<p>No <code>docs/**/*.md</code> pages were changed in this PR.</p>"
        )
    if others:
        listed = ", ".join(html.escape(p) for p in others[:20])
        more = f" (and {len(others)-20} more)" if len(others) > 20 else ""
        body_parts.append(
            "<div class=\"note\"><strong>Other changes in this PR</strong> affect "
            "rendering (e.g. <code>mkdocs.yml</code>, API sources, theme) but are "
            "not page-by-page diffed. See the full rendered preview for their "
            f"effect: changed files — {listed}{more}.</div>"
        )
    body = "\n".join(body_parts)
    doc = (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Documentation diff</title><style>" + badge_css + "</style>"
        "</head><body>" + body + "</body></html>"
    )
    (OUT_DIR / "index.html").write_text(doc, encoding="utf-8")


def main() -> int:
    base = os.environ.get("GITHUB_PR_BASE")
    if not base:
        print("GITHUB_PR_BASE (PR base SHA) is required", file=sys.stderr)
        return 2
    pr_number = os.environ.get("GITHUB_PR_NUMBER", "")
    entries = changed_entries(base)
    others = other_changed(base)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, str, str, str]] = []
    for status, path, old_path in entries:
        slug = write_diff_page(status, path, old_path, base)
        rows.append((status, path, slug, render_url(path)))
    write_index(rows, others, pr_number)
    print(
        f"pr-diff: {len(rows)} page(s) diffed, "
        f"{len(others)} other changed file(s) noted."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
