"""Parser for the local fixture files used by the recovery integration tests.

Each fixture is a directory-free ``.dat`` file under
``html5lib/tests/testdata-foster`` (kept apart from the ``testdata`` git
submodule) holding independent cases in a plain-text format::

    #data
    <!doctype html><table><b>misplaced<tr><td>cell</td></tr></b></table>
    #mode
    document
    #document-fragment
    table
    #description
    A formatting element that straddles a table is foster parented out.
    #document
    | <!DOCTYPE html>
    | <html>
    |   <head>
    |   <body>
    |     <b>
    |       "misplaced"
    |     <table>
    |       <tbody>
    |         <tr>
    |           <td>
    |             "cell"

``#mode`` defaults to ``document`` and, when it is ``fragment``, the
``#document-fragment`` section names the container element (``div`` when
omitted).  ``#document`` holds the independently derived expected tree in the
same notation the tree-builders' ``testSerializer`` uses.  A new ``#data``
section starts the next case; a line that begins with ``#`` but is not one of
the section names is a comment and is ignored.

The expected trees must be written by hand from the HTML parsing algorithm (or
from the published ``html5lib-tests`` expectations), never copied from one
backend's output: that is the point of giving the expectations an independent
source.
"""

from __future__ import absolute_import, division, unicode_literals

import io
import os
import re

from html5lib import constants


_SECTION_NAMES = frozenset([
    "data", "mode", "document-fragment", "description", "document",
])

_SECTION_RE = re.compile(r"^#([a-z-]+)\s*$")

_ELEMENT_RE = re.compile(r"^<([^<>]*)>$")
_ATTRIBUTE_RE = re.compile(r'^(?:(\w+) )?([^\s=]+)="([^"]*)"$')
_TEXT_RE = re.compile(r'^"(.*)"$', re.S)
_COMMENT_RE = re.compile(r"^<!--(.*)-->$", re.S)
_DOCTYPE_RE = re.compile(r"^<!DOCTYPE\s*(\S*)\s*>$")

_NAMESPACE_PREFIXES = {
    "html": constants.namespaces["html"],
    "svg": constants.namespaces["svg"],
    "math": constants.namespaces["mathml"],
    "xlink": constants.namespaces["xlink"],
    "xml": constants.namespaces["xml"],
    "xmlns": constants.namespaces["xmlns"],
}


class FixtureError(AssertionError):
    """Raised when a fixture file cannot be interpreted."""


def parse_fixture_file(path):
    """Return the list of cases held by ``path``."""
    with io.open(path, encoding="utf-8") as fixture_file:
        text = fixture_file.read()
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    cases = []
    sections = None
    current = None
    for line_number, line in enumerate(text.split("\n"), 1):
        match = _SECTION_RE.match(line)
        if match and match.group(1) in _SECTION_NAMES:
            name = match.group(1)
            if name == "data":
                if current is not None:
                    cases.append(_finish_case(current, path))
                current = {"__line__": line_number}
            elif current is None:
                raise FixtureError("%s:%d: section #%s outside a case"
                                   % (path, line_number, name))
            current[name] = []
            sections = name
            continue
        if line.startswith("#"):
            # A comment line ("# ...") is ignored wherever it appears.
            continue
        if current is None:
            if not line.strip():
                continue
            raise FixtureError("%s:%d: content before the first #data section"
                               % (path, line_number))
        if sections is None:  # pragma: no cover - defensive
            raise FixtureError("%s:%d: content without a section"
                               % (path, line_number))
        current[sections].append(line)

    if current is not None:
        cases.append(_finish_case(current, path))
    return cases


def _finish_case(raw, path):
    line_number = raw.pop("__line__")
    if "document" not in raw:
        raise FixtureError("%s:%d: case has no #document section"
                           % (path, line_number))

    # The single blank line that separates cases belongs to the file, not to
    # the document; anything else is part of the source exactly as written.
    data_lines = list(raw["data"])
    if data_lines and data_lines[-1] == "":
        data_lines.pop()
    data = "\n".join(data_lines)
    mode = "\n".join(raw.get("mode", [])).strip() or "document"
    if mode not in ("document", "fragment"):
        raise FixtureError("%s:%d: #mode must be document or fragment, got %r"
                           % (path, line_number, mode))
    container = "\n".join(raw.get("document-fragment", [])).strip() or "div"
    description = " ".join(line.strip() for line in raw.get("description", [])
                           if line.strip())
    if mode == "document" and "document-fragment" in raw:
        raise FixtureError("%s:%d: #document-fragment given for a document case"
                           % (path, line_number))

    expected = parse_expected(raw["document"], path, line_number, mode)
    return {
        "data": data,
        "mode": mode,
        "container": container,
        "description": description,
        "expected": expected,
        "path": path,
        "line": line_number,
    }


