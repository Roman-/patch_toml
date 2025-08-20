#!/usr/bin/env python3
# patch_toml.py
#
# Implements REQUIREMENTS.md (simplified format-preserving TOML patcher).
# - Rewrites only the targeted assignments (canonical spacing).
# - Deletes keys or a single section (non-recursive).
# - Replaces the top-of-file comment block.
# - Validates input TOML and TOML_VALUE payloads.
#
# Exit codes:
# 1 input unreadable / invalid TOML
# 2 requested path not found
# 3 ambiguous path
# 4 invalid option payload (parse error in path or TOML_VALUE) or missing tomllib
# 5 output path unwritable

from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import io
import math
import os
import re
import sys
from typing import List, Optional, Tuple

# ---- TOML parsing (values and file validation) ------------------------------

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    print(
        "error: Python 3.11+ is required (tomllib not found). "
        "Run with Python 3.11+.",
        file=sys.stderr,
    )
    sys.exit(4)


def parse_toml_value(value_src: str):
    """
    Parse a TOML literal from a 'key = VALUE' snippet using tomllib.
    Raise ValueError on parse errors.
    """
    snippet = f"__k__ = {value_src}\n"
    try:
        obj = tomllib.loads(snippet)
    except Exception as e:
        raise ValueError(str(e))
    return obj["__k__"]


def validate_toml_document(text: str) -> None:
    try:
        tomllib.loads(text)
    except Exception as e:
        raise ValueError(str(e))


# ---- Canonical TOML emission for single values ------------------------------

_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _escape_string(s: str) -> str:
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _format_key_segment(seg: str) -> str:
    return seg if _BARE_KEY_RE.match(seg) else _escape_string(seg)


def format_toml_value(obj) -> str:
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, int) and not isinstance(obj, bool):
        return str(obj)
    if isinstance(obj, float):
        if math.isnan(obj):
            return "nan"
        if math.isinf(obj):
            return "inf" if obj > 0 else "-inf"
        return repr(obj) if ("e" in repr(obj).lower()) else str(obj)
    if isinstance(obj, str):
        return _escape_string(obj)
    if isinstance(obj, _dt.datetime):
        return obj.isoformat()
    if isinstance(obj, _dt.date):
        return obj.isoformat()
    if isinstance(obj, _dt.time):
        return obj.isoformat()
    if isinstance(obj, dict):
        parts = []
        for k, v in obj.items():
            parts.append(f"{_format_key_segment(k)} = {format_toml_value(v)}")
        return "{ " + ", ".join(parts) + " }"
    if isinstance(obj, (list, tuple)):
        return "[" + ", ".join(format_toml_value(v) for v in obj) + "]"
    return _escape_string(str(obj))


# ---- CLI patch specification parsing ----------------------------------------

@dataclasses.dataclass(frozen=True)
class PathSegment:
    name: str
    index: Optional[int] = None  # for arrays-of-tables, applies to that table segment

    def as_string(self) -> str:
        base = self.name
        return f"{base}[{self.index}]" if self.index is not None else base


def _split_path_tokens(raw: str) -> List[str]:
    """
    Split a dotted path into segments, respecting quoted keys and [index] suffix.
    Example: '"my.key".child[2].grand' -> ['"my.key"', 'child[2]', 'grand']
    """
    s = raw.strip()
    out: List[str] = []
    i, n = 0, len(s)
    buf = io.StringIO()
    in_dq = False
    in_sq = False
    while i < n:
        ch = s[i]
        if in_dq:
            buf.write(ch)
            if ch == "\\":
                i += 1
                if i < n:
                    buf.write(s[i])
            elif ch == '"':
                in_dq = False
            i += 1
            continue
        if in_sq:
            buf.write(ch)
            if ch == "'":
                in_sq = False
            i += 1
            continue
        if ch == '"':
            in_dq = True
            buf.write(ch)
            i += 1
            continue
        if ch == "'":
            in_sq = True
            buf.write(ch)
            i += 1
            continue
        if ch == ".":
            out.append(buf.getvalue().strip())
            buf = io.StringIO()
            i += 1
            continue
        buf.write(ch)
        i += 1
    final = buf.getvalue().strip()
    if final != "":
        out.append(final)
    return out


