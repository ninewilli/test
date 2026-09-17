#!/usr/bin/env python
# -*- coding: UTF-8 -*-
'''
@Project ：Python 
@File    ：main.py
@IDE     ：PyCharm 
@Author  ：Cjx_1023
@Modifier：cchan & Gemini
@UpDateTime     ：2023/12/05
@Description: macOS 适配版本 - 单实例模式 (只登录一次) - 适配新版UI - 智能跳过已完成视频 - PPT深度阅读模式
'''
import math
import json
import re
import atexit
from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException, WebDriverException
import time
import argparse
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import pyautogui
except ImportError:
    pyautogui = None

from tqdm import tqdm

from browser import create_chrome_driver, find_chromedriver
from filler import AnswerFiller, load_answers

# 解析视频持续时间
def convertTime(time_str):
    try:
        minutes, seconds = map(int, time_str.split(':'))
        total_time = minutes * 60 + seconds
        return total_time
    except Exception:
        return 0

# 移动鼠标函数 (适配 Mac)
def mouseMoveTo(element, driver):
    if pyautogui is None:
        return
    try:
        canvas_x_offset = driver.execute_script(
            "return window.screenX + (window.outerWidth - window.innerWidth) / 2 - window.scrollX;")
        canvas_y_offset = driver.execute_script(
            "return window.screenY + (window.outerHeight - window.innerHeight) - window.scrollY;")
        
        element_location = (element.rect["x"] + canvas_x_offset + element.rect["width"] / 2,
                            element.rect["y"] + canvas_y_offset + element.rect["height"] / 2)
        
        pyautogui.moveTo(element_location[0], element_location[1], duration=0.1)
    except Exception:
        pass

def is_finite_positive(value):
    try:
        return value is not None and math.isfinite(float(value)) and float(value) > 0
    except Exception:
        return False

def get_video_state(driver):
    """
    读取当前视频 iframe 内的播放状态。只读取状态，不触发播放。
    """
    state = {
        "current": 0,
        "duration": 0,
        "ended": False,
        "paused": True,
    }

    try:
        js_state = driver.execute_script("""
            const video = document.querySelector('video');
            if (!video) {
                return null;
            }
            return {
                current: Number.isFinite(video.currentTime) ? video.currentTime : 0,
                duration: Number.isFinite(video.duration) ? video.duration : 0,
                ended: !!video.ended,
                paused: !!video.paused
            };
        """)
        if js_state:
            state.update(js_state)
    except Exception:
        pass

    try:
        duration_ele = driver.find_element(By.CLASS_NAME, "vjs-duration-display")
        current_ele = driver.find_element(By.CLASS_NAME, "vjs-current-time-display")
        ui_duration = convertTime(duration_ele.text)
        ui_current = convertTime(current_ele.text)
        if ui_duration > 0:
            state["duration"] = ui_duration
        if ui_current > 0:
            state["current"] = ui_current
    except Exception:
        pass

    return state

def video_already_completed(driver):
    """
    在点击播放前判断视频是否已经完成。
    """
    state = get_video_state(driver)
    current = float(state.get("current") or 0)
    duration = float(state.get("duration") or 0)

    if state.get("ended"):
        return True, current, duration, "ended 状态"

    if is_finite_positive(duration):
        remaining = duration - current
        if current > 0 and remaining <= 5:
            return True, current, duration, f"剩余 {remaining:.1f}s"

    return False, current, duration, ""

def module_already_completed(driver, module_iframe):
    """
    在进入视频/PPT iframe 前，从外层任务点标记判断模块是否已完成。
    只检查 iframe 紧邻的任务容器，避免兄弟节点的完成状态干扰。
    """
    try:
        return bool(driver.execute_script("""
            let node = arguments[0];
            for (let i = 0; node && i < 4; i += 1, node = node.parentElement) {
                const className = String(node.className || '');
                if (/ans-job-finished|icon_Completed|jobFinish|job-finished/.test(className)) {
                    return true;
                }
                // 只检查直接子元素中的状态指示器，排除包含 iframe 的容器（避免兄弟节点干扰）
                for (let j = 0; j < node.children.length; j++) {
                    const child = node.children[j];
                    const childClass = String(child.className || '');
                    if (/ans-job-finished|icon_Completed|jobFinish|job-finished/.test(childClass)
                        && !child.querySelector('iframe')) {
                        return true;
                    }
                }
            }
            return false;
        """, module_iframe))
    except Exception:
        return False

# PPT iframe 选择器：覆盖 PDF、PPT、文档等多种类型
PPT_SELECTORS = (
    'iframe[src*="/ananas/modules/pdf/index.html"],'
    'iframe[src*="/ananas/modules/ppt/index.html"],'
    'iframe[src*="/ananas/modules/doc/index.html"]'
)

# 视频iframe选择器
VIDEO_SELECTORS = 'iframe[src*="/ananas/modules/video/index.html"]'

CONTENT_ANALYSIS_SCRIPT = r"""
    const visible = (element) => {
        if (!element) return false;
        const style = getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden'
            && !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
    };
    const questionSelectors = [
        '.questionLi', '.TiMu', '.question-item', '.subject-item', '.topic-item',
        '[data-question-id]', '[data-questionid]', 'li[id^="question"]'
    ];
    const questions = [...document.querySelectorAll(questionSelectors.join(','))]
        .filter(visible);
    const frames = [...document.querySelectorAll('iframe,frame')];
    const frameSources = frames.map(frame => String(
        frame.src || frame.getAttribute('src') || ''
    ).toLowerCase());
    return {
        questions: questions.length,
        videos: document.querySelectorAll('video').length
            + frameSources.filter(src => src.includes('/ananas/modules/video/')).length,
        ppts: frameSources.filter(src => /\/ananas\/modules\/(pdf|ppt|doc)\//.test(src)).length,
        quizFrames: frameSources.filter(src => /(work|exam|quiz|test)/.test(src)).length,
        frameCount: frames.length
    };
"""


