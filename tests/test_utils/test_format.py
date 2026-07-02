"""Tests for src/utils/format.py"""

import pytest
from src.utils.format import (
    HTML, _safe, bold, italic, underline, strike, code, pre, link, fmt, join,
)


# ── HTML marker class ──────────────────────────────────────────────

class TestHTML:
    def test_is_str_subclass(self):
        assert issubclass(HTML, str)

    def test_instance_is_str(self):
        h = HTML("<b>x</b>")
        assert isinstance(h, str)
        assert h == "<b>x</b>"

    def test_identity_preserved(self):
        h = HTML("<b>ok</b>")
        assert _safe(h) is h


# ── _safe ──────────────────────────────────────────────────────────

class TestSafe:
    def test_none_returns_empty(self):
        assert _safe(None) == ""

    def test_plain_text_escaped(self):
        assert _safe('<script>alert("x")</script>') == "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"

    def test_html_passthrough(self):
        h = HTML("<b>bold</b>")
        assert _safe(h) == "<b>bold</b>"

    def test_numeric_escaped(self):
        assert _safe(42) == "42"
        assert _safe(3.14) == "3.14"

    def test_ampersand_escaped(self):
        assert _safe("a&b") == "a&amp;b"

    def test_quotes_escaped(self):
        assert _safe('a"b\'c') == "a&quot;b&#x27;c"


# ── Basic Formatters ───────────────────────────────────────────────

class TestBold:
    def test_wraps_in_b_tags(self):
        assert bold("hello") == HTML("<b>hello</b>")

    def test_escapes_plain_text(self):
        assert bold("a<b") == HTML("<b>a&lt;b</b>")

    def test_html_passthrough(self):
        result = bold(HTML("<i>x</i>"))
        assert result == HTML("<b><i>x</i></b>")

    def test_none_input(self):
        assert bold(None) == HTML("<b></b>")

    def test_returns_html_type(self):
        assert isinstance(bold("x"), HTML)


class TestItalic:
    def test_wraps_in_i_tags(self):
        assert italic("hello") == HTML("<i>hello</i>")

    def test_escapes(self):
        assert italic("a&b") == HTML("<i>a&amp;b</i>")


class TestUnderline:
    def test_wraps_in_u_tags(self):
        assert underline("hello") == HTML("<u>hello</u>")

    def test_escapes(self):
        assert underline("a<b") == HTML("<u>a&lt;b</u>")


class TestStrike:
    def test_wraps_in_s_tags(self):
        assert strike("hello") == HTML("<s>hello</s>")

    def test_escapes(self):
        assert strike("a&b") == HTML("<s>a&amp;b</s>")


class TestCode:
    def test_wraps_in_code_tags(self):
        assert code("/scan") == HTML("<code>/scan</code>")

    def test_escapes(self):
        assert code("a<b") == HTML("<code>a&lt;b</code>")


class TestPre:
    def test_basic_pre(self):
        assert pre("x=1") == HTML("<pre>x=1</pre>")

    def test_pre_with_lang(self):
        result = pre("print('hi')", lang="python")
        assert result == HTML('<pre><code class="language-python">print(&#x27;hi&#x27;)</code></pre>')

    def test_pre_lang_none(self):
        assert pre("code", lang=None) == HTML("<pre>code</pre>")

    def test_pre_lang_escaped(self):
        result = pre("x", lang='a"b')
        assert 'language-a&quot;b' in result

    def test_pre_escapes_content(self):
        assert pre("a<b") == HTML("<pre>a&lt;b</pre>")


class TestLink:
    def test_basic_link(self):
        assert link("click", "https://example.com") == HTML('<a href="https://example.com">click</a>')

    def test_escapes_url(self):
        result = link("x", 'https://e.com/"onmouseover=alert(1)')
        assert "&quot;" in result
        # The double-quote is escaped, so the href attribute value can't be broken out of
        href_part = result.split('href="')[1].split('">')[0]
        assert '"' not in href_part  # no unescaped quotes

    def test_escapes_text(self):
        result = link("a<b", "https://e.com")
        assert "a&lt;b" in result

    def test_html_text_passthrough(self):
        result = link(HTML("<b>bold</b>"), "https://e.com")
        assert "<b>bold</b>" in result


# ── Composers ──────────────────────────────────────────────────────

class TestFmt:
    def test_joins_parts(self):
        assert fmt("a", "b", "c") == HTML("abc")

    def test_sep_parameter(self):
        assert fmt("a", "b", sep=", ") == HTML("a, b")

    def test_escapes_plain_parts(self):
        assert fmt("a<b", "c>d") == HTML("a&lt;bc&gt;d")

    def test_html_parts_not_double_escaped(self):
        assert fmt(bold("A"), " ", italic("B")) == HTML("<b>A</b> <i>B</i>")

    def test_none_filtered(self):
        assert fmt("a", None, "b") == HTML("ab")

    def test_numeric_parts(self):
        assert fmt(1, 2, 3) == HTML("123")

    def test_empty(self):
        assert fmt() == HTML("")

    def test_returns_html_type(self):
        assert isinstance(fmt("x"), HTML)


class TestJoin:
    def test_default_newline_sep(self):
        assert join(["a", "b", "c"]) == HTML("a\nb\nc")

    def test_custom_sep(self):
        assert join(["a", "b"], sep=" | ") == HTML("a | b")

    def test_escapes_items(self):
        assert join(["a<b", "c>d"]) == HTML("a&lt;b\nc&gt;d")

    def test_empty_list(self):
        assert join([]) == HTML("")


# ── Composition ────────────────────────────────────────────────────

class TestComposition:
    def test_bold_code(self):
        result = bold(code("x"))
        assert result == HTML("<b><code>x</code></b>")

    def test_bold_italic_with_sep(self):
        result = fmt(bold("A"), " ", italic("B"))
        assert result == HTML("<b>A</b> <i>B</i>")

    def test_nested_link_in_bold(self):
        result = bold(link("click", "https://example.com"))
        assert result == HTML('<b><a href="https://example.com">click</a></b>')

    def test_join_of_fmt(self):
        result = join([fmt(bold("A"), " x"), fmt(italic("B"), " y")], sep=" | ")
        assert result == HTML("<b>A</b> x | <i>B</i> y")

    def test_pre_in_fmt(self):
        result = fmt("Output:\n", pre("x=1"))
        assert result == HTML("Output:\n<pre>x=1</pre>")
