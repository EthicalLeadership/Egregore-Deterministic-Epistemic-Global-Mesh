"""
ANCHORUM PdfDocument
====================
Stdlib-only PDF document model.
Parses PDF structure and exposes a clean API for extraction planes.

This is intentionally separate from extraction logic so that
`pdf_obstruction.py` and `extraction/pdf.py` can share the same model.
"""

from __future__ import annotations

import logging
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Internal object model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Name:
    name: str


@dataclass(frozen=True)
class _String:
    value: bytes

    def decode(self) -> str:
        return _decode_pdf_string(self.value)


@dataclass
class _Array:
    items: list[Any] = field(default_factory=list)


@dataclass
class _Dict:
    entries: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.entries.get(key, default)


@dataclass
class _Stream(_Dict):
    raw_data: bytes = b""

    def decoded(self) -> bytes:
        filters = self.entries.get("Filter")
        if filters is None:
            return self.raw_data
        if isinstance(filters, _Name) or not isinstance(filters, list):
            filters = [filters]
        data = self.raw_data
        for f in filters:
            if isinstance(f, _Name) and f.name == "FlateDecode":
                try:
                    data = zlib.decompress(data)
                except zlib.error as exc:
                    logger.debug("FlateDecode failed: %s", exc)
                    return b""
            else:
                return data
        return data


@dataclass(frozen=True)
class _Ref:
    obj_num: int
    gen_num: int


# ---------------------------------------------------------------------------
# 2. Tokenizer
# ---------------------------------------------------------------------------
class _Tokenizer:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def skip_ws(self) -> None:
        while self.pos < len(self.data):
            ch = self.data[self.pos]
            if ch in b" \t\r\n\x00\x0c":
                self.pos += 1
            elif ch == ord("%"):
                while self.pos < len(self.data) and self.data[self.pos] not in b"\r\n":
                    self.pos += 1
            else:
                break

    def peek(self, length: int = 1) -> bytes:
        return self.data[self.pos : self.pos + length]

    def read_token(self) -> bytes | None:
        self.skip_ws()
        if self.pos >= len(self.data):
            return None
        ch = self.data[self.pos]

        if ch in b"<>()[]{}%":
            if ch in b"<>" and self.peek(2) == b"<<":
                self.pos += 2
                return b"<<"
            if ch in b"<>" and self.peek(2) == b">>":
                self.pos += 2
                return b">>"
            self.pos += 1
            return bytes([ch])

        if ch == ord("/"):
            self.pos += 1
            start = self.pos
            while (
                self.pos < len(self.data)
                and self.data[self.pos] not in b" \t\r\n\x00\x0c<>()[]{}%/"
            ):
                self.pos += 1
            return self.data[start - 1 : self.pos]

        start = self.pos
        while (
            self.pos < len(self.data)
            and self.data[self.pos] not in b" \t\r\n\x00\x0c<>()[]{}%/"
        ):
            self.pos += 1
        return self.data[start : self.pos]

    def read_string(self) -> bytes:
        self.skip_ws()
        if self.pos >= len(self.data) or self.data[self.pos] != ord("("):
            return b""
        self.pos += 1
        out = bytearray()
        depth = 0
        while self.pos < len(self.data):
            ch = self.data[self.pos]
            if ch == ord("\\"):
                out.append(ch)
                self.pos += 1
                if self.pos < len(self.data):
                    out.append(self.data[self.pos])
                    self.pos += 1
                continue
            elif ch == ord("("):
                depth += 1
            elif ch == ord(")"):
                if depth == 0:
                    self.pos += 1
                    break
                depth -= 1
            out.append(ch)
            self.pos += 1
        return b"(" + bytes(out) + b")"

    def read_hex_string(self) -> bytes:
        self.skip_ws()
        if self.pos >= len(self.data) or self.data[self.pos] != ord("<"):
            return b""
        self.pos += 1
        start = self.pos
        while self.pos < len(self.data) and self.data[self.pos] != ord(">"):
            self.pos += 1
        value = self.data[start : self.pos]
        if self.pos < len(self.data) and self.data[self.pos] == ord(">"):
            self.pos += 1
        return b"<" + value + b">"


