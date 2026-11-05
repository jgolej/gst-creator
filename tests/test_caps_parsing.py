"""Typed capability fields from GStreamer inspection output."""

from fractions import Fraction
import unittest

from gst_creator.gstreamer.parser import (
    _parse_capabilities, _parse_caps_value,
)
from gst_creator.gstreamer.models import (
    CapsKind, FloatRange, FractionRange, IntRange,
)
from gst_creator.gstreamer.parser import parse_elements


class CapsParsingTests(unittest.TestCase):
    def test_scalar_values_respect_explicit_types(self):
        cases = (
            ("(string)123", "123"),
            ('(string)"hello, world"', "hello, world"),
            ("(int)42", 42),
            ("(uint64)0x10", 16),
            ("(double)1.25", 1.25),
            ("(boolean)false", False),
            ("(fraction)30000/1001", Fraction(30000, 1001)),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                value = _parse_caps_value(text)
                self.assertEqual(value, expected)
                self.assertIs(type(value), type(expected))

    def test_lists_and_nested_values(self):
        self.assertEqual(
            _parse_caps_value('{ (string)I420, (string)"RGB, extra" }'),
            ["I420", "RGB, extra"],
        )
        self.assertEqual(_parse_caps_value("(int){ 1, 2, 3 }"), [1, 2, 3])
        self.assertEqual(
            _parse_caps_value("{ [ 1, 10 ], { 20, 30 } }"),
            [IntRange(1, 10), [20, 30]],
        )
        self.assertEqual(_parse_caps_value("< 1, 2 >"), (1, 2))

    def test_numeric_and_fraction_ranges(self):
        self.assertEqual(_parse_caps_value("[ 1, 1920 ]"), IntRange(1, 1920))
        self.assertEqual(_parse_caps_value("[ 2, 10, 2 ]"), IntRange(2, 10, 2))
        self.assertEqual(
            _parse_caps_value("(double)[ 0, 1 ]"), FloatRange(0.0, 1.0),
        )
        self.assertEqual(
            _parse_caps_value("[ 0/1, 30000/1001 ]"),
            FractionRange(Fraction(0), Fraction(30000, 1001)),
        )

    def test_unknown_and_malformed_values_are_preserved(self):
        for text in ("(buffer)abcd", "1/0", "[ 1 ]", "{ 1, 2"):
            with self.subTest(text=text):
                self.assertEqual(_parse_caps_value(text), text)

    def test_inspection_populates_structures_and_multiline_fields(self):
        output = """Factory Details:
  Long-name  Example
Pad Templates:
  SRC template: 'src'
    Availability: Always
    Capabilities:
      video/x-raw
                 format: { (string)I420,
                           (string)RGB }
                  width: [ 1, 1920 ]
              framerate: [ 0/1, 60/1 ]
      audio/x-raw
                   rate: 48000
"""
        element = parse_elements("\n".join(
            f"example: {line}" for line in output.splitlines()
        ))[0]
        video, audio = element.pads[0].structures
        self.assertIs(element.pads[0].capabilities, CapsKind.SPECIFIC)
        self.assertEqual(video.media_type, "video/x-raw")
        self.assertEqual(video.fields["format"], ["I420", "RGB"])
        self.assertEqual(video.fields["width"], IntRange(1, 1920))
        self.assertEqual(
            video.fields["framerate"],
            FractionRange(Fraction(0), Fraction(60)),
        )
        self.assertEqual(audio.fields, {"rate": 48000})

    def test_special_caps_remain_distinct_without_original_text(self):
        for special in (CapsKind.ANY, CapsKind.EMPTY, CapsKind.UNKNOWN):
            kind, structures = _parse_capabilities([special.value.upper()])
            self.assertIs(kind, special)
            self.assertEqual(structures, ())

    def test_features_and_fields_preserve_original_text(self):
        kind, structures = _parse_capabilities(
            [
                'video/x-raw',
                '    format: (string)NV12',
                'video/x-raw(memory:DMABuf, meta:GstVideoMeta)',
                '    width: [ 64, 8192, 2 ]',
                'framerate: 30000/1001',
            ]
        )
        self.assertEqual(structures[0].media_type, "video/x-raw")
        self.assertEqual(
            structures[0].raw_text,
            'video/x-raw\nformat: (string)NV12',
        )
        self.assertEqual(
            structures[1].raw_text,
            'video/x-raw(memory:DMABuf, meta:GstVideoMeta)\n'
            'width: [ 64, 8192, 2 ]\n'
            'framerate: 30000/1001',
        )
        self.assertEqual(
            structures[1].media_type,
            "video/x-raw(memory:DMABuf, meta:GstVideoMeta)",
        )
        self.assertEqual(
            _parse_capabilities(
                "\n".join(s.raw_text for s in structures).splitlines()
            ),
            (kind, structures),
        )
