# 命名空间与上下文边界：HTML 片段在采集、解析与再序列化中的行为分析

本报告基于本仓库（html5lib，commit `fd4f032`）的实际源码与可复现实验，分析同一组
含 **table / select / SVG / MathML** 的 HTML 片段在三种上下文中的解析与再序列化差异：

- **A. 完整文档解析**：`HTMLParser().parse(frag)`（html5lib 自动补全 `html/head/body` 文档壳）
- **B. 通用片段解析**：`parseFragment(frag, container="div")`
- **C. 匹配容器片段解析**：`parseFragment(frag, container="table"|"select"|"svg"|"math"|...)`

## 复现方法

```bash
# 在仓库根目录（使用仓库自带 venv，导入的即本仓库源码）
.venv/bin/python -I analysis/namespace_context_experiments.py   # EXP 1–17
# EXP 18–22 的命令记录见 analysis/experiment-output-2.txt 头部
```

- 实验脚本：`analysis/namespace_context_experiments.py`
- 完整输出：`analysis/experiment-output.txt`、`analysis/experiment-output-2.txt`
- 树形转储使用 html5lib 自带的 `testSerializer`（`html5lib/treebuilders/etree.py:200`），
  命名空间以 `svg ` / `math ` / `xlink ` 等前缀显示；序列化使用
  `html5lib.serializer.serialize(..., omit_optional_tags=False, quote_attr_values="always")`。

---

## 1. 上下文在解析管线中起作用的四个位置（代码证据）

`container` 不是简单的"外层标签名"，它在解析器中被消费于四个位置：

1. **分词器初始状态**（`html5lib/html5parser.py:136-148`，`HTMLParser.reset`）：
   `container` 为 `title`/`textarea` 时进入 RCDATA 状态，为
   `style`/`script`/`xmp`/`iframe`/`noembed`/`noframes` 时进入 RAWTEXT 状态，
   为 `plaintext` 时进入 PLAINTEXT 状态。此时整个片段被当作纯文本，
   `<b>` 等标签不再被解析（EXP 15：`<b>not bold</b>` 在 `container="title"` 下
   序列化为 `&lt;b&gt;not bold&lt;/b&gt;`）。
   注意 `constants.py:590` 中常量名 `cdataElements` 实际存放的是规范意义上的
   RCDATA 元素（`title`、`textarea`），属于历史命名，阅读代码时易误读。

2. **初始插入模式**（`html5lib/html5parser.py:340-382`，`resetInsertionMode`）：
   片段解析时插入一个隐式 `html` 根元素后，按 `container` 查 `newModes` 表
   （`select→inSelect`、`table→inTable`、`td/th→inCell`、`tr→inRow`、
   `tbody/thead/tfoot→inTableBody`、`caption→inCaption`、`colgroup→inColumnGroup` 等），
   查不到则落入 `inBody`。**`svg`/`math` 不在表中**——这是后文 EXP 18/19 差异的根源。

3. **主循环的"当前节点命名空间"分派**（`html5lib/html5parser.py:212-226`）：
   每个 token 根据 `self.tree.openElements[-1]` 的命名空间决定走当前插入模式还是
   `inForeignContent`。规范在此处要求使用 **adjusted current node**
   （片段场景下栈底只有隐式 `html` 根时，应回看 context 元素），
   而 html5lib 直接使用栈顶元素，**没有实现 adjusted current node**
   （全文件 grep 无 `adjusted`）。后果见 §3.4。

4. **片段结果的截取**（`html5lib/treebuilders/base.py:405-409`，`getFragment`）：
   片段解析结束后只把隐式 `html` 根元素（`openElements[0]`）的子节点
   搬入 `DOCUMENT_FRAGMENT`。因此任何被"寄养"（foster parenting）到根上的
   文本/元素都会成为片段的顶层节点，且片段永远没有 `html/head/body` 壳（EXP 17）。

---

## 2. 实验片段与结果总览

