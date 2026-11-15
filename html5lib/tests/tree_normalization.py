"""Backend-neutral tree normalization used by the table/formatting recovery tests.

The etree, DOM and ``lxml`` tree builders store the *same* HTML infoset in
different shapes:

* ``xml.etree.ElementTree`` keeps text in ``.text``/``.tail`` strings, uses a
  ``DOCUMENT_ROOT`` element for documents and a ``DOCUMENT_FRAGMENT`` element
  for fragments, and prefixes names with their namespace URI.
* ``xml.dom.minidom`` stores every run of text as its own ``Text`` node, uses a
  ``Document``/``DocumentFragment`` node, and splits ``prefix:local`` element
  names into a prefix and a local name.
* ``lxml.etree`` keeps ``.text``/``.tail`` like ElementTree, coerces names that
  are not legal XML names (``:`` becomes ``U0003A``), represents a document as
  an ``_ElementTree`` and a fragment as a plain list of nodes/strings.

Those differences are *representation*, not parsing results, so this module
maps each backend's native tree onto one canonical tuple tree.  It deliberately
does **not** normalize anything that carries meaning:

* element order, nesting and the element that owns a run of text,
* the namespace of every element,
* the namespace, name and value of every attribute.

Only adjacent text nodes are coalesced (DOM keeps them apart, etree/lxml join
them) and attributes are sorted (their order in the source is not significant).

Known backend *limitations* are surfaced rather than hidden: ``lxml`` rewrites
``--`` inside comments and XML-invalid names, and those rewrites raise
``DataLossWarning``; the tests treat such warnings as failures.  The same
applies to the parser's other ``DataLossWarning``s (for example the one for an
unrepresentable doctype), so legitimate-but-lossy representations cannot pass
silently.

The module also provides the serialization round-trip helpers.  ``html5lib``'s
HTML serializer cannot re-attach namespace prefixes to attributes (there is a
``TODO`` for namespace support in ``serializer.py``), so the round-trip
comparison ignores *attribute namespaces* while still checking attribute
names/values, element namespaces, and text order/ownership exactly.
"""

from __future__ import absolute_import, division, unicode_literals

import re
import warnings

from six import string_types, text_type

import xml.etree.ElementTree as ElementTree

from xml.dom import Node as DomNode

from html5lib import constants, treebuilders, treewalkers
from html5lib._ihatexml import InfosetFilter
from html5lib.serializer import HTMLSerializer

try:  # lxml is an optional dependency
    from lxml import etree as lxml_etree
except ImportError:  # pragma: no cover - exercised only without lxml
    lxml_etree = None


_EXPANDED_NAME_RE = re.compile(r"^\{([^}]*)\}(.*)$", re.DOTALL)
_INFOSET = InfosetFilter()

_NAMESPACE_PREFIXES = {
    constants.namespaces["html"]: "html",
    constants.namespaces["svg"]: "svg",
    constants.namespaces["mathml"]: "math",
    constants.namespaces["xlink"]: "xlink",
    constants.namespaces["xml"]: "xml",
    constants.namespaces["xmlns"]: "xmlns",
}

_ETREE_COMMENT_TAG = ElementTree.Comment("asd").tag
_ETREE_DOCTYPE_TAG = "<!DOCTYPE>"


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def split_expanded_name(name):
    """Split ``{uri}local`` into ``(uri, local)``, undoing XML-name coercion."""
    match = _EXPANDED_NAME_RE.match(name)
    if match:
        return match.group(1) or None, _INFOSET.fromXmlName(match.group(2))
    return None, _INFOSET.fromXmlName(name)


def _canonical_attributes(pairs):
    attributes = [(namespace or None, name, value)
                  for namespace, name, value in pairs]
    # Sort on a fully string-valued key so ``None`` namespaces compare cleanly.
    return tuple(sorted(attributes,
                        key=lambda item: (item[0] or "", item[1], item[2])))


