"""Lossless block syntax for GEM resource files, implemented independently.

This is a structural parser, not a macro interpreter. Braces, macro parentheses,
quoted strings, escapes and comments are preserved so unknown contracts survive.
"""
from __future__ import annotations

import copy
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Iterator


class SyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class Trivia:
    raw: str
    start: int = -1
    end: int = -1

    def render(self) -> str:
        return self.raw

    def __deepcopy__(self, memo):
        return self


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
        for item in self.items:
            if isinstance(item, Atom):
                return item.value
        return ""

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
        return _clone_tree(self)

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
        return _clone_tree(self)

    def append(self, node: Node) -> None:
        self.items.extend([Trivia("\n"), node, Trivia("\n")])

    def remove(self, node: Node) -> None:
        self.items = [item for item in self.items if item is not node]

    def render(self) -> str:
        return "".join(item.render() for item in self.items)


def _clone_tree(root):
    """An independent copy of a node or document, without deepcopy's generic
    object reconstruction (the armor catalog cloned 400,000 volume nodes).
    Nodes and atoms are new objects with their own attributes; whitespace and
    comments are immutable and shared."""
    def fresh(item):
        copied = object.__new__(type(item))
        copied.__dict__.update(item.__dict__)
        return copied
    top = fresh(root)
    top.items = []
    pending = [(root.items, top.items)]
    while pending:
        source, target = pending.pop()
        for item in source:
            kind = type(item)
            if kind is Trivia:
                target.append(item)
            elif kind is Atom:
                target.append(fresh(item))
            elif isinstance(item, (Node, Document)):
                copied = fresh(item)
                copied.items = []
                target.append(copied)
                pending.append((item.items, copied.items))
            else:
                target.append(copy.deepcopy(item))
    return top


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


_CLOSERS = {"{": "}", "(": ")", "[": "]"}
# Alternatives in precedence order. Whitespace is what str.isspace() accepts
# (re's \s is the same table), plus the BOM. A bare word stops before
# whitespace, brackets, quotes, ';' and the start of a comment.
_TOKEN = re.compile(r"""
    (?P<space>[\s\ufeff]+)
  | (?P<line>(?:;|//)[^\n\r]*)
  | (?P<block>/\*)
  | (?P<open>[{(\[])
  | (?P<close>[})\]])
  | (?P<quote>")
  | (?P<atom>[^\s{}()\[\]";][^\s{}()\[\]";/]*(?:/(?![/*])[^\s{}()\[\]";/]*)*)
""", re.VERBOSE)
_QUOTED = re.compile(r'([^"\\]*(?:\\[\s\S][^"\\]*)*)"')
_ESCAPE = re.compile(r'\\([\s\S])')


def _unescape(match):
    following = match.group(1)
    return following if following in ('"', "\\") else match.group()


_PARSE_CACHE = OrderedDict()
_PARSE_BYTES = 0
# Room for the whole registration include graph (about 5 MB of text with
# MACE), read again by every new index of a session.
_PARSE_BUDGET = 16*1024*1024
_PARSE_ENTRIES = 1024
# Texts parsed once, by hash and length: a repeat earns a cached tree. A hash
# collision only caches a tree early; lookups still compare the whole text.
_PARSE_SEEN = OrderedDict()
_PARSE_SEEN_LIMIT = 8192
_PARSE_LOCK = threading.Lock()


def _copy_parsed(document):
    """Copy pristine parser trees without deepcopy's object reconstruction."""
    result = Document(); pending = [(document.items, result.items)]
    while pending:
        source, target = pending.pop()
        for item in source:
            if isinstance(item, Node):
                node = Node([], item.opener, item.start, item.end)
                target.append(node); pending.append((item.items, node.items))
            elif isinstance(item, Atom):
                target.append(Atom(item.value, item.raw, item.quoted, item.start, item.end))
            else:
                target.append(item)  # Whitespace and comments are immutable.
    return result