def _unquote_key(tok: str) -> str:
    tok = tok.strip()
    if tok.startswith('"') and tok.endswith('"'):
        body = tok[1:-1]
        out = []
        i = 0
        while i < len(body):
            ch = body[i]
            if ch == "\\":
                i += 1
                if i < len(body):
                    esc = body[i]
                    mapping = {
                        '"': '"',
                        "\\": "\\",
                        "b": "\b",
                        "t": "\t",
                        "n": "\n",
                        "r": "\r",
                    }
                    out.append(mapping.get(esc, esc))
            else:
                out.append(ch)
            i += 1
        return "".join(out)
    if tok.startswith("'") and tok.endswith("'"):
        return tok[1:-1]
    return tok


_INDEX_RE = re.compile(r"^(?P<name>.+?)\[(?P<idx>\d+)\]$")


def parse_path_with_indices(path: str) -> List[PathSegment]:
    toks = _split_path_tokens(path)
    segs: List[PathSegment] = []
    for t in toks:
        t = t.strip()
        m = _INDEX_RE.match(t)
        if m:
            name = _unquote_key(m.group("name").strip())
            idx = int(m.group("idx"))
            segs.append(PathSegment(name=name, index=idx))
        else:
            name = _unquote_key(t)
            segs.append(PathSegment(name=name, index=None))
    return segs


def split_set_expression(s: str) -> Tuple[str, str, Optional[str]]:
    """
    Split a --set STRING of the form:
        path = TOML_VALUE [# inline comment]
    Return (path, value_src, comment or None).
    """
    # Find '=' outside quotes
    i = 0
    n = len(s)
    in_dq = in_sq = False
    eq_pos = -1
    while i < n:
        ch = s[i]
        if ch == '"' and not in_sq:
            in_dq = not in_dq
            i += 1
            continue
        if ch == "'" and not in_dq:
            in_sq = not in_sq
            i += 1
            continue
        if ch == "=" and not in_dq and not in_sq:
            eq_pos = i
            break
        i += 1
    if eq_pos == -1:
        raise ValueError("missing '=' in --set expression")

    path = s[:eq_pos].strip()
    rhs = s[eq_pos + 1 :].strip()
    if not path:
        raise ValueError("empty path before '='")

    # Find '#' not in quotes for inline comment
    i = 0
    n = len(rhs)
    in_dq = in_sq = False
    hash_pos = -1
    while i < n:
        ch = rhs[i]
        if ch == '"' and not in_sq:
            in_dq = not in_dq
            i += 1
            continue
        if ch == "'" and not in_dq:
            in_sq = not in_sq
            i += 1
            continue
        if ch == "#" and not in_dq and not in_sq:
            hash_pos = i
            break
        i += 1

    if hash_pos != -1:
        value_src = rhs[:hash_pos].rstrip()
        comment = rhs[hash_pos + 1 :].strip()
    else:
        value_src = rhs
        comment = None

    if value_src == "":
        raise ValueError("empty TOML value in --set expression")

    return path, value_src, comment


# ---- Source document scanning and edits -------------------------------------

@dataclasses.dataclass
class Header:
    # Represents either a normal table [a.b] or an array-of-tables [[a.b]]
    kind: str  # 'table' or 'aot' or 'root'
    path: List[str]  # dotted segments
    aot_index: Optional[int]  # only for kind == 'aot', 0-based
    start_line: int  # header line index (for root: -1)
    end_line: int    # inclusive; set later

    def id_segments(self) -> List[PathSegment]:
        if self.kind == 'aot':
            return [PathSegment(name=s) for s in self.path[:-1]] + [PathSegment(self.path[-1], self.aot_index)]
        elif self.kind == 'table':
            return [PathSegment(name=s) for s in self.path]
        else:
            return []  # root

    def dotted(self) -> str:
        segs = []
        for seg in self.id_segments():
            segs.append(seg.as_string())
        return ".".join(segs)


_HEADER_TABLE_RE = re.compile(r"^\s*\[(?!\[)(?P<body>.+?)\]\s*(?:#.*)?$")
_HEADER_AOT_RE = re.compile(r"^\s*\[\[(?P<body>.+?)\]\]\s*(?:#.*)?$")


def _parse_header_line(line: str) -> Optional[Tuple[str, List[str]]]:
    m = _HEADER_TABLE_RE.match(line)
    if m:
        body = m.group("body").strip()
        return "table", [_unquote_key(tok) for tok in _split_path_tokens(body)]
    m = _HEADER_AOT_RE.match(line)
    if m:
        body = m.group("body").strip()
        return "aot", [_unquote_key(tok) for tok in _split_path_tokens(body)]
    return None