def analyze_current_page(driver, timeout=15):
    """只读扫描顶层及嵌套 iframe，判断题目、视频和文档内容。"""
    deadline = time.monotonic() + max(timeout, 0)
    latest = {"questions": 0, "videos": 0, "ppts": 0, "quizFrames": 0, "frames": 0}

    def scan_frame(frame_path):
        try:
            state = driver.execute_script(CONTENT_ANALYSIS_SCRIPT) or {}
            latest["questions"] += int(state.get("questions", 0))
            # Parent pages expose media iframe URLs while child frames expose
            # the actual element; max avoids counting the same task twice.
            latest["videos"] = max(latest["videos"], int(state.get("videos", 0)))
            latest["ppts"] = max(latest["ppts"], int(state.get("ppts", 0)))
            latest["quizFrames"] += int(state.get("quizFrames", 0))
            frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
            latest["frames"] += len(frames)
        except Exception:
            return

        for index in range(len(frames)):
            try:
                frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
                driver.switch_to.frame(frames[index])
                scan_frame(f"{frame_path}/{index}")
                driver.switch_to.parent_frame()
            except Exception:
                driver.switch_to.default_content()
                for raw_index in frame_path.split("/")[1:]:
                    parent_frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
                    driver.switch_to.frame(parent_frames[int(raw_index)])

    while True:
        latest = {"questions": 0, "videos": 0, "ppts": 0, "quizFrames": 0, "frames": 0}
        driver.switch_to.default_content()
        scan_frame("top")
        driver.switch_to.default_content()
        if latest["questions"] or latest["videos"] or latest["ppts"] or latest["quizFrames"]:
            return latest
        if time.monotonic() >= deadline:
            return latest
        time.sleep(1)

def locate_main_iframe(driver, timeout=20):
    """重新定位主内容 iframe，避免 StaleElementReferenceException。"""
    driver.switch_to.default_content()
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((By.ID, 'iframe'))
    )

def get_chapter_elements(driver):
    """
    获取当前页面的所有章节链接元素
    """
    # 尝试获取新版元素
    new_ui_elements = driver.find_elements(By.CSS_SELECTOR, ".posCatalog_select .posCatalog_name")
    if new_ui_elements:
        return new_ui_elements, True # True 表示新版 UI
    
    # 尝试获取旧版元素
    root_elements = driver.find_elements(By.CLASS_NAME, 'onetoone')
    if root_elements:
        a_elements = root_elements[0].find_elements(By.TAG_NAME, 'a')
        return a_elements, False # False 表示旧版 UI
    
    return [], False


LOCKED_TEXT_MARKERS = ("未解锁", "尚未解锁", "已锁定", "暂未开放", "未开放")
def catalog_item_is_locked(driver, row, link=None):
    """识别新版/旧版目录中的未解锁项。"""
    try:
        return bool(driver.execute_script(r"""
            const row = arguments[0];
            const link = arguments[1];
            const text = String((row && row.innerText) || '').replace(/\s+/g, ' ');
            if (['未解锁', '尚未解锁', '已锁定', '暂未开放', '未开放']
                .some(marker => text.includes(marker))) {
                return true;
            }
            const nodes = [row, link, ...(row ? row.querySelectorAll('*') : [])].filter(Boolean);
            if (nodes.some(node => {
                const classes = String(node.className || '').toLowerCase();
                const title = String(node.getAttribute && node.getAttribute('title') || '').toLowerCase();
                const ariaDisabled = node.getAttribute && node.getAttribute('aria-disabled');
                return /(^|[\s_-])(lock|locked|disabled?)([\s_-]|$)/.test(classes)
                    || /(未解锁|锁定|未开放)/.test(title)
                    || ariaDisabled === 'true'
                    || Boolean(node.disabled);
            })) {
                return true;
            }
            return false;
        """, row, link))
    except Exception:
        text = " ".join(filter(None, [getattr(row, "text", ""), getattr(link, "text", "")]))
        return any(marker in text for marker in LOCKED_TEXT_MARKERS)