def _merge_adjacent_text(children):
    """Collapse runs of adjacent text nodes and recurse into elements."""
    merged = []
    for node in children:
        if node[0] == "text" and merged and merged[-1][0] == "text":
            merged[-1] = ("text", merged[-1][1] + node[1])
        else:
            merged.append(node)

    result = []
    for node in merged:
        if node[0] == "element":
            result.append(("element", node[1], node[2], node[3],
                           _merge_adjacent_text(node[4])))
        else:
            result.append(node)
    return tuple(result)


# ---------------------------------------------------------------------------
# xml.etree.ElementTree
# ---------------------------------------------------------------------------

def _etree_children(element):
    children = []
    if element.text:
        children.append(("text", element.text))
    for child in element:
        tag = child.tag
        if tag == _ETREE_DOCTYPE_TAG:
            children.append(("doctype", child.text or None,
                             child.attrib.get("publicId") or None,
                             child.attrib.get("systemId") or None))
        elif tag == _ETREE_COMMENT_TAG:
            children.append(("comment", child.text or ""))
        else:
            namespace, name = split_expanded_name(tag)
            attributes = _canonical_attributes(
                split_expanded_name(key) + (value,)
                for key, value in child.attrib.items())
            children.append(("element", namespace, name, attributes,
                             _etree_children(child)))
        if child.tail:
            children.append(("text", child.tail))
    return tuple(children)


def canonicalize_etree(tree, mode):
    # ``fullTree=True`` makes both documents and fragments elements.
    return (mode, _etree_children(tree))


# ---------------------------------------------------------------------------
# xml.dom.minidom
# ---------------------------------------------------------------------------

def _dom_attributes(element):
    pairs = []
    for attribute_name in element.attributes.keys():
        attribute = element.getAttributeNode(attribute_name)
        if attribute.namespaceURI:
            pairs.append((attribute.namespaceURI,
                          attribute.localName or attribute.name,
                          attribute.value))
        else:
            pairs.append((None, attribute.name, attribute.value))
    return _canonical_attributes(pairs)


def _dom_children(node):
    children = []
    for child in node.childNodes:
        node_type = child.nodeType
        if node_type in (DomNode.TEXT_NODE, DomNode.CDATA_SECTION_NODE):
            children.append(("text", child.nodeValue))
        elif node_type == DomNode.COMMENT_NODE:
            children.append(("comment", child.nodeValue))
        elif node_type == DomNode.DOCUMENT_TYPE_NODE:
            children.append(("doctype", child.name or None,
                             child.publicId or None, child.systemId or None))
        elif node_type == DomNode.ELEMENT_NODE:
            # ``nodeName`` (not ``localName``) keeps ``prefix:local`` tag
            # names intact, matching the html5lib element name.
            children.append(("element", child.namespaceURI or None,
                             child.nodeName, _dom_attributes(child),
                             _dom_children(child)))
        elif node_type == DomNode.ENTITY_REFERENCE_NODE:
            children.extend(_dom_children(child))
        else:  # pragma: no cover - unexpected node type
            raise AssertionError("unexpected DOM node type %r" % (node_type,))
    return tuple(children)


def canonicalize_dom(tree, mode):
    if mode == "document":
        if tree.nodeType != DomNode.DOCUMENT_NODE:  # pragma: no cover
            raise AssertionError("expected a Document, got %r" % (tree,))
    return (mode, _dom_children(tree))


# ---------------------------------------------------------------------------
# lxml.etree
# ---------------------------------------------------------------------------

def _lxml_children(element):
    children = []
    if element.text:
        children.append(("text", element.text))
    for child in element:
        if not isinstance(child.tag, text_type):
            children.append(("comment", child.text or ""))
        else:
            namespace, name = split_expanded_name(child.tag)
            attributes = _canonical_attributes(
                split_expanded_name(key) + (value,)
                for key, value in child.attrib.items())
            children.append(("element", namespace, name, attributes,
                             _lxml_children(child)))
        if child.tail:
            children.append(("text", child.tail))
    return tuple(children)


