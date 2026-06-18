#!/usr/bin/env python3
"""check_grounding.py — drift-checker for a project-grounding layer.

Stdlib-only (no third-party imports). Validates the grounding docs registered in
``<repo-root>/<grounding_dir>/grounding.yml`` against the canonical contract in
``references/conventions.md``:

  * front-matter schema (closed key set + enums),
  * source_anchors and inline ``[[anchor: ...]]`` resolve to real paths/symbols,
  * INV-id cross-references (declared in invariants.md; referenced in
    rules.md / verification.md / ADRs) all resolve,
  * grounding.yml ``kind`` equals each doc's ``grounding_kind``,
  * required documents are present and parse cleanly,
  * staleness and committed-blob ``sha`` drift (warnings; errors under --strict).

Exit code: 0 if no errors (warnings allowed unless strict), else 1. Config errors
(missing/unreadable grounding.yml) exit 2.

Usage:
    python3 scripts/check_grounding.py [--dir docs/grounding] [--strict] [--quiet]

Run it from anywhere inside the target repo; the repo root is found via
``git rev-parse --show-toplevel`` (fallback: current working directory).
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys

# --- Canonical, closed constants (copied byte-for-byte from conventions.md) ---
KIND_VALUES = {
    "code-map", "domain-glossary", "domain-rules", "flow",
    "invariants", "verification", "adr",
}
STATUS_VALUES = {"draft", "reviewed", "stale"}
ALLOWED_DOC_KEYS = {"grounding_kind", "status", "last_verified", "source_anchors", "owners"}
REQUIRED_DOC_KEYS = ("grounding_kind", "status", "last_verified", "source_anchors")
ALLOWED_ANCHOR_KEYS = {"path", "symbol", "sha"}
REFERENCING_KINDS = {"domain-rules", "verification", "adr"}

# --- Shared grammars (one regex each, everywhere) ---
INLINE_ANCHOR_RE = re.compile(r"\[\[anchor:\s*([^\]#]+?)(?:#([^\]]+))?\s*\]\]")
INV_ID_RE = re.compile(r"\bINV-\d{3,}\b")
ADR_ID_RE = re.compile(r"\bADR-(\d{4})\b")
FLOW_ID_RE = re.compile(r"[a-z0-9-]+")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


class YamlSubsetError(Exception):
    """Raised when input violates the closed YAML subset (see conventions.md §4)."""


class FrontMatterError(Exception):
    """Raised when the front-matter fences are missing/malformed (§2.1)."""


# --------------------------------------------------------------------------- #
# Minimal YAML-subset parser (the single reference implementation, §4)
# --------------------------------------------------------------------------- #
def _strip_inline_comment(line: str) -> str:
    """Remove a trailing ``#`` comment that is outside quotes; keep leading spaces."""
    out = []
    quote = None
    for i, c in enumerate(line):
        if quote:
            out.append(c)
            if c == quote:
                quote = None
        elif c in ("'", '"'):
            quote = c
            out.append(c)
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out.append(c)
    return "".join(out).rstrip()


def _dequote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _parse_scalar_or_flow(value: str):
    v = value.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if inner == "":
            return []
        return [x for x in (_dequote(p) for p in inner.split(",")) if x != ""]
    return _dequote(v)


def _looks_like_mapping(content: str) -> bool:
    if not content or content[0] in "[{\"'":
        return False
    head = content.split(":", 1)[0].strip()
    return ":" in content and head != "" and " " not in head


def _leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_block_sequence(lines, start):
    """Parse a block sequence beginning at ``start``. Returns (items, next_index).

    Supports scalar items (``  - x``) and block-mapping items (``  - k: v`` with
    4-space-aligned continuation keys) — and nothing else.
    """
    items = []
    i, n = start, len(lines)
    while i < n:
        raw = lines[i]
        if "\t" in raw:
            raise YamlSubsetError(f"tab character not allowed (line {i + 1})")
        line = _strip_inline_comment(raw)
        if line.strip() == "":
            i += 1
            continue
        indent = _leading_spaces(line)
        if indent < 2:
            break  # dedent -> sequence ended
        body = line.lstrip(" ")
        if indent != 2 or not body.startswith("- "):
            raise YamlSubsetError(f"malformed sequence item (line {i + 1}): {raw!r}")
        content = body[2:].strip()
        if _looks_like_mapping(content):
            item = {}
            k, _, v = content.partition(":")
            item[k.strip()] = _parse_scalar_or_flow(v)
            i += 1
            while i < n:
                raw2 = lines[i]
                if "\t" in raw2:
                    raise YamlSubsetError(f"tab character not allowed (line {i + 1})")
                l2 = _strip_inline_comment(raw2)
                if l2.strip() == "":
                    i += 1
                    continue
                b2 = l2.lstrip(" ")
                if _leading_spaces(l2) >= 4 and ":" in b2 and not b2.startswith("- "):
                    k2, _, v2 = b2.partition(":")
                    item[k2.strip()] = _parse_scalar_or_flow(v2)
                    i += 1
                else:
                    break
            items.append(item)
        else:
            items.append(_parse_scalar_or_flow(content))
            i += 1
    return items, i


