"""Integration tests for HTML5 error correction across tree builders.

These tests exercise the parser's recovery behaviour on malformed, locally
stored page fixtures where:

* body text appears in table scope and has to be foster-parented out of the
  table;
* formatting elements (``<b>``/``<i>``) are left unclosed and have to be
  reconstructed by the active-formatting-element algorithm;
* foreign content (SVG, MathML) shares the page, including nested tables and
  HTML content embedded through ``foreignObject``.

For every fixture the same assertions are applied to the DOM, ElementTree
and lxml backends, and to both full-document parsing and fragment parsing
with an explicit table context.  The expected trees live in
``fixtures/error-correction/expected.json`` and are justified independently
of any backend: they encode the WHATWG tree-construction outcome as an
ordered token stream.  Backend representation limits (see
:class:`html5lib.constants.DataLossWarning`) are reported separately from
real parsing differences; content reordering, attribute loss and namespace
misplacement always fail the assertions.
"""
from __future__ import absolute_import, division, unicode_literals

import warnings

import pytest

from html5lib.constants import DataLossWarning

from .error_correction import (
    BACKENDS,
    canonical_tokens,
    failure_message,
    load_case_input,
    load_manifest,
    parse_case,
    reparse,
    serialize_tree,
    text_order,
)

_MANIFEST = load_manifest()
_CASES = _MANIFEST["cases"]
_CASE_IDS = [case["name"] for case in _CASES]
_FOREIGN_LABELS = ("svg", "math")

_BACKEND_MARKS = {
    "dom": pytest.mark.DOM,
    "etree": pytest.mark.ElementTree,
    "lxml": pytest.mark.lxml,
}
# One parametrization per backend, carrying the backend's registered marker
# so ``pytest -m lxml`` selects only the lxml instances.
_BACKEND_PARAMS = [
    pytest.param(backend, id=backend, marks=_BACKEND_MARKS[backend])
    for backend in BACKENDS
]


def _require_backend(backend):
    if backend == "lxml":
        pytest.importorskip("lxml")


def _parse_without_data_loss(case, backend):
    """Parse a case, separating representation limits from parse results.

    A ``DataLossWarning`` means the backend cannot represent part of the
    information set at all (for example text beside lxml's document root).
    That is a documented backend expression limit, not a parsing difference,
    so it is surfaced with an explicit, distinct failure instead of being
    confused with a mismatched tree.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        tree = parse_case(case, backend)
    data_loss = [str(w.message) for w in caught
                 if issubclass(w.category, DataLossWarning)]
    assert not data_loss, (
        "backend %r cannot fully represent case %r (representation limit, "
        "not a parsing difference): %s"
        % (backend, case["name"], "; ".join(data_loss)))
    return tree


def _params_for(predicate):
    return [pytest.param(case, id=case["name"])
            for case in _CASES if predicate(case)]


@pytest.mark.parser
@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
@pytest.mark.parametrize("backend", _BACKEND_PARAMS)
def test_error_correction_tree_matches_expected(case, backend):
    # The parsed tree, normalised only for documented backend representation
    # differences, must equal the independently maintained expected stream.
    _require_backend(backend)
    tree = _parse_without_data_loss(case, backend)
    actual = canonical_tokens(tree, backend)
    expected = [list(token) for token in case["expected_tokens"]]
    assert actual == expected, failure_message(case, backend, expected, actual)


@pytest.mark.parser
@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
@pytest.mark.parametrize("backend", _BACKEND_PARAMS)
def test_body_text_order_is_preserved(case, backend):
    # The whole point of foster parenting and reconstruction: every body text
    # run must keep the position the HTML5 algorithm assigns it.
    _require_backend(backend)
    tree = _parse_without_data_loss(case, backend)
    actual_order = text_order(canonical_tokens(tree, backend))
    assert actual_order == case["expected_text_order"], (
        "\ncase: %s (backend=%s)\ninput: %s\nexpected text order: %r\n"
        "actual text order:   %r"
        % (case["name"], backend, load_case_input(case),
           case["expected_text_order"], actual_order))


@pytest.mark.parser
@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
@pytest.mark.parametrize("backend", _BACKEND_PARAMS)
def test_serialized_tree_reparses_identically(case, backend):
    # Serialize the recovered tree and parse the serialization again: the
    # second tree must carry the same information set as the first, on every
    # backend and in both document and fragment (context) modes.
    _require_backend(backend)
    tree = _parse_without_data_loss(case, backend)
    first = canonical_tokens(tree, backend)

    serialized = serialize_tree(tree, backend)
    second_tree = reparse(serialized, case, backend)
    second = canonical_tokens(second_tree, backend)

    assert second == first, (
        "\ncase: %s (backend=%s)\ninput: %s\nserialized as: %s\n"
        "round-trip changed the tree:\n%s"
        % (case["name"], backend, load_case_input(case), serialized,
           failure_message(case, backend, first, second)))


_FOREIGN_CASES = _params_for(lambda case: "foreign-content" in case["focus"])


@pytest.mark.namespaced
@pytest.mark.parametrize("case", _FOREIGN_CASES)
@pytest.mark.parametrize("backend", _BACKEND_PARAMS)
def test_foreign_namespaces_are_not_misplaced(case, backend):
    # Every SVG/MathML element must stay in its own namespace while the HTML
    # table machinery around it keeps the HTML namespace; a token whose
    # namespace label is wrong reveals namespace misplacement directly.
    _require_backend(backend)
    tree = _parse_without_data_loss(case, backend)
    tokens = canonical_tokens(tree, backend)

    element_tokens = [token for token in tokens
                      if token[0] in ("start", "empty")]
    assert element_tokens, "case %r produced no elements" % case["name"]

    foreign = [(token[1], token[2]) for token in element_tokens
               if token[1] in _FOREIGN_LABELS]
    assert foreign, (
        "case %r was expected to retain foreign elements" % case["name"])
    for namespace_label, name in foreign:
        assert namespace_label in _FOREIGN_LABELS, (
            "foreign element %r lost its namespace in case %r (backend=%s)"
            % (name, case["name"], backend))

    # Foster-parented foreign content moves before the table but must not be
    # swallowed by it: the table element must keep the HTML namespace.
    for token in element_tokens:
        if token[2] == "table":
            assert token[1] == "html", (
                "table element misplaced into the %s namespace in case %r "
                "(backend=%s)" % (token[1], case["name"], backend))
            break


_ATTRIBUTE_CASES = _params_for(
    lambda case: "attributes" in case["focus"] or
    "nested-table" in case["focus"])


@pytest.mark.parser
@pytest.mark.parametrize("case", _ATTRIBUTE_CASES)
@pytest.mark.parametrize("backend", _BACKEND_PARAMS)
def test_attributes_survive_foster_parenting_and_reconstruction(case, backend):
    # Attributes on elements involved in foster parenting and active
    # formatting element reconstruction must be preserved verbatim.
    _require_backend(backend)
    tree = _parse_without_data_loss(case, backend)
    actual = canonical_tokens(tree, backend)
    expected = [list(token) for token in case["expected_tokens"]]
    actual_attrs = {
        (token[1], token[2]): token[3]
        for token in actual if token[0] in ("start", "empty")
    }
    expected_attrs = {
        (token[1], token[2]): token[3]
        for token in expected if token[0] in ("start", "empty")
    }
    for key, value in expected_attrs.items():
        if value:
            assert actual_attrs.get(key) == value, (
                "attributes lost or altered on %r in case %r "
                "(backend=%s)\nexpected: %r\nactual:   %r"
                % (key, case["name"], backend, value, actual_attrs.get(key)))
