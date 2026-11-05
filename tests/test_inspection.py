"""Parser coverage using captured GStreamer inspection output."""

from dataclasses import FrozenInstanceError
from fractions import Fraction
from pathlib import Path
import unittest

from gst_creator.gstreamer.parser import parse_elements
from gst_creator.gstreamer.models import (
    CapsKind, FloatRange, FractionRange, IntRange, PadAvailability,
    PadDirection, PropertyFlags, PropertyType,
)


FIXTURES = Path(__file__).parent / "fixtures"


def prefixed(name, text):
    return "\n".join(f"{name}: {line}" for line in text.splitlines())


class InspectionTests(unittest.TestCase):
    def setUp(self):
        output = "\n".join(
            prefixed(name, (FIXTURES / f"{name}.txt").read_text())
            for name in ("tee", "fakesink")
        )
        self.elements = {item.factory.name: item for item in parse_elements(output)}

    def test_factory_and_plugin_metadata_are_separate(self):
        sink = self.elements["fakesink"]
        self.assertEqual(sink.plugin.version, "1.28.2")
        self.assertEqual(sink.factory.description, "Black hole for data")
        self.assertEqual(sink.plugin.description, "GStreamer core elements")
        self.assertIn("Author", sink.factory.raw_text)
        self.assertNotIn("Filename", sink.factory.raw_text)
        self.assertIn("Filename", sink.plugin.raw_text)
        self.assertNotIn("Pad Templates:", sink.plugin.raw_text)

    def test_templates_include_request_pads_without_instances(self):
        tee = self.elements["tee"]
        self.assertEqual(len(tee.pads), 2)
        src = tee.pads[1]
        self.assertEqual(src.name_template, "src_%u")
        self.assertIs(src.direction, PadDirection.SRC)
        self.assertIs(src.availability, PadAvailability.ON_REQUEST)
        self.assertIs(src.capabilities, CapsKind.ANY)
        self.assertIn("SRC template: 'src_%u'", src.raw_text)
        self.assertIn("Availability: On request", src.raw_text)
        self.assertNotIn("SINK template:", src.raw_text)

    def test_properties_retain_types_ranges_defaults_and_choices(self):
        properties = {
            prop.name: prop for prop in self.elements["fakesink"].properties
        }
        self.assertIs(
            properties["blocksize"].type, PropertyType.UNSIGNED_INTEGER,
        )
        self.assertEqual(
            properties["blocksize"].value_range, IntRange(0, 4294967295),
        )
        self.assertEqual(properties["blocksize"].default_value, 4096)
        self.assertIsNone(properties["last-message"].default_value)
        self.assertIsNone(properties["parent"].default_value)
        self.assertEqual(properties["state-error"].choices[1].nick,
                         "null-to-ready")
        self.assertEqual(properties["state-error"].choices[1].value, "1")
        self.assertEqual(
            properties["silent"].flags,
            PropertyFlags(readable=True, writable=True),
        )
        self.assertIn("average-rate:", properties["stats"].raw_text)

    def test_property_types_ignore_specific_gstreamer_names(self):
        cases = (
            ('Enum "Example"', PropertyType.ENUM),
            ('Flags "Example"', PropertyType.FLAGS),
            ('Object of type "GstPad"', PropertyType.OBJECT),
            ('Boxed pointer of type "GstStructure"', PropertyType.BOXED),
            ("Caps (NULL)", PropertyType.CAPS),
            ("Caps NULL", PropertyType.CAPS),
            ("An unfamiliar type", PropertyType.UNKNOWN),
        )
        for kind, expected in cases:
            with self.subTest(kind=kind):
                text = (
                    "Factory Details:\nElement Properties:\n"
                    "  setting : Example\n"
                    f"    {kind}\n"
                )
                element = parse_elements(prefixed("example", text))[0]
                self.assertIs(element.properties[0].type, expected)

    def test_defaults_have_converted_python_types(self):
        cases = (
            ("Boolean", "false", False),
            ("Integer", "-42", -42),
            ("Unsigned Integer64", "18446744073709551615",
             18446744073709551615),
            ("Double", "1,25", 1.25),
            ("String", '"0,5"', "0,5"),
            ("String", '"null"', "null"),
            ("String", '""', ""),
            ("String", "null", None),
            ('Enum "Example"', '2, "second"', 2),
            ('Flags "Example"', '0x00000003, "first+second"', 3),
            ("Fraction", "30000/1001", Fraction(30000, 1001)),
            ("Caps", "(NULL)", None),
        )
        for kind, default, expected in cases:
            with self.subTest(kind=kind, default=default):
                text = (
                    "Factory Details:\nElement Properties:\n"
                    "  setting : Example\n"
                    f"    {kind}. Default: {default}\n"
                )
                element = parse_elements(prefixed("example", text))[0]
                prop = element.properties[0]
                self.assertEqual(prop.default_value, expected)
                self.assertIs(type(prop.default_value), type(expected))

    def test_property_ranges_have_converted_bounds(self):
        cases = (
            ("Integer", "-10 - 20", IntRange(-10, 20)),
            ("Unsigned Integer64", "0 - 18446744073709551615",
             IntRange(0, 18446744073709551615)),
            ("Double", "-1,5 - 2,5", FloatRange(-1.5, 2.5)),
            ("Float", "-1e-10 - 1e+10", FloatRange(-1e-10, 1e10)),
            ("Fraction", "0/1 - 30000/1001",
             FractionRange(Fraction(0), Fraction(30000, 1001))),
            ("Integer", "invalid", None),
            ("Integer", "10 - 1", None),
        )
        for kind, bounds, expected in cases:
            with self.subTest(kind=kind, bounds=bounds):
                text = (
                    "Factory Details:\nElement Properties:\n"
                    "  setting : Example\n"
                    f"    {kind}. Range: {bounds}\n"
                )
                element = parse_elements(prefixed("example", text))[0]
                self.assertEqual(element.properties[0].value_range, expected)

    def test_property_flags_are_independent_booleans(self):
        cases = (
            ("", PropertyFlags()),
            ("readable", PropertyFlags(readable=True)),
            ("writable", PropertyFlags(writable=True)),
            ("deprecated", PropertyFlags(deprecated=True)),
            ("readable, writable, deprecated", PropertyFlags(True, True, True)),
        )
        for text_flags, expected in cases:
            with self.subTest(flags=text_flags):
                text = (
                    "Factory Details:\n"
                    "Element Properties:\n"
                    "  setting : Example\n"
                    f"    flags: {text_flags}, controllable, "
                    "changeable in NULL, READY, PAUSED or PLAYING state, "
                    "0x80000000\n"
                    "    Boolean. Default: false\n"
                )
                element = parse_elements(prefixed("example", text))[0]
                prop = element.properties[0]
                self.assertEqual(prop.flags, expected)
                self.assertIn("0x80000000", prop.raw_text)

    def test_definitions_are_immutable_and_preserve_unmodeled_sections(self):
        sink = self.elements["fakesink"]
        with self.assertRaises(FrozenInstanceError):
            sink.factory.name = "changed"
        self.assertIsInstance(sink.properties, tuple)
        with self.assertRaises(FrozenInstanceError):
            sink.properties[0].flags.writable = False
        self.assertIn("Element Flags:", sink.raw_text)
        self.assertIn("GstBaseSink", sink.raw_text)
        self.assertIn("Element Signals:", sink.raw_text)
        self.assertIn('"handoff"', sink.raw_text)

    def test_caps_do_not_consume_neighbor_sections(self):
        text = '''Factory Details:
  Long-name                Example
  Klass                    Test
Pad Templates:
  SRC template: 'src_%u'
    Availability: Sometimes
    Capabilities:
      video/x-raw
                 format: { (string)RGB, (string)BGR }
                  width: [ 1, 4096 ]
    Type: GstPad
Element Actions:
  "reset" -> gboolean : g_signal_emit_by_name (element, "reset",
                                                gint arg0,
                                                gboolean * p_return_value);
Future Section:
  details we cannot interpret yet
'''
        element = parse_elements(prefixed("example", text))[0]
        self.assertEqual(
            element.pads[0].structures[0].fields,
            {"format": ["RGB", "BGR"], "width": IntRange(1, 4096)},
        )
        self.assertIn("Element Actions:", element.raw_text)
        self.assertIn("Future Section:", element.raw_text)
        self.assertEqual(element.plugin.name, "Unknown")


if __name__ == "__main__":
    unittest.main()
