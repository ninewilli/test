import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gui


class GuiConfigTest(unittest.TestCase):
    def test_build_command_contains_selected_options(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            answers = root / "answers.json"
            driver = root / "chromedriver.exe"
            answers.write_text("{}", encoding="utf-8")
            driver.touch()
            config = gui.LaunchConfig(
                url="https://example.com/course?id=1",
                profile_dir=str(root / "profile"),
                answers_path=str(answers),
                driver_path=str(driver),
                answer_wait=45,
                force=True,
                submit_answers=True,
            )

            command = gui.build_command(config)

            self.assertEqual(command[:2], [sys.executable, "-u"])
            self.assertIn("--profile-dir", command)
            self.assertIn("--driver", command)
            self.assertIn("--force", command)
            self.assertIn("--submit-answers", command)
            self.assertEqual(command[command.index("--answer-wait") + 1], "45")

    def test_validation_rejects_invalid_inputs(self):
        config = gui.LaunchConfig(
            url="not-a-url",
            profile_dir="",
            answers_path="missing.json",
            driver_path="missing.exe",
            answer_wait=0,
        )

        self.assertEqual(len(gui.validate_config(config)), 4)

    def test_validation_accepts_minimal_valid_config(self):
        with tempfile.NamedTemporaryFile(suffix=".json") as answers:
            config = gui.LaunchConfig(
                url="https://mooc.example.edu/course",
                profile_dir="",
                answers_path=answers.name,
            )
            self.assertEqual(gui.validate_config(config), [])

    def test_non_frozen_command_uses_python_main(self):
        config = gui.LaunchConfig(
            url="https://mooc.example.edu/course",
            profile_dir="",
            answers_path=str(Path(gui.__file__).with_name("answers.json")),
        )

        command = gui.build_command(config)

        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], "-u")
        self.assertTrue(command[2].endswith("main.py"))


if __name__ == "__main__":
    unittest.main()
