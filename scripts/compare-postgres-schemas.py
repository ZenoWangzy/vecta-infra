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
    index = 0
    while index < len(source):
        character = source[index]
        if character.isspace():
            index += 1
            continue
        if source[index : index + 2] == "--":
            end = source.find("\n", index)
            if end < 0:
                end = len(source)
            tokens.append(Token("comment", source[index:end]))
            index = end
            continue
        if source[index : index + 2] == "/*":
            end = _read_block_comment(source, index)
            tokens.append(Token("comment", source[index:end]))
            index = end
            continue
        if character in "'\"":
            end = _read_quoted(source, index, character)
            tokens.append(Token("quoted", source[index:end]))
            index = end
            continue
        if character == "$":
            end = _dollar_quote_end(source, index)
            if end is not None:
                tokens.append(Token("quoted", source[index:end]))
                index = end
                continue
        if character.isalpha() or character == "_":
            end = index + 1
            while end < len(source) and (source[end].isalnum() or source[end] == "_"):
                end += 1
            tokens.append(Token("word", source[index:end]))
            index = end
            continue
        if character.isdigit():
            end = index + 1
            while end < len(source) and (source[end].isalnum() or source[end] in "._"):
                end += 1
            tokens.append(Token("number", source[index:end]))
            index = end
            continue
        operator = next(
            (candidate for candidate in MULTI_CHARACTER_OPERATORS if source.startswith(candidate, index)),
            None,
        )
        if operator is not None:
            tokens.append(Token("symbol", operator))
            index += len(operator)
            continue
        tokens.append(Token("symbol", character))
        index += 1
    return tokens


def _literal_span(tokens: Sequence[Token], start: int) -> tuple[int, int] | None:
    if start >= len(tokens):
        return None
    if tokens[start].kind == "quoted":
        return start, start + 1
    if _is_word(tokens[start], "e") and start + 1 < len(tokens) and tokens[start + 1].kind == "quoted":
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
        index = literal_end + 3
        if index >= len(tokens):
            return None
        if _is_symbol(tokens[index], ","):
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
            replacement.append(Token("symbol", ","))
        replacement.extend(item)
        replacement.extend((Token("symbol", "::"), Token("word", "text")))
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


def _strip_full_outer_groups(tokens: Sequence[Token]) -> list[Token]:
    result = list(tokens)
    while len(result) >= 2 and _is_symbol(result[0], "("):
        if _matching(result, 0) != len(result) - 1:
            break
        result = result[1:-1]
    return result


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


def _and4_core(tokens: Sequence[Token]) -> list[Token] | None:
    core = _strip_full_outer_groups(tokens)
    parts = _split_top_level_and(core)
    return core if parts is not None else None


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
        core = _and4_core(inner)
        if core is None:
            normalized.extend((tokens[index], *inner, tokens[end]))
        else:
            replacement = [Token("symbol", "("), *core, Token("symbol", ")")]
            original = [tokens[index], *inner, tokens[end]]
            if replacement != original:
                count += 1
            normalized.extend(replacement)
        index = end + 1
    return normalized, count


def _canonical_tokens(source: str) -> tuple[list[Token], int, int]:
    tokens, array_count = normalize_any_arrays(lex(source))
    tokens, and4_count = normalize_four_item_and_groups(tokens)
    return tokens, array_count, and4_count


def _token_digest(tokens: Sequence[Token]) -> str:
    payload = b"".join(
        token.kind.encode() + b"\0" + token.text.encode() + b"\0" for token in tokens
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

    def rejects(candidate: str) -> None:
        assert not compare_bytes(source.encode(), candidate.encode()).equivalent

    rejects(restored.replace("'closed'::text", "'changed'::text", 1))
    rejects(restored.replace("ANY", "ALL", 1))
    rejects(restored.replace("'open'::text, 'closed'::text", "'closed'::text, 'open'::text", 1))
    rejects(restored.replace("'closed'::text", "'closed'::varchar", 1))
    rejects(restored.replace("'open'::text, 'closed'::text", "'open'::text", 1))
    rejects(restored.replace("view_0", "renamed_view", 1))
    rejects(restored + "DROP VIEW view_0;\n")
    rejects(restored.replace("CREATE VIEW", "ALTER VIEW", 1))
    rejects(restored + "CREATE TABLE unexpected (id integer);\n")
    rejects(restored + "SET search_path = public;\n")
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
