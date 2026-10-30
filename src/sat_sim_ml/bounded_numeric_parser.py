"""LMFE numeric-bound gate. Never edits emitted tokens or values.

The serving environment pins LMFE. Its stock number state ignores numeric
bounds, so this adapter carries the active property/item schema into the
number state and refuses termination until that value satisfies its bounds.
Full JSON Schema validation remains mandatory after generation.
"""
from copy import copy
from decimal import Decimal, InvalidOperation
import math
import re

from lmformatenforcer.characterlevelparser import CharacterLevelParser, UnionParser, ForceStopParser
from lmformatenforcer.jsonschemaparser import (
    JsonSchemaParser, NumberParsingState, ObjectParsingState, ListParsingState,
)


def numeric_variants(schema):
    if schema.type in ('number', 'integer'):
        return [schema]
    return [item for variant in (schema.anyOf or []) for item in numeric_variants(variant)]


class BoundedNumber(NumberParsingState):
    def _clone(self):
        return copy(self)

    def can_end(self):
        if not super().can_end():
            return False
        try:
            value = Decimal(self.parsed_string.strip())
            return self._value_allowed(value)
        except (ValueError, InvalidOperation, OverflowError):
            return False

    def _value_allowed(self, value):
        try:
            if not math.isfinite(float(value)):
                return False
            for schema in self.bound_schemas:
                if schema.type == 'integer' and value != value.to_integral_value():
                    continue
                if any(limit is not None and bad(value, Decimal(str(limit))) for limit, bad in [
                    (schema.minimum, lambda v, b: v < b),
                    (schema.maximum, lambda v, b: v > b),
                    (schema.exclusiveMinimum, lambda v, b: v <= b),
                    (schema.exclusiveMaximum, lambda v, b: v >= b),
                ]):
                    continue
                if schema.multipleOf is not None and value % Decimal(str(schema.multipleOf)) != 0:
                    continue
                return True
            return False
        except (ValueError, InvalidOperation, OverflowError):
            return False

    def get_allowed_characters(self):
        chars = super().get_allowed_characters()
        text = self.parsed_string.strip()
        if not text and all(s.minimum is not None and Decimal(str(s.minimum)) >= 0 for s in self.bound_schemas):
            chars = chars.replace('-', '')
        if not text and all(s.maximum is not None and Decimal(str(s.maximum)) < 0 for s in self.bound_schemas):
            chars = ''.join(c for c in chars if not c.isdigit())
        parts = re.split('[eE]', text, maxsplit=1)
        if len(parts) == 2:
            mantissa, exponent = parts
            suffixes = self._exponent_suffixes(mantissa)
            following = {suffix[len(exponent)] for suffix in suffixes
                         if suffix.startswith(exponent) and len(suffix) > len(exponent)}
            chars = ''.join(c for c in chars if c in following or c.isspace() and self.can_end())
        elif text and not self.can_end() and sum(c.isdigit() for c in text) >= 17:
            # The simulator consumes binary64 numbers. Bound invalid mantissa
            # growth and let the model choose a legal scientific exponent;
            # otherwise a rejected value can append zeros until its token budget.
            try:
                if Decimal(text) != 0:
                    chars = ''.join(c for c in chars if c in 'eE') if self._exponent_suffixes(text) else ''
                else:
                    chars = chars.replace('0', '')
            except InvalidOperation:
                pass
        if self.parsed_string.strip() and not self.can_end():
            chars = ''.join(c for c in chars if not c.isspace())
        return chars

    def _exponent_suffixes(self, mantissa):
        if getattr(self, '_suffix_mantissa', None) != mantissa:
            value = Decimal(mantissa)
            suffixes = []
            # Canonical exponents spanning binary64's finite decimal range.
            # This constrains representation, not the decoded answer's value.
            for exponent in range(-324, 309):
                if self._value_allowed(value.scaleb(exponent)):
                    suffixes.append(str(exponent))
                    if exponent >= 0:
                        suffixes.append('+' + str(exponent))
            self._suffix_mantissa, self._suffixes = mantissa, suffixes
        return self._suffixes


def bind_numbers(parser):
    if isinstance(parser, UnionParser):
        return UnionParser([bind_numbers(p) for p in parser.parsers])
    if not isinstance(parser, JsonSchemaParser):
        return parser
    parser = copy(parser)
    parser.object_stack = parser.object_stack[:]
    for i, item in enumerate(parser.object_stack):
        if isinstance(item, ListParsingState) and item.max_items == 0 and item.seen_list_opener and not item.seen_list_closer:
            # Stock LMFE pushes an item parser after '[' even for maxItems=0.
            # Keep only whitespace and the parent's closing bracket available.
            parser.object_stack = parser.object_stack[:i + 1] + [ForceStopParser(allow_whitespace=True)]
            break
    for i, item in enumerate(parser.object_stack):
        if not isinstance(item, NumberParsingState) or isinstance(item, BoundedNumber):
            continue
        schema = parser.context.model_class
        for parent in reversed(parser.object_stack[:i]):
            if isinstance(parent, ObjectParsingState):
                schema = (parent.schema_object.properties or {}).get(parent.current_key)
                if schema is None:
                    schema = parent.schema_object.additionalProperties
                break
            if isinstance(parent, ListParsingState):
                schema = parent.list_member_type
                break
        variants = numeric_variants(schema) if hasattr(schema, 'type') else []
        if variants:
            bounded = BoundedNumber(item.root, item.allow_floating_point)
            bounded.__dict__.update(item.__dict__)
            bounded.bound_schemas = variants
            parser.object_stack[i] = bounded
    return parser


class BoundedJsonParser(CharacterLevelParser):
    def __init__(self, delegate):
        self.delegate = bind_numbers(delegate)

    @property
    def config(self):
        return self.delegate.config

    @config.setter
    def config(self, value):
        self.delegate.config = value
        # LMFE child states retain their original root. Propagate the actual
        # tokenizer alphabet there as well, including roots held by union arms.
        def update(parser):
            if isinstance(parser, UnionParser):
                for child in parser.parsers:
                    update(child)
            elif isinstance(parser, JsonSchemaParser):
                parser.config = value
                for item in parser.object_stack:
                    update(item)
            elif hasattr(parser, 'root'):
                parser.root.config = value
        update(self.delegate)

    def add_character(self, character):
        return BoundedJsonParser(self.delegate.add_character(character))

    def get_allowed_characters(self):
        return self.delegate.get_allowed_characters()

    def can_end(self):
        return self.delegate.can_end()

    def shortcut_key(self):
        return self.delegate.shortcut_key()