def _is_comment_or_blank(line: str) -> bool:
    s = line.strip()
    return (not s) or s.startswith("#")


def index_headers(lines: List[str]) -> List[Header]:
    headers: List[Header] = []
    aot_counters: dict[Tuple[str, ...], int] = {}
    # root pseudo-section
    root = Header(kind="root", path=[], aot_index=None, start_line=-1, end_line=-1)
    headers.append(root)
    for i, line in enumerate(lines):
        parsed = _parse_header_line(line)
        if not parsed:
            continue
        kind, path = parsed
        if kind == "aot":
            key = tuple(path)
            idx = aot_counters.get(key, 0)
            aot_counters[key] = idx + 1
            headers.append(Header(kind="aot", path=path, aot_index=idx, start_line=i, end_line=i))
        else:
            headers.append(Header(kind="table", path=path, aot_index=None, start_line=i, end_line=i))
    # compute end_line for each header
    for h_idx, h in enumerate(headers):
        if h.kind == "root":
            next_start = headers[h_idx + 1].start_line if h_idx + 1 < len(headers) else len(lines)
            h.end_line = next_start - 1
            continue
        next_start = headers[h_idx + 1].start_line if h_idx + 1 < len(headers) else len(lines)
        end = next_start - 1
        # Trim trailing comment/blank lines from the block to preserve standalone comments
        j = end
        while j > h.start_line and _is_comment_or_blank(lines[j]):
            j -= 1
        h.end_line = j
    return headers


def _segments_equal(a: List[PathSegment], b: List[PathSegment]) -> bool:
    if len(a) != len(b):
        return False
    for sa, sb in zip(a, b):
        if sa.name != sb.name:
            return False
        if sa.index != sb.index:
            return False
    return True


def find_section_block(
    headers: List[Header], target: List[PathSegment]
) -> Tuple[Header, int, int]:
    """
    Find a section block (start,end) lines that correspond to the given path segments.
    """
    if not target:
        for h in headers:
            if h.kind == "root":
                return h, h.start_line + 1, h.end_line
        raise RuntimeError("root header missing")

    if target[-1].index is not None:
        table_path = [t.name for t in target[:-1]] + [target[-1].name]
        wanted_idx = target[-1].index
        candidates = [
            h for h in headers if h.kind == "aot" and h.path == table_path and h.aot_index == wanted_idx
        ]
        if not candidates:
            raise KeyError("section not found")
        h = candidates[0]
        return h, h.start_line + 1, h.end_line
    else:
        table_path = [t.name for t in target]
        candidates = [h for h in headers if h.kind == "table" and h.path == table_path]
        if candidates:
            h = candidates[0]
            return h, h.start_line + 1, h.end_line
        aot_candidates = [h for h in headers if h.kind == "aot" and h.path == table_path]
        if len(aot_candidates) == 0:
            raise KeyError("section not found")
        if len(aot_candidates) > 1:
            raise RuntimeError("ambiguous")
        h = aot_candidates[0]
        return h, h.start_line + 1, h.end_line


def _parse_assignment_key_segments(lhs: str) -> Optional[List[str]]:
    lhs = lhs.strip()
    if not lhs or lhs.startswith("#"):
        return None
    toks = _split_path_tokens(lhs)
    return [_unquote_key(t) for t in toks]


def _find_equals_outside_quotes(line: str) -> int:
    in_dq = in_sq = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"' and not in_sq:
            if i > 0 and line[i - 1] == "\\":
                i += 1
                continue
            in_dq = not in_dq
            i += 1
            continue
        if ch == "'" and not in_dq:
            in_sq = not in_sq
            i += 1
            continue
        if ch == "=" and not in_dq and not in_sq:
            return i
        i += 1
    return -1


TRIPLE_DQ = '"' * 3
TRIPLE_SQ = "'" * 3