def _lxml_document_children(tree):
    children = []
    info = tree.docinfo
    if info.internalDTD:
        children.append(("doctype", _INFOSET.fromXmlName(info.root_name) or None,
                         info.public_id or None, info.system_url or None))
    node = tree.getroot()
    while node.getprevious() is not None:
        node = node.getprevious()
    while node is not None:
        if not isinstance(node.tag, text_type):
            children.append(("comment", node.text or ""))
        else:
            namespace, name = split_expanded_name(node.tag)
            children.append(("element", namespace, name,
                             _canonical_attributes(
                                 split_expanded_name(key) + (value,)
                                 for key, value in node.attrib.items()),
                             _lxml_children(node)))
        if node.tail:
            children.append(("text", node.tail))
        node = node.getnext()
    return tuple(children)


def _lxml_fragment_children(tree):
    children = []
    for item in tree:
        if isinstance(item, string_types):
            children.append(("text", item))
            continue
        if not isinstance(item.tag, text_type):
            children.append(("comment", item.text or ""))
        else:
            namespace, name = split_expanded_name(item.tag)
            children.append(("element", namespace, name,
                             _canonical_attributes(
                                 split_expanded_name(key) + (value,)
                                 for key, value in item.attrib.items()),
                             _lxml_children(item)))
        if item.tail:
            children.append(("text", item.tail))
    return tuple(children)


def canonicalize_lxml(tree, mode):
    if mode == "document":
        return (mode, _lxml_document_children(tree))
    return (mode, _lxml_fragment_children(tree))


CANONICALIZERS = {
    "etree": canonicalize_etree,
    "dom": canonicalize_dom,
}
if lxml_etree is not None:
    CANONICALIZERS["lxml"] = canonicalize_lxml


# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------

BACKENDS = {
    "etree": {
        "builder": treebuilders.getTreeBuilder("etree", ElementTree,
                                               fullTree=True),
        "walker": treewalkers.getTreeWalker("etree", ElementTree),
        "marker": "ElementTree",
    },
    "dom": {
        "builder": treebuilders.getTreeBuilder("dom"),
        "walker": treewalkers.getTreeWalker("dom"),
        "marker": "DOM",
    },
}
if lxml_etree is not None:
    BACKENDS["lxml"] = {
        "builder": treebuilders.getTreeBuilder("lxml"),
        "walker": treewalkers.getTreeWalker("lxml"),
        "marker": "lxml",
    }

BACKEND_NAMES = tuple(sorted(BACKENDS))


def canonicalize(backend, tree, mode):
    if mode not in ("document", "fragment"):  # pragma: no cover
        raise ValueError("mode must be 'document' or 'fragment', got %r" % (mode,))
    return _merge_root(CANONICALIZERS[backend](tree, mode))


def _merge_root(node):
    if node[0] == "document":
        return ("document", _merge_adjacent_text(node[1]))
    if node[0] == "fragment":
        return ("fragment", _merge_adjacent_text(node[1]))
    if node[0] == "element":
        return ("element", node[1], node[2], node[3],
                _merge_adjacent_text(node[4]))
    return node  # pragma: no cover


# ---------------------------------------------------------------------------
# Parsing and serialization
# ---------------------------------------------------------------------------

def parse(backend, source, mode, container="div", namespace_html=True,
          scripting=False):
    """Parse ``source`` with ``backend``; return ``(raw_tree, data_loss)``.

    ``data_loss`` lists the ``DataLossWarning`` messages raised while parsing.
    A ``DataLossWarning`` means the backend could not represent part of the
    document, which is exactly the kind of silent content loss these tests are
    meant to surface; other warnings are not relevant here and are ignored.
    """
    from html5lib.html5parser import HTMLParser

    parser = HTMLParser(tree=BACKENDS[backend]["builder"],
                        namespaceHTMLElements=namespace_html)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if mode == "document":
            tree = parser.parse(source, scripting=scripting)
        else:
            tree = parser.parseFragment(source, container=container,
                                        scripting=scripting)
    return tree, [str(warning.message) for warning in caught
                  if issubclass(warning.category, constants.DataLossWarning)]


