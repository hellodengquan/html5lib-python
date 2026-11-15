"""Integration tests for cross-backend HTML error recovery around tables.

The parser rewrites misnested markup according to the HTML parsing algorithm:
stray text and elements inside a table are *foster parented* in front of it,
misnested formatting elements are closed and later *reconstructed*, and foreign
(SVG/MathML) subtrees keep their own namespaces.  Every one of those rules runs
in the tree builder, and html5lib ships three of them, each with a different
idea of how to store a tree.

These tests pin down that the three backends describe the *same* document once
their storage quirks are factored out:

* the tree structure and the order and ownership of every run of text,
* element namespaces (HTML vs SVG vs MathML),
* attribute namespaces, names and values.

The expectations live in ``testdata-foster/*.dat`` and were written from the
parsing algorithm, so a backend cannot satisfy them by simply agreeing with
another backend.  Each case is checked twice: once on the parsed tree and once
on the tree after a serialize/re-parse round trip.  A third check compares the
backends directly so a regression shows up as a backend disagreement even
before an expectation is consulted.

Legitimate representation differences are normalized (adjacent text nodes,
attribute order, ``lxml``'s non-XML name coercions); anything that changes
where content lives, drops an attribute, or moves an element into a different
namespace is reported as a failure with the fixture source recovered in the
message.
"""

from __future__ import absolute_import, division, unicode_literals

import pytest

from . import foster_fixtures
from . import tree_normalization as normalization


_CASES = []


def _load_cases():
    if _CASES:
        return _CASES
    for path in foster_fixtures.fixture_files():
        for case in foster_fixtures.parse_fixture_file(path):
            _CASES.append(case)
    return _CASES


def _case_id(case):
    name = case["description"].split(".")[0][:50]
    return "%s:%d:%s" % (case["path"].split("/")[-1], case["line"],
                         name or case["data"][:40])


ALL_CASES = _load_cases()

_CASE_PARAMS = [pytest.param(case, id=_case_id(case)) for case in ALL_CASES]


def _backend_params():
    """Backend ids, marked so ``-m ElementTree``/``-m DOM``/``-m lxml`` work."""
    return [pytest.param(name, id=name,
                         marks=[getattr(pytest.mark,
                                        normalization.BACKENDS[name]["marker"])])
            for name in normalization.BACKEND_NAMES]


def _fixture_source(case):
    """A failure message that lets the offending web content be reproduced."""
    expected = normalization.render_tree(case["expected"])
    return "\n".join([
        "Fixture: %s (line %d)" % (case["path"], case["line"]),
        "Description: %s" % (case["description"] or "<none>"),
        "Mode: %s%s" % (case["mode"],
                        "" if case["mode"] == "document"
                        else " (container: %s)" % case["container"]),
        "Content:",
        case["data"],
        "Expected tree:",
        expected,
    ])


def _explain_mismatch(backend, case, actual, stage):
    lines = [_fixture_source(case),
             "Backend: %s" % backend,
             "Stage: %s" % stage,
             "Actual tree:",
             normalization.render_tree(actual),
             "Expected content placement:",
             normalization.describe_content_paths(case["expected"]),
             "Actual content placement:",
             normalization.describe_content_paths(actual)]
    return "\n".join(lines)


@pytest.mark.parametrize("backend", _backend_params())
@pytest.mark.parametrize("case", _CASE_PARAMS)
def test_expected_tree_matches_every_backend(backend, case):
    """Each backend must produce the hand-written expected tree."""
    tree, data_loss = normalization.parse(
        backend, case["data"], case["mode"], container=case["container"])
    assert not data_loss, "\n".join([
        _fixture_source(case),
        "Backend %s reported data loss: %s" % (backend, data_loss),
    ])

    actual = normalization.canonicalize(backend, tree, case["mode"])
    assert actual == case["expected"], _explain_mismatch(
        backend, case, actual, "parsed tree")


@pytest.mark.parametrize("backend", _backend_params())
@pytest.mark.parametrize("case", _CASE_PARAMS)
def test_expected_tree_survives_serialization_round_trip(backend, case):
    """Serializing and re-parsing must not move content or drop attributes.

    Attribute *namespaces* are relaxed for this comparison because html5lib's
    HTML serializer cannot re-attach namespace prefixes to attributes (see the
    ``TODO`` in ``serializer.py``); attribute names, values and element
    namespaces are still compared exactly.
    """
    tree, _ = normalization.parse(
        backend, case["data"], case["mode"], container=case["container"])
    first = normalization.canonicalize(backend, tree, case["mode"])

    serialized = normalization.serialize_tree(backend, tree)
    if isinstance(serialized, bytes):
        serialized = serialized.decode("utf-8")

    reparsed, data_loss = normalization.parse(
        backend, serialized, case["mode"], container=case["container"])
    assert not data_loss, "\n".join([
        _fixture_source(case),
        "Backend %s reported data loss while re-parsing %r: %s"
        % (backend, serialized, data_loss),
    ])

    second = normalization.canonicalize(backend, reparsed, case["mode"])

    relaxed_first = normalization.relax_attribute_namespaces(first)
    relaxed_second = normalization.relax_attribute_namespaces(second)
    assert relaxed_first == relaxed_second, "\n".join([
        _fixture_source(case),
        "Backend: %s" % backend,
        "Serialized form: %s" % serialized,
        "Tree before serialization:",
        normalization.render_tree(first),
        "Tree after re-parsing:",
        normalization.render_tree(second),
        "Content placement before:",
        normalization.describe_content_paths(first),
        "Content placement after:",
        normalization.describe_content_paths(second),
    ])