def _value_block_end(lines: List[str], start_line: int, start_col: int) -> int:
    """
    Determine inclusive end line index of a value that starts on start_line at start_col (right after '=').
    Handles multi-line arrays, inline tables, and multi-line strings.
    """
    i = start_line
    depth_square = 0
    depth_curly = 0
    in_dq = False
    in_sq = False
    in_triple_dq = False
    in_triple_sq = False

    def advance_line_text(i: int) -> str:
        return lines[i][start_col:] if i == start_line else lines[i]

    while i < len(lines):
        text = advance_line_text(i)
        j = 0
        while j < len(text):
            ch = text[j]
            nxt3 = text[j : j + 3]
            if in_triple_dq:
                if nxt3 == TRIPLE_DQ:
                    in_triple_dq = False
                    j += 3
                    continue
                j += 1
                continue
            if in_triple_sq:
                if nxt3 == TRIPLE_SQ:
                    in_triple_sq = False
                    j += 3
                    continue
                j += 1
                continue
            if in_dq:
                if ch == "\\":
                    j += 2
                    continue
                if ch == '"':
                    in_dq = False
                j += 1
                continue
            if in_sq:
                if ch == "'":
                    in_sq = False
                j += 1
                continue
            # not in any string
            if nxt3 == TRIPLE_DQ:
                in_triple_dq = True
                j += 3
                continue
            if nxt3 == TRIPLE_SQ:
                in_triple_sq = True
                j += 3
                continue
            if ch == '"':
                in_dq = True
                j += 1
                continue
            if ch == "'":
                in_sq = True
                j += 1
                continue
            if ch == "[":
                depth_square += 1
                j += 1
                continue
            if ch == "]":
                if depth_square > 0:
                    depth_square -= 1
                j += 1
                continue
            if ch == "{":
                depth_curly += 1
                j += 1
                continue
            if ch == "}":
                if depth_curly > 0:
                    depth_curly -= 1
                j += 1
                continue
            if ch == "#" and depth_square == 0 and depth_curly == 0:
                # Inline comment starts; end of value on this line
                return i
            j += 1
        # end of line reached
        if depth_square == 0 and depth_curly == 0 and not (in_dq or in_sq or in_triple_dq or in_triple_sq):
            return i
        i += 1
    return len(lines) - 1


def find_assignment_block_by_full_path(
    lines: List[str], headers: List[Header], full_path: List[PathSegment]
) -> Tuple[int, int, List[str]]:
    if not full_path:
        raise KeyError("invalid empty path")
    table_path: List[PathSegment] = full_path[:-1]
    key_path: List[str] = [full_path[-1].name]

    try:
        header, start, end = find_section_block(headers, table_path)
    except KeyError:
        raise KeyError("section not found")
    except RuntimeError:
        raise RuntimeError("ambiguous")

    matches: List[Tuple[int, int, List[str]]] = []
    i = max(start, 0)
    while i <= end and i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or stripped.startswith("["):
            i += 1
            continue
        eq_pos = _find_equals_outside_quotes(line)
        if eq_pos == -1:
            i += 1
            continue
        lhs = line[:eq_pos]
        ksegs = _parse_assignment_key_segments(lhs)
        if not ksegs:
            i += 1
            continue
        full_key = header.id_segments() + [PathSegment(n) for n in ksegs]
        if _segments_equal(full_key, full_path):
            end_block = _value_block_end(lines, i, eq_pos + 1)
            matches.append((i, end_block, ksegs))
        i += 1

    if not matches:
        if table_path and table_path[-1].index is None:
            tp = [t.name for t in table_path]
            aot_candidates = [h for h in headers if h.kind == "aot" and h.path == tp]
            if len(aot_candidates) > 1:
                raise RuntimeError("ambiguous")
        raise KeyError("key not found")

    if len(matches) > 1:
        raise RuntimeError("ambiguous")
    return matches[0]


# ---- Top-of-file comment replacement ----------------------------------------

def replace_top_comment_block(text: str, new_text: str) -> str:
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s == "" or s.startswith("#"):
            i += 1
            continue
        break
    new_lines: List[str] = []
    if new_text is not None:
        for raw_line in new_text.splitlines():
            if raw_line.strip() == "":
                new_lines.append("#\n")
            else:
                new_lines.append("# " + raw_line.rstrip() + "\n")
        new_lines.append("\n")
    return "".join(new_lines + lines[i:])


# ---- Patching operations -----------------------------------------------------

@dataclasses.dataclass
class SetPatch:
    path: List[PathSegment]
    value_src: str
    value_obj: object
    comment: Optional[str]


@dataclasses.dataclass
class DeleteKeyPatch:
    path: List[PathSegment]


@dataclasses.dataclass
class DeleteSectionPatch:
    path: List[PathSegment]


