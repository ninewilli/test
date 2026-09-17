import unittest
import urllib.parse
import sys
import tempfile
from unittest.mock import Mock, patch
from pathlib import Path

from selenium import webdriver

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from browser import create_chrome_driver


class ChapterMappingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile_directory = tempfile.TemporaryDirectory()
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument(f"--user-data-dir={cls.profile_directory.name}")
        cls.driver = create_chrome_driver(options)

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
        cls.profile_directory.cleanup()

    def test_old_ui_maps_progress_marker_to_its_own_chapter(self):
        html = """
            <div class="onetoone">
                <h4 id="cur1">
                    <span class="roundpointStudent blue"></span>
                    <a title="done">done</a>
                </h4>
                <h5 id="quiz1"><a title="章节测验">quiz</a></h5>
                <h4 id="cur9">
                    <span class="roundpointStudent orange01"></span>
                    <a title="chapter9">chapter9</a>
                </h4>
                <h5 id="quiz9">
                    <span class="roundpointStudent orange01"></span>
                    <a title="章节测验">quiz</a>
                </h5>
            </div>
        """
        self.driver.get(
            "data:text/html;charset=utf-8," + urllib.parse.quote(html)
        )

        self.assertEqual(main.scan_progress(self.driver), ["quiz1", "cur9", "quiz9"])

    def test_new_ui_keeps_unfinished_quizzes_in_catalog_order(self):
        html = """
            <div class="posCatalog_select">
                <a class="posCatalog_name">8.1 视频</a>
                <span class="icon_Completed"></span>
            </div>
            <div class="posCatalog_select">
                <a class="posCatalog_name">8.2 章节测验</a>
            </div>
            <div class="posCatalog_select">
                <a class="posCatalog_name">8.3 视频</a>
            </div>
            <div class="posCatalog_select locked">
                <a class="posCatalog_name" aria-disabled="true">8.4 未解锁视频</a>
            </div>
        """
        self.driver.get(
            "data:text/html;charset=utf-8," + urllib.parse.quote(html)
        )

        self.assertEqual(main.scan_progress(self.driver), [1, 2])

    def test_old_ui_skips_locked_or_unopened_items(self):
        html = """
            <div class="onetoone">
                <h4 id="locked1" class="chapter locked">
                    <span class="roundpointStudent orange01"></span>
                    <a title="未解锁视频">未解锁</a>
                </h4>
                <h4 id="open1">
                    <span class="roundpointStudent orange01"></span>
                    <a href="/chapter/1" title="可观看视频">可观看视频</a>
                </h4>
            </div>
        """
        self.driver.get("data:text/html;charset=utf-8," + urllib.parse.quote(html))

        self.assertEqual(main.scan_progress(self.driver), ["open1"])

    def test_scan_excludes_items_already_handled_in_this_run(self):
        html = """
            <div class="onetoone">
                <h4 id="quiz1">
                    <span class="roundpointStudent orange01"></span>
                    <a href="/quiz/1" title="章节测验">章节测验</a>
                </h4>
                <h4 id="video1">
                    <span class="roundpointStudent orange01"></span>
                    <a href="/video/1" title="视频">视频</a>
                </h4>
            </div>
        """
        self.driver.get("data:text/html;charset=utf-8," + urllib.parse.quote(html))

        self.assertEqual(main.scan_progress(self.driver, {"quiz1"}), ["video1"])

    def test_quiz_title_detection_is_case_insensitive(self):
        self.assertTrue(main.is_quiz_title("8.2章节测验"))
        self.assertTrue(main.is_quiz_title("Chapter Quiz"))
        self.assertFalse(main.is_quiz_title("8.3 工程伦理视频"))

    def test_requested_chapter_maps_to_8_2_answers(self):
        url = (
            "https://mooc.mooc.ucas.edu.cn/mooc-ans/mycourse/studentstudy"
            "?chapterId=661755&courseId=350140000040577"
        )
        answers_path = Path(main.__file__).with_name("answers.json")
        self.assertEqual(main.mapped_answer_set(answers_path, url), "8.2章节测验")

    def test_extracts_answer_mapping_from_javascript_catalog_link(self):
        html = '<a id="quiz" href="javascript:void(0)" onclick="openChapter(661755)">8.2章节测验</a>'
        self.driver.get("data:text/html;charset=utf-8," + urllib.parse.quote(html))
        element = self.driver.find_element("id", "quiz")
        answers_path = Path(main.__file__).with_name("answers.json")

        answer_set, mapping_url = main.mapped_answer_from_element(
            self.driver, answers_path, element
        )

        self.assertEqual(answer_set, "8.2章节测验")
        self.assertIn("chapterId=661755", mapping_url)

    def test_completed_task_waits_two_seconds_then_refreshes(self):
        driver = Mock()
        with patch.object(main.time, "sleep") as sleep:
            self.assertTrue(main.wait_and_refresh(driver, "视频 1/1"))

        driver.switch_to.default_content.assert_called_once_with()
        sleep.assert_called_once_with(2)
        driver.refresh.assert_called_once_with()

    def test_analyzes_quiz_page_from_visible_questions(self):
        html = '<div class="TiMu"><input type="radio">题目一</div>'
        self.driver.get("data:text/html;charset=utf-8," + urllib.parse.quote(html))

        analysis = main.analyze_current_page(self.driver, timeout=0)

        self.assertEqual(analysis["questions"], 1)
        self.assertEqual(analysis["videos"], 0)

    def test_analyzes_video_page_from_video_element(self):
        self.driver.get("data:text/html;charset=utf-8," + urllib.parse.quote("<video controls></video>"))

        analysis = main.analyze_current_page(self.driver, timeout=0)

        self.assertEqual(analysis["videos"], 1)
        self.assertEqual(analysis["questions"], 0)


if __name__ == "__main__":
    unittest.main()