# ---------------------------------------------------------------------------
# 3. Value parser
# ---------------------------------------------------------------------------
def _parse_value(tok: _Tokenizer) -> Any:  # noqa: C901
    tok.skip_ws()
    if tok.pos >= len(tok.data):
        return None
    token = tok.read_token()
    if token is None:
        return None

    if token == b"<<":
        d: dict[str, Any] = {}
        while True:
            tok.skip_ws()
            if tok.peek(2) == b">>":
                tok.read_token()
                break
            key_tok = tok.read_token()
            if key_tok is None or key_tok == b">>":
                break
            if not key_tok.startswith(b"/"):
                continue
            key = _decode_name(key_tok[1:])
            d[key] = _parse_value(tok)
        tok.skip_ws()
        if tok.peek(6).lower() == b"stream":
            return _parse_stream(tok, d)
        return _Dict(entries=d)

    if token == b"[":
        arr: list[Any] = []
        while True:
            tok.skip_ws()
            if tok.peek(1) == b"]":
                tok.read_token()
                break
            val = _parse_value(tok)
            if val is None:
                break
            arr.append(val)
        return _Array(items=arr)

    if token == b"(":
        tok.pos -= 1
        return _String(value=tok.read_string())

    if token == b"<" and tok.peek(2) != b"<<":
        tok.pos -= 1
        return _String(value=tok.read_hex_string())

    if token.startswith(b"/"):
        return _Name(_decode_name(token[1:]))

    if token == b"true":
        return True
    if token == b"false":
        return False
    if token == b"null":
        return None

    # Number / reference
    if re.match(rb"[+-]?\d+", token) or re.match(rb"[+-]?\d*\.\d+", token):
        if b"." in token:
            try:
                return float(token)
            except ValueError:
                return token
        obj_num = int(token)
        save_pos = tok.pos
        tok.skip_ws()
        gen_tok = tok.read_token()
        if gen_tok is not None and re.match(rb"\d+", gen_tok):
            gen_num = int(gen_tok)
            tok.skip_ws()
            r_tok = tok.read_token()
            if r_tok == b"R":
                return _Ref(obj_num=obj_num, gen_num=gen_num)
        tok.pos = save_pos
        return obj_num

    return token


def _parse_stream(tok: _Tokenizer, entries: dict[str, Any]) -> _Stream:
    tok.skip_ws()
    if tok.data[tok.pos : tok.pos + 6].lower() == b"stream":
        tok.pos += 6
    else:
        tok.read_token()
    if tok.peek(2) == b"\r\n":
        tok.pos += 2
    elif tok.peek(1) == b"\n":
        tok.pos += 1
    length = entries.get("Length")
    if isinstance(length, int) and length >= 0:
        raw = tok.data[tok.pos : tok.pos + length]
        tok.pos += length
    else:
        end_pos = tok.data.find(b"endstream", tok.pos)
        raw = tok.data[tok.pos : end_pos] if end_pos != -1 else b""
        tok.pos = end_pos if end_pos != -1 else len(tok.data)
    tok.skip_ws()
    if tok.peek(9).lower() == b"endstream":
        tok.pos += 9
    else:
        tok.read_token()
    return _Stream(entries=entries, raw_data=raw)