@pytest.mark.parametrize("case", _CASE_PARAMS)
def test_backends_agree_with_each_other(case):
    """All backends must describe the same tree, independent of the fixtures.

    This catches a backend regression that a shared expectation might mask,
    and reports which backend is the outlier rather than trusting the first
    backend's output.
    """
    trees = {}
    for backend in normalization.BACKEND_NAMES:
        tree, _ = normalization.parse(
            backend, case["data"], case["mode"], container=case["container"])
        trees[backend] = normalization.canonicalize(backend, tree, case["mode"])

    distinct = {}
    for backend, tree in trees.items():
        distinct.setdefault(tree, []).append(backend)

    if len(distinct) == 1:
        return

    groups = sorted(distinct.items(), key=lambda item: -len(item[1]))
    reference_tree, reference_backends = groups[0]
    lines = [_fixture_source(case),
             "Backends disagree; largest agreeing group: %s"
             % ", ".join(sorted(reference_backends))]
    for tree, backends in groups[1:]:
        lines += ["Backend(s) %s produced:" % ", ".join(sorted(backends)),
                  normalization.render_tree(tree),
                  "Content placement:",
                  normalization.describe_content_paths(tree)]
    assert False, "\n".join(lines)


@pytest.mark.parametrize("case", _CASE_PARAMS)
def test_fixture_expectation_preserves_all_source_text(case):
    """The fixture's expectations must account for every visible character.

    This guards the fixtures themselves: parsing never deletes character data,
    so a non-whitespace character in the source has to appear somewhere in the
    expected tree.  Without this check a fixture could quietly stop mentioning
    a run of text that the parser moved, and the other tests would pass while
    ignoring it.
    """
    source_text = "".join(_source_text_runs(case["data"]))
    expected_text = "".join(text for text, _ in
                            normalization.content_paths(case["expected"]))
    # Recovery may reorder text (foster parenting moves it in front of the
    # table), so compare the multiset of characters rather than the order.
    assert sorted(source_text) == sorted(expected_text), "\n".join([
        _fixture_source(case),
        "Visible source text:   %r" % source_text,
        "Visible expected text: %r" % expected_text,
    ])


def _source_text_runs(data):
    """Return the visible character runs of the source, ignoring whitespace.

    Markup, comments and attribute values are skipped; the remaining text is
    concatenated so the result can be compared against the tree's text
    regardless of how the parser redistributed it between nodes.
    """
    text_runs = []
    position = 0
    length = len(data)
    while position < length:
        character = data[position]
        if character == "<":
            if data.startswith("<!--", position):
                end = data.find("-->", position + 4)
                position = length if end < 0 else end + 3
                continue
            end = data.find(">", position + 1)
            position = length if end < 0 else end + 1
            continue
        quote = None
        end = position
        while end < length:
            if quote:
                if data[end] == quote:
                    quote = None
            elif data[end] in "\"'":
                quote = data[end]
            elif data[end] == "<":
                break
            end += 1
        run = data[position:end]
        if run.strip():
            text_runs.append(run)
        position = end if end != position else end + 1
    return text_runs


def test_fixtures_cover_the_required_scenarios():
    """The fixture set must exercise every dimension the tests are about.

    Failing here means the suite would still be green while no longer
    checking whole-page parsing, fragment parsing, nested tables, foreign
    namespaces, or formatting-element reconstruction.
    """
    assert ALL_CASES, "no fixtures were found under testdata-foster"

    def has(case, *needles):
        haystack = case["data"]
        return any(needle in haystack for needle in needles)

    def namespaces_in(case):
        found = set()
        for namespace, _ in _element_namespaces(case["expected"]):
            found.add(namespace)
        return found

    documents = [case for case in ALL_CASES if case["mode"] == "document"]
    fragments = [case for case in ALL_CASES if case["mode"] == "fragment"]

    assert documents, "no whole-page fixtures"
    assert fragments, "no fragment fixtures"
    assert any(case["container"] == "table" for case in fragments), \
        "no fragment fixture uses a table context"
    assert any(case["container"] in ("td", "th") for case in fragments), \
        "no fragment fixture uses a cell context"
    assert any(has(case, "<table") and has(case, "class=\"inner\"")
               for case in ALL_CASES), "no nested-table fixture"
    assert any(normalization.constants.namespaces["svg"] in namespaces_in(case)
               for case in ALL_CASES), "no SVG fixture"
    assert any(normalization.constants.namespaces["mathml"] in namespaces_in(case)
               for case in ALL_CASES), "no MathML fixture"
    assert any(has(case, "<b", "<i", "<a") and has(case, "<table")
               for case in ALL_CASES), \
        "no fixture mixes a formatting element with a table"


def _element_namespaces(tree):
    found = []

    def visit(children):
        for node in children:
            if node[0] == "element":
                found.append((node[1], node[2]))
                visit(node[4])

    visit(tree[1])
    return found


def test_fixture_render_round_trips_through_the_parser():
    """Rendering an expectation and parsing it back yields the same tree.

    ``render_tree`` and the fixture parser must agree, otherwise a broken
    fixture could look correct while encoding something else entirely.
    """
    for case in ALL_CASES:
        rendered = normalization.render_tree(case["expected"])
        lines = rendered.split("\n")
        reparsed = foster_fixtures.parse_expected(
            lines, case["path"], case["line"], case["mode"])
        assert reparsed == case["expected"], "\n".join([
            _fixture_source(case),
            "Re-rendered expectation:",
            rendered,
        ])
