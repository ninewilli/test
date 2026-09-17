from __future__ import annotations
import time
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from selenium.common.exceptions import (
    JavascriptException,
    NoAlertPresentException,
    StaleElementReferenceException,
    UnexpectedAlertPresentException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver


SUPPORTED_TYPES = {"single", "multiple", "judge", "text"}
SAVE_SUCCESS_TEXTS = ("保存成功", "暂存成功", "操作成功")
SUBMIT_SUCCESS_TEXTS = ("提交成功", "操作成功", "已提交")


def accept_pending_alert(driver: WebDriver, timeout: float = 0) -> str | None:
    deadline = time.monotonic() + max(timeout, 0)
    while True:
        try:
            alert = driver.switch_to.alert
            text = str(alert.text or "").strip()
            alert.accept()
            return text
        except NoAlertPresentException:
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)


def save_alert_succeeded(text: str | None) -> bool:
    return bool(text and any(marker in text for marker in SAVE_SUCCESS_TEXTS))


def action_alert_succeeded(text: str | None, submit: bool = False) -> bool:
    markers = SUBMIT_SUCCESS_TEXTS if submit else SAVE_SUCCESS_TEXTS
    return bool(text and any(marker in text for marker in markers))


def load_answers(
    path: str | Path,
    answer_set: str | None = None,
    source_url: str | None = None,
    search_all: bool = False,
) -> list[dict[str, Any]]:
    answer_path = Path(path)
    with answer_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, dict) and "answer_sets" in payload:
        answer_sets = payload["answer_sets"]
        if not isinstance(answer_sets, dict) or not answer_sets:
            raise ValueError("answer_sets 必须是非空对象")

        mapped_set = None
        if source_url and not search_all:
            chapter_id = parse_qs(urlparse(source_url).query).get("chapterId", [None])[0]
            chapter_sets = payload.get("chapter_sets", {})
            if chapter_id and isinstance(chapter_sets, dict):
                mapped_set = chapter_sets.get(chapter_id)
        if mapped_set and answer_set and mapped_set != answer_set:
            raise ValueError(
                f"当前 URL 的 chapterId 对应“{mapped_set}”，不能使用“{answer_set}”"
            )
        if not answer_set and mapped_set:
            answer_set = mapped_set

        if not answer_set and search_all:
            answers = [
                {**item, "_answer_set": str(set_name)}
                for set_name, set_answers in answer_sets.items()
                for item in set_answers
            ]
        elif not answer_set:
            choices = "、".join(str(name) for name in answer_sets)
            raise ValueError(f"该文件包含多个答案组，请使用 --set 选择: {choices}")
        elif answer_set not in answer_sets:
            choices = "、".join(str(name) for name in answer_sets)
            raise ValueError(f"未找到答案组“{answer_set}”，可选: {choices}")
        else:
            answers = answer_sets[answer_set]
    else:
        answers = payload.get("answers") if isinstance(payload, dict) else payload
    if not isinstance(answers, list) or not answers:
        raise ValueError("答案文件必须包含非空 answers 数组")

    validated: list[dict[str, Any]] = []
    for index, item in enumerate(answers, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 条答案必须是对象")
        if not any(item.get(key) not in (None, "") for key in ("question", "id", "index")):
            raise ValueError(f"第 {index} 条答案至少需要 question、id 或 index 之一")
        answer_type = str(item.get("type", "")).lower()
        if answer_type not in SUPPORTED_TYPES:
            raise ValueError(f"第 {index} 条答案 type 必须是: {', '.join(sorted(SUPPORTED_TYPES))}")
        if "answer" not in item:
            raise ValueError(f"第 {index} 条答案缺少 answer")
        if answer_type == "multiple" and not isinstance(item["answer"], list):
            raise ValueError(f"第 {index} 条多选题 answer 必须是数组")
        validated.append(item)
    return validated


@dataclass
class FillReport:
    frame_count: int = 0
    question_count: int = 0
    filled: list[dict[str, Any]] = field(default_factory=list)
    problems: list[dict[str, Any]] = field(default_factory=list)
    used_entry_indexes: set[int] = field(default_factory=set)
    draft_saved: bool = False
    draft_save_detail: str = ""
    action_mode: str = "save"
    submitted: bool = False
    submit_detail: str = ""

    @property
    def filled_count(self) -> int:
        return len(self.filled)

    def unused_answers(self, answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [answer for index, answer in enumerate(answers) if index not in self.used_entry_indexes]

    @property
    def action_completed(self) -> bool:
        return self.submitted if self.action_mode == "submit" else self.draft_saved

    @property
    def action_detail(self) -> str:
        return self.submit_detail if self.action_mode == "submit" else self.draft_save_detail


class AnswerFiller:
    def __init__(
        self,
        driver: WebDriver,
        answers: list[dict[str, Any]],
        script_path: str | Path | None = None,
        search_only: bool = False,
        submit: bool = False,
    ):
        self.driver = driver
        self.answers = answers
        self.search_only = search_only
        self.submit = submit
        self.script_path = Path(script_path or Path(__file__).with_name("filler.js"))
        self.script = self.script_path.read_text(encoding="utf-8")

    def fill_current_page(self) -> FillReport:
        report = FillReport()
        report.action_mode = "submit" if self.submit else "save"
        self.driver.switch_to.default_content()
        self._fill_frame_tree(report, "top")
        self.driver.switch_to.default_content()
        if report.filled_count:
            action_result = self._save_draft_in_frame_tree("top", submit=self.submit)
            action_succeeded = bool(action_result.get("saved"))
            action_detail = str(action_result.get("detail", ""))
            confirmation_alert = None
            if self.submit:
                deadline = time.monotonic() + 10
                confirmation = {"clicked": False}
                while not confirmation.get("clicked") and not confirmation.get("alert_text"):
                    pending_alert = accept_pending_alert(self.driver, timeout=0)
                    if pending_alert:
                        confirmation = {"clicked": False, "alert_text": pending_alert}
                        break
                    self.driver.switch_to.default_content()
                    confirmation = self._click_submit_confirmation_in_frame_tree("top")
                    if time.monotonic() >= deadline:
                        break
                    if not confirmation.get("clicked") and not confirmation.get("alert_text"):
                        time.sleep(0.1)
                if confirmation.get("clicked"):
                    action_detail += f"；{confirmation.get('detail', '已点击提交确认按钮')}"
                elif not confirmation.get("alert_text"):
                    candidates = confirmation.get("candidates", [])
                    hint = f"，候选元素: {' | '.join(candidates)}" if candidates else ""
                    action_detail += f"；未找到可点击的提交确认按钮{hint}"
                confirmation_alert = confirmation.get("alert_text")
            alert_text = accept_pending_alert(self.driver, timeout=3 if self.submit else 0.5)
            alert_text = alert_text or confirmation_alert
            if alert_text:
                action_succeeded = action_alert_succeeded(alert_text, self.submit)
                action_detail += f"；页面提示“{alert_text}”"
            if self.submit and not action_succeeded:
                completion = self._wait_for_submission_completion(timeout=10)
                if completion.get("completed"):
                    action_succeeded = True
                    action_detail += f"；{completion.get('detail', '页面显示任务点已完成')}"
            if self.submit:
                report.submitted = action_succeeded
                report.submit_detail = action_detail
            else:
                report.draft_saved = action_succeeded
                report.draft_save_detail = action_detail
        else:
            detail = "没有成功填写的题目，未执行提交" if self.submit else "没有成功填写的题目，未执行暂时保存"
            if self.submit:
                report.submit_detail = detail
            else:
                report.draft_save_detail = detail
        try:
            self.driver.switch_to.default_content()
        except UnexpectedAlertPresentException as exc:
            alert_text = accept_pending_alert(self.driver, timeout=0.5) or exc.alert_text
            succeeded = action_alert_succeeded(alert_text, self.submit)
            if self.submit:
                report.submitted = succeeded
                report.submit_detail += f"；页面提示“{alert_text}”"
            else:
                report.draft_saved = succeeded
                report.draft_save_detail += f"；页面提示“{alert_text}”"
            self.driver.switch_to.default_content()
        return report

    def current_submission_status(self) -> dict[str, Any]:
        self.driver.switch_to.default_content()
        result = self._submission_completed_in_frame_tree("top")
        self.driver.switch_to.default_content()
        return result

    def _save_draft_in_frame_tree(self, frame_path: str, submit: bool = False) -> dict[str, Any]:
        time.sleep(1)
        script = r"""
            const submitting = arguments[0];
            const allowed = ['暂时保存', '临时保存', '保存草稿'];
            const controls = [...document.querySelectorAll(
                'button,input[type="button"],input[type="submit"],a,[role="button"]'
            )];
            const label = (element) => String(
                element.value || element.innerText || element.textContent
                || element.getAttribute('aria-label') || element.title || ''
            ).replace(/[\s\u00a0]+/g, '');
            const button = controls.find((element) => {
                const text = label(element);
                const visible = !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
                const matches = submitting
                    ? (text === '提交' || text === '确认提交') && !text.includes('保存')
                    : allowed.some((label) => text.includes(label)) && !text.includes('提交');
                return matches && visible && !element.disabled
                    && element.getAttribute('aria-disabled') !== 'true';
            });
            if (!button) return null;
            const text = label(button);
            button.click();
            return { saved: !submitting, clicked: true, detail: `已点击“${text}”` };
        """
        try:
            result = self.driver.execute_script(script, submit)
            alert_text = accept_pending_alert(self.driver, timeout=0.1)
            if alert_text:
                return {
                    "saved": action_alert_succeeded(alert_text, submit),
                    "clicked": True,
                    "detail": f"页面提示“{alert_text}”",
                    "frame": frame_path,
                }
            if result:
                result["frame"] = frame_path
                return result
        except UnexpectedAlertPresentException as exc:
            alert_text = accept_pending_alert(self.driver, timeout=0.5) or exc.alert_text
            return {
                "saved": action_alert_succeeded(alert_text, submit),
                "clicked": True,
                "detail": f"页面提示“{alert_text}”",
                "frame": frame_path,
            }
        except JavascriptException:
            pass

        frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
        for index in range(len(frames)):
            try:
                frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
                if self._is_editor_frame(frames[index]):
                    continue
                self.driver.switch_to.frame(frames[index])
                time.sleep(1)
                result = self._save_draft_in_frame_tree(f"{frame_path}/{index}", submit=submit)
                self.driver.switch_to.parent_frame()
                if result.get("saved") or result.get("clicked"):
                    return result
            except Exception:
                self.driver.switch_to.default_content()
                self._restore_frame_path(frame_path)
        target = "提交" if submit else "暂时保存"
        return {"saved": False, "detail": f"未找到可见的“{target}”按钮"}

    def _click_submit_confirmation_in_frame_tree(self, frame_path: str) -> dict[str, Any]:
        candidate_hints: list[str] = []
        script = r"""
            const label = (element) => String(
                element.value || element.innerText || element.textContent
                || element.getAttribute('aria-label') || element.title || ''
            ).replace(/[\s\u00a0]+/g, '');
            const visible = (element) => {
                const style = getComputedStyle(element);
                return !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length)
                    && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const dialogs = [...document.querySelectorAll(
                '#confirmSubWin,[role="alertdialog"],[role="dialog"],dialog,.layui-layer,.modal,.popDiv,.popUp,.popup,.dialog,.confirm,.confirm-box,.maskDiv1'
            )].filter(visible);
            const roots = dialogs.length ? dialogs : [document.body];
            const exact = roots.flatMap((root) => [...root.querySelectorAll('*')]).filter((element) => (
                ['确定', '是'].includes(label(element)) && visible(element)
            ));
            const clickable = exact.map((element) => element.closest(
                'button,a,input[type="button"],input[type="submit"],[role="button"],[onclick],.bluebtn,.sure,.yes,.confirm-btn'
            ) || element).filter((element) => (
                visible(element) && !element.disabled && element.getAttribute('aria-disabled') !== 'true'
            ));
            const yes = clickable[clickable.length - 1];
            const candidates = exact.slice(-5).map((element) => {
                const target = element.closest('button,a,[role="button"],[onclick]') || element;
                return `${target.tagName}.${String(target.className || '').replace(/\s+/g, '.')}`;
            });
            if (!yes) return { clicked: false, candidates };
            yes.scrollIntoView({ block: 'center', inline: 'center' });
            yes.focus();
            for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup']) {
                yes.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
            }
            yes.click();
            return {
                clicked: true,
                detail: `已点击提交确认“${label(yes)}”（${yes.tagName}.${String(yes.className || '').replace(/\s+/g, '.')}）`,
                candidates
            };
        """
        try:
            result = self.driver.execute_script(script)
            alert_text = accept_pending_alert(self.driver, timeout=0.1)
            if alert_text:
                return {
                    "clicked": True,
                    "detail": "已点击提交确认按钮",
                    "alert_text": alert_text,
                    "frame": frame_path,
                }
            if result and result.get("clicked"):
                result["frame"] = frame_path
                return result
            if result:
                candidate_hints.extend(
                    f"{frame_path}:{hint}" for hint in result.get("candidates", [])
                )
        except UnexpectedAlertPresentException as exc:
            alert_text = accept_pending_alert(self.driver, timeout=0.5) or exc.alert_text
            return {"clicked": False, "alert_text": alert_text, "frame": frame_path}
        except JavascriptException:
            pass

        frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
        for index in range(len(frames)):
            try:
                frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
                if self._is_editor_frame(frames[index]):
                    continue
                self.driver.switch_to.frame(frames[index])
                result = self._click_submit_confirmation_in_frame_tree(f"{frame_path}/{index}")
                self.driver.switch_to.parent_frame()
                if result.get("clicked") or result.get("alert_text"):
                    return result
                candidate_hints.extend(
                    f"{frame_path}/{index}:{hint}" for hint in result.get("candidates", [])
                )
            except Exception:
                self.driver.switch_to.default_content()
                self._restore_frame_path(frame_path)
        return {"clicked": False, "candidates": candidate_hints}

    def _wait_for_submission_completion(self, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + max(timeout, 0)
        while True:
            self.driver.switch_to.default_content()
            result = self._submission_completed_in_frame_tree("top")
            if result.get("completed"):
                self.driver.switch_to.default_content()
                return result
            if time.monotonic() >= deadline:
                self.driver.switch_to.default_content()
                return {"completed": False}
            time.sleep(0.1)

    def _submission_completed_in_frame_tree(self, frame_path: str) -> dict[str, Any]:
        script = r"""
            const text = String(document.body && document.body.innerText || '').replace(/[\s\u00a0]+/g, '');
            const markers = ['任务点已完成', '查看已批阅作业', '本次成绩', '最终成绩'];
            const marker = markers.find((value) => text.includes(value));
            return marker ? { completed: true, detail: `页面显示“${marker}”` } : null;
        """
        try:
            result = self.driver.execute_script(script)
            if result:
                result["frame"] = frame_path
                return result
        except JavascriptException:
            pass

        frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
        for index in range(len(frames)):
            try:
                frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
                if self._is_editor_frame(frames[index]):
                    continue
                self.driver.switch_to.frame(frames[index])
                result = self._submission_completed_in_frame_tree(f"{frame_path}/{index}")
                self.driver.switch_to.parent_frame()
                if result.get("completed"):
                    return result
            except Exception:
                self.driver.switch_to.default_content()
                self._restore_frame_path(frame_path)
        return {"completed": False}

    def _fill_frame_tree(self, report: FillReport, frame_path: str) -> None:
        report.frame_count += 1
        try:
            result = self.driver.execute_script(self.script, self.answers, {"searchOnly": self.search_only})
        except JavascriptException as exc:
            report.problems.append({"status": "script-error", "frame": frame_path, "detail": str(exc)})
            return

        if result:
            report.question_count += int(result.get("questionCount", 0))
            for item in result.get("results", []):
                item["frame"] = frame_path
                if item.get("status") == "filled":
                    report.filled.append(item)
                    if item.get("entryIndex") is not None:
                        report.used_entry_indexes.add(int(item["entryIndex"]))
                elif item.get("status") != "unmatched-question" or item.get("question"):
                    report.problems.append(item)

        frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
        for index in range(len(frames)):
            time.sleep(1)
            try:
                frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
                if self._is_editor_frame(frames[index]):
                    continue
                self.driver.switch_to.frame(frames[index])
                self._fill_frame_tree(report, f"{frame_path}/{index}")
                self.driver.switch_to.parent_frame()
            except (StaleElementReferenceException, IndexError):
                self.driver.switch_to.default_content()
                self._restore_frame_path(frame_path)
            except Exception as exc:  # Cross-origin and unloaded frames are reported, not fatal.
                report.problems.append(
                    {"status": "frame-error", "frame": f"{frame_path}/{index}", "detail": str(exc)}
                )
                self.driver.switch_to.default_content()
                self._restore_frame_path(frame_path)

    @staticmethod
    def _is_editor_frame(frame) -> bool:
        frame_id = frame.get_attribute("id") or ""
        frame_class = frame.get_attribute("class") or ""
        frame_src = frame.get_attribute("src") or ""
        return (
            frame_id.startswith("ueditor_")
            or "edui-" in frame_class
            or frame_src.startswith("javascript:")
        )

    def _restore_frame_path(self, frame_path: str) -> None:
        if frame_path == "top":
            return
        for raw_index in frame_path.split("/")[1:]:
            frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
            self.driver.switch_to.frame(frames[int(raw_index)])
