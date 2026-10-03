"""walk_tree base_path: a course-folder root keeps its course segment.

Regression for the 2026-10-01 runs: root_folder_id pointed at the
שדות אלקטרומגנטיים course folder itself, walk_tree dropped the root's name, and
exam PDFs whose filenames don't name the course were routed to General.
Run: .venv/bin/python -m tests.test_prepass
"""
import unittest

from core import prepass

COURSE = "שדות אלקטרומגנטיים"
TREE = {
    "root": [{"id": "exams", "name": "מבחנים", "type": "folder"}],
    "exams": [{"id": "f1", "name": "2024 קיץ מועד ב.pdf", "type": "file",
               "md5Checksum": "m1"}],
}


def lister(fid):
    return TREE.get(fid, [])


class WalkTreeBasePath(unittest.TestCase):
    def test_default_omits_root(self):
        [f] = prepass.walk_tree("root", lister)
        self.assertEqual(f["parent_path"], ["מבחנים"])

    def test_base_path_prefixes_every_file(self):
        [f] = prepass.walk_tree("root", lister, base_path=[COURSE])
        self.assertEqual(f["parent_path"], [COURSE, "מבחנים"])

    def test_root_base_path_course_root(self):
        self.assertEqual(
            prepass.root_base_path(COURSE, {COURSE: "Electromagnetic Fields"}),
            [COURSE])

    def test_root_base_path_semester_root(self):
        self.assertEqual(
            prepass.root_base_path("סמסטר א", {COURSE: "Electromagnetic Fields"}),
            [])


if __name__ == "__main__":
    unittest.main()