def apply_set(lines: List[str], setp: SetPatch) -> List[str]:
    headers = index_headers(lines)
    try:
        start, end, ksegs = find_assignment_block_by_full_path(lines, headers, setp.path)
    except KeyError:
        print("error: requested path not found", file=sys.stderr)
        sys.exit(2)
    except RuntimeError:
        print("error: ambiguous path", file=sys.stderr)
        sys.exit(3)

    lhs = ".".join(_format_key_segment(s) for s in ksegs)
    rhs = format_toml_value(setp.value_obj)
    if setp.comment is not None and setp.comment != "":
        new_line = f"{lhs} = {rhs} # {setp.comment}\n"
    else:
        new_line = f"{lhs} = {rhs}\n"

    return lines[:start] + [new_line] + lines[end + 1 :]


def apply_delete_key(lines: List[str], delp: DeleteKeyPatch) -> List[str]:
    headers = index_headers(lines)
    try:
        start, end, _ksegs = find_assignment_block_by_full_path(lines, headers, delp.path)
    except KeyError:
        print("error: requested path not found", file=sys.stderr)
        sys.exit(2)
    except RuntimeError:
        print("error: ambiguous path", file=sys.stderr)
        sys.exit(3)
    return lines[:start] + lines[end + 1 :]


def apply_delete_section(lines: List[str], delsec: DeleteSectionPatch) -> List[str]:
    headers = index_headers(lines)
    try:
        header, start, end = find_section_block(headers, delsec.path)
    except KeyError:
        print("error: requested path not found", file=sys.stderr)
        sys.exit(2)
    except RuntimeError:
        print("error: ambiguous path", file=sys.stderr)
        sys.exit(3)
    if header.kind == "root":
        print("error: cannot delete root section", file=sys.stderr)
        sys.exit(4)
    del_start = header.start_line
    del_end = end
    return lines[:del_start] + lines[del_end + 1 :]


# ---- CLI --------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="patch_toml — apply precise value changes to a TOML config with simple, predictable formatting.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("input", help="input TOML path")
    p.add_argument("output", help="output TOML path")
    p.add_argument(
        "--set",
        dest="sets",
        action="append",
        default=[],
        help="Repeatable. path = TOML_VALUE [# inline comment]\n"
             "  e.g. --set 'logger.stdout_level = 6 # disable'\n"
             "       --set 'device.report_address = \"\"'\n"
             "       --set 'servers[0].host = \"localhost\"'",
    )
    p.add_argument(
        "--delete-key",
        dest="del_keys",
        action="append",
        default=[],
        help="Repeatable. Delete a single key by path.\n  e.g. --delete-key 'logger.file_level'",
    )
    p.add_argument(
        "--delete-section",
        dest="del_secs",
        action="append",
        default=[],
        help="Repeatable. Delete a section (table) by path (non-recursive).\n"
             "Arrays-of-tables require an index: 'servers[2]'",
    )
    p.add_argument(
        "--top-comment",
        dest="top_comment",
        default=None,
        help="Replace or create the top-of-file comment block (supports newlines).",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        print(f"error: cannot read input: {e}", file=sys.stderr)
        return 1

    try:
        validate_toml_document(text)
    except Exception as e:
        print(f"error: invalid input TOML: {e}", file=sys.stderr)
        return 1

    if args.top_comment is not None:
        text = replace_top_comment_block(text, args.top_comment)

    lines = text.splitlines(keepends=True)

    # Apply in simple, predictable groups: sets -> delete-key -> delete-section
    for spec in args.sets:
        try:
            path_str, value_src, comment = split_set_expression(spec)
            path = parse_path_with_indices(path_str)
            value_obj = parse_toml_value(value_src)
        except ValueError as e:
            print(f"error: invalid --set payload: {e}", file=sys.stderr)
            return 4
        setp = SetPatch(path=path, value_src=value_src, value_obj=value_obj, comment=comment)
        lines = apply_set(lines, setp)

    for pth in args.del_keys:
        try:
            path = parse_path_with_indices(pth)
        except Exception as e:
            print(f"error: invalid --delete-key payload: {e}", file=sys.stderr)
            return 4
        delp = DeleteKeyPatch(path=path)
        lines = apply_delete_key(lines, delp)

    for pth in args.del_secs:
        try:
            path = parse_path_with_indices(pth)
        except Exception as e:
            print(f"error: invalid --delete-section payload: {e}", file=sys.stderr)
            return 4
        delsec = DeleteSectionPatch(path=path)
        lines = apply_delete_section(lines, delsec)

    out_text = "".join(lines)

    try:
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            f.write(out_text)
    except Exception as e:
        print(f"error: cannot write output: {e}", file=sys.stderr)
        return 5

    return 0


if __name__ == "__main__":
    # With no arguments, argparse will print usage and exit with code 2.
    sys.exit(main())