def parse_yaml_subset(lines):
    """Parse a list of lines under the closed subset into a dict."""
    result = {}
    i, n = 0, len(lines)
    while i < n:
        raw = lines[i]
        if "\t" in raw:
            raise YamlSubsetError(f"tab character not allowed (line {i + 1})")
        line = _strip_inline_comment(raw)
        if line.strip() == "":
            i += 1
            continue
        if _leading_spaces(line) != 0:
            raise YamlSubsetError(f"unexpected indentation at top level (line {i + 1}): {raw!r}")
        if ":" not in line:
            raise YamlSubsetError(f"expected 'key: value' (line {i + 1}): {raw!r}")
        key, _, val = line.partition(":")
        key = key.strip()
        if val.strip() == "":
            seq, i = _parse_block_sequence(lines, i + 1)
            result[key] = seq
        else:
            result[key] = _parse_scalar_or_flow(val)
            i += 1
    return result


def split_front_matter(text: str):
    """Return (front_matter_lines, body). Enforces the line-1 ``---`` fence (§2.1)."""
    if text.startswith("﻿"):
        raise FrontMatterError("file begins with a BOM; line 1 must be exactly '---'")
    lines = text.split("\n")
    if not lines or lines[0].rstrip("\r") != "---":
        raise FrontMatterError("line 1 must be exactly '---' (no blank line / indent / BOM before it)")
    for j in range(1, len(lines)):
        if lines[j].rstrip("\r") == "---":
            return lines[1:j], "\n".join(lines[j + 1:])
    raise FrontMatterError("no closing '---' front-matter fence found")


# --------------------------------------------------------------------------- #
# git helpers
# --------------------------------------------------------------------------- #
def _git(args, cwd):
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False,
        )
    except (OSError, ValueError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def git_root():
    return _git(["rev-parse", "--show-toplevel"], cwd=os.getcwd())


def git_blob_sha(root, rel_path):
    return _git(["rev-parse", f"HEAD:{rel_path}"], cwd=root)


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.oks = []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg, promote=False):
        if promote:
            self.errors.append(f"{msg}  [strict: warning promoted to error]")
        else:
            self.warnings.append(msg)

    def ok(self, msg):
        self.oks.append(msg)


def _parse_date(value):
    try:
        return datetime.datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _as_bool(value):
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _check_path_and_symbol(report, root, rel, path, symbol, git_available, sha, strict):
    """Validate one anchor (source or inline). Path is repo-root-relative."""
    if os.path.isabs(path):
        report.error(f"{rel}: anchor path is absolute (must be repo-root-relative): {path}")
        return
    if path.startswith("./"):
        report.error(f"{rel}: anchor path must not start with './': {path}")
        return
    norm = os.path.normpath(path)
    if norm == ".." or norm.startswith(".." + os.sep):
        report.error(f"{rel}: anchor path escapes the repo root: {path}")
        return
    full = os.path.join(root, norm)
    if not os.path.exists(full):
        report.error(f"{rel}: anchor path does not exist: {path}")
        return
    if symbol:
        if not os.path.isfile(full):
            report.error(f"{rel}: anchor '{path}#{symbol}' has a #symbol but points at a directory")
        else:
            try:
                ftext = open(full, encoding="utf-8", errors="replace").read()
            except OSError as exc:
                report.error(f"{rel}: cannot read {path} for symbol check: {exc}")
                ftext = ""
            if not re.search(r"\b" + re.escape(symbol) + r"\b", ftext):
                report.error(f"{rel}: symbol '{symbol}' not found in {path}")
    if sha:
        if git_available:
            current = git_blob_sha(root, norm)
            if current is not None and current != sha:
                report.warn(
                    f"{rel}: sha drift on {path} (recorded {sha[:10]}…, HEAD {current[:10]}…)",
                    promote=strict,
                )
            # current is None -> path not in HEAD -> skip silently (§7.3)
        # git absent -> skip silently (§7.3)


