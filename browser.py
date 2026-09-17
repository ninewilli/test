from __future__ import annotations

import os
import shutil
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service


def _version_key(path: Path) -> tuple[int, ...]:
    for part in reversed(path.parts):
        pieces = part.split(".")
        if pieces and all(piece.isdigit() for piece in pieces):
            return tuple(int(piece) for piece in pieces)
    return ()


def find_chromedriver(explicit_path: str | None = None) -> Path | None:
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"ChromeDriver 不存在: {path}")
        return path

    environment_path = os.environ.get("CHROMEDRIVER")
    if environment_path:
        path = Path(environment_path).expanduser().resolve()
        if path.is_file():
            return path

    command_path = shutil.which("chromedriver")
    if command_path:
        return Path(command_path).resolve()

    executable = "chromedriver.exe" if os.name == "nt" else "chromedriver"
    bundled_root = Path(__file__).resolve().parent / "tools"
    bundled = list(bundled_root.glob(f"**/{executable}")) if bundled_root.is_dir() else []
    if bundled:
        return max(bundled, key=_version_key)

    cache_root = Path.home() / ".cache" / "selenium" / "chromedriver"
    cached = list(cache_root.glob(f"**/{executable}")) if cache_root.is_dir() else []
    if cached:
        return max(cached, key=_version_key)
    return None


def create_chrome_driver(
    options: webdriver.ChromeOptions,
    driver_path: str | None = None,
) -> webdriver.Chrome:
    bundled_driver = find_chromedriver(driver_path)
    bundled_root = Path(__file__).resolve().parent / "tools"
    if (
        driver_path
        or os.environ.get("CHROMEDRIVER")
        or (bundled_driver and bundled_root in bundled_driver.parents)
    ):
        return webdriver.Chrome(
            service=Service(executable_path=str(bundled_driver)),
            options=options,
        )

    # Let Selenium Manager match the installed Chrome version first. A cached
    # driver can be stale after Chrome auto-updates.
    try:
        return webdriver.Chrome(options=options)
    except Exception as manager_error:
        resolved_driver = find_chromedriver()
        if not resolved_driver:
            raise manager_error
        return webdriver.Chrome(
            service=Service(executable_path=str(resolved_driver)),
            options=options,
        )
