"""The one place that knows what a binary package archive is called.

Until this module existed, six places composed the archive filename from
their own f-string and five more took it apart with their own regular
expression, and the two halves disagreed: the builders wrote
``<name>-<version>.igos.tar.gz`` while pkm's download cache had already
moved to ``<name>-<version>-<release>.igos.tar.gz`` so that two releases of
one version could coexist on disk. A package archive that cannot name its
release cannot be told apart from the build it replaced.

These cases pin both directions and, above all, the case the naive regular
expression gets wrong: an upstream version that itself ends in ``-<digits>``.
"""
import unittest

from pkm.archive_names import (
    SUFFIX,
    archive_filename,
    parse_archive_filename,
)


class TestTheNameIsComposedFromTheFields(unittest.TestCase):
    def test_a_stated_release_is_part_of_the_name(self):
        self.assertEqual(
            archive_filename("forge", "1.0.0", 245),
            "forge-1.0.0-245.igos.tar.gz",
        )

    def test_the_release_may_arrive_as_a_string(self):
        self.assertEqual(
            archive_filename("forge", "1.0.0", "245"),
            "forge-1.0.0-245.igos.tar.gz",
        )

    def test_an_unstated_release_is_not_invented(self):
        # A recipe-less LFS core package built by the shell knows no release.
        # Writing "-1" would assert a build number nothing recorded.
        self.assertEqual(
            archive_filename("man-pages", "6.9.1", None),
            "man-pages-6.9.1.igos.tar.gz",
        )
        self.assertEqual(
            archive_filename("man-pages", "6.9.1", ""),
            "man-pages-6.9.1.igos.tar.gz",
        )

    def test_a_release_that_is_not_a_whole_number_is_refused(self):
        for bad in ("1.2", "beta", "-3", "1 "):
            with self.subTest(release=bad):
                with self.assertRaises(ValueError):
                    archive_filename("forge", "1.0.0", bad)

    def test_an_empty_name_or_version_is_refused(self):
        with self.assertRaises(ValueError):
            archive_filename("", "1.0.0", 1)
        with self.assertRaises(ValueError):
            archive_filename("forge", "", 1)


class TestTheNameIsTakenApartWithoutGuessing(unittest.TestCase):
    def test_a_file_that_is_not_an_archive_is_not_parsed(self):
        self.assertIsNone(parse_archive_filename("forge-1.0.0.tar.gz"))
        self.assertIsNone(parse_archive_filename("forge-1.0.0.igos.src.tar.gz"))

    def test_without_recipe_knowledge_no_release_is_asserted(self):
        # The stem alone cannot tell "version 7.1.2, release 13" from the
        # upstream version "7.1.2-13". Refusing to guess is the only answer
        # that cannot be wrong about bytes it has not identified.
        parsed = parse_archive_filename("imagemagick-7.1.2-13" + SUFFIX)
        self.assertEqual(parsed.name, "imagemagick")
        self.assertEqual(parsed.version, "7.1.2-13")
        self.assertIsNone(parsed.release)

    def test_recipe_knowledge_resolves_the_release(self):
        known = {"imagemagick": "7.1.2-13"}
        parsed = parse_archive_filename(
            "imagemagick-7.1.2-13-4" + SUFFIX, known=known)
        self.assertEqual(parsed.name, "imagemagick")
        self.assertEqual(parsed.version, "7.1.2-13")
        self.assertEqual(parsed.release, 4)

    def test_the_same_knowledge_reads_a_release_less_name(self):
        known = {"imagemagick": "7.1.2-13"}
        parsed = parse_archive_filename(
            "imagemagick-7.1.2-13" + SUFFIX, known=known)
        self.assertEqual(parsed.version, "7.1.2-13")
        self.assertIsNone(parsed.release)

    def test_a_hyphenated_package_name_is_not_split_inside(self):
        known = {"man-pages": "6.9.1"}
        parsed = parse_archive_filename("man-pages-6.9.1-2" + SUFFIX, known=known)
        self.assertEqual(parsed.name, "man-pages")
        self.assertEqual(parsed.version, "6.9.1")
        self.assertEqual(parsed.release, 2)

    def test_the_longest_matching_recipe_name_wins(self):
        known = {"gcc": "14.2.0", "gcc-core": "14.2.0"}
        parsed = parse_archive_filename("gcc-core-14.2.0-3" + SUFFIX, known=known)
        self.assertEqual(parsed.name, "gcc-core")
        self.assertEqual(parsed.version, "14.2.0")
        self.assertEqual(parsed.release, 3)

    def test_a_name_no_recipe_knows_still_parses_conservatively(self):
        known = {"forge": "1.0.0"}
        parsed = parse_archive_filename("stranger-2.0" + SUFFIX, known=known)
        self.assertEqual(parsed.name, "stranger")
        self.assertEqual(parsed.version, "2.0")
        self.assertIsNone(parsed.release)

    def test_a_stem_with_no_version_at_all_is_not_parsed(self):
        self.assertIsNone(parse_archive_filename("forge" + SUFFIX))


class TestTheTwoDirectionsAgree(unittest.TestCase):
    def test_what_is_composed_is_read_back(self):
        known = {"forge": "1.0.0", "man-pages": "6.9.1",
                 "imagemagick": "7.1.2-13"}
        for name, version, release in (
            ("forge", "1.0.0", 245),
            ("forge", "1.0.0", None),
            ("man-pages", "6.9.1", 2),
            ("imagemagick", "7.1.2-13", 4),
            ("imagemagick", "7.1.2-13", None),
        ):
            with self.subTest(name=name, release=release):
                fn = archive_filename(name, version, release)
                parsed = parse_archive_filename(fn, known=known)
                self.assertEqual(parsed.name, name)
                self.assertEqual(parsed.version, version)
                self.assertEqual(parsed.release, release)


if __name__ == "__main__":
    unittest.main()