def scan_progress(driver, excluded_refs=None):
    """
    扫描当前课程页面的进度，返回未完成章节的引用列表。
    新版 UI 使用元素索引，旧版 UI 使用稳定的章节 DOM ID。
    """
    print("正在扫描章节进度...")
    
    try:
        # 等待页面加载
        WebDriverWait(driver, 15).until(lambda d: d.find_elements(By.CLASS_NAME, 'onetoone') or d.find_elements(By.CLASS_NAME, 'posCatalog_select'))
    except TimeoutException:
        print("扫描超时：未找到章节列表，请确认已进入课程章节页面。")
        return []

    elements, is_new_ui = get_chapter_elements(driver)
    print(f"识别到 {len(elements)} 个章节 (UI模式: {'新版' if is_new_ui else '旧版'})")
    
    excluded_refs = set(excluded_refs or ())
    ret = []
    print("-----------------------未完成章节列表---------------------")
    
    if is_new_ui:
        for i, el in enumerate(elements):
            try:
                # 获取完整的 div.posCatalog_select 行，图标不一定在链接的直接父节点。
                row_div = el.find_element(
                    By.XPATH,
                    "./ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' posCatalog_select ')][1]",
                )
                row_text = row_div.text
                completed_icon = row_div.find_elements(By.CLASS_NAME, "icon_Completed")

                if catalog_item_is_locked(driver, row_div, el):
                    print(f"  [跳过未解锁] {row_text.strip() or i}")
                    continue
                
                # 如果没有 icon_Completed 且文本不包含“已完成”
                if not completed_icon and "已完成" not in row_text:
                    ret.append(i)
            except:
                pass
    else:
        # 旧版目录会异步更新进度，任何元素过期时必须丢弃半截结果并整次重试。
        scan_succeeded = False
        for attempt in range(1, 4):
            candidate_refs = []
            try:
                # 按目录 DOM 顺序扫描。部分测验没有进度圆点，不能只遍历圆点。
                root = driver.find_element(By.CLASS_NAME, 'onetoone')
                rows = root.find_elements(By.XPATH, ".//*[self::h4 or self::h5]")
                for row in rows:
                    links = row.find_elements(By.TAG_NAME, "a")
                    if not links:
                        continue
                    chapter_link = links[0]
                    row_text = chapter_link.get_attribute("title") or chapter_link.text
                    if catalog_item_is_locked(driver, row, chapter_link):
                        print(f"  [跳过未解锁] {row_text or row.get_attribute('id')}")
                        continue

                    markers = row.find_elements(By.CLASS_NAME, 'roundpointStudent')
                    marker_classes = " ".join(
                        marker.get_attribute('class') or '' for marker in markers
                    )
                    row_classes = row.get_attribute('class') or ''
                    completed = (
                        'orange01' not in marker_classes
                        and (
                            bool(markers)
                            or '已完成' in row.text
                            or any(name in row_classes for name in ('completed', 'finished'))
                        )
                    )
                    incomplete = 'orange01' in marker_classes
                    quiz_without_marker = not markers and is_quiz_title(row_text)
                    if completed or not (incomplete or quiz_without_marker):
                        continue

                    row_id = row.get_attribute("id")
                    if row_id:
                        candidate_refs.append(row_id)
                ret = candidate_refs
                scan_succeeded = True
                break
            except StaleElementReferenceException:
                print(f"  [重试] 目录扫描期间页面更新 ({attempt}/3)...")
                time.sleep(1)
            except Exception as e:
                print(f"旧版扫描出错: {e}")
                return None

        if not scan_succeeded:
            print("扫描失败：目录持续更新，无法获得一致的章节列表。")
            return None

    if excluded_refs:
        skipped = [ref for ref in ret if ref in excluded_refs]
        if skipped:
            print(f"本次运行已处理，跳过重复项目: {skipped}")
        ret = [ref for ref in ret if ref not in excluded_refs]
    print("未完成章节引用：", ret)
    return ret

def is_quiz_title(title):
    normalized = (title or "").lower()
    return "测验" in normalized or "quiz" in normalized