| # | 片段 | 关键观察 |
|---|------|----------|
| EXP 1 | `<table>lead<tr><td>cell</td></tr></table>tail` | foster parenting；container=table 时 `<table>` 标签本身被吞 |
| EXP 2 | `<tr><td>cell</td></tr>` | 文档/div 下标签被忽略只剩文本；container=table 下补出 `tbody` |
| EXP 3 | `<table>hello<td>x</td></table>` | 文本寄养位置在三种上下文中的去向 |
| EXP 4 | `<select><option>one<option>two</select><p>after</p>` | container=select 下嵌套 select 被吞、`<p>` 被吞、文本并入 option |
| EXP 5 | `<option>one<option>two` | option 在 body 与 select 下均可解析（结果一致） |
| EXP 6/7 | select 与 table 互相嵌套 | `inSelectInTable`；select 内的 table 标签被吞、文本留在 select 内 |
| EXP 8/9 | 含 `viewBox`/`xlink:href`/`foreignObject` 的 SVG | 属性/标签大小写修正只在 foreign content 中发生 |
| EXP 10 | 含 `definitionurl`/`annotation-xml` 的 MathML | 同上；HTML 集成点 |
| EXP 11/12 | 属性大小写与命名空间 | 序列化器丢弃属性命名空间（往返损失） |
| EXP 13/14 | 片段序列化后再解析 | 必须用相同 container，否则结构丢失 |
| EXP 15 | container 切换分词器状态 | RCDATA/RAWTEXT |
| EXP 16 | `namespaceHTMLElements=False` | 后端表示开关 |
| EXP 17 | 文档壳差异 | 片段无 `html/head/body` |
| EXP 18/19 | 不含外层 `<svg>`/`<math>` 的片段 + `container="svg"/"math"` | **container 对命名空间完全无效**（html5lib 限制） |
| EXP 20 | `<svg><desc>…</desc><p>…</p><table>…` | breakout 元素把后续内容弹出 SVG |
| EXP 21 | select 内的 table 标记 | 标签被吞，文本 `"x"` 留在 select 内 |
| EXP 22 | etree vs dom 后端 | 序列化结果一致（丢前缀是序列化器行为，与后端无关） |

---

## 3. 四类差异的机理与证据

### 3.1 插入模式（insertion mode）——table

**同一片段，结构完全不同：**

`<tr><td>cell</td></tr>`（EXP 2）：

- 完整文档 / `container="div"`：`tr`、`td` 落入 `InBodyPhase.startTagMisplaced`
  （`html5parser.py:1276-1284`）被**直接忽略**，只剩文本 `cell`，
  并报 `unexpected-start-tag-ignored`。
- `container="table"`：初始模式为 `inTable`，`tr` 触发
  `InTablePhase.startTagImplyTbody`（`html5parser.py:1741-1743`）
  **隐式补出 `<tbody>`**，得到 `tbody>tr>td` 完整结构。

**foster parenting（文本位置移动）**：`<table>hello<td>x</td></table>`（EXP 3）中，
`hello` 不允许作为 table 的子文本，被 `InTablePhase.insertText`
（`html5parser.py:1714-1719`，设置 `insertFromTable=True` 后转交 inBody）与
`TreeBuilder.getTableMisnestedNodePosition`（`treebuilders/base.py:365-389`）
**移动到 table 之前**：

- 完整文档 / div：`hello` 成为 body/片段中 table 的前一个兄弟节点；
- `container="table"`：栈中根本没有 table 元素，`getTableMisnestedNodePosition`
  退化为 `fosterParent = openElements[0]`（隐式 html 根），`hello` 成为片段顶层文本，
  最终片段为 `"hello" + tbody>tr>td`。

**container=table 时 `<table>` 标签本身被吞**（EXP 1）：`InTablePhase.startTagTable`
（`html5parser.py:1745-1750`）先按"闭合当前 table"处理，而 innerHTML 模式下
`endTagTable` 走 `assert self.parser.innerHTML` 分支（`html5parser.py:1790-1793`），
token 不再重新处理——这与规范"fragment case 忽略该 token"等价，但会留下两条
parse error（含一条未命名的 `XXX-undefined-error`）。结果：片段中不再出现
`table` 元素本身，只有其内部结构。

### 3.2 插入模式——select

`<select><option>one<option>two</select><p>after</p>`（EXP 4）：

