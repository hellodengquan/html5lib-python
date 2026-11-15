"""Tests that splitting a byte stream into chunks does not change parsing.

A real fetcher hands the parser a non-seekable byte stream whose ``read(n)``
may return fewer than ``n`` bytes (sockets, ``http_client.HTTPResponse``...).
html5lib wraps such streams in :class:`~html5lib._inputstream.BufferedStream`.
These tests feed identical page bytes through a continuous read (the
baseline) and through streams that only ever hand over a few bytes per
``read()`` call, and assert that the chunking cannot change:

* the final HTML parse tree,
* the body text,
* the character encoding (and its confidence) that is finally used,
* the parse error list.

The combinations cover BOMs, transport-layer encoding declarations, the
meta prescan and mid-parse encoding changes (with recovery of the bytes
already consumed), as well as multibyte characters split across chunk
boundaries.
"""
from __future__ import absolute_import, division, unicode_literals

import codecs
from io import BytesIO

import pytest

from html5lib import HTMLParser
from html5lib._inputstream import (BufferedStream, HTMLBinaryInputStream,
                                   HTMLInputStream)
from html5lib.constants import EOF
import xml.etree.ElementTree as ET


CHUNK_SIZES = (1, 2, 3, 4, 5, 7, 64, 1024)


class ShortReadStream(object):
    """Non-seekable stream returning at most ``limit`` bytes per read().

    Deliberately exposes no ``seek``/``tell`` so that
    :meth:`HTMLBinaryInputStream.openStream` wraps it in a
    :class:`BufferedStream`, mirroring a socket-backed HTTP response.
    """

    def __init__(self, data, limit):
        self._data = data
        self._pos = 0
        self.limit = limit
        self.read_calls = 0

    def read(self, n=-1):
        self.read_calls += 1
        if n is None or n < 0:
            n = len(self._data) - self._pos
        n = min(n, self.limit)
        part = self._data[self._pos:self._pos + n]
        self._pos += len(part)
        return part


class AlternatingShortReadStream(ShortReadStream):
    """Non-seekable stream whose per-read limit cycles through 1, 2, ...

    This puts chunk boundaries in a different place on every read, so a
    multibyte character or a BOM cannot consistently line up with a boundary.
    """

    def __init__(self, data, period=3):
        super(AlternatingShortReadStream, self).__init__(data, 1)
        self.period = period
        self.calls = 0

    def read(self, n=-1):
        self.calls += 1
        self.limit = 1 + (self.calls - 1) % self.period
        return super(AlternatingShortReadStream, self).read(n)


def body_text(doc):
    """Concatenate all text nodes below the body (and loose text)."""
    parts = []

    def visit(element):
        if element.text:
            parts.append(element.text)
        for child in list(element):
            visit(child)
            if child.tail:
                parts.append(child.tail)

    root = doc.getroot() if hasattr(doc, "getroot") else doc
    body = root.find(".//body")
    visit(body if body is not None else root)
    return "".join(parts)


def parse_case(data, chunk, **kwargs):
    """Parse ``data`` and return every observable result of the fetch.

    ``chunk`` of ``None`` means the bytes are passed straight in (continuous
    read baseline); otherwise the bytes go through a short-read stream.
    """
    parser = HTMLParser(namespaceHTMLElements=False)
    if chunk is None:
        source = data
    elif chunk == "alternating":
        source = AlternatingShortReadStream(data)
    else:
        source = ShortReadStream(data, chunk)
    doc = parser.parse(source, useChardet=False, **kwargs)
    stream = parser.tokenizer.stream
    return {
        "tree": ET.tostring(doc, encoding="unicode"),
        "text": body_text(doc),
        "encoding": parser.documentEncoding,
        "confidence": stream.charEncoding[1],
        "errors": parser.errors,
    }


# ---------------------------------------------------------------------------
# A. Continuous reads versus short reads on identical bytes
# ---------------------------------------------------------------------------

DOCUMENTS = {
    "utf8_multibyte": (
        "<!doctype html><title>t</title>"
        "<p>Café 中文 \U0001f600 —</p>".encode("utf-8"),
        {}),
    "default_windows1252": (
        b"<!doctype html><title>t</title><p>caf\xe9 na\xefve</p>",
        {}),
    "transport_iso8859_2": (
        "<!doctype html><title>t</title><p>kuň</p>".encode("iso-8859-2"),
        {"transport_encoding": "iso-8859-2"}),
    "utf8_bom": (
        codecs.BOM_UTF8 +
        "<!doctype html><title>héllo</title><p>中文</p>".encode("utf-8"),
        {}),
    "utf16le_bom": (
        "﻿<!doctype html><title>t</title><p>中文\U0001f600</p>".encode("utf-16"),
        {}),
    "meta_within_prescan": (
        b"<head><meta charset='utf-8'></head>"
        b"<p>\xc3\xa9 \xe4\xb8\xad</p>",
        {}),
    "meta_after_prescan": (
        b"<title>x</title>" + b"a" * 1100 +
        b"<meta charset='utf-8'><p>\xc3\xa9</p>",
        {}),
}


