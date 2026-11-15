# -*- coding: utf-8 -*-
"""Reproducible experiments: same fragments, full-document parse vs
parseFragment(container=...). Run from the repo root with the repo venv:

    .venv/bin/python analysis/namespace_context_experiments.py

All tree dumps use html5lib's own testSerializer (shows namespaces as
"svg circle" / "math mi" prefixes). Serializations use html5lib.serializer
with omit_optional_tags=False, quote_attr_values="always".
"""
from __future__ import print_function, unicode_literals

import html5lib
from html5lib import parse, parseFragment, serialize
from html5lib.html5parser import HTMLParser
from html5lib.treebuilders import getTreeBuilder

print("html5lib from:", html5lib.__file__)

_tb = getTreeBuilder("etree")


def dump(node):
    return _tb(namespaceHTMLElements=True).testSerializer(node)


def ser(node, **opts):
    opts.setdefault("omit_optional_tags", False)
    opts.setdefault("quote_attr_values", "always")
    return serialize(node, tree="etree", **opts)


def errors_of(parser):
    return ["%s %s" % (pos, code) for (pos, code, _dv) in parser.errors]


def show(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def run_doc(frag, label="full document parse"):
    p = HTMLParser(namespaceHTMLElements=True)
    doc = p.parse(frag)
    print("\n--- %s ---" % label)
    print(dump(doc))
    print("serialized:", ser(doc))
    if p.errors:
        print("parse errors:", errors_of(p))
    return doc


def run_frag(frag, container, label=None):
    p = HTMLParser(namespaceHTMLElements=True)
    f = p.parseFragment(frag, container=container)
    label = label or "parseFragment(container=%r)" % container
    print("\n--- %s ---" % label)
    print(dump(f))
    print("serialized:", ser(f))
    if p.errors:
        print("parse errors:", errors_of(p))
    return f


# ----------------------------------------------------------------------
show("EXP 1: TABLE fragment  '<table>lead<tr><td>cell</td></tr></table>tail'")
F = "<table>lead<tr><td>cell</td></tr></table>tail"
run_doc(F)
run_frag(F, "div")
run_frag(F, "table")

show("EXP 2: TABLE INTERNALS fragment  '<tr><td>cell</td></tr>'")
F = "<tr><td>cell</td></tr>"
run_doc(F)
run_frag(F, "div")
run_frag(F, "table")

show("EXP 3: TABLE fragment with foster-parented text  '<table>hello<td>x</td></table>'")
F = "<table>hello<td>x</td></table>"
run_doc(F)
run_frag(F, "div")
run_frag(F, "table")

# ----------------------------------------------------------------------
show("EXP 4: SELECT fragment  '<select><option>one<option>two</select><p>after</p>'")
F = "<select><option>one<option>two</select><p>after</p>"
run_doc(F)
run_frag(F, "div")
run_frag(F, "select")

show("EXP 5: bare OPTION fragment  '<option>one<option>two'")
F = "<option>one<option>two"
run_doc(F)
run_frag(F, "div")
run_frag(F, "select")

show("EXP 6: SELECT inside TABLE  '<table><tr><td><select><option>a</select></td></tr></table>'")
F = "<table><tr><td><select><option>a</option></select></td></tr></table>"
run_doc(F)

show("EXP 7: TABLE markup inside SELECT  '<select><option>a</option><table><tr><td>x</td></tr></table></select>'")
F = "<select><option>a</option><table><tr><td>x</td></tr></table></select>"
run_doc(F)
run_frag(F, "select")

# ----------------------------------------------------------------------
show("EXP 8: SVG fragment with viewBox/xlink/foreignObject")
F = ('<svg viewBox="0 0 10 10"><circle xlink:href="#a"/>'
     '<foreignObject><p>html</p></foreignObject></svg>')
run_doc(F)
run_frag(F, "div")
run_frag(F, "svg")

show("EXP 9: lowercase SVG tag names  '<svg><foreignobject><p>x</p></foreignobject></svg>'")
F = "<svg><foreignobject><p>x</p></foreignobject></svg>"
run_doc(F)
run_frag(F, "svg")

show("EXP 10: MATHML fragment  '<math><mi definitionurl=\"d\">x</mi><annotation-xml encoding=\"text/html\"><p>hi</p></annotation-xml></math>'")
F = ('<math><mi definitionurl="d">x</mi>'
     '<annotation-xml encoding="text/html"><p>hi</p></annotation-xml></math>')
run_doc(F)
run_frag(F, "div")
run_frag(F, "math")

# ----------------------------------------------------------------------
show("EXP 11: attribute case in HTML vs SVG context")
run_doc('<div viewbox="h" attributename="h">t</div>')
run_doc('<svg viewbox="0 0 1 1" attributename="a"></svg>')
run_doc('<svg><a xlink:href="#x" xml:lang="en">t</a></svg>')

show("EXP 12: serializer drops attribute namespaces (round-trip loss)")
f = parseFragment('<svg><a xlink:href="#x" xml:lang="en">t</a></svg>',
                  container="div")
s = ser(f)
print("fragment serialized:", s)
f2 = parseFragment(s, container="div")
print("re-parsed tree:")
print(dump(f2))

show("EXP 13: round-trip of a table-context fragment re-parsed as div")
f = parseFragment("<tr><td>cell</td></tr>", container="table")
s = ser(f)
print("container=table fragment serialized:", s)
print("-- re-parse with container='div' --")
print(dump(parseFragment(s, container="div")))
print("-- re-parse with container='table' --")
print(dump(parseFragment(s, container="table")))

show("EXP 14: round-trip of a select-context fragment re-parsed as div")
f = parseFragment("<option>one<option>two", container="select")
s = ser(f)
print("container=select fragment serialized:", s)
print("-- re-parse with container='div' --")
print(dump(parseFragment(s, container="div")))
print("-- re-parse with container='select' --")
print(dump(parseFragment(s, container="select")))

show("EXP 15: container switches tokenizer state (RCDATA/RAWTEXT)")
run_frag("<b>not bold</b>", "title")
run_frag("<b>not bold</b>", "textarea")
run_frag("</style><b>x</b>", "style")

show("EXP 16: namespaceHTMLElements=False (backend representation switch)")
doc = html5lib.parse("<p>x</p><svg><circle/></svg>",
                     namespaceHTMLElements=False)
print(dump(doc))

show("EXP 17: full document keeps <head>/<body>; fragment has no document shell")
run_doc("<p>hello</p>")
run_frag("<p>hello</p>", "div")