def mapped_answer_set(answers_path, source_url):
    """返回 URL 对应的答案组；没有映射或文件无效时返回 None。"""
    if not answers_path or not source_url:
        return None
    try:
        with Path(answers_path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        chapter_id = parse_qs(urlparse(source_url).query).get("chapterId", [None])[0]
        chapter_sets = payload.get("chapter_sets", {}) if isinstance(payload, dict) else {}
        return chapter_sets.get(chapter_id) if chapter_id else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def mapped_answer_from_element(driver, answers_path, element):
    """从 href、data 属性或 onclick 中识别答案表已知的 chapterId。"""
    try:
        with Path(answers_path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        chapter_sets = payload.get("chapter_sets", {}) if isinstance(payload, dict) else {}
        if not isinstance(chapter_sets, dict):
            return None, None
        markup = driver.execute_script("return arguments[0].outerHTML || '';", element) or ""
        matches = [
            str(chapter_id)
            for chapter_id in chapter_sets
            if re.search(rf"(?<!\d){re.escape(str(chapter_id))}(?!\d)", markup)
        ]
        if len(matches) == 1:
            chapter_id = matches[0]
            return str(chapter_sets[chapter_id]), f"https://mooc.local/?chapterId={chapter_id}"
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return None, None


def wait_and_refresh(driver, task_name, delay=2):
    """任务完成后等待并刷新顶层页面，让平台及时更新完成状态。"""
    try:
        driver.switch_to.default_content()
        print(f"    {task_name}已完成，等待 {delay} 秒后刷新页面...")
        time.sleep(delay)
        driver.refresh()
        return True
    except Exception as exc:
        print(f"    [停止] {task_name}完成后刷新页面失败: {exc}")
        return False


def close_driver(driver):
    """Best-effort cleanup used for normal returns and interrupted runs."""
    if driver is None:
        return
    try:
        driver.quit()
    except WebDriverException:
        pass


def process_quiz(driver, answers_path, submit=False, wait_seconds=60, source_url=None):
    """填写当前测验。未启用提交时只暂存，并要求用户检查。"""
    mapping_url = source_url or driver.current_url
    try:
        answers = load_answers(answers_path, source_url=mapping_url)
    except (OSError, ValueError) as exc:
        print(f"    [停止] 无法为当前测验加载答案: {exc}")
        return False

    status = AnswerFiller(driver, answers).current_submission_status()
    if status.get("completed"):
        print(f"    [跳过] 测验已完成: {status.get('detail', '页面显示已完成')}")
        return True

    deadline = time.monotonic() + max(wait_seconds, 0)
    while True:
        report = AnswerFiller(driver, answers, submit=submit).fill_current_page()
        if report.question_count or time.monotonic() >= deadline:
            break
        time.sleep(1)

    print(f"    发现 {report.question_count} 道题，成功填写 {report.filled_count} 道。")
    for problem in report.problems:
        print(
            f"    [{problem.get('status', '需检查')}] "
            f"{problem.get('question') or problem.get('frame', '未知题目')}: "
            f"{problem.get('detail', '')}"
        )

    if report.question_count == 0:
        print("    [停止] 页面中未发现测验题目。")
        return False
    if report.filled_count != report.question_count:
        print("    [停止] 存在未匹配题目，不会继续到下一章节。")
        return False
    if not report.action_completed:
        action = "提交" if submit else "暂存"
        print(f"    [停止] 未确认{action}成功: {report.action_detail}")
        return False
    action = "测验提交" if submit else "测验答题并暂存"
    if not wait_and_refresh(driver, action):
        return False
    if not submit:
        print("    答案已暂存。请在浏览器中检查；确认无误后使用 --submit-answers 自动提交并继续。")
        return False

    print(f"    测验已提交: {report.action_detail}")
    return True


def process_single_chapter(
    driver,
    chapter_ref,
    force=False,
    answers_path=None,
    submit_answers=False,
    answer_wait=60,
):
    """
    处理单个章节：包含视频播放和PPT查看
    :param driver: WebDriver 实例
    :param chapter_ref: 新版 UI 的章节索引，或旧版 UI 的章节 DOM ID
    :param force: 是否强制播放（即使无法获取时长）
    """
    print(f"\n>>> 开始处理章节 {chapter_ref}...")
    
    # 1. 重新获取元素 (防止页面刷新后元素失效)
    if isinstance(chapter_ref, int):
        elements, _ = get_chapter_elements(driver)
        if chapter_ref >= len(elements):
            print("索引越界，跳过")
            return False
        target_chapter = elements[chapter_ref]
    else:
        try:
            chapter_row = driver.find_element(By.ID, chapter_ref)
            target_chapter = chapter_row.find_element(By.TAG_NAME, "a")
        except NoSuchElementException:
            print(f"未找到章节 {chapter_ref}，跳过")
            return False

    chapter_title = target_chapter.get_attribute("title") or target_chapter.text
    chapter_href = target_chapter.get_attribute("href")

    try:
        chapter_row = target_chapter.find_element(
            By.XPATH, "./ancestor::*[contains(@class,'posCatalog_select') or self::h4 or self::h5][1]"
        )
    except NoSuchElementException:
        chapter_row = target_chapter
    if catalog_item_is_locked(driver, chapter_row, target_chapter):
        print(f"  [跳过未解锁] {chapter_title or chapter_ref}")
        return None
    
    # 2. 点击进入章节
    try:
        driver.execute_script("arguments[0].scrollIntoViewIfNeeded(true);", target_chapter)
        time.sleep(1)
        # 尝试常规点击，失败则使用 JS 点击
        try:
            target_chapter.click()
        except:
            driver.execute_script("arguments[0].click();", target_chapter)
    except Exception as e:
        print(f"点击章节失败: {e}")
        return False

    time.sleep(5) # 等待内容加载

    answer_set = mapped_answer_set(answers_path, chapter_href)
    element_mapping_url = None
    if not answer_set:
        answer_set, element_mapping_url = mapped_answer_from_element(
            driver, answers_path, target_chapter
        )
    analysis = analyze_current_page(driver, timeout=min(max(answer_wait, 0), 20))
    print(
        "  - 页面分析结果: "
        f"题目 {analysis['questions']}，视频 {analysis['videos']}，"
        f"PPT/文档 {analysis['ppts']}，iframe {analysis['frames']}"
    )

    page_has_quiz = bool(
        analysis["questions"]
        or (
            analysis["quizFrames"]
            and not analysis["videos"]
            and not analysis["ppts"]
        )
    )
    quiz_fallback = is_quiz_title(chapter_title) or bool(answer_set)
    if page_has_quiz or quiz_fallback:
        mapping_url = element_mapping_url or (chapter_href if answer_set else driver.current_url)
        answer_set = answer_set or mapped_answer_set(answers_path, mapping_url)
        reason = "页面中发现题目" if page_has_quiz else "目录/章节映射兜底"
        print(f"  - 检测到章节测验 ({reason}): {chapter_title}")
        if answer_set:
            print(f"    已匹配答案组: {answer_set}")
        return process_quiz(
            driver,
            answers_path,
            submit=submit_answers,
            wait_seconds=answer_wait,
            source_url=mapping_url,
        )

    if not analysis["videos"] and not analysis["ppts"]:
        print("  [停止] 页面分析未发现题目、视频或 PPT/文档，可能尚未解锁或加载失败。")
        return False

    # 3. 切换到主 iframe
    try:
        iframe1 = locate_main_iframe(driver)
        driver.switch_to.frame(iframe1)
    except TimeoutException:
        print("未找到内容 iframe，可能该章节为空或加载失败。")
        return False

    # ---------------- 视频处理 ----------------
    try:
        # 查找所有视频 iframe
        video_frames = driver.find_elements(By.CSS_SELECTOR, VIDEO_SELECTORS)
        total_videos = len(video_frames)
        print(f"  - 检测到视频数量: {total_videos}")

        processed = 0
        while processed < total_videos:
            try:
                # 重新定位 iframe 和视频帧列表
                driver.switch_to.default_content()
                iframe1 = locate_main_iframe(driver)
                driver.switch_to.frame(iframe1)
                video_frames = driver.find_elements(By.CSS_SELECTOR, VIDEO_SELECTORS)

                if processed >= len(video_frames):
                    print(f"    [警告] DOM 中剩余视频帧({len(video_frames)})少于预期(已处理 {processed}, 总计 {total_videos})")
                    break

                current_frame = video_frames[processed]

                # 在进入 iframe 前检测任务点是否已完成
                if module_already_completed(driver, current_frame):
                    print(f"    [跳过] 视频 {processed+1}/{total_videos} 任务点已标记完成，无需播放。")
                    processed += 1
                    continue

                driver.switch_to.frame(current_frame)

                # 播放逻辑
                print(f"    正在处理视频 {processed+1}/{total_videos}...")

                try:
                    WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.TAG_NAME, "video")))
                except TimeoutException:
                    pass

                completed, pre_current, pre_duration, reason = video_already_completed(driver)
                if completed:
                    print(f"    [跳过] 视频 {processed+1}/{total_videos} 已播放完毕（当前: {pre_current:.1f}s / 总时长: {pre_duration:.1f}s，{reason}）。")
                    processed += 1
                    continue

                start_btn = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.CLASS_NAME, "vjs-big-play-button")))
                driver.execute_script("arguments[0].click();", start_btn)
                print("    已点击播放按钮，等待视频加载...")
                time.sleep(3)  # 增加等待时间，确保视频开始播放

                # 静音处理
                try:
                    print("    正在设置静音...")
                    # 尝试多种方式设置静音
                    # 方式1: 通过音量按钮
                    try:
                        volume_btn = driver.find_element(By.CLASS_NAME, "vjs-mute-control")
                        if "vjs-vol-0" not in volume_btn.get_attribute("class"):
                            volume_btn.click()
                            print("    已通过音量按钮设置静音")
                    except:
                        pass

                    # 方式2: 通过 JavaScript 直接设置视频元素静音
                    try:
                        video_element = driver.find_element(By.TAG_NAME, "video")
                        driver.execute_script("arguments[0].muted = true;", video_element)
                        driver.execute_script("arguments[0].volume = 0;", video_element)
                        print("    已通过 JavaScript 设置静音")
                    except:
                        pass
                except Exception as e:
                    print(f"    静音设置失败（继续播放）: {e}")

                # 获取时长和当前进度
                duration_ele = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "vjs-duration-display")))
                current_ele = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "vjs-current-time-display")))

                # 等待视频真正开始播放（时间开始变化）
                print("    等待视频开始播放...")
                initial_time = convertTime(current_ele.text)
                wait_count = 0
                while wait_count < 10:  # 最多等待10秒
                    time.sleep(1)
                    current_check = convertTime(current_ele.text)
                    if current_check > initial_time or current_check > 0:
                        print(f"    视频已开始播放 (当前时间: {current_check}s)")
                        break
                    wait_count += 1

                total_time = convertTime(duration_ele.text)
                current_time_val = convertTime(current_ele.text)

                print(f"    [调试] 视频总时长: {total_time}s, 当前时间: {current_time_val}s")

                # 如果总时长为0，尝试通过 JavaScript 获取视频时长
                if total_time == 0:
                    print("    视频时长未加载，尝试通过 JavaScript 获取...")
                    try:
                        video_element = driver.find_element(By.TAG_NAME, "video")
                        js_duration = driver.execute_script("return arguments[0].duration;", video_element)
                        if js_duration and js_duration > 0:
                            total_time = int(js_duration)
                            print(f"    通过 JavaScript 获取到时长: {total_time}s")
                    except:
                        pass

                # 如果总时长为0，说明可能还没加载完成，等待一下
                if total_time == 0:
                    print("    视频时长未加载，等待中...")
                    for _ in range(5):
                        time.sleep(1)
                        total_time = convertTime(duration_ele.text)
                        if total_time > 0:
                            break
                        # 再次尝试 JavaScript 获取
                        try:
                            video_element = driver.find_element(By.TAG_NAME, "video")
                            js_duration = driver.execute_script("return arguments[0].duration;", video_element)
                            if js_duration and js_duration > 0:
                                total_time = int(js_duration)
                                break
                        except:
                            pass

                # 如果仍然无法获取时长，根据 force 参数决定是否继续
                if total_time == 0:
                    if force:
                        print(f"    [强制模式] 无法获取视频时长，将使用智能检测方式继续播放...")
                        # 使用智能检测方式：通过检测视频播放状态来判断
                        total_time = 0  # 标记为未知时长
                    else:
                        print(f"    [警告] 无法获取视频时长，跳过此视频")
                        print(f"    提示: 使用 --force 参数可以强制播放")
                        processed += 1
                        continue

                # 如果总时长为0（强制模式），使用智能检测
                if total_time == 0:
                    print(f"    开始播放视频 (强制模式：时长未知，将智能检测完成状态)")

                    # 倍速设置 (尝试)
                    try:
                        speed_btn = driver.find_element(By.CLASS_NAME, "vjs-playback-rate")
                        speed_btn.click()
                        time.sleep(0.5)
                        speed_btn.click() # 切换到 2x
                        print("    已设置倍速播放")
                    except:
                        print("    无法设置倍速（可能不支持）")

                    # 强制模式：通过检测视频播放状态来判断是否完成
                    last_time = current_time_val
                    consecutive_paused_count = 0
                    max_no_progress = 30  # 如果30秒没有进度，认为视频已完成
                    no_progress_count = 0

                    print("    正在播放（强制模式）...")
                    while True:
                        try:
                            curr_time = last_time
                            video_playing = True

                            # 优先使用 JavaScript 获取视频状态（更准确）
                            try:
                                video_element = driver.find_element(By.TAG_NAME, "video")
                                js_current = driver.execute_script("return arguments[0].currentTime;", video_element)
                                js_duration = driver.execute_script("return arguments[0].duration;", video_element)
                                js_paused = driver.execute_script("return arguments[0].paused;", video_element)
                                js_ended = driver.execute_script("return arguments[0].ended;", video_element)

                                if js_ended:
                                    print("\n    视频播放完成（检测到 ended 状态）。")
                                    break

                                if js_duration > 0 and js_current >= js_duration - 2:
                                    print(f"\n    视频播放完成（当前: {js_current:.1f}s / 总时长: {js_duration:.1f}s）。")
                                    break

                                if js_current is not None and js_current >= 0:
                                    curr_time = int(js_current)

                                video_playing = not js_paused

                            except:
                                # 回退到页面元素
                                try:
                                    curr_time = convertTime(current_ele.text)
                                except:
                                    pass

                            # 检查视频是否在播放（时间是否在增加）
                            if curr_time > last_time:
                                no_progress_count = 0
                                last_time = curr_time
                                print(f"    播放中... {curr_time}s", end='\r')
                            else:
                                no_progress_count += 1

                            # 检测暂停状态（只在真正检测到暂停时才处理）
                            if not video_playing:
                                consecutive_paused_count += 1
                                # 只有在连续检测到暂停超过3秒时才尝试恢复
                                if consecutive_paused_count == 3:
                                    try:
                                        play_control = driver.find_element(By.CLASS_NAME, "vjs-play-control")
                                        if "vjs-paused" in play_control.get_attribute("class"):
                                            play_control.click()
                                            print("\n    检测到暂停，已恢复播放")
                                    except:
                                        pass
                                    consecutive_paused_count = 0
                            else:
                                consecutive_paused_count = 0

                            # 如果30秒没有进度，可能视频已完成
                            if no_progress_count >= max_no_progress:
                                print(f"\n    检测到长时间无进度，可能视频已完成。")
                                break

                            time.sleep(1)

                        except Exception as e:
                            # 静默处理错误
                            time.sleep(1)

                    print("    视频处理完成。")
                    processed += 1
                    if not wait_and_refresh(driver, f"视频 {processed}/{total_videos}"):
                        return False
                    return True

                # 正常模式：有明确的时长
                # 如果剩余时间少于 5 秒，则跳过（但确保不是刚播放就判断）
                remaining_time = total_time - current_time_val
                if remaining_time < 5 and current_time_val > 10:  # 只有当已经播放超过10秒且剩余少于5秒时才跳过
                    print(f"    视频 {processed+1}/{total_videos} 已完成 ({current_time_val}s / {total_time}s，剩余 {remaining_time}s)。")
                    processed += 1
                    if not wait_and_refresh(driver, f"视频 {processed}/{total_videos}"):
                        return False
                    return True

                print(f"    开始播放视频 (总时长: {total_time}s, 当前: {current_time_val}s, 剩余: {remaining_time}s)")

                # 倍速设置 (尝试)
                try:
                    speed_btn = driver.find_element(By.CLASS_NAME, "vjs-playback-rate")
                    speed_btn.click()
                    time.sleep(0.5)
                    speed_btn.click() # 切换到 2x
                    print("    已设置倍速播放")
                except:
                    print("    无法设置倍速（可能不支持）")

                # 循环检测直到结束，使用 tqdm 显示进度条
                with tqdm(total=total_time, desc=f"    视频 {processed+1}/{total_videos}", unit="s", leave=True, ncols=80) as pbar:
                    # 初始化进度条到当前位置
                    if current_time_val > 0:
                        pbar.update(current_time_val)

                    last_time = current_time_val
                    consecutive_paused_count = 0  # 连续检测到暂停的次数

                    while True:
                        try:
                            # 优先使用 JavaScript 获取视频时间（更准确可靠）
                            curr_time = current_time_val
                            video_playing = True

                            try:
                                video_element = driver.find_element(By.TAG_NAME, "video")
                                js_current = driver.execute_script("return arguments[0].currentTime;", video_element)
                                js_paused = driver.execute_script("return arguments[0].paused;", video_element)
                                js_ended = driver.execute_script("return arguments[0].ended;", video_element)

                                if js_ended:
                                    pbar.n = total_time
                                    pbar.refresh()
                                    print("\n    视频播放完成（检测到 ended 状态）。")
                                    break

                                # 使用 JavaScript 获取的时间（更准确）
                                if js_current is not None and js_current >= 0:
                                    curr_time = int(js_current)

                                video_playing = not js_paused

                            except Exception as js_error:
                                # 如果 JavaScript 获取失败，回退到页面元素
                                try:
                                    curr_time = convertTime(current_ele.text)
                                except:
                                    curr_time = last_time

                            # 更新进度条（使用更准确的时间）
                            if curr_time > pbar.n:
                                pbar.update(curr_time - pbar.n)

                            # 检查是否播放完成（剩余时间少于3秒）
                            remaining = total_time - curr_time
                            if remaining < 3 and remaining >= 0:
                                pbar.n = total_time
                                pbar.refresh()
                                print("\n    视频播放完成。")
                                break

                            # 检测暂停状态（只在真正检测到暂停时才处理）
                            if not video_playing:
                                consecutive_paused_count += 1
                                # 只有在连续检测到暂停超过3秒时才尝试恢复
                                if consecutive_paused_count == 3:
                                    try:
                                        play_control = driver.find_element(By.CLASS_NAME, "vjs-play-control")
                                        if "vjs-paused" in play_control.get_attribute("class"):
                                            play_control.click()
                                            print("\n    检测到暂停，已恢复播放")
                                    except:
                                        pass
                            else:
                                consecutive_paused_count = 0
                                last_time = curr_time

                            time.sleep(1)

                        except Exception as e:
                            # 静默处理错误，避免频繁打印
                            time.sleep(1)

                print(f"    视频 {processed+1}/{total_videos} 处理完成。")
                processed += 1
                if not wait_and_refresh(driver, f"视频 {processed}/{total_videos}"):
                    return False
                return True

            except StaleElementReferenceException:
                print(f"    [重试] 视频 {processed+1}/{total_videos} 帧引用过期，重新定位...")
                time.sleep(1)
                continue
            except Exception as e:
                print(f"    视频 {processed+1}/{total_videos} 处理出错: {e}")
                import traceback
                traceback.print_exc()
                processed += 1  # 前进计数器，避免死循环

    except Exception as e:
        print(f"  视频模块出错: {e}")

    # ---------------- PPT 处理 ----------------
    try:
        driver.switch_to.default_content()
        iframe1 = locate_main_iframe(driver)
        driver.switch_to.frame(iframe1)
        ppt_frames = driver.find_elements(By.CSS_SELECTOR, PPT_SELECTORS)
        total_ppts = len(ppt_frames)
        if total_ppts > 0:
            print(f"  - 检测到 PPT/文档数量: {total_ppts}")

        processed = 0
        while processed < total_ppts:
            try:
                # 重新定位 iframe 和 PPT 帧列表
                driver.switch_to.default_content()
                iframe1 = locate_main_iframe(driver)
                driver.switch_to.frame(iframe1)
                ppt_frames = driver.find_elements(By.CSS_SELECTOR, PPT_SELECTORS)

                if processed >= len(ppt_frames):
                    print(f"    [警告] DOM 中剩余 PPT 帧({len(ppt_frames)})少于预期(已处理 {processed}, 总计 {total_ppts})")
                    break

                current_frame = ppt_frames[processed]

                # 在进入 iframe 前检测任务点是否已完成
                if module_already_completed(driver, current_frame):
                    print(f"    [跳过] PPT {processed+1}/{total_ppts} 任务点已标记完成，无需阅读。")
                    processed += 1
                    continue

                driver.switch_to.frame(current_frame)

                print(f"    正在处理 PPT {processed+1}/{total_ppts}...")

                # 1. 查找并点击 PPT 查看器自带的全屏按钮
                fullscreen_clicked = False
                fullscreen_selectors = [
                    'div.fullsrceen',
                    'span.fullsrceenIcon',
                    'button[title="全屏"]',
                    'span[title="全屏"]',
                    'div[title="全屏"]',
                    'a[title="全屏"]',
                    '[class*="fullscreen"]',
                    '[class*="FullScreen"]',
                    '[class*="full-screen"]',
                    '.bpFullScreen',
                    '#fullscreen',
                    '[data-action="fullscreen"]',
                ]
                for selector in fullscreen_selectors:
                    try:
                        fs_btn = WebDriverWait(driver, 2).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                        )
                        fs_btn.click()
                        fullscreen_clicked = True
                        print(f"    已点击 PPT 全屏按钮 ({selector})")
                        time.sleep(2)
                        break
                    except (TimeoutException, NoSuchElementException):
                        continue

                if not fullscreen_clicked:
                    print("    未找到 PPT 全屏按钮，继续常规阅读")

                # 2. 进入嵌套 iframe #panView，逐步滚动 PPT
                try:
                    panview = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.ID, 'panView'))
                    )
                    driver.switch_to.frame(panview)
                    print("    已进入 #panView iframe")

                    # 获取页数和总高度
                    page_count = driver.execute_script(
                        "return document.querySelectorAll('.pageNum').length;"
                    )
                    scroll_height = driver.execute_script(
                        "return document.documentElement.scrollHeight;"
                    )
                    client_height = driver.execute_script(
                        "return document.documentElement.clientHeight;"
                    )

                    print(f"    PPT 共 {page_count} 页，总高度 {scroll_height}px，可视高度 {client_height}px")

                    if scroll_height <= client_height:
                        print("    PPT 无需滚动（内容未超出视口）")
                    else:
                        print("    开始逐步滚动...")
                        # 每次滚动一页的高度，模拟真实阅读
                        step_height = client_height * 1.5
                        total_steps = int((scroll_height - client_height) / step_height) + 1

                        for i in range(total_steps):
                            driver.execute_script(
                                "document.documentElement.scrollTop += arguments[0];",
                                step_height
                            )
                            time.sleep(0.5)

                        # 确保滚到底部
                        driver.execute_script(
                            "document.documentElement.scrollTop = document.documentElement.scrollHeight;"
                        )
                        print("    已滚动到底部")

                except TimeoutException:
                    print("    未找到 #panView iframe，跳过 PPT 滚动")
                except Exception as e:
                    print(f"    PPT 滚动出错: {e}")

                # 3. 停留等待完成记录
                print("    已触底，停留 3 秒以确认完成...")
                time.sleep(3)

                # 4. 退出全屏
                if fullscreen_clicked:
                    try:
                        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                        time.sleep(1)
                    except:
                        pass

                print(f"    PPT {processed+1}/{total_ppts} 处理完毕。")
                processed += 1
                if not wait_and_refresh(driver, f"PPT {processed}/{total_ppts}"):
                    return False
                return True

            except StaleElementReferenceException:
                print(f"    [重试] PPT {processed+1}/{total_ppts} 帧引用过期，重新定位...")
                time.sleep(1)
                continue
            except Exception as e:
                print(f"    PPT {processed+1}/{total_ppts} 处理出错: {e}")
                import traceback
                traceback.print_exc()
                processed += 1  # 前进计数器，避免死循环

    except Exception as e:
        print(f"  PPT模块出错: {e}")

    # 4. 退出 iframe，准备返回
    driver.switch_to.default_content()
    return True