- 完整文档 / div：结构原样保留，`<p>after</p>` 是 select 的兄弟。
- `container="select"`：初始模式 `inSelect`。片段中的 `<select>` 触发
  `InSelectPhase.startTagSelect`（`html5parser.py:2305-2307`）——
  "select in select"按闭合处理；`</select>` 触发 `endTagSelect`，
  而 innerHTML 下无 select 在作用域内，走 `assert self.parser.innerHTML` 分支；
  `<p>` 被 `startTagOther` 忽略；文本 `after` 被 `processCharacters`
  **直接插入当前节点（最后一个 option）**，于是得到
  `<option>one</option><option>twoafter</option>`——**文本归属改变**。

反向嵌套（EXP 7/21）：select 内的 `<table><tr><td>` 标签全部被
`InSelectPhase` 忽略并报 `unexpected-start-tag-in-select`，但文本 `x`
**留在 select 元素内**（不被寄养到 select 外）。这与 table 的 foster parenting
形成对照：select 是"吞标签留文本"，table 是"文本搬家"。

select 在 table 单元格内（EXP 6）走 `inSelectInTable`
（`InBodyPhase.startTagSelect`，`html5parser.py:1231-1243`），行为正常。

### 3.3 属性大小写

分词器把所有标签名/属性名一律小写化；是否恢复 camelCase 取决于**解析时所处的
命名空间上下文**，由三张表完成（`html5lib/constants.py:440-523`）：

- `adjustSVGAttributes`（58 项：`viewbox→viewBox`、`attributename→attributeName`…）
- `adjustMathMLAttributes`（`definitionurl→definitionURL`）
- `adjustForeignAttributes`（`xlink:href→(xlink ns, href)`、`xml:lang→(xml ns, lang)`、
  `xmlns`→(xmlns ns, xmlns) 等 11 项）
- 标签名另有 `InForeignContentPhase.adjustSVGTagNames`
  （`html5parser.py:2428-2467`：`foreignobject→foreignObject` 等 36 项）

调用点：`InBodyPhase.startTagSvg/startTagMath`（`html5parser.py:1252-1274`）和
`InForeignContentPhase.processStartTag`（`html5parser.py:2491-2497`）。

实验证据（EXP 11）：

- `<div viewbox="h" attributename="h">` → 属性保持小写（HTML 上下文不调整）；
- `<svg viewbox="0 0 1 1" attributename="a">` → 恢复为 `viewBox`/`attributeName`；
- `<svg><a xlink:href="#x" xml:lang="en">` → 属性进入 xlink/xml 命名空间。

**这意味着同一段采集到的标记，"它出现在 SVG 内还是 HTML 内"会改变属性的大小写
与命名空间归属**；而大小写信息一旦在 HTML 上下文中丢失（如 `viewbox`），
后续无法可靠恢复——`viewbox` 既可能是原本就小写的自定义属性，也可能是
被错误上下文解析的 `viewBox`。

### 3.4 命名空间

**进入 foreign content 的唯一入口是 `<svg>`/`<math>` 开始标签本身**
（`startTagSvg`/`startTagMath` 给 token 显式赋 `token["namespace"]`）。
之后子元素通过 `InForeignContentPhase.processStartTag` 继承当前节点命名空间
（`html5parser.py:2498`），并在 `foreignObject`/`annotation-xml[encoding=text/html]`/
MathML 文本集成点处切回 HTML（`isHTMLIntegrationPoint`，`html5parser.py:172-183`；
EXP 8/10 中 `<p>` 落在 `html` 命名空间）。

**已证实的 html5lib 限制（偏离规范与浏览器）**：`container="svg"` 或
`container="math"` 而片段本身不含外层 `<svg>`/`<math>` 标签时，
container **对命名空间没有任何影响**（EXP 18/19）：

```
parseFragment('<circle viewbox="0 0 1 1"/>…', container="svg")
→ <html circle> 元素（HTML 命名空间）、viewbox 保持小写、foreignobject 不修正
```

原因（§1 第 2、3 点）：`resetInsertionMode` 的 `newModes` 无 `svg`/`math` 项，
且主循环未实现规范的 adjusted current node 规则——栈底是 HTML 命名空间的隐式
`html` 根，所有 token 都走 `inBody`。浏览器中
`svgElement.innerHTML = '<circle viewbox="…">'` 会得到 SVG 命名空间的
`circle` 和修正后的 `viewBox`。**采集系统若用 html5lib 的
`container="svg"/"math"` 模拟 `innerHTML`，会得到与浏览器不同的树。**

