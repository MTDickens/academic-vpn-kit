import tempfile
import unittest
from pathlib import Path

from test_review import avpn
from import_google_domains import flatten


class GoogleImportTests(unittest.TestCase):
    def test_recursive_includes_attributes_exact_and_cycles(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            (data / 'google').write_text('include:child\ngoogle.com @cn\n')
            (data / 'child').write_text('include:google\nfull:ai.google.dev\ndomain:youtube.com @ads # comment\n')
            entries, sources = flatten(data)
            self.assertEqual(entries, ['full:ai.google.dev', 'google.com', 'youtube.com'])
            self.assertEqual(sources, ['child', 'google'])

    def test_unknown_syntax_and_filtered_includes_are_not_silently_dropped(self):
        for rule in ['regexp:.*', 'keyword:google', 'include:child @cn', 'include:../secret']:
            with self.subTest(rule=rule), tempfile.TemporaryDirectory() as td:
                data = Path(td)
                (data / 'google').write_text(rule)
                with self.assertRaises(ValueError):
                    flatten(data)

    def test_known_cdn_regex_requires_covering_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            pattern = r'regexp:^r+[0-9]+(---|\.)sn-(2x3|ni5|j5o)\w{5}\.googlevideo\.com$ @cn'
            (data / 'google').write_text(pattern + '\ngoogle.com\n')
            with self.assertRaises(ValueError):
                flatten(data)
            (data / 'google').write_text(pattern + '\ngooglevideo.com\n')
            self.assertEqual(flatten(data)[0], ['googlevideo.com'])