def parse(text: str | bytes, *, max_depth: int = 256, lenient: bool = False) -> Document:
    """lenient: a stray closing bracket that matches no open block is kept as
    text instead of failing. The game loads mod files with such typos; the
    tool must read them too, and rendering returns the file unchanged."""
    if isinstance(text, bytes):
        text = decode_text(text)
    key = (text, max_depth, lenient) if len(text) <= 256*1024 else None
    if key is not None:
        with _PARSE_LOCK:
            cached = _PARSE_CACHE.get(key)
            if cached is not None: _PARSE_CACHE.move_to_end(key)
        if cached is not None: return _copy_parsed(cached)
    length = len(text)

    def fail(message: str, position: int) -> None:
        lines = text[:position].replace("\r\n", "\n").replace("\r", "\n")
        line = lines.count("\n") + 1
        column = len(lines) - lines.rfind("\n")
        raise SyntaxError(f"{message} at line {line}, column {column}")

    # One token per step; the regex engine walks the characters. Open blocks
    # are a stack (items, opener, start, expected closer).
    items, stack, cursor = [], [], 0
    match = _TOKEN.match
    while cursor < length:
        token = match(text, cursor)
        kind, start, cursor = token.lastgroup, cursor, token.end()
        if kind == "space" or kind == "line":
            # A line comment ends at \n or at a lone \r: some mod files (MEC)
            # use old Mac line breaks; it must not swallow the rest of them.
            items.append(Trivia(token.group(), start, cursor))
        elif kind == "atom":
            raw = token.group()
            items.append(Atom(raw, raw, False, start, cursor))
        elif kind == "quote":
            body = _QUOTED.match(text, cursor)
            if body is None:
                fail("Unterminated quoted string", start)
            cursor = body.end()
            value = body.group(1)
            if "\\" in value:
                # Preserve unknown escapes: GEM paths often contain Windows separators.
                value = _ESCAPE.sub(_unescape, value)
            items.append(Atom(value, text[start:cursor], True, start, cursor))
        elif kind == "open":
            if len(stack) + 1 > max_depth:
                fail("Maximum block nesting exceeded", cursor)
            char = token.group()
            stack.append((items, char, start, _CLOSERS[char]))
            items = []
        elif kind == "close":
            char = token.group()
            if not stack or char != stack[-1][3]:
                if lenient:
                    items.append(Trivia(char, start, cursor))
                    continue
                fail(f"Unexpected {char!r}", start)
            parent, opener, opened, _ = stack.pop()
            parent.append(Node(items, opener, opened, cursor))
            items = parent
        else:  # block comment
            end = text.find("*/", cursor)
            if end < 0:
                fail("Unterminated block comment", start)
            cursor = end + 2
            items.append(Trivia(text[start:cursor], start, cursor))
    if stack:
        fail(f"Missing closing {stack[-1][3]!r}", length)

    result = Document(items)
    if key is not None:
        global _PARSE_BYTES
        # Most files are read once. Keep a pristine copy (it costs a third of
        # the parse) only when the same text comes back.
        seen = (hash(text), len(text), max_depth, lenient)  # never holds the text
        with _PARSE_LOCK:
            repeated = _PARSE_SEEN.pop(seen, False)
            if not repeated:
                _PARSE_SEEN[seen] = True
                while len(_PARSE_SEEN) > _PARSE_SEEN_LIMIT: _PARSE_SEEN.popitem(last=False)
        if repeated:
            template = _copy_parsed(result)
            with _PARSE_LOCK:
                if key not in _PARSE_CACHE:
                    _PARSE_CACHE[key] = template; _PARSE_BYTES += 2*len(text)
                    while _PARSE_BYTES > _PARSE_BUDGET or len(_PARSE_CACHE) > _PARSE_ENTRIES:
                        old, _ = _PARSE_CACHE.popitem(last=False); _PARSE_BYTES -= 2*len(old[0])
    return result


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