**breakout（EXP 20）**：`<svg><desc>…</desc><p>…` 中 `<p>` 属于
`InForeignContentPhase.breakoutElements`（`html5parser.py:2419-2426`，
含 `table`、`div`、`p`、`font[color/face/size]` 等），触发
`unexpected-html-element-in-foreign-content`，解析器**弹出整个 svg 子树**，
后续 `<p>`、`<table>` 全部落在 HTML 命名空间、成为 svg 的兄弟。序列化结果中
`</svg>` 被提前闭合——这是规范行为，但意味着"SVG 内混入 HTML 标签"会改变
其后所有内容的命名空间归属。

---

## 4. 序列化与再解析边界

### 4.1 序列化器丢弃属性命名空间（已证实，往返损失）

`html5lib/serializer.py:301-303`：

```python
for (_, attr_name), attr_value in token["data"].items():
    # TODO: Add namespace support here
    k = attr_name
```

属性键 `(namespace, localname)` 中的命名空间被**直接丢弃**。EXP 12/22
（etree 与 dom 后端结果相同，说明这是序列化器行为而非后端差异）：

```
解析  <svg><a xlink:href="#x" xml:lang="en">t</a></svg>
序列化 <svg><a href="#x" lang="en">t</a></svg>        ← xlink:/xml: 前缀丢失
再解析 → href/lang 成为无命名空间的普通属性
```

对比：`treeadapters/sax.py` 用 `constants.unadjustForeignAttributes`
（`constants.py:522-523`）把 `(ns, local)` 还原为 `xlink:href` 等限定名，
说明项目里已有正确的还原表，只是 serializer 没有使用。

**元素名不受影响**：`viewBox`、`foreignObject`、`definitionURL` 的大小写保存在
local name 里，序列化后原样输出（EXP 8-10），再解析时由同一张调整表恢复，
可以往返。

### 4.2 元素命名空间在序列化中不可见，但可由解析器重建——有例外

序列化输出不含任何命名空间标记（`<svg>` 就是字面 `<svg>`）。再解析时
`startTagSvg`/`startTagMath` 会重新进入 foreign content，因此
**元素命名空间可以往返**——前提是再解析的上下文一致。例外：

- 若第一次解析依赖了 `container`（如 `container="table"` 补出的 `tbody`），
  序列化串里并没有记录这个上下文；
- breakout 场景（EXP 20）中提前闭合的 `</svg>` 是解析的结果而非输入，
  再解析会得到同一棵树（幂等），这点反而是安全的。

### 4.3 片段序列化后再解析：container 必须随片段一起保存

EXP 13：`container="table"` 解析 `<tr><td>cell</td></tr>` →
序列化为 `<tbody><tr><td>cell</td></tr></tbody>` →
**用 `container="div"` 再解析**：`tbody/tr/td` 全部命中 `startTagMisplaced`
被忽略，只剩文本 `cell`——**结构静默丢失**。用 `container="table"` 再解析则
完全还原。

EXP 14（对照）：`container="select"` 的 `<option>one</option><option>two</option>`
用 `container="div"` 再解析结果相同，因为 `option` 在 body 中也可解析
（`InBodyPhase.startTagOpt`）。**能否跨 container 往返取决于片段内容命中的
插入模式分派，没有通用保证。**

### 4.4 文档壳与 doctype

完整文档解析总是补出 `html/head/body`（EXP 17），无 doctype 时报
`expected-doctype-but-got-start-tag`；片段解析产出 `DOCUMENT_FRAGMENT`，
无壳、无 doctype。对"正文文本提取"而言两者等价（body 内容 = 片段内容），
但对"按路径/XPath 定位"的采集逻辑不等价。

### 4.5 后端表示限制

- **etree 后端**（`treebuilders/etree.py`）：文本存于 `.text`/`.tail`，
  命名空间用 Clark 记法 `{ns}local`；`getFragment` 返回标签为
  `DOCUMENT_FRAGMENT` 的元素（`etree.py:337-338`）。`testSerializer`
  用 `constants.prefixes` 把命名空间显示为 `svg `/`xlink ` 前缀。
- **dom 后端**：`DocumentFragment` 节点。两者经 treewalker 产生的 token 流一致
  （EXP 22），**后端选择不改变解析结果，只改变 API 形态**。