# ---------------------------------------------------------------------------
# 4. Name / string decoding
# ---------------------------------------------------------------------------
def _decode_name(data: bytes) -> str:
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == ord("#") and i + 2 < len(data):
            try:
                result.append(int(data[i + 1 : i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        result.append(data[i])
        i += 1
    return bytes(result).decode("utf-8", errors="replace")


def _decode_pdf_string(data: bytes) -> str:  # noqa: C901
    if not data:
        return ""
    if data.startswith(b"<") and data.endswith(b">"):
        hex_part = data[1:-1].replace(b" ", b"").replace(b"\n", b"").replace(b"\r", b"")
        try:
            decoded = bytes.fromhex(hex_part.decode("ascii", errors="ignore"))
            if b"\x00" in decoded:
                return decoded.decode("utf-16-be", errors="replace")
            return decoded.decode("utf-8", errors="replace")
        except ValueError:
            return ""

    if data.startswith(b"(") and data.endswith(b")"):
        data = data[1:-1]

    out = bytearray()
    i = 0
    depth = 0
    while i < len(data):
        ch = data[i]
        if ch == ord("\\"):
            if i + 1 >= len(data):
                break
            nxt = data[i + 1]
            if nxt in b"nrtbf()\\":
                escapes = {
                    ord("n"): b"\n",
                    ord("r"): b"\r",
                    ord("t"): b"\t",
                    ord("b"): b"\b",
                    ord("f"): b"\f",
                }
                out.extend(escapes.get(nxt, bytes([nxt])))
                i += 2
                continue
            octal = b""
            j = i + 1
            while j < len(data) and len(octal) < 3 and ord("0") <= data[j] <= ord("7"):
                octal += bytes([data[j]])
                j += 1
            if octal:
                out.append(int(octal, 8))
                i = j
                continue
            out.append(nxt)
            i += 2
            continue
        elif ch == ord("("):
            depth += 1
        elif ch == ord(")"):
            if depth == 0:
                break
            depth -= 1
        out.append(ch)
        i += 1

    decoded = bytes(out)
    if decoded.startswith(b"\xfe\xff"):
        return decoded.decode("utf-16-be", errors="replace")
    if b"\x00" in decoded:
        return decoded.decode("utf-16-be", errors="replace")
    try:
        return decoded.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return decoded.decode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# 5. PdfDocument
# ---------------------------------------------------------------------------
class PdfDocument:
    """Stdlib-only PDF document parser."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.objects: dict[tuple[int, int], Any] = {}
        self.xref: dict[tuple[int, int], int] = {}
        self.xref_objstm: dict[tuple[int, int], int] = {}
        self._trailers: list[dict[str, Any]] = []
        self._parse()

    @classmethod
    def from_path(cls, path: Path | str) -> PdfDocument:
        with open(path, "rb") as f:
            return cls(f.read())

    @classmethod
    def from_stream(cls, stream: BinaryIO) -> PdfDocument:
        return cls(stream.read())

    # -----------------------------------------------------------------------
    # Parsing
    # -----------------------------------------------------------------------
    def _parse(self) -> None:
        self._parse_objects()
        self._parse_xref_and_trailers()
        self._resolve_object_streams()

    def _parse_objects(self) -> None:
        for m in re.finditer(rb"(\d+)\s+(\d+)\s+obj", self.data):
            obj_num = int(m.group(1))
            gen_num = int(m.group(2))
            start = m.end()
            end_pos = self.data.find(b"endobj", start)
            if end_pos == -1:
                continue
            obj_data = self.data[start:end_pos]
            try:
                tok = _Tokenizer(obj_data)
                value = _parse_value(tok)
                self.objects[(obj_num, gen_num)] = value
            except Exception as exc:
                logger.debug("Failed to parse object %d %d: %s", obj_num, gen_num, exc)

    def _parse_xref_and_trailers(self) -> None:  # noqa: C901
        pos = 0
        while True:
            m = re.search(rb"xref\s*\r?\n", self.data[pos:], re.DOTALL)
            if not m:
                break
            pos = pos + m.end()
            while pos < len(self.data):
                line_end = self.data.find(b"\n", pos)
                if line_end == -1:
                    line_end = len(self.data)
                line = self.data[pos:line_end].strip()
                if line == b"trailer" or line.startswith(b"trailer"):
                    break
                if line == b"" or line.startswith(b"%"):
                    pos = line_end + 1
                    continue
                parts = line.split()
                if len(parts) == 2:
                    try:
                        start_idx = int(parts[0])
                        count = int(parts[1])
                        pos = line_end + 1
                        for idx in range(count):
                            nl = self.data.find(b"\n", pos)
                            if nl == -1:
                                nl = len(self.data)
                            entry = self.data[pos:nl].strip()
                            eparts = entry.split()
                            if len(eparts) >= 3:
                                try:
                                    off_or_num = int(eparts[0])
                                    gen = int(eparts[1])
                                    typ = eparts[2].decode("ascii", errors="replace")
                                    oid = (start_idx + idx, gen)
                                    if typ == "n":
                                        self.xref[oid] = off_or_num
                                    elif typ == "f":
                                        pass
                                except ValueError:
                                    pass
                            pos = nl + 1
                        continue
                    except ValueError:
                        break
                pos = line_end + 1
            # Parse trailer dict after this xref
            tok = _Tokenizer(self.data[pos:])
            val = _parse_value(tok)
            if isinstance(val, _Dict):
                self._trailers.append(_to_plain(val))
            pos = self.data.find(b">>", pos)
            if pos == -1:
                break
            pos += 2

        # If no xref/trailer found, try last trailer keyword only
        if not self._trailers:
            tpos = self.data.rfind(b"trailer")
            if tpos != -1:
                tok = _Tokenizer(self.data[tpos + len(b"trailer") :])
                val = _parse_value(tok)
                if isinstance(val, _Dict):
                    self._trailers.append(_to_plain(val))

    def _resolve_object_streams(self) -> None:
        for (obj_num, gen_num), obj in list(self.objects.items()):
            if (
                isinstance(obj, _Stream)
                and isinstance(obj.get("Type"), _Name)
                and obj.get("Type").name == "ObjStm"
            ):
                try:
                    decoded = obj.decoded()
                    self._parse_object_stream(decoded, obj_num)
                    self.xref_objstm[(obj_num, gen_num)] = 1
                except Exception as exc:
                    logger.debug("Object stream parse failed: %s", exc)

    def _parse_object_stream(self, data: bytes, stream_obj_num: int) -> None:
        # Object stream: "<objnum1> <offset1> <objnum2> <offset2> ... data"
        header_end = data.find(b" ")
        if header_end == -1:
            return
        # Read header pairs
        header_bytes = bytearray()
        i = 0
        while i < len(data):
            if data[i] == ord(" "):
                pass
            elif data[i] in b"0123456789":
                header_bytes.append(data[i])
            else:
                break
            i += 1
        # Simpler: split first line/whitespace section
        try:
            text = data.decode("latin-1", errors="ignore")
            tokens = text.split()
            pairs: list[tuple[int, int]] = []
            idx = 0
            while idx + 1 < len(tokens):
                try:
                    onum = int(tokens[idx])
                    off = int(tokens[idx + 1])
                    pairs.append((onum, off))
                    idx += 2
                except ValueError:
                    break
            if not pairs:
                return
            header_len = len(" ".join(tokens[:idx]))
            body = data[header_len:].lstrip()
            for onum, off in pairs:
                obj_data = body[off:]
                tok = _Tokenizer(obj_data)
                value = _parse_value(tok)
                self.objects[(onum, 0)] = value
        except Exception as exc:
            logger.debug("Object stream parse error: %s", exc)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------
    def get_trailer(self) -> dict[str, Any]:
        return self._trailers[-1] if self._trailers else {}

    def get_all_trailers(self) -> list[dict[str, Any]]:
        return self._trailers

    def get_info(self) -> dict[str, Any] | None:
        trailer = self.get_trailer()
        info_ref = trailer.get("Info")
        if info_ref is None:
            return None
        info = self._resolve(info_ref)
        if isinstance(info, (_Dict, dict)):
            return _to_plain(info)
        return None

    def get_catalog(self) -> dict[str, Any] | None:
        trailer = self.get_trailer()
        root_ref = trailer.get("Root")
        if root_ref is None:
            return None
        root = self._resolve(root_ref)
        if isinstance(root, (_Dict, dict)):
            return _to_plain(root)
        return None

    def get_object(self, ref: tuple[int, int] | int | Any) -> Any:
        if isinstance(ref, tuple) and len(ref) == 2:
            return _to_plain(self._resolve(ref))
        if isinstance(ref, int):
            return _to_plain(self._resolve((ref, 0)))
        if isinstance(ref, _Ref):
            return _to_plain(self._resolve(ref))
        return _to_plain(ref)

    def get_page_count(self) -> int:
        cat = self.get_catalog()
        if not cat:
            return 0
        pages_ref = cat.get("Pages")
        pages = self._resolve(pages_ref)
        if isinstance(pages, _Dict):
            count = pages.get("Count")
            if isinstance(count, int):
                return count
        return 0

    def iter_pages(self):
        cat = self.get_catalog()
        if not cat:
            return
        pages_ref = cat.get("Pages")
        pages = self._resolve(pages_ref)
        if not isinstance(pages, _Dict):
            return
        yield from self._walk_pages(pages)

    def _walk_pages(self, node: Any):
        node = self._resolve(node)
        if not isinstance(node, _Dict):
            return
        kids = node.get("Kids")
        if isinstance(kids, _Array):
            for kid in kids.items:
                yield from self._walk_pages(kid)
            return
        yield node

    def get_page_resources(self, page: Any) -> dict[str, Any] | None:
        if isinstance(page, _Dict):
            res = page.get("Resources")
            resolved = self._resolve(res)
            if isinstance(resolved, _Dict):
                return _to_plain(resolved)
        return None

    def get_page_content(self, page: Any) -> bytes:
        if not isinstance(page, _Dict):
            return b""
        contents = page.get("Contents")
        contents = self._resolve(contents)
        if contents is None:
            return b""
        streams: list[_Stream] = []
        if isinstance(contents, _Stream):
            streams = [contents]
        elif isinstance(contents, _Array):
            for c in contents.items:
                c = self._resolve(c)
                if isinstance(c, _Stream):
                    streams.append(c)
        return b"".join(s.decoded() for s in streams)

    def walk_name_tree(self, tree: Any) -> list[str]:
        """Flatten a PDF name tree (e.g., EmbeddedFiles, JavaScript)."""
        if tree is None:
            return []
        tree = self._resolve(tree)
        if not isinstance(tree, _Dict):
            return []
        results: list[str] = []
        names = tree.get("Names")
        if isinstance(names, _Array):
            for i in range(0, len(names.items), 2):
                if i + 1 < len(names.items):
                    key = names.items[i]
                    names.items[i + 1]
                    if isinstance(key, _String):
                        results.append(key.decode())
                    elif isinstance(key, bytes):
                        results.append(_decode_pdf_string(key))
                    else:
                        results.append(str(key))
        kids = tree.get("Kids")
        if isinstance(kids, _Array):
            for kid in kids.items:
                results.extend(self.walk_name_tree(kid))
        return results

    # -----------------------------------------------------------------------
    # Resolution helpers
    # -----------------------------------------------------------------------
    def _resolve(self, value: Any) -> Any:
        if (
            isinstance(value, tuple)
            and len(value) == 2
            and all(isinstance(x, int) for x in value)
        ):
            return self.objects.get(value, value)
        if isinstance(value, _Ref):
            return self.objects.get((value.obj_num, value.gen_num), value)
        if isinstance(value, _Dict):
            new_entries = {k: self._resolve(v) for k, v in value.entries.items()}
            if isinstance(value, _Stream):
                return _Stream(entries=new_entries, raw_data=value.raw_data)
            return _Dict(entries=new_entries)
        if isinstance(value, _Array):
            return _Array(items=[self._resolve(i) for i in value.items])
        return value


# ---------------------------------------------------------------------------
# 6. Conversion to plain dict
# ---------------------------------------------------------------------------
def _to_plain(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, _Name):
        return "/" + value.name
    if isinstance(value, _String):
        return value.decode()
    if isinstance(value, _Dict):
        return {k: _to_plain(v) for k, v in value.entries.items()}
    if isinstance(value, _Array):
        return [_to_plain(i) for i in value.items]
    if isinstance(value, _Ref):
        return (value.obj_num, value.gen_num)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, bytes):
        return _decode_pdf_string(value)
    return value
