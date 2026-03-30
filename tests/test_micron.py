from rngit import micron


class TestMicron:
    def test_convert_markdown(self):
        assert micron.convert_markdown("Test") == b"Test\n"
        assert micron.convert_markdown("_Test_") == b"`*Test`*\n"
        assert micron.convert_markdown("*Test*") == b"`*Test`*\n"
        assert micron.convert_markdown("__Test__") == b"`!Test`!\n"
        assert micron.convert_markdown("**Test**") == b"`!Test`!\n"
        assert micron.convert_markdown("\nTest") == b"\nTest\n"
        assert micron.convert_markdown("<em>Test`</em>") == b"<em>Test\\`</em>\n"
        assert micron.convert_markdown("`Test`") == b"`F222`BdddTest``\n"
        assert micron.convert_markdown("# Test") == b"> Test\n"
        assert micron.convert_markdown("## Test") == b">> Test\n"
        assert micron.convert_markdown("### Test") == b">>> Test\n"
        assert micron.convert_markdown("---") == b"-\n"
        assert micron.convert_markdown("___") == b"-\n"
        assert (
            micron.convert_markdown("<https://google.com>")
            == b"`_`[https://google.com]`_\n"
        )
        assert micron.convert_markdown('[foo]: /url "title"') == b""
        assert micron.convert_markdown('[link](/uri "title")') == b"`_`[link`/uri]`_\n"
        assert (
            micron.convert_markdown("![alt](/src/address)")
            == b"`_`[alt`/src/address]`_\n"
        )
        assert (
            micron.convert_markdown(""" - Test1
 - Test2
 """)
            == b"""\\- Test1
\\- Test2
"""
        )
        assert (
            micron.convert_markdown(""" * Test1
 * Test2
 """)
            == b"""* Test1
* Test2
"""
        )
        assert (
            micron.convert_markdown(""" 1. Test1
 3. Test2
 """)
            == b"""1. Test1
2. Test2
"""
        )

        assert (
            micron.convert_markdown("""# Test
Test `test` <em>Test`</em>
 - Test1
 - Test2
---
```python
import sys
```
> This is a quote

<https://google.com>

[foo]: /url "title"

[link](/uri "title")

![alt](/src/address)
""")
            == b"""> Test
Test `F222`Bdddtest`` <em>Test\\`</em>
\\- Test1
\\- Test2
-
`F222`Bddd
import sys
``
`B5d5`F222

`*This is a quote

``

`_`[https://google.com]`_


`_`[link`/uri]`_

`_`[alt`/src/address]`_
"""
        )
