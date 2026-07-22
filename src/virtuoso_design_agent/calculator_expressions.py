"""Conservative structural comparison for Maestro calculator expressions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .spectre_values import spectre_values_equal


class CalculatorExpressionError(ValueError):
    """Raised when an expression is outside the deliberately small grammar."""


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str


def _tokens(expression: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    while index < len(expression):
        character = expression[index]
        if character.isspace():
            index += 1
            continue
        if character == '"':
            index += 1
            value: list[str] = []
            while index < len(expression):
                character = expression[index]
                index += 1
                if character == '"':
                    tokens.append(_Token("string", "".join(value)))
                    break
                if character == "\\":
                    if index >= len(expression):
                        raise CalculatorExpressionError("truncated string escape")
                    escaped = expression[index]
                    index += 1
                    value.append(
                        {"n": "\n", "r": "\r", "t": "\t"}.get(escaped, escaped)
                    )
                else:
                    value.append(character)
            else:
                raise CalculatorExpressionError("unterminated string")
            continue
        if expression.startswith("**", index):
            tokens.append(_Token("operator", "**"))
            index += 2
            continue
        if character in "+-*/(),":
            kind = "operator" if character in "+-*/" else character
            tokens.append(_Token(kind, character))
            index += 1
            continue
        if character.isdigit() or (
            character == "."
            and index + 1 < len(expression)
            and expression[index + 1].isdigit()
        ):
            start = index
            while index < len(expression) and expression[index].isdigit():
                index += 1
            if index < len(expression) and expression[index] == ".":
                index += 1
                while index < len(expression) and expression[index].isdigit():
                    index += 1
            if index < len(expression) and expression[index] in "eE":
                exponent = index
                index += 1
                if index < len(expression) and expression[index] in "+-":
                    index += 1
                digits = index
                while index < len(expression) and expression[index].isdigit():
                    index += 1
                if digits == index:
                    index = exponent
            while index < len(expression) and expression[index].isalpha():
                index += 1
            tokens.append(_Token("number", expression[start:index]))
            continue
        if character.isalpha() or character in "_?":
            start = index
            index += 1
            while index < len(expression) and (
                expression[index].isalnum() or expression[index] in "_?$.:"
            ):
                index += 1
            tokens.append(_Token("symbol", expression[start:index]))
            continue
        raise CalculatorExpressionError(
            f"unsupported calculator expression character: {character!r}"
        )
    return tokens


class _Parser:
    def __init__(self, expression: str) -> None:
        self.tokens = _tokens(expression)
        self.index = 0

    def _peek(self) -> _Token | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _take(self) -> _Token:
        token = self._peek()
        if token is None:
            raise CalculatorExpressionError("truncated calculator expression")
        self.index += 1
        return token

    def parse(self) -> Any:
        if not self.tokens:
            raise CalculatorExpressionError("empty calculator expression")
        parsed = self._expression(0)
        if self._peek() is not None:
            raise CalculatorExpressionError("unexpected trailing calculator token")
        return parsed

    def _expression(self, minimum_precedence: int) -> Any:
        token = self._take()
        if token.kind == "operator" and token.value in {"+", "-"}:
            left: Any = ("unary", token.value, self._expression(40))
        elif token.kind == "(":
            left = self._expression(0)
            closing = self._take()
            if closing.kind != ")":
                raise CalculatorExpressionError("unterminated grouped expression")
        elif token.kind in {"number", "string"}:
            left = (token.kind, token.value)
        elif token.kind == "symbol":
            if self._peek() is not None and self._peek().kind == "(":
                self._take()
                arguments: list[Any] = []
                while self._peek() is not None and self._peek().kind != ")":
                    arguments.append(self._expression(0))
                    if self._peek() is not None and self._peek().kind == ",":
                        self._take()
                closing = self._take()
                if closing.kind != ")":
                    raise CalculatorExpressionError("unterminated function call")
                left = ("call", token.value, tuple(arguments))
            else:
                left = ("symbol", token.value)
        else:
            raise CalculatorExpressionError("invalid calculator expression operand")

        precedences = {"+": 10, "-": 10, "*": 20, "/": 20, "**": 30}
        while True:
            operator = self._peek()
            if operator is None or operator.kind != "operator":
                break
            precedence = precedences.get(operator.value)
            if precedence is None or precedence < minimum_precedence:
                break
            self._take()
            right_precedence = precedence if operator.value == "**" else precedence + 1
            right = self._expression(right_precedence)
            left = ("binary", operator.value, left, right)
        return left


def _equivalent(left: Any, right: Any) -> bool:
    if not isinstance(left, tuple) or not isinstance(right, tuple):
        return left == right
    if left[0] != right[0] or len(left) != len(right):
        return False
    if left[0] == "number":
        return spectre_values_equal(left[1], right[1])
    if left[0] == "call":
        return left[1] == right[1] and len(left[2]) == len(right[2]) and all(
            _equivalent(left_arg, right_arg)
            for left_arg, right_arg in zip(left[2], right[2], strict=True)
        )
    return all(
        _equivalent(left_value, right_value)
        for left_value, right_value in zip(left[1:], right[1:], strict=True)
    )


def calculator_expressions_equal(left: Any, right: Any) -> bool:
    """Compare supported expressions after syntax and engineering-unit normalization.

    Unsupported syntax deliberately falls back to exact string equality. The helper
    never evaluates user input and never treats reordered operations as equivalent.
    """

    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if left_text == right_text:
        return True
    try:
        return _equivalent(_Parser(left_text).parse(), _Parser(right_text).parse())
    except CalculatorExpressionError:
        return False
