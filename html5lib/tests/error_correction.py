"""Support code for the HTML error-correction integration tests.

The tests in ``test_error_correction.py`` check that the DOM, ElementTree and
lxml tree builders produce the same *information set* when recovering from
malformed table markup (foster parenting, unclosed formatting elements and
foreign namespaces).  They deliberately do not compare one backend's output
against another's: every backend is compared against the hand-maintained
expected token streams in ``fixtures/error-correction/expected.json``.

Representation differences that are intrinsic to a tree backend, and
therefore legal to normalise away, are:

* how text is stored (DOM text nodes versus ElementTree ``text``/``tail``
  versus lxml tails) -- the tree walkers already hide this;
* where a run of characters is split into ``Characters`` and
  ``SpaceCharacters`` tokens -- adjacent character tokens are merged;
* attribute ordering -- attributes are compared as a sorted collection;
* namespace *prefixes* -- namespaces are compared by their URI, using the
  short labels defined in the manifest.

Everything that carries author-visible meaning stays strict and is asserted
without normalisation: document order of every element and text run, element
ownership (parent/child relationships expressed by token nesting), attribute
names/values and the namespace an element or attribute belongs to.
"""
from __future__ import absolute_import, division, unicode_literals

import io
import json
import os

from html5lib import parse, parseFragment, serialize
from html5lib.constants import namespaces
from html5lib.filters.lint import Filter as Lint
from html5lib import treewalkers

try:  # lxml is an optional dependency
    import lxml  # noqa: F401
except ImportError:
    BACKENDS = ("dom", "etree")
else:
    BACKENDS = ("dom", "etree", "lxml")

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures",
                           "error-correction")
MANIFEST_PATH = os.path.join(FIXTURE_DIR, "expected.json")

# Full namespace URI -> short label used inside the manifest.  The labels are
# only a notation; equality is always established through the real URIs in
# html5lib.constants.namespaces.
NAMESPACE_LABELS = {
    namespaces["html"]: "html",
    namespaces["svg"]: "svg",
    namespaces["mathml"]: "math",
    None: None,
}

# Token kinds emitted by the tree walkers that describe an element.
_ELEMENT_KINDS = {
    "StartTag": "start",
    "EndTag": "end",
    "EmptyTag": "empty",
}


def load_manifest():
    """Load the independent expected results shared by every backend."""
    with io.open(MANIFEST_PATH, encoding="utf-8") as fp:
        return json.load(fp)


def load_case_input(case):
    """Return the malformed markup for a case, reading local fixture files.

    Document cases keep their markup in a ``.html`` fixture so that the
    original page content can be inspected directly when a test fails;
    fragment cases carry their markup inline in the manifest.
    """
    if case["fixture"] is not None:
        path = os.path.join(FIXTURE_DIR, case["fixture"])
        with io.open(path, encoding="utf-8") as fp:
            return fp.read()
    return case["input"]


def _normalise_attribute_key(key):
    """Turn a walker attribute key into a manifest ``(namespace, name)``."""
    if isinstance(key, tuple):
        namespace, name = key[0], key[1]
    else:
        namespace, name = None, key
    return [NAMESPACE_LABELS.get(namespace, namespace), name]


def canonical_tokens(tree, backend):
    """Return the backend-independent canonical token stream for a tree.

    The tree walker for the given backend yields the same token dictionaries
    the serializer consumes; running it through the lint filter additionally
    verifies structural sanity (matching tags, well-formed attributes).

    The result is a list of ``[kind, namespace, name, attributes]`` lists,
    where adjacent character tokens are merged and attributes are sorted so
    that backend-specific storage details cannot cause spurious failures.
    """
    walker = treewalkers.getTreeWalker(backend)
    tokens = []
    for token in Lint(walker(tree)):
        kind = token["type"]
        if kind in _ELEMENT_KINDS:
            attributes = sorted(
                _normalise_attribute_key(key) + [value]
                for key, value in (token.get("data") or {}).items()
            )
            tokens.append([
                _ELEMENT_KINDS[kind],
                NAMESPACE_LABELS.get(token["namespace"], token["namespace"]),
                token["name"],
                attributes,
            ])
        elif kind in ("Characters", "SpaceCharacters"):
            tokens.append(["text", None, token["data"], []])
        elif kind == "Comment":
            tokens.append(["comment", None, token["data"], []])
        elif kind == "Doctype":
            tokens.append(["doctype", None, token["name"], []])
        else:
            # SerializerError/Entity tokens never occur for these fixtures;
            # fail loudly rather than silently dropping information.
            raise AssertionError("unexpected walker token: %r" % (token,))

    # Merge adjacent character tokens: the Characters/SpaceCharacters split is
    # a walker presentation choice, not a difference in the parsed tree.
    merged = []
    for token in tokens:
        if (token[0] == "text" and merged and merged[-1][0] == "text"):
            merged[-1][2] += token[2]
        else:
            merged.append(token)
    return merged


def text_order(tokens):
    """Extract the body text runs in document order from a token stream."""
    return [token[2] for token in tokens if token[0] == "text"]


def parse_case(case, backend):
    """Parse a manifest case with the requested tree builder."""
    markup = load_case_input(case)
    if case["mode"] == "fragment":
        return parseFragment(markup, container=case["container"],
                             treebuilder=backend)
    return parse(markup, treebuilder=backend)


def reparse(markup, case, backend):
    """Parse serialized output again, using the same parsing context."""
    if case["mode"] == "fragment":
        return parseFragment(markup, container=case["container"],
                             treebuilder=backend)
    return parse(markup, treebuilder=backend)


def serialize_tree(tree, backend):
    """Serialize a parsed tree with the matching backend's tree walker."""
    return serialize(tree, tree=backend, omit_optional_tags=False)


def render_diff(expected, actual):
    """Build a readable first-difference report for an assertion message."""
    lines = []
    for index in range(max(len(expected), len(actual))):
        if index >= len(expected):
            lines.append("  unexpected extra token at %d: %r"
                         % (index, actual[index]))
            break
        if index >= len(actual):
            lines.append("  missing token at %d: %r"
                         % (index, expected[index]))
            break
        if expected[index] != actual[index]:
            lines.append("  first difference at token %d:" % index)
            lines.append("    expected: %r" % expected[index])
            lines.append("    actual:   %r" % actual[index])
            break
    return "\n".join(lines)


def failure_message(case, backend, expected, actual):
    """Include everything needed to reconstruct the failing page."""
    return "\n".join([
        "",
        "Case: %s (backend=%s, mode=%s%s)" % (
            case["name"], backend, case["mode"],
            ", container=%s" % case["container"]
            if case["container"] else ""),
        "Fixture: %s" % (case["fixture"] or "<inline fragment input>"),
        "Input markup:",
        load_case_input(case),
        "Expected %d tokens, got %d tokens" % (len(expected), len(actual)),
        render_diff(expected, actual),
    ])
