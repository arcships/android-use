"""Keep Android pixels and hierarchy facts explicit; selectors never evaluate code."""

import re
import xml.etree.ElementTree as ET

from .models import Selector

BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def parse_tree(xml: str) -> list[dict]:
    root = ET.fromstring(xml)
    nodes = []

    def visit(element, parent=None):
        current = parent
        if element.tag == "node":
            a = element.attrib
            match = BOUNDS.fullmatch(a.get("bounds", ""))
            x1, y1, x2, y2 = map(int, match.groups()) if match else (0, 0, 0, 0)
            current = len(nodes)
            node = {
                "id": current,
                "parent": parent,
                "class_name": a.get("class", ""),
                "text": "" if a.get("password") == "true" else a.get("text", ""),
                "description": "" if a.get("password") == "true" else a.get("content-desc", ""),
                "resource_id": a.get("resource-id", ""),
                "package": a.get("package", ""),
                "rect": [x1, y1, max(0, x2 - x1), max(0, y2 - y1)],
            }
            for key in ("enabled", "clickable", "focused", "focusable", "scrollable", "password"):
                node[key] = a.get(key) == "true"
            nodes.append(node)
        for child in element:
            visit(child, current)

    visit(root)
    return nodes


def in_viewport(node: dict, size: tuple[int, int]) -> bool:
    x, y, w, h = node["rect"]
    return w > 0 and h > 0 and x < size[0] and y < size[1] and x + w > 0 and y + h > 0


def matches(nodes: list[dict], selector: Selector) -> list[dict]:
    filters = selector.model_dump(exclude_none=True, exclude={"index"})

    def match(node):
        for key, value in filters.items():
            if key.endswith("_contains"):
                if value not in node[key.removesuffix("_contains")]:
                    return False
            elif node[key] != value:
                return False
        return True

    found = [node for node in nodes if match(node)]
    if selector.index is not None:
        return found[selector.index : selector.index + 1]
    return found


def tap_point(node: dict, size: tuple[int, int]) -> list[int]:
    x, y, w, h = node["rect"]
    return [(max(0, x) + min(size[0], x + w)) // 2, (max(0, y) + min(size[1], y + h)) // 2]


def compact(nodes: list[dict], size: tuple[int, int], max_nodes: int) -> dict:
    visible = [n for n in nodes if in_viewport(n, size)]
    # Keep semantic controls/text first so large decorative layouts don't consume the budget.
    useful = [
        n
        for n in visible
        if n["text"]
        or n["description"]
        or n["clickable"]
        or n["focusable"]
        or n["scrollable"]
        or n["resource_id"]
    ]
    if not useful:
        useful = visible
    result = []
    for n in useful[:max_nodes]:
        item = {k: v for k, v in n.items() if k not in ("focusable",) and v != ""}
        item["tap"] = tap_point(n, size)
        result.append(item)
    return {
        "nodes": result,
        "total_nodes": len(nodes),
        "returned_nodes": len(result),
        "truncated": len(useful) > max_nodes,
        "coverage": "Current accessibility snapshot only; custom drawing or virtualized rows may be absent.",
    }
