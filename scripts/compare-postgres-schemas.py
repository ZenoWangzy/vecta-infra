#!/usr/bin/env python3
"""Fail-closed comparison for PostgreSQL schema-dump deparser variants.

This is deliberately a lexer, not a general SQL parser.  It only rewrites the
two observed PostgreSQL 16 round-trip forms; every other token remains part of
the comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import NamedTuple, Sequence


class Token(NamedTuple):
    kind: str
    text: str
    leading: str = ""


class Comparison(NamedTuple):
    equivalent: bool
    classification: str
    source_sha256: str
    restored_sha256: str
    source_canonical_sha256: str
    restored_canonical_sha256: str
    source_token_count: int
    restored_token_count: int
    source_any_array_variants: int
    restored_any_array_variants: int
    source_and4_variants: int
    restored_and4_variants: int


MULTI_CHARACTER_OPERATORS = (
    "#>>",
    "!~*",
    "::",
    "->>",
    "#>",
    "->",
    "<=",
    ">=",
    "<>",
    "!=",
    "&&",
    "||",
    ":=",
    "=>",
    "!~",
    "~*",
)


def _is_word(token: Token, value: str) -> bool:
    return token.kind == "word" and token.text.lower() == value


def _is_symbol(token: Token, value: str) -> bool:
    return token.kind == "symbol" and token.text == value


def _read_quoted(source: str, start: int, quote: str) -> int:
    index = start + 1
    while index < len(source):
        if source[index] == "\\" and index + 1 < len(source):
            index += 2
            continue
        if source[index] != quote:
            index += 1
            continue
        if index + 1 < len(source) and source[index + 1] == quote:
            index += 2
            continue
        return index + 1
    raise ValueError("unterminated quoted token")


def _read_block_comment(source: str, start: int) -> int:
    depth = 1
    index = start + 2
    while index < len(source) - 1:
        if source[index : index + 2] == "/*":
            depth += 1
            index += 2
        elif source[index : index + 2] == "*/":
            depth -= 1
            index += 2
            if depth == 0:
                return index
        else:
            index += 1
    raise ValueError("unterminated block comment")


def _dollar_quote_end(source: str, start: int) -> int | None:
    index = start + 1
    if index < len(source) and source[index] == "$":
        tag_end = index + 1
    else:
        if index >= len(source) or not (source[index].isalpha() or source[index] == "_"):
            return None
        index += 1
        while index < len(source) and (source[index].isalnum() or source[index] == "_"):
            index += 1
        if index >= len(source) or source[index] != "$":
            return None
        tag_end = index + 1
    marker = source[start:tag_end]
    close = source.find(marker, tag_end)
    if close < 0:
        raise ValueError("unterminated dollar-quoted token")
    return close + len(marker)


def lex(source: str) -> list[Token]:
    tokens: list[Token] = []
    pending_whitespace = ""

    def add(kind: str, text: str) -> None:
        nonlocal pending_whitespace
        tokens.append(Token(kind, text, pending_whitespace))
        pending_whitespace = ""

    index = 0
    while index < len(source):
        character = source[index]
        if character.isspace():
            end = index + 1
            while end < len(source) and source[end].isspace():
                end += 1
            pending_whitespace += source[index:end]
            index = end
            continue
        if source[index : index + 2] == "--":
            end = source.find("\n", index)
            if end < 0:
                end = len(source)
            add("comment", source[index:end])
            index = end
            continue
        if source[index : index + 2] == "/*":
            end = _read_block_comment(source, index)
            add("comment", source[index:end])
            index = end
            continue
        if character == "'":
            end = _read_quoted(source, index, character)
            add("single_quoted_literal", source[index:end])
            index = end
            continue
        if character == '"':
            end = _read_quoted(source, index, character)
            add("double_quoted_identifier", source[index:end])
            index = end
            continue
        if character == "$":
            end = _dollar_quote_end(source, index)
            if end is not None:
                add("dollar_quoted", source[index:end])
                index = end
                continue
        if character.isalpha() or character == "_":
            end = index + 1
            while end < len(source) and (source[end].isalnum() or source[end] == "_"):
                end += 1
            add("word", source[index:end])
            index = end
            continue
        if character.isdigit():
            end = index + 1
            while end < len(source) and (source[end].isalnum() or source[end] in "._"):
                end += 1
            add("number", source[index:end])
            index = end
            continue
        operator = next(
            (candidate for candidate in MULTI_CHARACTER_OPERATORS if source.startswith(candidate, index)),
            None,
        )
        if operator is not None:
            add("symbol", operator)
            index += len(operator)
            continue
        add("symbol", character)
        index += 1
    if pending_whitespace:
        tokens.append(Token("whitespace", pending_whitespace))
    return tokens


def _literal_span(tokens: Sequence[Token], start: int) -> tuple[int, int] | None:
    if start >= len(tokens):
        return None
    if tokens[start].kind == "single_quoted_literal":
        return start, start + 1
    if (
        _is_word(tokens[start], "e")
        and start + 1 < len(tokens)
        and tokens[start + 1].kind == "single_quoted_literal"
        and tokens[start + 1].leading == ""
    ):
        return start, start + 2
    return None


def _any_prefix_parens(tokens: Sequence[Token], array_start: int) -> int:
    index = array_start - 1
    count = 0
    while index >= 0 and _is_symbol(tokens[index], "("):
        index -= 1
        count += 1
    return count if index >= 0 and _is_word(tokens[index], "any") else 0


def _match_any_array(tokens: Sequence[Token], start: int) -> tuple[list[Token], int, bool] | None:
    if start + 1 >= len(tokens) or not _is_word(tokens[start], "array") or not _is_symbol(tokens[start + 1], "["):
        return None
    prefix_parens = _any_prefix_parens(tokens, start)
    if not prefix_parens:
        return None

    index = start + 2
    items: list[list[Token]] = []
    separators: list[Token] = []
    casts: list[tuple[Token, Token]] = []
    while True:
        literal = _literal_span(tokens, index)
        if literal is None:
            return None
        literal_start, literal_end = literal
        if literal_end + 2 >= len(tokens):
            return None
        if not _is_symbol(tokens[literal_end], "::") or not _is_word(tokens[literal_end + 1], "character"):
            return None
        if not _is_word(tokens[literal_end + 2], "varying"):
            return None
        items.append(list(tokens[literal_start:literal_end]))
        casts.append((tokens[literal_end], tokens[literal_end + 1]))
        index = literal_end + 3
        if index >= len(tokens):
            return None
        if _is_symbol(tokens[index], ","):
            separators.append(tokens[index])
            index += 1
            continue
        if not _is_symbol(tokens[index], "]"):
            return None
        break

    cast_start = index + 1
    array_wrapper = _is_symbol(tokens[cast_start], ")") if cast_start < len(tokens) else False
    if array_wrapper:
        if prefix_parens != 2:
            return None
        cast_start += 1
    elif prefix_parens not in (1, 2):
        return None
    cast_end = cast_start + 4
    if cast_end > len(tokens):
        return None
    if not (
        _is_symbol(tokens[cast_start], "::")
        and _is_word(tokens[cast_start + 1], "text")
        and _is_symbol(tokens[cast_start + 2], "[")
        and _is_symbol(tokens[cast_start + 3], "]")
    ):
        return None

    replacement = [tokens[start], tokens[start + 1]]
    for item_index, item in enumerate(items):
        if item_index:
            replacement.append(separators[item_index - 1])
        replacement.extend(item)
        cast, cast_type = casts[item_index]
        replacement.extend(
            (
                cast,
                Token("word", "text", cast_type.leading),
            )
        )
    replacement.append(tokens[index])

    if array_wrapper:
        if cast_end >= len(tokens) or not _is_symbol(tokens[cast_end], ")"):
            return None
        return replacement, cast_end, True
    if prefix_parens == 2:
        if cast_end + 1 >= len(tokens) or not (
            _is_symbol(tokens[cast_end], ")") and _is_symbol(tokens[cast_end + 1], ")")
        ):
            return None
        return replacement, cast_end + 1, True
    if cast_end >= len(tokens) or not _is_symbol(tokens[cast_end], ")"):
        return None
    return replacement, cast_end, False


def normalize_any_arrays(tokens: Sequence[Token]) -> tuple[list[Token], int]:
    normalized: list[Token] = []
    count = 0
    index = 0
    while index < len(tokens):
        match = _match_any_array(tokens, index)
        if match is None:
            normalized.append(tokens[index])
            index += 1
            continue
        replacement, end, remove_wrapper = match
        if remove_wrapper:
            if not normalized or not _is_symbol(normalized[-1], "("):
                raise ValueError("array wrapper boundary is not structurally stable")
            normalized.pop()
        normalized.extend(replacement)
        count += 1
        index = end
    return normalized, count


OPENING_DELIMITERS = {"(": ")", "[": "]", "{": "}"}
CLOSING_DELIMITERS = {closing: opening for opening, closing in OPENING_DELIMITERS.items()}


def _validate_delimiters(tokens: Sequence[Token]) -> None:
    stack: list[str] = []
    for token in tokens:
        if token.kind != "symbol":
            continue
        if token.text in OPENING_DELIMITERS:
            stack.append(token.text)
        elif token.text in CLOSING_DELIMITERS:
            if not stack or stack[-1] != CLOSING_DELIMITERS[token.text]:
                raise ValueError("unbalanced delimiter")
            stack.pop()
    if stack:
        raise ValueError("unbalanced delimiter")


def _matching(tokens: Sequence[Token], start: int, opening: str = "(", closing: str = ")") -> int:
    if start >= len(tokens) or not _is_symbol(tokens[start], opening):
        raise ValueError("unbalanced delimiter")
    depth = 0
    for index in range(start, len(tokens)):
        if _is_symbol(tokens[index], opening):
            depth += 1
        elif _is_symbol(tokens[index], closing):
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("unbalanced delimiter")


def _strip_full_outer_groups(tokens: Sequence[Token]) -> tuple[list[Token], bool]:
    result = list(tokens)
    stripped = False
    while len(result) >= 2 and _is_symbol(result[0], "("):
        if _matching(result, 0) != len(result) - 1:
            break
        result = result[1:-1]
        stripped = True
    return result, stripped


def _split_top_level_and(tokens: Sequence[Token]) -> list[list[Token]] | None:
    parts: list[list[Token]] = [[]]
    stack: list[str] = []
    closing_for = {")": "(", "]": "[", "}": "{",
    }
    opening = set("([{")
    for token in tokens:
        if token.kind == "symbol" and token.text in opening:
            stack.append(token.text)
            parts[-1].append(token)
            continue
        if token.kind == "symbol" and token.text in closing_for:
            if not stack or stack[-1] != closing_for[token.text]:
                return None
            stack.pop()
            parts[-1].append(token)
            continue
        if not stack and _is_word(token, "or"):
            return None
        if not stack and _is_word(token, "and"):
            parts.append([])
            continue
        parts[-1].append(token)
    if stack or len(parts) != 4 or any(not part for part in parts):
        return None
    return parts


def _and4_core(tokens: Sequence[Token]) -> tuple[list[Token], bool] | None:
    core, stripped = _strip_full_outer_groups(tokens)
    parts = _split_top_level_and(core)
    return (core, stripped) if parts is not None else None


def normalize_four_item_and_groups(tokens: Sequence[Token]) -> tuple[list[Token], int]:
    normalized: list[Token] = []
    count = 0
    index = 0
    while index < len(tokens):
        if not _is_symbol(tokens[index], "("):
            normalized.append(tokens[index])
            index += 1
            continue
        end = _matching(tokens, index)
        inner, nested_count = normalize_four_item_and_groups(tokens[index + 1 : end])
        count += nested_count
        result = _and4_core(inner)
        if result is None or not result[1]:
            normalized.extend((tokens[index], *inner, tokens[end]))
        else:
            core, _ = result
            replacement = [tokens[index], *core, Token("symbol", ")", tokens[end].leading)]
            original = [tokens[index], *inner, tokens[end]]
            if replacement != original:
                count += 1
            normalized.extend(replacement)
        index = end + 1
    return normalized, count


def _canonical_tokens(source: str) -> tuple[list[Token], int, int]:
    tokens = lex(source)
    _validate_delimiters(tokens)
    tokens, array_count = normalize_any_arrays(tokens)
    tokens, and4_count = normalize_four_item_and_groups(tokens)
    return tokens, array_count, and4_count


def _token_digest(tokens: Sequence[Token]) -> str:
    payload = b"".join(
        token.leading.encode()
        + b"\0"
        + token.kind.encode()
        + b"\0"
        + token.text.encode()
        + b"\0"
        for token in tokens
    )
    return hashlib.sha256(payload).hexdigest()


def compare_bytes(source: bytes, restored: bytes) -> Comparison:
    source_sha256 = hashlib.sha256(source).hexdigest()
    restored_sha256 = hashlib.sha256(restored).hexdigest()
    source_text = source.decode("utf-8")
    restored_text = restored.decode("utf-8")
    source_tokens, source_arrays, source_and4 = _canonical_tokens(source_text)
    restored_tokens, restored_arrays, restored_and4 = _canonical_tokens(restored_text)
    source_canonical_sha256 = _token_digest(source_tokens)
    restored_canonical_sha256 = _token_digest(restored_tokens)

    if source == restored:
        classification = "EXACT"
        equivalent = True
    elif source_canonical_sha256 == restored_canonical_sha256 and max(
        source_arrays, restored_arrays, source_and4, restored_and4
    ):
        classification = "POSTGRES_DEPARSE_EQUIVALENT"
        equivalent = True
    else:
        classification = "UNSUPPORTED_SCHEMA_DIFFERENCE"
        equivalent = False
    return Comparison(
        equivalent,
        classification,
        source_sha256,
        restored_sha256,
        source_canonical_sha256,
        restored_canonical_sha256,
        len(lex(source_text)),
        len(lex(restored_text)),
        source_arrays,
        restored_arrays,
        source_and4,
        restored_and4,
    )


def report_text(result: Comparison) -> str:
    state = "equivalent" if result.equivalent else "rejected"
    return "\n".join(
        (
            f"schema_compare={state}",
            f"classification={result.classification}",
            f"source_schema_sha256={result.source_sha256}",
            f"restored_schema_sha256={result.restored_sha256}",
            f"source_canonical_sha256={result.source_canonical_sha256}",
            f"restored_canonical_sha256={result.restored_canonical_sha256}",
            f"source_token_count={result.source_token_count}",
            f"restored_token_count={result.restored_token_count}",
            f"source_any_array_variants={result.source_any_array_variants}",
            f"restored_any_array_variants={result.restored_any_array_variants}",
            f"source_four_item_and_variants={result.source_and4_variants}",
            f"restored_four_item_and_variants={result.restored_and4_variants}",
        )
    ) + "\n"


def _self_test_fixture() -> tuple[str, str]:
    source_lines: list[str] = []
    restored_lines: list[str] = []
    for index in range(49):
        array = "ARRAY['open'::character varying, 'closed'::character varying]::text[]"
        if index % 2:
            array = "ARRAY['open'::character varying, 'closed'::character varying])::text[]"
            source_lines.append(
                f"CREATE VIEW view_{index} AS SELECT status = ANY (({array});"
            )
        else:
            source_lines.append(
                f"CREATE VIEW view_{index} AS SELECT status = ANY (({array}));"
            )
        restored_lines.append(
            f"CREATE VIEW view_{index} AS SELECT status = ANY (ARRAY['open'::text, 'closed'::text]);"
        )
    source_lines.append(
        'ALTER TABLE ONLY public.demo ADD CONSTRAINT demo_four_check CHECK ((("a" IS NOT NULL) AND ("b" IS NOT NULL) AND ("c" IS NOT NULL) AND ("d" IS NOT NULL)));'
    )
    restored_lines.append(
        'ALTER TABLE ONLY public.demo ADD CONSTRAINT demo_four_check CHECK (("a" IS NOT NULL) AND ("b" IS NOT NULL) AND ("c" IS NOT NULL) AND ("d" IS NOT NULL));'
    )
    return "\n".join(source_lines) + "\n", "\n".join(restored_lines) + "\n"


def self_test() -> None:
    source, restored = _self_test_fixture()
    result = compare_bytes(source.encode(), restored.encode())
    assert result.equivalent
    assert result.classification == "POSTGRES_DEPARSE_EQUIVALENT"
    assert result.source_any_array_variants == 49
    assert result.restored_any_array_variants == 0
    assert result.source_and4_variants == 1
    assert result.restored_and4_variants == 0

    def rejects_pair(left: str, right: str) -> None:
        try:
            result = compare_bytes(left.encode(), right.encode())
        except ValueError:
            return
        assert not result.equivalent

    def rejects(candidate: str) -> None:
        rejects_pair(source, candidate)

    rejects(restored.replace("status = ANY", "status  = ANY", 1))
    rejects(restored.replace("'closed'::text", "'changed'::text", 1))
    rejects(restored.replace("ANY", "ALL", 1))
    rejects(restored.replace("'open'::text, 'closed'::text", "'closed'::text, 'open'::text", 1))
    rejects(restored.replace("'closed'::text", "'closed'::varchar", 1))
    rejects(restored.replace("'open'::text, 'closed'::text", "'open'::text", 1))
    double_source = source.replace("'open'::character varying", '"open"::character varying', 1)
    double_restored = restored.replace("'open'::text", '"open"::text', 1)
    rejects_pair(double_source, double_restored)
    dollar = "$tag$"
    dollar_source = source.replace(
        "'open'::character varying", f"{dollar}open{dollar}::character varying", 1
    )
    dollar_restored = restored.replace("'open'::text", f"{dollar}open{dollar}::text", 1)
    rejects_pair(dollar_source, dollar_restored)
    rejects(restored.replace("view_0", "renamed_view", 1))
    rejects(restored + "DROP VIEW view_0;\n")
    rejects(restored.replace("CREATE VIEW", "ALTER VIEW", 1))
    rejects(restored + "CREATE TABLE unexpected (id integer);\n")
    rejects(restored + "SET search_path = public;\n")
    for closing in (")", "]", "}"):
        malformed = f"CREATE VIEW malformed AS SELECT 1{closing};"
        rejects_pair(malformed, malformed)
    print("schema comparator self-check: ok (49 ANY-array + 1 AND variants; mutations rejected)")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?")
    parser.add_argument("restored", nargs="?")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    if args.source is None or args.restored is None:
        parser.error("source and restored schema paths are required")

    try:
        result = compare_bytes(Path(args.source).read_bytes(), Path(args.restored).read_bytes())
    except (OSError, UnicodeDecodeError, ValueError) as error:
        print(f"schema comparator rejected input: {error}", file=sys.stderr)
        return 2
    rendered = report_text(result)
    if args.report is not None:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result.equivalent else 1


if __name__ == "__main__":
    raise SystemExit(main())
