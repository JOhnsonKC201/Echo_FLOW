"""Regression: winsdk toast XML must escape metacharacters so titles/messages
containing &, <, > don't produce malformed XML that silently drops the toast."""
from __future__ import annotations

import xml.dom.minidom as minidom

from src.notify import _build_toast_xml


def test_toast_xml_with_metacharacters_is_well_formed():
    xml = _build_toast_xml("R&D <update>", "a < b && c > d")
    # Must parse as well-formed XML (raises if not).
    doc = minidom.parseString(xml)
    texts = [n.firstChild.data for n in doc.getElementsByTagName("text")]
    assert texts[0] == "R&D <update>"
    assert texts[1] == "a < b && c > d"
