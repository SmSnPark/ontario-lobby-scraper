import re
from typing import Self

from lxml import etree

Element = etree._Element


class Marker:
    def __init__(self, elem: str | Element) -> None:
        if isinstance(elem, str) or isinstance(elem, bytes):
            self.etree = etree.HTML(elem)
        elif isinstance(elem, Element):
            self.etree = elem
        else:
            raise TypeError(
                "Marker takes either an HTML string or an existing etree object!"
            )

    def txt(self, expr: str = ".") -> str:
        """Takes XPATH expression and returns text of first matching element.
        If no expr, returns text of current element. Uses the `text()` XPATH function, not lxml `Element.text` attribute.
        """
        values = self.etree.xpath(f"{expr}/text()")
        return self._clean_txt("".join(values))

    def extract(self, pat: str, expr: str = ".") -> str | tuple[str]:
        """Gets regex capture from element's text, using re.search. If `expr`, uses text of first matching element. If not, uses current elem."""
        match = re.search(pat, self.txt(expr))
        if len(match.groups()) == 1:
            return match.group(1)
        elif len(match.groups()) > 1:
            return match.groups()
        else:
            raise ValueError(f"No matches found for pat {pat}!")

    def first(self, expr: str) -> Self:
        """Returns first matching element object as Marker."""
        res = self.etree.xpath(expr)
        return Marker(res[0])

    def all(self, expr: str) -> list[Self]:
        """Returns all matching element objects as Markers."""
        return [Marker(r) for r in self.etree.xpath(expr)]

    def attr(self, attribute: str) -> str:
        """Returns value of the given attribute."""
        return self.etree.xpath(f"./@{attribute}")[0]

    def to_str(self) -> str:
        """Returns string version of self, via etree.tostring()."""
        return etree.tostring(self.etree).decode()

    @staticmethod
    def _clean_txt(s: str) -> str:
        """Standard processing to remove whitespace from element text."""
        #xpath 2.0 can apply normalize-space() across a list easily (https://stackoverflow.com/a/3359596)
        #but not xpath 1.0, which is the only version supported by LXML so we regex
        return re.sub(r"\s+", " ", s.strip())
