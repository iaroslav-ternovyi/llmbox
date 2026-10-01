"""Minimal GGUF header reader (stdlib only, so it also runs on hosts without extra packages).

Reads the key/value metadata and the tensor directory from any byte source: a local file or a remote
file via HTTP range requests (e.g. a Hugging Face `resolve` URL). Only the header is read, never the
weights. Tensor byte sizes come from consecutive data offsets, so no per-quant-type size table is needed.
"""
from __future__ import annotations

import struct
import urllib.request
from dataclasses import dataclass, field
try:
    from . import UA
except ImportError:   # shipped to a host as a single file next to the agent (llmbox/host.py)
    UA = "llmbox"

GGUF_MAGIC = b"GGUF"
_SCALARS = {  # gguf value type -> struct format
    0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d",
}
T_STRING, T_ARRAY = 8, 9
BIG_ARRAY = 64  # arrays longer than this are summarized (tokenizer vocab/merges), not kept


class _Source:
    """Sequential reader over a random-access byte source, fetched in chunks."""

    def __init__(self, fetch, chunk: int = 4 << 20):
        self._fetch = fetch  # fetch(offset, length) -> bytes
        self._chunk = chunk
        self._buf = b""
        self._buf_start = 0
        self.pos = 0

    def read(self, n: int) -> bytes:
        end = self.pos + n
        if not (self._buf_start <= self.pos and end <= self._buf_start + len(self._buf)):
            want = max(n, self._chunk)
            data = self._fetch(self.pos, want)
            if len(data) < n:
                raise EOFError(f"GGUF source ended at {self.pos + len(data)} (needed {n} bytes at {self.pos})")
            self._buf, self._buf_start = data, self.pos
        off = self.pos - self._buf_start
        self.pos = end
        return self._buf[off:off + n]

    def unpack(self, fmt: str):
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        n = self.unpack("<Q")
        return self.read(n).decode("utf-8", "replace")


def file_fetcher(path: str):
    def fetch(offset: int, length: int) -> bytes:
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read(length)
    return fetch


def http_fetcher(url: str, token: str | None = None):
    def fetch(offset: int, length: int) -> bytes:
        req = urllib.request.Request(url, headers={"Range": f"bytes={offset}-{offset + length - 1}", "User-Agent": UA})
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    return fetch


@dataclass
class Tensor:
    name: str
    shape: tuple[int, ...]
    ggml_type: int
    offset: int  # relative to the data section
    nbytes: int = 0

    @property
    def n_elements(self) -> int:
        n = 1
        for d in self.shape:
            n *= d
        return n


@dataclass
class GGUFHeader:
    version: int
    kv: dict = field(default_factory=dict)          # small values kept verbatim
    arrays: dict = field(default_factory=dict)      # big arrays: key -> (elem_type, length)
    tensors: list[Tensor] = field(default_factory=list)
    data_start: int = 0
    header_bytes: int = 0

    @property
    def arch(self) -> str:
        return self.kv.get("general.architecture", "?")

    def get(self, key: str, default=None):
        """Architecture-relative lookup: get('block_count') -> kv['<arch>.block_count']."""
        return self.kv.get(key, self.kv.get(f"{self.arch}.{key}", default))


def _read_value(src: _Source, vtype: int):
    if vtype in _SCALARS:
        return src.unpack(_SCALARS[vtype])
    if vtype == T_STRING:
        return src.string()
    if vtype == T_ARRAY:
        etype = src.unpack("<I")
        n = src.unpack("<Q")
        if n > BIG_ARRAY:
            if etype in _SCALARS:
                src.read(struct.calcsize(_SCALARS[etype]) * n)
            else:
                for _ in range(n):
                    _read_value(src, etype)
            return ("__array__", etype, n)
        return [_read_value(src, etype) for _ in range(n)]
    raise ValueError(f"unknown GGUF value type {vtype}")


def read_header(fetch, file_size: int | None = None, keep: frozenset = frozenset()) -> GGUFHeader:
    """`keep`: big arrays to keep in full (e.g. 'tokenizer.ggml.tokens'); the others are only summarized."""
    src = _Source(fetch)
    if src.read(4) != GGUF_MAGIC:
        raise ValueError("not a GGUF file")
    version = src.unpack("<I")
    if version < 2:
        raise ValueError(f"GGUF v{version} is not supported")
    n_tensors = src.unpack("<Q")
    n_kv = src.unpack("<Q")
    h = GGUFHeader(version=version)
    for _ in range(n_kv):
        key = src.string()
        vtype = src.unpack("<I")
        if key in keep and vtype == T_ARRAY:
            etype, n = src.unpack("<I"), src.unpack("<Q")
            h.kv[key] = [_read_value(src, etype) for _ in range(n)]
            continue
        val = _read_value(src, vtype)
        if isinstance(val, tuple) and val and val[0] == "__array__":
            h.arrays[key] = (val[1], val[2])
        else:
            h.kv[key] = val
    for _ in range(n_tensors):
        name = src.string()
        n_dims = src.unpack("<I")
        shape = tuple(src.unpack("<Q") for _ in range(n_dims))
        gtype = src.unpack("<I")
        offset = src.unpack("<Q")
        h.tensors.append(Tensor(name, shape, gtype, offset))
    align = int(h.kv.get("general.alignment", 32))
    h.header_bytes = src.pos
    h.data_start = (src.pos + align - 1) // align * align
    # byte size of each tensor = distance to the next tensor's offset (the last one runs to the end of the file)
    order = sorted(h.tensors, key=lambda t: t.offset)
    for a, b in zip(order, order[1:]):
        a.nbytes = b.offset - a.offset
    if order:
        last = order[-1]
        last.nbytes = (file_size - h.data_start - last.offset) if file_size else 0
    return h