@pytest.mark.parametrize("chunk", list(CHUNK_SIZES) + ["alternating"])
@pytest.mark.parametrize("doc_name", sorted(DOCUMENTS))
def test_chunking_does_not_change_result(doc_name, chunk):
    data, kwargs = DOCUMENTS[doc_name]
    baseline = parse_case(data, None, **kwargs)
    result = parse_case(data, chunk, **kwargs)
    assert result == baseline, (doc_name, chunk)


def test_short_read_stream_is_buffered():
    # The non-seekable input must actually go through BufferedStream,
    # otherwise this test module would not be exercising the short-read path.
    parser = HTMLParser(namespaceHTMLElements=False)
    parser.parse(ShortReadStream(DOCUMENTS["utf8_multibyte"][0], 1),
                 useChardet=False)
    assert isinstance(parser.tokenizer.stream.rawStream, BufferedStream)


def test_seekable_stream_is_not_buffered():
    parser = HTMLParser(namespaceHTMLElements=False)
    parser.parse(BytesIO(DOCUMENTS["utf8_multibyte"][0]), useChardet=False)
    assert not isinstance(parser.tokenizer.stream.rawStream, BufferedStream)


# ---------------------------------------------------------------------------
# B. Encoding changes at different boundaries; recovery of consumed content
# ---------------------------------------------------------------------------

def _reparse_document(padding):
    """Tentative windows-1252 page that switches to utf-8 via a meta tag.

    The padding places the meta tag at different byte offsets so the
    reparse is triggered after different amounts of already-consumed input.
    """
    return (b"<title>Caf\xc3\xa9 \xe4\xb8\xad\xe6\x96\x87</title>" +
            b"a" * padding +
            b"<meta charset='utf-8'>"
            b"<p>post-reparse \xc3\xa9 \xe4\xb8\xad</p>")


@pytest.mark.parametrize("chunk", list(CHUNK_SIZES) + ["alternating"])
@pytest.mark.parametrize("padding", [0, 1, 3, 17, 100, 1020, 2000])
def test_reparse_recovers_consumed_content(chunk, padding):
    data = _reparse_document(padding)
    baseline = parse_case(data, None)
    result = parse_case(data, chunk)

    assert result == baseline
    # The meta prescan window is 1024 bytes.
    assert result["encoding"] == "utf-8"
    assert result["confidence"] == "certain"
    # Content decoded under the tentative windows-1252 encoding must have
    # been re-read correctly after the encoding change, not left as mojibake.
    assert "Café 中文" in result["tree"]
    assert "CafÃ©" not in result["tree"]
    assert "post-reparse é 中" in result["text"]


def _http_equiv_document(padding):
    return (b"<title>Caf\xc3\xa9</title>" + b" " * padding +
            b"<meta http-equiv='content-type' "
            b"content='text/html; charset=utf-8'>"
            b"<p>\xe4\xb8\xad</p>")


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 64, "alternating"])
@pytest.mark.parametrize("padding", [0, 5, 1100])
def test_reparse_via_http_equiv_meta(chunk, padding):
    data = _http_equiv_document(padding)
    baseline = parse_case(data, None)
    result = parse_case(data, chunk)
    assert result == baseline
    assert result["encoding"] == "utf-8"
    assert result["confidence"] == "certain"
    assert "Café" in result["tree"]


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, "alternating"])
def test_certain_encoding_meta_same_encoding_no_reparse(chunk):
    # A BOM makes the encoding "certain"; a matching meta tag must not
    # trigger a reparse and the page must still parse identically.
    data = (codecs.BOM_UTF8 + b"<meta charset='utf-8'>"
            b"<p>\xe4\xb8\xad\xe6\x96\x87</p>")
    baseline = parse_case(data, None)
    result = parse_case(data, chunk)
    assert result == baseline
    assert result["encoding"] == "utf-8"
    assert result["confidence"] == "certain"
    assert result["text"] == "中文"


def _drain_stream(stream):
    chars = []
    while True:
        char = stream.char()
        if char is EOF:
            break
        chars.append(char)
    return "".join(chars)


@pytest.mark.parametrize("chunk", [1, 2, 3, 4, 5, 8])
def test_stream_level_decoding_matches_baseline(chunk):
    # Directly exercise the input stream: every character (including those
    # already consumed before a reparse) must decode to the same text.
    data = _reparse_document(37)

    baseline_stream = HTMLBinaryInputStream(BytesIO(data), useChardet=False)
    baseline_text = _drain_stream(baseline_stream)

    raw = BufferedStream(ShortReadStream(data, chunk))
    short_stream = HTMLBinaryInputStream(raw, useChardet=False)
    assert isinstance(short_stream.rawStream, BufferedStream)
    assert _drain_stream(short_stream) == baseline_text
    assert short_stream.charEncoding[0].name == baseline_stream.charEncoding[0].name