- `namespaceHTMLElements=False`（EXP 16）：HTML 元素落入无命名空间
  （`<html>` 而非 `<html html>`），SVG/MathML 仍带命名空间。这是构建期开关，
  混用两种设置产出的树做比较会得到假阳性差异。

---

## 5. 差异归类：规范行为 vs 实现限制 vs 后端限制

| 现象 | 归类 | 证据 |
|------|------|------|
| table 内文本 foster parenting 到表前 | **HTML 规范行为** | `base.py:365-389`，EXP 1/3 |
| `<tr>/<td>` 在 body 中被忽略只剩文本 | **规范行为** | `html5parser.py:1276` `startTagMisplaced`，EXP 2 |
| 隐式补 `<tbody>` | **规范行为** | `html5parser.py:1741`，EXP 2 |
| container=table 时 `<table>` 标签被吞 | **规范行为**（fragment case 忽略该 token；html5lib 经由 innerHTML 分支达到同等效果，但多报未命名错误） | `html5parser.py:1745-1750, 1790-1793`，EXP 1 |
| select 吞掉 table/p 等标签、文本并入当前 option/select | **规范行为** | `html5parser.py:2277-2375`，EXP 4/7/21 |
| SVG/MathML 属性与标签名 camelCase 修正 | **规范行为** | `constants.py:440-523`，`html5parser.py:2428-2467`，EXP 8-11 |
| breakout 元素弹出 foreign content | **规范行为** | `html5parser.py:2419-2426`，EXP 20 |
| container=title/textarea/style 切换分词器状态 | **规范行为** | `html5parser.py:139-148`，EXP 15 |
| `container="svg"/"math"` 不影响命名空间（缺 adjusted current node） | **html5lib 实现限制**（偏离规范与浏览器 innerHTML 行为） | `html5parser.py:212-226, 340-382`，EXP 18/19 |
| 序列化丢失属性命名空间前缀（`xlink:href`→`href`） | **html5lib 实现限制**（代码内 TODO；`treeadapters/sax.py` 已有正确还原逻辑） | `serializer.py:301-303`，EXP 12/22 |
| 片段无 `html/head/body` 壳 | **规范行为** | `base.py:405-409`，EXP 17 |
| etree 的 `.text/.tail` 文本模型、`DOCUMENT_FRAGMENT` 标签 | **后端表示限制**（不影响 token 流） | `treebuilders/etree.py`，EXP 22 |
| `namespaceHTMLElements` 开关改变 HTML 元素命名空间表示 | **后端表示开关** | EXP 16 |

---

## 6. 哪些差异会改变网页采集结果

**会改变采集结果（结构性/文本性差异）：**

1. **用错 container（或一律用 div）解析表格/选择器内部片段**：结构被静默丢弃
   （`<tbody>` 只剩文本）或文本归属改变（`twoafter`）。这是采集场景最大的风险，
   因为它**不产生异常，只产生错误的树**。
2. **foster parenting**：table 前的"引导文本"在 DOM 中不属于 table。按
   "table 的子文本"抽取会漏掉它；按"body 文本流"抽取则顺序与源码不同
   （文本被移到表前）。这是规范行为，浏览器同样如此——采集系统应与浏览器对齐，
   而不是与源码对齐。
3. **breakout**：SVG 中混入 `p`/`table`/`div` 等会使其后内容全部弹出 SVG。
   按命名空间过滤（如"提取所有 svg 命名空间节点"）会漏掉被弹出的内容。
4. **`container="svg"/"math"` 的假象**：html5lib 中该参数不改变命名空间，
   采集方若依赖它模拟浏览器 `innerHTML`，会得到全 HTML 命名空间的树，
   且 `viewBox`/`foreignObject` 等大小写不被修正。
5. **序列化往返**：`xlink:href`→`href`、`xml:lang`→`lang` 不可逆；
   table 上下文片段用 div 再解析结构丢失。

**不改变采集结果（可安全忽略的表象差异）：**

- 文档壳（`html/head/body`）与 doctype 的有无——正文内容相同；
- etree/dom 后端选择、`DOCUMENT_FRAGMENT` 的表现形式；
- parse errors 列表本身（它是诊断信息，多数对应规范定义的恢复行为）。

---

## 7. 兼容抽取方案建议