def _namespaced_name(token, path, line_number):
    """Resolve an element name token at the start of an expectation line.

    A bare name is an HTML element (the same convention the tree-construction
    ``.dat`` files use); a ``prefix name`` form names an element in one of the
    foreign namespaces, where the prefix must be one we know.
    """
    parts = token.split(" ")
    if len(parts) == 1:
        return constants.namespaces["html"], parts[0]
    if len(parts) != 2:
        raise FixtureError("%s:%d: cannot parse name %r"
                           % (path, line_number, token))
    prefix, local_name = parts
    if prefix not in _NAMESPACE_PREFIXES:
        raise FixtureError("%s:%d: unknown namespace prefix %r"
                           % (path, line_number, prefix))
    return _NAMESPACE_PREFIXES[prefix], local_name


def parse_expected(lines, path, line_number=0, mode="document"):
    """Turn ``|``-prefixed expectation lines into a canonical tree tuple."""
    root_children = _parse_children(lines, path, line_number)
    return (mode, root_children)


def _parse_children(lines, path, line_number):
    root = []
    # Stack entries are (indent, children_list).  ``last_element`` maps an
    # element's indent to the element node last opened at that indent, which is
    # the owner of any attribute lines that follow it at indent + 2.
    stack = [(-2, root)]
    last_element = {}

    for offset, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        if not raw.startswith("|"):
            raise FixtureError("%s:%d: expectation line lacks a leading '|': %r"
                               % (path, line_number + offset, raw))
        content = raw[1:]
        if content.startswith(" "):
            content = content[1:]
        indent = len(content) - len(content.lstrip(" "))
        token = content.strip()

        comment = _COMMENT_RE.match(token)
        doctype = _DOCTYPE_RE.match(token)
        element = _ELEMENT_RE.match(token)
        attribute = _ATTRIBUTE_RE.match(token)
        text = _TEXT_RE.match(token)

        if comment or doctype or element or text:
            while len(stack) > 1 and indent <= stack[-1][0]:
                stack.pop()
            if not stack:  # pragma: no cover - guarded by the root entry
                raise FixtureError("%s:%d: bad indentation in %r"
                                   % (path, line_number + offset, raw))

        parent = stack[-1][1]
        if comment:
            data = comment.group(1)
            if data.startswith(" "):
                data = data[1:]
            if data.endswith(" "):
                data = data[:-1]
            parent.append(("comment", data))
        elif doctype:
            parent.append(("doctype", doctype.group(1) or None, None, None))
        elif element:
            namespace, name = _namespaced_name(element.group(1), path,
                                               line_number + offset)
            node = ["element", namespace, name, [], []]
            parent.append(node)
            last_element[indent] = node
            stack.append((indent, node[4]))
        elif attribute:
            owner = last_element.get(indent - 2)
            if owner is None:
                raise FixtureError(
                    "%s:%d: attribute %r has no element at indent %d"
                    % (path, line_number + offset, token, indent - 2))
            prefix = attribute.group(1)
            namespace = _NAMESPACE_PREFIXES[prefix] if prefix else None
            owner[3].append((namespace, attribute.group(2),
                             attribute.group(3)))
        elif text:
            parent.append(("text", text.group(1)))
        else:
            raise FixtureError("%s:%d: cannot parse expectation line %r"
                               % (path, line_number + offset, raw))

    return _freeze(root)


def _freeze(children):
    result = []
    for node in children:
        if node[0] == "element":
            attributes = tuple(sorted(
                [(namespace, name, value)
                 for namespace, name, value in node[3]],
                key=lambda item: (item[0] or "", item[1], item[2])))
            result.append(("element", node[1], node[2], attributes,
                           _freeze(node[4])))
        elif node[0] == "text" and result and result[-1][0] == "text":
            result[-1] = ("text", result[-1][1] + node[1])
        else:
            result.append(tuple(node))
    return tuple(result)


def fixture_files():
    """Return every fixture file shipped with the suite."""
    directory = os.path.join(os.path.dirname(__file__), "testdata-foster")
    if not os.path.isdir(directory):
        return []
    names = sorted(name for name in os.listdir(directory)
                   if name.endswith(".dat"))
    return [os.path.join(directory, name) for name in names]