# ---------------------------------------------------------------------------
# C. Multibyte truncation and stable error recording
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("chunk", [1, 2, 3, 4, 5, 6, "alternating"])
def test_multibyte_chars_split_across_chunks(chunk):
    # 3-byte Chinese, 3-byte BMP punctuation and a 4-byte emoji, packed with
    # no ASCII padding so every chunk boundary cuts a multibyte sequence.
    data = "<p>中文—\U0001f600文</p>".encode("utf-8")
    baseline = parse_case(data, None, override_encoding="utf-8")
    result = parse_case(data, chunk, override_encoding="utf-8")
    assert result == baseline
    assert result["text"] == "中文—\U0001f600文"
    assert result["errors"] == baseline["errors"]


@pytest.mark.parametrize("chunk", [1, 2, 3, 5, 8, "alternating"])
def test_invalid_codepoint_errors_are_stable(chunk):
    # C0/C1 control characters are invalid Unicode codepoints; the input
    # stream records one "invalid-codepoint" error per character and the
    # tokenizer surfaces them as parse errors with stable positions.
    data = "ab\x01cd\x7fef\x9f".encode("utf-8")
    baseline = parse_case(data, None, override_encoding="utf-8")
    result = parse_case(data, chunk, override_encoding="utf-8")
    assert result == baseline
    invalid = [error for error in result["errors"]
               if error[1] == "invalid-codepoint"]
    assert len(invalid) == 3
    # Full tuples, including (line, column) and datavars, must be identical.
    assert all(error == ((1, 9), "invalid-codepoint", {}) for error in invalid)


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, "alternating"])
def test_invalid_byte_replacement_is_stable(chunk):
    # 0xff is never valid in UTF-8; the codec uses "replace" and emits
    # U+FFFD regardless of where the chunk boundary falls.
    data = b"<p>x\xffy\xe4\xb8z</p>"
    baseline = parse_case(data, None, override_encoding="utf-8")
    result = parse_case(data, chunk, override_encoding="utf-8")
    assert result == baseline
    assert "�" in result["text"]


def test_truncated_multibyte_at_every_prefix():
    # Every prefix of a UTF-8 document truncates either ASCII or a multibyte
    # sequence at EOF; each truncated prefix must parse the same way through
    # 1-byte reads as through a continuous read.
    full = "<p>中文</p><p>é\U0001f600</p>".encode("utf-8")
    for cut in range(1, len(full) + 1):
        prefix = full[:cut]
        baseline = parse_case(prefix, None, override_encoding="utf-8")
        result = parse_case(prefix, 1, override_encoding="utf-8")
        assert result == baseline, cut


# ---------------------------------------------------------------------------
# D. BufferedStream short-read semantics and BOM regressions
# ---------------------------------------------------------------------------

def test_buffered_stream_accumulates_short_reads():
    stream = BufferedStream(ShortReadStream(b"abcdefgh", 1))
    assert stream.read(4) == b"abcd"
    assert stream.tell() == 4
    assert stream.read(2) == b"ef"
    assert stream.tell() == 6


def test_buffered_stream_partial_read_at_eof():
    stream = BufferedStream(ShortReadStream(b"abc", 1))
    assert stream.read(10) == b"abc"
    assert stream.read(3) == b""


def test_buffered_stream_seek_then_refill():
    stream = BufferedStream(ShortReadStream(b"abcdef", 2))
    assert stream.read(3) == b"abc"
    stream.seek(2)
    # Reading spans the end of the buffered data and must refill correctly.
    assert stream.read(3) == b"cde"
    stream.seek(0)
    assert stream.read(6) == b"abcdef"


def test_utf8_bom_detected_with_one_byte_reads():
    # Regression: a 3-byte BOM spread over three 1-byte reads used to be
    # missed, falling back to windows-1252.
    stream = HTMLInputStream(ShortReadStream(codecs.BOM_UTF8 + b"'", 1),
                             useChardet=False)
    assert stream.charEncoding[0].name == "utf-8"
    assert stream.char() == "'"


@pytest.mark.parametrize("chunk", [1, 2, 3, 4])
def test_utf16_bom_detected_at_every_chunk_size(chunk):
    # Regression: a UTF-16 BOM on a short-read stream used to raise an
    # AssertionError inside BufferedStream.seek, or misdetect the encoding.
    data = "﻿<title>t</title>".encode("utf-16")

    baseline_stream = HTMLBinaryInputStream(BytesIO(data), useChardet=False)
    baseline_text = _drain_stream(baseline_stream)

    stream = HTMLInputStream(ShortReadStream(data, chunk), useChardet=False)
    assert stream.charEncoding[0].name == "utf-16le"
    # The decoded text (BOM character included, as on a continuous stream)
    # must match the continuous-read baseline exactly.
    assert _drain_stream(stream) == baseline_text