def serialize_tree(backend, tree):
    walker = BACKENDS[backend]["walker"](tree)
    serializer = HTMLSerializer(omit_optional_tags=False)
    return serializer.render(walker)


def relax_attribute_namespaces(node):
    """Drop attribute namespace bindings, keeping local names and values.

    Used only for the serialization round-trip: HTML serialization cannot
    re-attach prefixes to attributes.  Element namespaces and text are left
    untouched so namespace misplacement and content movement still fail.
    """
    if node[0] == "element":
        attributes = tuple(sorted(((None, name, value)
                                   for namespace, name, value in node[3]),
                                  key=lambda item: (item[1], item[2])))
        return ("element", node[1], node[2], attributes,
                tuple(relax_attribute_namespaces(child) for child in node[4]))
    if node[0] in ("document", "fragment"):
        return (node[0], tuple(relax_attribute_namespaces(child)
                               for child in node[1]))
    return node


# ---------------------------------------------------------------------------
# Content ownership
# ---------------------------------------------------------------------------

def content_paths(tree):
    """Return ``(text, tuple_of_ancestor_identities)`` for every text run.

    Ancestor identities include both the namespace URI and the local name, so
    a run of text that keeps its position but is re-parented to an element in
    the wrong namespace is reported as a difference.
    """
    paths = []

    def visit(children, ancestors):
        for node in children:
            if node[0] == "text":
                paths.append((node[1], ancestors))
            elif node[0] == "element":
                visit(node[4], ancestors + ((node[1], node[2]),))

    if tree[0] not in ("document", "fragment"):  # pragma: no cover
        raise AssertionError("expected a document or fragment root")
    visit(tree[1], ())
    return tuple(paths)


def describe_path(path):
    """Render an element identity path for failure messages."""
    parts = []
    for namespace, name in path:
        prefix = _NAMESPACE_PREFIXES.get(namespace)
        parts.append("%s %s" % (prefix, name) if prefix and namespace !=
                     constants.namespaces["html"] else name)
    return "/".join(parts) if parts else "<root>"


def describe_content_paths(tree):
    lines = []
    for text, path in content_paths(tree):
        lines.append('"%s" in %s' % (text, describe_path(path)))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Human readable rendering (fixture format and failure messages)
# ---------------------------------------------------------------------------

def render_tree(tree):
    """Render a canonical tree in the ``|`` prefixed fixture notation."""
    lines = []

    def namespaced(namespace, name, for_attribute=False):
        if namespace is None:
            return name
        if not for_attribute and namespace == constants.namespaces["html"]:
            return name
        prefix = _NAMESPACE_PREFIXES.get(namespace, namespace)
        return "%s %s" % (prefix, name)

    def visit(children, depth):
        # Indentation matches the tree-construction ``.dat`` convention: one
        # ``|`` per line followed by two spaces per nesting level.
        for node in children:
            indent = "|" + " " * (2 * depth + 1)
            if node[0] == "text":
                lines.append('%s"%s"' % (indent, node[1]))
            elif node[0] == "comment":
                lines.append("%s<!-- %s -->" % (indent, node[1]))
            elif node[0] == "doctype":
                lines.append("%s<!DOCTYPE %s>" % (indent, node[1] or ""))
            elif node[0] == "element":
                lines.append("%s<%s>" % (indent,
                                         namespaced(node[1], node[2])))
                for namespace, name, value in node[3]:
                    lines.append('%s%s="%s"' % (
                        "|" + " " * (2 * depth + 3),
                        namespaced(namespace, name, for_attribute=True),
                        value))
                visit(node[4], depth + 1)
            else:  # pragma: no cover
                raise AssertionError("cannot render node %r" % (node,))

    visit(tree[1], 0)
    return "\n".join(lines)