def _adr_file_number(rel):
    base = os.path.basename(rel)
    m = re.match(r"(\d{4})-.+\.md$", base)
    return m.group(1) if m else None


def validate_document(report, root, grounding_dir, rel, entry_kind, *,
                      staleness_days, strict, git_available, today):
    """Validate one registered grounding document. Returns a result dict."""
    result = {"kind": entry_kind, "parsed": False, "declared": set(), "referenced": set()}
    start_errors = len(report.errors)
    full = os.path.join(root, grounding_dir, rel)

    if not os.path.isfile(full):
        report.error(f"{rel}: document file not found at {grounding_dir}/{rel}")
        return result

    try:
        text = open(full, encoding="utf-8").read()
    except OSError as exc:
        report.error(f"{rel}: cannot read document: {exc}")
        return result

    try:
        fm_lines, body = split_front_matter(text)
        fm = parse_yaml_subset(fm_lines)
    except (FrontMatterError, YamlSubsetError) as exc:
        report.error(f"{rel}: front-matter error: {exc}")
        return result

    # Closed key set
    for k in sorted(set(fm) - ALLOWED_DOC_KEYS):
        report.error(f"{rel}: unknown front-matter key '{k}'")
    for k in REQUIRED_DOC_KEYS:
        if k not in fm:
            report.error(f"{rel}: missing required front-matter key '{k}'")

    # Enums
    gk = fm.get("grounding_kind")
    if gk is not None and gk not in KIND_VALUES:
        report.error(f"{rel}: invalid grounding_kind '{gk}'")
    st = fm.get("status")
    if st is not None and st not in STATUS_VALUES:
        report.error(f"{rel}: invalid status '{st}'")

    # grounding.yml kind must equal grounding_kind
    if gk is not None and gk != entry_kind:
        report.error(f"{rel}: grounding.yml kind '{entry_kind}' != front-matter grounding_kind '{gk}'")

    # last_verified + staleness
    lv = fm.get("last_verified")
    lv_date = _parse_date(lv) if lv is not None else None
    if lv is not None and lv_date is None:
        report.error(f"{rel}: last_verified is not a valid \"YYYY-MM-DD\" date: {lv!r}")
    if lv_date is not None:
        age = (today - lv_date).days
        if age > staleness_days:
            report.warn(f"{rel}: stale — last_verified {lv} is {age}d old (> {staleness_days})", promote=strict)

    # source_anchors
    sa = fm.get("source_anchors")
    if sa is not None:
        if not isinstance(sa, list):
            report.error(f"{rel}: source_anchors must be a list (use [] if none)")
        else:
            for idx, anchor in enumerate(sa):
                if not isinstance(anchor, dict):
                    report.error(f"{rel}: source_anchors[{idx}] must be a mapping with a 'path'")
                    continue
                for k in sorted(set(anchor) - ALLOWED_ANCHOR_KEYS):
                    report.error(f"{rel}: source_anchors[{idx}] has unknown key '{k}'")
                p = anchor.get("path")
                if not p:
                    report.error(f"{rel}: source_anchors[{idx}] missing required 'path'")
                    continue
                _check_path_and_symbol(report, root, rel, p, anchor.get("symbol"),
                                       git_available, anchor.get("sha"), strict)

    # owners
    ow = fm.get("owners")
    if ow is not None and not isinstance(ow, list):
        report.error(f"{rel}: owners must be an inline list, e.g. [a, b]")

    # Body: strip HTML comments before scanning (illustrative examples in comments
    # are not validated), then resolve inline anchors and collect INV ids.
    clean_body = HTML_COMMENT_RE.sub("", body)
    for m in INLINE_ANCHOR_RE.finditer(clean_body):
        apath = m.group(1).strip()
        asym = m.group(2).strip() if m.group(2) else None
        _check_path_and_symbol(report, root, rel, apath, asym, git_available, None, strict)

    inv_ids = set(INV_ID_RE.findall(clean_body))
    if entry_kind == "invariants":
        result["declared"] = inv_ids
    if entry_kind in REFERENCING_KINDS:
        result["referenced"] = inv_ids

    # ADR must DECLARE its own id (ADR-<filenum>, normally the H1 title).
    # Cross-references to OTHER ADRs (e.g. "see ADR-0004") are allowed.
    if entry_kind == "adr":
        fnum = _adr_file_number(rel)
        if fnum is None:
            report.error(f"{rel}: ADR filename must be decisions/NNNN-<slug>.md (4-digit number)")
        elif fnum not in set(ADR_ID_RE.findall(clean_body)):
            report.error(f"{rel}: ADR body must declare its own id ADR-{fnum} (matching the filename); "
                         f"cross-references to other ADRs are allowed")

    # Flow id kebab-case == basename
    if entry_kind == "flow":
        base = os.path.basename(rel)
        base = base[:-3] if base.endswith(".md") else base
        if not FLOW_ID_RE.fullmatch(base):
            report.error(f"{rel}: flow id '{base}' must be kebab-case [a-z0-9-]+")

    parsed_clean = (gk is not None) and (len(report.errors) == start_errors)
    result["parsed"] = parsed_clean
    if parsed_clean:
        report.ok(f"{rel}: valid ({entry_kind})")
    return result


