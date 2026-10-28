"""Lossless block syntax for GEM resource files, implemented independently.

This is a structural parser, not a macro interpreter. Braces, macro parentheses,
quoted strings, escapes and comments are preserved so unknown contracts survive.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Iterator


class SyntaxError(ValueError):
    pass


@dataclass
class Trivia:
    raw: str
    start: int = -1
    end: int = -1

    def render(self) -> str:
        return self.raw


@dataclass
class Atom:
    value: str
    raw: str
    quoted: bool = False
    start: int = -1
    end: int = -1

    def render(self) -> str:
        return self.raw

    def set(self, value: str) -> None:
        self.value = str(value)
        self.raw = quote(self.value) if self.quoted else self.value


@dataclass
class Node:
    items: list = field(default_factory=list)
    opener: str = "{"
    start: int = -1
    end: int = -1

    @property
    def atoms(self) -> list[Atom]:
        return [item for item in self.items if isinstance(item, Atom)]

    @property
    def head(self) -> str:
        return self.atoms[0].value if self.atoms else ""

    @property
    def args(self) -> list[str]:
        return [atom.value for atom in self.atoms[1:]]

    @property
    def children(self) -> list[Node]:
        return [item for item in self.items if isinstance(item, Node)]

    def find(self, head: str) -> Node | None:
        return next((node for node in self.children if node.head.casefold() == head.casefold()), None)

    def find_all(self, head: str) -> list[Node]:
        return [node for node in self.children if node.head.casefold() == head.casefold()]

    def walk(self, head: str | None = None) -> Iterator[Node]:
        if head is None or self.head.casefold() == head.casefold():
            yield self
        for node in self.children:
            yield from node.walk(head)

    def clone(self) -> Node:
        return copy.deepcopy(self)

    def append(self, node: Node) -> None:
        self.items.extend([Trivia("\n"), node, Trivia("\n")])

    def remove(self, node: Node) -> None:
        self.items = [item for item in self.items if item is not node]

    def render(self) -> str:
        return self.opener + "".join(item.render() for item in self.items) + {"{": "}", "(": ")", "[": "]"}[self.opener]


@dataclass
class Document:
    items: list = field(default_factory=list)

    @property
    def children(self) -> list[Node]:
        return [item for item in self.items if isinstance(item, Node)]

    def find(self, head: str) -> Node | None:
        return next((node for node in self.children if node.head.casefold() == head.casefold()), None)

    def find_all(self, head: str) -> list[Node]:
        return [node for node in self.children if node.head.casefold() == head.casefold()]

    def walk(self, head: str | None = None) -> Iterator[Node]:
        for node in self.children:
            yield from node.walk(head)

    def clone(self) -> Document:
        return copy.deepcopy(self)

    def append(self, node: Node) -> None:
        self.items.extend([Trivia("\n"), node, Trivia("\n")])

    def remove(self, node: Node) -> None:
        self.items = [item for item in self.items if item is not node]

    def render(self) -> str:
        return "".join(item.render() for item in self.items)


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse(text: str | bytes, *, max_depth: int = 256, lenient: bool = False) -> Document:
    """lenient: a stray closing bracket that matches no open block is kept as
    text instead of failing. The game loads mod files with such typos; the
    tool must read them too, and rendering returns the file unchanged."""
    if isinstance(text, bytes):
        text = decode_text(text)
    length, cursor = len(text), 0

    def fail(message: str, position: int) -> None:
        lines = text[:position].replace("\r\n", "\n").replace("\r", "\n")
        line = lines.count("\n") + 1
        column = len(lines) - lines.rfind("\n")
        raise SyntaxError(f"{message} at line {line}, column {column}")

    def read_items(close: str | None, depth: int) -> list:
        nonlocal cursor
        if depth > max_depth:
            fail("Maximum block nesting exceeded", cursor)
        items = []
        while cursor < length:
            start, char = cursor, text[cursor]
            if char in "})]":
                if char != close:
                    if lenient:
                        cursor += 1
                        items.append(Trivia(char, start, cursor))
                        continue
                    fail(f"Unexpected {char!r}", cursor)
                cursor += 1
                return items
            if char.isspace() or char == "\ufeff":
                cursor += 1
                while cursor < length and (text[cursor].isspace() or text[cursor] == "\ufeff"):
                    cursor += 1
                items.append(Trivia(text[start:cursor], start, cursor))
            elif char == ";" or text.startswith("//", cursor):
                # A line ends at \n or at a lone \r: some mod files (MEC) use old
                # Mac line breaks, and a comment must not swallow the rest of them.
                ends = [end for end in (text.find("\n", cursor), text.find("\r", cursor)) if end >= 0]
                cursor = min(ends) if ends else length
                items.append(Trivia(text[start:cursor], start, cursor))
            elif text.startswith("/*", cursor):
                end = text.find("*/", cursor + 2)
                if end < 0:
                    fail("Unterminated block comment", start)
                cursor = end + 2
                items.append(Trivia(text[start:cursor], start, cursor))
            elif char in "{([":
                cursor += 1
                children = read_items({"{": "}", "(": ")", "[": "]"}[char], depth + 1)
                items.append(Node(children, char, start, cursor))
            elif char == '"':
                cursor += 1
                value = []
                while cursor < length and text[cursor] != '"':
                    if text[cursor] == "\\" and cursor + 1 < length:
                        following = text[cursor + 1]
                        # Preserve unknown escapes: GEM paths often contain Windows separators.
                        if following in ('"', "\\"):
                            value.append(following)
                        else:
                            value.extend(("\\", following))
                        cursor += 2
                    else:
                        value.append(text[cursor])
                        cursor += 1
                if cursor >= length:
                    fail("Unterminated quoted string", start)
                cursor += 1
                items.append(Atom("".join(value), text[start:cursor], True, start, cursor))
            else:
                cursor += 1
                while cursor < length and not text[cursor].isspace() and text[cursor] not in '{}()[]";':
                    if text.startswith("//", cursor) or text.startswith("/*", cursor):
                        break
                    cursor += 1
                items.append(Atom(text[start:cursor], text[start:cursor], False, start, cursor))
        if close:
            fail(f"Missing closing {close!r}", length)
        return items

    return Document(read_items(None, 0))


def decode_text(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1251")


def block(text: str) -> Node:
    nodes = parse(text).children
    if len(nodes) != 1:
        raise ValueError("Expected exactly one block")
    return nodes[0]


def bone_name(node: Node) -> str | None:
    if node.head.casefold() != "bone":
        return None
    args = node.args
    if not args:
        return None
    return args[1] if args[0].casefold() in ("revolute", "prizmatic", "prismatic") and len(args) > 1 else args[0]


def replace_atoms(node: Node | Document, mapping: dict[str, str]) -> None:
    """Replace exact token values; never search/replace comments or substring names."""
    for item in node.items:
        if isinstance(item, Atom) and item.value in mapping:
            item.set(mapping[item.value])
        elif isinstance(item, Node):
            replace_atoms(item, mapping)


def entity_root(document: Document) -> Node | None:
    """The {game_entity ...} block of a .def. Some definitions (e.g. MG
    lafettes) use a bare {...} block; the engine reads it the same way, so it
    is named game_entity in place and compiles like any other entity."""
    # Static wrecks may be plain {Entity ...} blocks (MACE is_6_xx.def);
    # props such as ammo crates are {actor ...} (inf_crate_ger.def).
    root = document.find("game_entity") or document.find("entity") or document.find("actor")
    if root is not None:
        return root
    blocks = document.children
    if len(blocks) == 1 and blocks[0].opener == "{" and not blocks[0].atoms:
        blocks[0].items[0:0] = [Atom("game_entity", "game_entity"), Trivia("\n")]
        return blocks[0]
    return None
