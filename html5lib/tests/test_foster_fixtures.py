"""Unit tests for the fixture format and the tree normalization helpers.

``test_foster_parenting.py`` relies on these two modules to interpret its
fixtures and to compare backend trees.  A bug here could make every integration
test pass vacuously, so the parsers and the normalization are exercised
directly and their failure modes are pinned down.
"""

from __future__ import absolute_import, division, unicode_literals

import io
import os
import tempfile

import pytest

from html5lib import constants

from . import foster_fixtures
from . import tree_normalization as normalization


def _write_fixture(text):
    handle, path = tempfile.mkstemp(suffix=".dat")
    os.close(handle)
    with io.open(path, "w", encoding="utf-8") as fixture_file:
        fixture_file.write(text)
    return path


def _parse_fixture(text):
    path = _write_fixture(text)
    try:
        return foster_fixtures.parse_fixture_file(path)
    finally:
        os.remove(path)


def test_parse_fixture_file_reads_sections_and_attributes():
    cases = _parse_fixture("""
#data
<div class="wrap"><span>hi</span></div>
#mode
fragment
#document-fragment
div
#description
Two levels of nesting with an attribute.
#document
| <div>
|   class="wrap"
|   <span>
|     "hi"
""")
    assert len(cases) == 1
    case = cases[0]
    assert case["data"] == '<div class="wrap"><span>hi</span></div>'
    assert case["mode"] == "fragment"
    assert case["container"] == "div"
    assert case["description"] == "Two levels of nesting with an attribute."
    expected = case["expected"]
    assert expected[0] == "fragment"
    (div,) = expected[1]
    assert div[0] == "element"
    assert div[2] == "div"
    assert div[3] == ((None, "class", "wrap"),)
    (span,) = div[4]
    assert span[4] == (("text", "hi"),)


def test_parse_fixture_file_defaults_mode_and_container():
    cases = _parse_fixture("""
#data
<p>x</p>
#document
| <p>
|   "x"
""")
    case = cases[0]
    assert case["mode"] == "document"
    assert case["container"] == "div"
    assert case["expected"][0] == "document"


def test_parse_fixture_file_preserves_newlines_in_data():
    cases = _parse_fixture("""
#data
<p>a</p>
<b>b</b>
#document
| <p>
|   "a"
| <b>
|   "b"
""")
    assert cases[0]["data"] == "<p>a</p>\n<b>b</b>"


def test_parse_fixture_file_handles_multiple_cases():
    cases = _parse_fixture("""
#data
a
#document
| "a"

#data
b
#document
| "b"
""")
    assert [case["data"] for case in cases] == ["a", "b"]


def test_fixture_namespace_prefixes():
    cases = _parse_fixture("""
#data
<svg><use xlink:href="#a"/></svg>
#document
| <svg svg>
|   <svg use>
|     xlink href="#a"
""")
    (svg,) = cases[0]["expected"][1]
    assert svg[1] == constants.namespaces["svg"]
    (use,) = svg[4]
    assert use[1] == constants.namespaces["svg"]
    assert use[3] == ((constants.namespaces["xlink"], "href", "#a"),)


def test_comment_lines_between_cases_are_ignored():
    cases = _parse_fixture("""
# a comment
#data
<p>x</p>
# a comment inside the case
#document
| <p>
|   "x"
""")
    assert len(cases) == 1


@pytest.mark.parametrize("text,fragment", [
    ("no #data at all\n", "content before the first #data"),
    ("#data\nx\n", "no #document"),
    ("#data\nx\n#mode\nbogus\n#document\n| \"x\"\n", "must be document or fragment"),
    ("#data\nx\n#document-fragment\ntable\n#document\n| \"x\"\n",
     "given for a document case"),
    ("#data\nx\n#document\nnot prefixed\n", "lacks a leading"),
    ("#data\nx\n#document\n|     bogus=\"1\"\n",
     "has no element"),
    ("#data\nx\n#document\n| <foo bar baz>\n", "cannot parse name"),
    ("#data\nx\n#document\n| <wat thing>\n", "unknown namespace prefix"),
])
def test_parse_fixture_file_rejects_malformed_input(text, fragment):
    with pytest.raises(foster_fixtures.FixtureError) as excinfo:
        _parse_fixture(text)
    assert fragment in str(excinfo.value)


def test_render_tree_round_trips_through_parse_expected():
    case = _parse_fixture("""
#data
<!doctype html><!--note--><table><tr><td><svg><desc>d</desc></svg>t</td></tr></table>
#document
| <!DOCTYPE html>
| <!--note-->
| <table>
|   <tbody>
|     <tr>
|       <td>
|         <svg svg>
|           <svg desc>
|             "d"
|         "t"
""")[0]
    rendered = normalization.render_tree(case["expected"])
    reparsed = foster_fixtures.parse_expected(
        rendered.split("\n"), "<roundtrip>", 0, case["mode"])
    assert reparsed == case["expected"]


def _parse(backend, source, mode="document", container="div"):
    tree, data_loss = normalization.parse(backend, source, mode,
                                          container=container)
    assert not data_loss, "%s reported data loss: %s" % (backend, data_loss)
    return normalization.canonicalize(backend, tree, mode)


def test_text_ownership_is_namespace_sensitive():
    document = _parse(
        "dom",
        "<table><tr><td><svg><desc>one</desc></svg><p>two</p></td></tr></table>")
    paths = dict((text, path) for text, path in
                 normalization.content_paths(document))
    assert any(namespace == constants.namespaces["svg"]
               for namespace, _ in paths["one"])
    assert all(namespace == constants.namespaces["html"]
               for namespace, _ in paths["two"])
    assert normalization.describe_content_paths(document)


def test_adjacent_text_keeps_element_boundaries():
    document = _parse("etree", "<p>a<b>b</b>c</p>")
    (html,) = document[1]
    (body,) = html[4][1:]
    (p,) = body[4]
    assert [child[0] for child in p[4]] == ["text", "element", "text"]