以下建议基于 §5 的归类；标注【已证实】的是实验直接验证的行为，
标注【建议】的是拟议的采集侧改动。

1. **保存并传递 container 元数据**【建议】：片段落库时记录其来源上下文
   （至少记录最近祖先中的 `table/select/svg/math/title/textarea/style` 等
   影响插入模式或分词器状态的元素）。再解析时使用相同 `container`。
   不要把"片段 HTML 字符串"当作自包含的——它不是。
2. **正文文本提取**【建议】：以解析后树的文本内容（`.text/.tail` 或
   `itertext()`）为准，而非源码位置；这样自然兼容 foster parenting 的搬移。
   对 select 片段，注意文本可能并入最后一个 option（规范行为）。
3. **属性保真**【建议】：结构比较与去重应基于**解析后的树**（含命名空间的
   属性键），而非序列化字符串。若必须序列化存储，在序列化前用
   `html5lib.constants.unadjustForeignAttributes` 把 `(ns, local)` 键还原为
   `xlink:href` 等限定名（`treeadapters/sax.py` 已示范此用法），
   否则 `xlink:href`/`xml:lang` 会在往返中退化为无命名空间属性【已证实：EXP 12】。
4. **SVG/MathML 片段的 container 变通**【建议】：在 html5lib 修复 adjusted
   current node 之前，对已知来自 SVG/MathML 上下文的片段，手工包一层
   `<svg>`/`<math>` 再按 `container="div"` 解析，解析后取第一层子节点——
   这样命名空间、属性大小写、标签名修正全部生效（EXP 8/9 证明包裹外层标签后
   行为正确）。直接使用 `container="svg"/"math"` 是无效的【已证实：EXP 18/19】。
5. **命名空间表示统一**【建议】：采集管线固定 `namespaceHTMLElements=True`
   （默认值），避免混用表示造成假阳性差异【已证实：EXP 16】。
6. **不要做的归一化**（会掩盖真实结构错误）【建议】：
   - 不要把所有片段一律按 `container="div"` 解析后"补齐"表格标签——
     `<tr>` 在 div 下被忽略是规范行为，补标签是在猜测丢失的结构；
   - 不要忽略 parse errors 静默继续——`unexpected-start-tag-ignored`、
     `unexpected-start-tag-in-select` 等错误码是"结构已被丢弃"的唯一信号，
     应进入采集质量监控；
   - 不要用"序列化后再解析是否相等"做完整性校验——它对 table 上下文片段
     和 namespaced 属性必然误报（§4.1、§4.3）；
   - 不要把属性名一律小写化归一——SVG 的 `viewBox` 与 HTML 的 `viewbox`
     是不同属性，小写化会把规范修正的结果重新破坏。
7. **html5lib 侧的可行修复方向**【建议，非本报告实施范围】：
   - 在 `HTMLParser.mainLoop` 实现 adjusted current node（片段场景且栈深为 1
     时以 container 元素为当前节点），使 `container="svg"/"math"` 与浏览器一致；
   - 在 `HTMLSerializer.serialize` 的 StartTag 分支使用
     `unadjustForeignAttributes` 还原属性限定名，消除 §4.1 的往返损失。

---

## 附：关键源码位置索引

| 主题 | 位置 |
|------|------|
| container 消费（分词器状态/初始模式） | `html5lib/html5parser.py:114-161` |
| 插入模式重置表 | `html5lib/html5parser.py:340-382` |
| 主循环命名空间分派（缺 adjusted current node） | `html5lib/html5parser.py:212-226` |
| 属性/标签名调整表 | `html5lib/constants.py:440-523`；`html5lib/html5parser.py:2428-2467` |
| table foster parenting | `html5lib/html5parser.py:1714-1719`；`html5lib/treebuilders/base.py:350-389` |
| body 中忽略表格内部标签 | `html5lib/html5parser.py:1276-1284` |
| select 模式 | `html5lib/html5parser.py:2277-2375` |
| foreign content / breakout / 集成点 | `html5lib/html5parser.py:2416-2528`；`172-183` |
| 片段截取 | `html5lib/treebuilders/base.py:405-409` |
| 序列化器丢属性命名空间（TODO） | `html5lib/serializer.py:301-303` |
| 限定名还原表（serializer 未用） | `html5lib/constants.py:522-523`；`html5lib/treeadapters/sax.py` |