def run(grounding_dir_override, strict_cli, quiet):
    root = git_root()
    git_available = root is not None
    if root is None:
        root = os.getcwd()

    grounding_dir = (grounding_dir_override or "docs/grounding").strip().strip("/\\")
    gpath = os.path.join(root, grounding_dir, "grounding.yml")
    if not os.path.isfile(gpath):
        print(f"error: grounding.yml not found at {grounding_dir}/grounding.yml (repo root: {root})",
              file=sys.stderr)
        return 2
    try:
        cfg = parse_yaml_subset(open(gpath, encoding="utf-8").read().split("\n"))
    except (YamlSubsetError, OSError) as exc:
        print(f"error: cannot parse {grounding_dir}/grounding.yml: {exc}", file=sys.stderr)
        return 2

    strict = bool(strict_cli) or _as_bool(cfg.get("strict", "false"))
    try:
        staleness_days = int(str(cfg.get("staleness_days", "90")).strip())
    except ValueError:
        staleness_days = 90
    documents = cfg.get("documents") or []
    required = cfg.get("required_documents") or []
    today = datetime.date.today()

    report = Report()
    present_clean_kinds = set()
    declared, referenced = set(), set()
    seen = set()

    if not isinstance(documents, list) or not documents:
        report.error("grounding.yml: documents[] is empty or malformed")
        documents = []

    for entry in documents:
        if not isinstance(entry, dict):
            report.error(f"grounding.yml: malformed documents[] entry: {entry!r}")
            continue
        f, k = entry.get("file"), entry.get("kind")
        if not f or not k:
            report.error(f"grounding.yml: documents[] entry missing file/kind: {entry!r}")
            continue
        if k not in KIND_VALUES:
            report.error(f"{f}: invalid kind '{k}' in grounding.yml")
        if f in seen:
            report.warn(f"{f}: registered more than once in grounding.yml", promote=strict)
        seen.add(f)
        res = validate_document(report, root, grounding_dir, f, k,
                                staleness_days=staleness_days, strict=strict,
                                git_available=git_available, today=today)
        if res["parsed"] and res["kind"] == k:
            present_clean_kinds.add(k)
        declared |= res["declared"]
        referenced |= res["referenced"]

    for rk in required:
        if rk not in present_clean_kinds:
            report.error(f"required document of kind '{rk}' is missing or has errors")

    for inv in sorted(referenced - declared):
        report.error(f"dangling INV reference {inv} (referenced but not declared in invariants.md)")

    _print_report(report, quiet, grounding_dir, git_available)
    return 1 if report.errors else 0


def _print_report(report, quiet, grounding_dir, git_available):
    if not quiet:
        for m in report.oks:
            print(f"  [OK]   {m}")
        if report.warnings:
            print()
            for m in report.warnings:
                print(f"  [WARN] {m}")
        if report.errors:
            print()
            for m in report.errors:
                print(f"  [ERR]  {m}")
        print()
        if not git_available:
            print("  (note: git not available — sha drift checks skipped)")
    status = "FAIL" if report.errors else "OK"
    print(f"grounding [{grounding_dir}]: {status} — "
          f"{len(report.errors)} error(s), {len(report.warnings)} warning(s), {len(report.oks)} ok")


def build_parser():
    p = argparse.ArgumentParser(
        prog="check_grounding.py",
        description="Drift-checker for a project-grounding layer (stdlib-only).",
    )
    p.add_argument("--dir", metavar="<grounding_dir>",
                   help="Grounding directory relative to repo root (default: docs/grounding).")
    p.add_argument("--strict", action="store_true",
                   help="Promote all warnings (staleness, sha drift) to errors. ORed with grounding.yml strict.")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress per-item lines; still print the summary and keep the exit code.")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return run(args.dir, args.strict, args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
