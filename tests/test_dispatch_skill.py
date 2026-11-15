"""The dispatch skill and the lane profiles of this plugin: skills/dispatch/SKILL.md and
profiles/breadcrumbs-<lane>.config.toml are generated from breadcrumbs/skills/dispatch (tools/skills.py in the umbrella
repository), so they are not edited here."""
import os
import re
import tomllib
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "dispatch", "SKILL.md")
LANES = ("lead", "build", "cheap", "check")


class DispatchSkillTest(unittest.TestCase):
    def setUp(self):
        with open(SKILL, encoding="utf-8") as f:
            self.text = f.read()

    def test_the_frontmatter_names_the_skill_and_its_description_fits(self):
        head = re.match(r"---\nname: dispatch\ndescription: (.+)\n---\n", self.text)
        self.assertIsNotNone(head)
        assert head is not None
        self.assertTrue(0 < len(head.group(1)) <= 60, head.group(1))

    def test_it_teaches_each_lane_and_leaves_no_template_marker(self):
        for word in ("pinpoint", "scaffold", "dispatch", "fan-out", "inspect", "validate", "trim", "orchestrate"):
            self.assertIn(word, self.text)
        self.assertNotIn("<!--", self.text)
        self.assertNotIn("@@", self.text)

    def test_the_command_line_it_teaches_is_a_file_of_this_plugin(self):
        for rel in re.findall(r"\b(bin/[a-z-]+)", self.text):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, rel)), rel)
        self.assertIn("bin/", self.text)


class LaneProfileTest(unittest.TestCase):
    def test_each_lane_is_a_profile_with_the_model_and_effort_of_its_row(self):
        with open(SKILL, encoding="utf-8") as f:
            rows = {r[0].split()[0]: r for r in ([c.strip() for c in line.strip("|").split("|")]
                                                 for line in f.read().splitlines() if line.startswith("| "))}
        for lane in LANES:
            with open(os.path.join(ROOT, "profiles", "breadcrumbs-%s.config.toml" % lane), "rb") as f:
                cfg = tomllib.load(f)
            self.assertEqual(cfg, {"model": rows[lane][2], "model_reasoning_effort": rows[lane][3]})
            self.assertIn("`-p breadcrumbs-%s`" % lane, rows[lane][4])


if __name__ == "__main__":
    unittest.main()