def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='超星慕课刷课脚本')
    parser.add_argument('-url', '--url', type=str, required=True,
                        help='课程章节页面的URL')
    parser.add_argument('--force', action='store_true',
                        help='强制播放模式：即使无法获取视频时长也继续播放')
    parser.add_argument(
        '--answers',
        default=str(Path(__file__).with_name('answers.json')),
        help='章节测验答案 JSON（默认使用项目内 answers.json）',
    )
    parser.add_argument(
        '--submit-answers',
        action='store_true',
        help='填写测验后提交并继续；不指定时仅暂存并停止，供人工检查',
    )
    parser.add_argument(
        '--answer-wait',
        type=int,
        default=60,
        help='等待测验题目和 iframe 加载的秒数（默认 60）',
    )
    parser.add_argument('--profile-dir', help='独立 Chrome 用户数据目录，用于保留登录状态')
    parser.add_argument('--driver', help='chromedriver.exe 路径；默认查找 PATH 和 Selenium 缓存')
    args = parser.parse_args()

    answers_path = Path(args.answers).expanduser().resolve()
    if not answers_path.is_file():
        parser.error(f'答案文件不存在: {answers_path}')
    
    print("="*60)
    print("   超星慕课刷课脚本 (Mac 单实例版)")
    print("   特点：只登录一次，自动顺序刷课，无需重复扫码。")
    print("="*60 + "\n")

    # 1. 启动浏览器 (只做一次)
    options = webdriver.ChromeOptions()
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-background-mode')
    if args.profile_dir:
        profile_path = Path(args.profile_dir).expanduser().resolve()
        profile_path.mkdir(parents=True, exist_ok=True)
        options.add_argument(f'--user-data-dir={profile_path}')
    resolved_driver = find_chromedriver(args.driver)
    bundled_driver = resolved_driver and Path(__file__).resolve().parent / "tools" in resolved_driver.parents
    if args.driver or bundled_driver:
        print(f"使用 ChromeDriver: {resolved_driver}")
    else:
        print("由 Selenium Manager 自动匹配 ChromeDriver。")

    driver = None
    try:
        driver = create_chrome_driver(options, args.driver)
        atexit.register(close_driver, driver)
        try:
            driver.maximize_window()
        except WebDriverException:
            pass
    except (FileNotFoundError, WebDriverException) as exc:
        print(f"浏览器启动失败: {exc}")
        print("请关闭使用同一 --profile-dir 的 Chrome 窗口后重试；也可用 --driver 指定兼容版本。")
        return
    
    # 2. 手动登录引导
    url = args.url
    try:
        driver.get(url)
        print("\n>>> 浏览器已打开。")
        print(">>> 请手动扫码登录，并【进入具体的课程章节列表页面】。")
        print(">>> (确保能看到左侧的章节目录)")
        input(">>> 准备就绪后，请按回车键 (Enter) 开始全自动刷课...")
    except Exception as e:
        print(f"浏览器启动失败: {e}")
        return

    # 3. 记录课程主页 URL，方便后续返回
    course_list_url = driver.current_url
    print(f"已锁定课程主页: {course_list_url}")
    handled_refs = set()

    while True:
        # 4. 扫描进度
        # 每次循环都扫描一次，确保状态最新
        unfinished_indices = scan_progress(driver, excluded_refs=handled_refs)

        if unfinished_indices is None:
            print("\n目录扫描失败，自动流程停止；浏览器保持在当前页面供检查。")
            return
        
        if not unfinished_indices:
            print("\n恭喜！所有章节已显示完成 (或未检测到未完成章节)。")
            break
            
        print(f"\n本轮待处理章节数: {len(unfinished_indices)}")
        
        # 5. 顺序处理
        for idx in unfinished_indices:
            try:
                # 确保在列表页
                if driver.current_url != course_list_url:
                    driver.get(course_list_url)
                    time.sleep(3)
                
                succeeded = process_single_chapter(
                    driver,
                    idx,
                    force=args.force,
                    answers_path=answers_path,
                    submit_answers=args.submit_answers,
                    answer_wait=args.answer_wait,
                )

                if succeeded is None:
                    handled_refs.add(idx)
                    print("  < 该项目尚未解锁，返回目录重新扫描其他可处理项目...")
                    driver.get(course_list_url)
                    time.sleep(3)
                    break

                if not succeeded:
                    print("\n自动流程已停在当前章节，请根据上面的提示检查浏览器。")
                    return

                handled_refs.add(idx)
                
                # 每完成一个任务点就返回目录，并放弃本轮缓存的未完成列表。
                print("  < 返回目录页并重新扫描未完成项目...")
                driver.get(course_list_url)
                time.sleep(3) # 等待列表刷新
                break

            except Exception as e:
                print(f"处理过程中发生异常: {e}")
                # 尝试恢复到目录页
                try:
                    driver.get(course_list_url)
                    time.sleep(5)
                    break
                except:
                    break
        
        # 询问是否再次扫描 (防止有漏网之鱼)
        print("\n一轮循环结束，准备重新扫描状态...")
        time.sleep(2)

    print("脚本运行结束。")
    close_driver(driver)
    atexit.unregister(close_driver)

if __name__ == "__main__":
    main()
