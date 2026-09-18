from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from urllib.parse import urlparse


FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
CONFIG_PATH = APP_ROOT / ".gui-config.json"


def default_answers_path() -> Path:
    external = APP_ROOT / "answers.json"
    bundled = RESOURCE_ROOT / "answers.json"
    if FROZEN and not external.is_file() and bundled.is_file():
        try:
            shutil.copy2(bundled, external)
        except OSError:
            return bundled
    return external if external.is_file() else bundled


@dataclass
class LaunchConfig:
    url: str
    profile_dir: str
    answers_path: str
    driver_path: str = ""
    answer_wait: int = 60
    force: bool = True
    submit_answers: bool = False


def validate_config(config: LaunchConfig) -> list[str]:
    problems: list[str] = []
    parsed = urlparse(config.url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        problems.append("请输入有效的课程 URL。")
    if not Path(config.answers_path).expanduser().is_file():
        problems.append("答案文件不存在。")
    if config.driver_path and not Path(config.driver_path).expanduser().is_file():
        problems.append("ChromeDriver 文件不存在。")
    if not 1 <= config.answer_wait <= 600:
        problems.append("答题等待时间必须在 1 到 600 秒之间。")
    return problems


def build_command(config: LaunchConfig) -> list[str]:
    if FROZEN:
        worker_name = "UCAS-MOOC-Worker.exe" if os.name == "nt" else "UCAS-MOOC-Worker"
        command = [str(APP_ROOT / worker_name)]
    else:
        command = [sys.executable, "-u", str(APP_ROOT / "main.py")]
    command.extend([
        "--url",
        config.url.strip(),
        "--answers",
        str(Path(config.answers_path).expanduser().resolve()),
        "--answer-wait",
        str(config.answer_wait),
    ])
    if config.profile_dir:
        command.extend(
            ["--profile-dir", str(Path(config.profile_dir).expanduser().resolve())]
        )
    if config.driver_path:
        command.extend(
            ["--driver", str(Path(config.driver_path).expanduser().resolve())]
        )
    if config.force:
        command.append("--force")
    if config.submit_answers:
        command.append("--submit-answers")
    return command


class MoocApp(tk.Tk):
    COLORS = {
        "canvas": "#F3F6F8",
        "surface": "#FFFFFF",
        "header": "#17324D",
        "text": "#172B3A",
        "muted": "#607383",
        "border": "#D8E1E7",
        "accent": "#0B7A75",
        "accent_active": "#08645F",
        "warning": "#C46A24",
        "danger": "#B64040",
        "log": "#101820",
        "log_text": "#D7E2EA",
    }

    def __init__(self) -> None:
        super().__init__()
        self.title("UCAS MOOC 学习助手")
        self.geometry("1120x740")
        self.minsize(1040, 720)
        self.configure(bg=self.COLORS["canvas"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.process: subprocess.Popen[str] | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._stopping = False

        defaults = self._load_config()
        self.url_var = tk.StringVar(value=defaults.url)
        self.profile_var = tk.StringVar(value=defaults.profile_dir)
        self.answers_var = tk.StringVar(value=defaults.answers_path)
        self.driver_var = tk.StringVar(value=defaults.driver_path)
        self.wait_var = tk.IntVar(value=defaults.answer_wait)
        self.force_var = tk.BooleanVar(value=defaults.force)
        self.submit_var = tk.BooleanVar(value=defaults.submit_answers)
        self.status_var = tk.StringVar(value="待机")

        self._configure_styles()
        self._build_layout()
        self.after(80, self._drain_events)

    def _default_config(self) -> LaunchConfig:
        return LaunchConfig(
            url="",
            profile_dir=str(APP_ROOT / ".chrome-profile"),
            answers_path=str(default_answers_path()),
        )

    def _load_config(self) -> LaunchConfig:
        defaults = self._default_config()
        try:
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            known = asdict(defaults)
            known.update({key: value for key, value in payload.items() if key in known})
            return LaunchConfig(**known)
        except (OSError, ValueError, TypeError):
            return defaults

    def _current_config(self) -> LaunchConfig:
        try:
            wait_seconds = int(self.wait_var.get())
        except (tk.TclError, ValueError):
            wait_seconds = 0
        return LaunchConfig(
            url=self.url_var.get(),
            profile_dir=self.profile_var.get(),
            answers_path=self.answers_var.get(),
            driver_path=self.driver_var.get(),
            answer_wait=wait_seconds,
            force=self.force_var.get(),
            submit_answers=self.submit_var.get(),
        )

    def _save_config(self) -> None:
        try:
            CONFIG_PATH.write_text(
                json.dumps(asdict(self._current_config()), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(
            "TFrame", background=self.COLORS["canvas"]
        )
        style.configure(
            "Surface.TFrame", background=self.COLORS["surface"]
        )
        style.configure(
            "TLabel",
            background=self.COLORS["canvas"],
            foreground=self.COLORS["text"],
            font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "Surface.TLabel", background=self.COLORS["surface"]
        )
        style.configure(
            "Field.TLabel",
            background=self.COLORS["surface"],
            foreground=self.COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Section.TLabel",
            background=self.COLORS["surface"],
            foreground=self.COLORS["text"],
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        style.configure(
            "TEntry", fieldbackground="#FBFCFD", bordercolor=self.COLORS["border"], padding=6
        )
        style.configure(
            "TSpinbox", fieldbackground="#FBFCFD", bordercolor=self.COLORS["border"], padding=5
        )
        style.configure(
            "TCheckbutton",
            background=self.COLORS["surface"],
            foreground=self.COLORS["text"],
            font=("Microsoft YaHei UI", 10),
        )
        style.map("TCheckbutton", background=[("active", self.COLORS["surface"])])
        style.configure(
            "Primary.TButton",
            background=self.COLORS["accent"],
            foreground="white",
            borderwidth=0,
            padding=(18, 9),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("active", self.COLORS["accent_active"]), ("disabled", "#A7B8BE")],
        )
        style.configure(
            "Secondary.TButton",
            background="#E8EFF2",
            foreground=self.COLORS["text"],
            borderwidth=0,
            padding=(14, 8),
            font=("Microsoft YaHei UI", 9),
        )
        style.map("Secondary.TButton", background=[("active", "#DCE7EB")])
        style.configure(
            "Danger.TButton",
            background="#F6E7E7",
            foreground=self.COLORS["danger"],
            borderwidth=0,
            padding=(14, 8),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map("Danger.TButton", background=[("active", "#ECD3D3")])
        style.configure(
            "Icon.TButton",
            background="#EEF3F5",
            foreground=self.COLORS["muted"],
            borderwidth=0,
            padding=(7, 6),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map("Icon.TButton", background=[("active", "#DCE7EB")])

    def _build_layout(self) -> None:
        header = tk.Frame(self, bg=self.COLORS["header"], height=82)
        header.pack(fill="x")
        header.pack_propagate(False)
        title_wrap = tk.Frame(header, bg=self.COLORS["header"])
        title_wrap.pack(fill="both", expand=True, padx=26, pady=11)
        tk.Label(
            title_wrap,
            text="UCAS MOOC 学习助手",
            bg=self.COLORS["header"],
            fg="white",
            font=("Microsoft YaHei UI", 20, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_wrap,
            text="配置课程后启动浏览器，完成登录，再开始自动学习。",
            bg=self.COLORS["header"],
            fg="#C8D6E2",
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(3, 0))

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=0, minsize=390)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        settings = ttk.Frame(body, style="Surface.TFrame", padding=18)
        settings.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        settings.columnconfigure(0, weight=1)
        settings_header = ttk.Frame(settings, style="Surface.TFrame")
        settings_header.grid(row=0, column=0, sticky="ew", pady=(0, 11))
        settings_header.columnconfigure(0, weight=1)
        ttk.Label(settings_header, text="运行设置", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(
            settings_header,
            text="重置表单",
            style="Secondary.TButton",
            command=self._reset_form,
        ).grid(row=0, column=1, sticky="e")

        self._field(settings, 1, "课程 URL", self.url_var)
        self._path_field(settings, 3, "登录配置目录", self.profile_var, directory=True)
        self._path_field(settings, 5, "答案文件", self.answers_var, json_file=True)
        self._path_field(settings, 7, "ChromeDriver（可选）", self.driver_var)

        wait_row = ttk.Frame(settings, style="Surface.TFrame")
        wait_row.grid(row=9, column=0, sticky="ew", pady=(1, 8))
        wait_row.columnconfigure(0, weight=1)
        ttk.Label(wait_row, text="答题加载等待", style="Field.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Spinbox(
            wait_row, from_=1, to=600, textvariable=self.wait_var, width=7, justify="center"
        ).grid(row=0, column=1, padx=(8, 5))
        ttk.Label(wait_row, text="秒", style="Surface.TLabel").grid(row=0, column=2)

        toggles = ttk.Frame(settings, style="Surface.TFrame")
        toggles.grid(row=10, column=0, sticky="ew", pady=(0, 8))
        ttk.Checkbutton(toggles, text="无法获取时长时继续播放", variable=self.force_var).pack(
            anchor="w", pady=2
        )
        ttk.Checkbutton(toggles, text="自动提交章节测验", variable=self.submit_var).pack(
            anchor="w", pady=2
        )

        notice = tk.Frame(settings, bg="#FFF4E8", highlightthickness=1, highlightbackground="#F0D5B6")
        notice.grid(row=11, column=0, sticky="ew", pady=(0, 11))
        tk.Label(
            notice,
            text="自动提交开启后，匹配成功的答案会直接提交。",
            bg="#FFF4E8",
            fg="#8A4D1D",
            justify="left",
            wraplength=320,
            padx=12,
            pady=7,
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w")

        self.launch_button = ttk.Button(
            settings, text="启动浏览器", style="Primary.TButton", command=self._launch
        )
        self.launch_button.grid(row=12, column=0, sticky="ew")
        self.continue_button = ttk.Button(
            settings,
            text="登录完成，开始学习",
            style="Secondary.TButton",
            command=self._continue,
            state="disabled",
        )
        self.continue_button.grid(row=13, column=0, sticky="ew", pady=(9, 0))

        log_panel = ttk.Frame(body, style="Surface.TFrame", padding=0)
        log_panel.grid(row=0, column=1, sticky="nsew")
        log_panel.columnconfigure(0, weight=1)
        log_panel.rowconfigure(1, weight=1)
        log_header = ttk.Frame(log_panel, style="Surface.TFrame", padding=(18, 14))
        log_header.grid(row=0, column=0, sticky="ew")
        log_header.columnconfigure(1, weight=1)
        ttk.Label(log_header, text="运行日志", style="Section.TLabel").grid(row=0, column=0)
        self.status_label = tk.Label(
            log_header,
            textvariable=self.status_var,
            bg="#E4ECEF",
            fg=self.COLORS["muted"],
            padx=10,
            pady=4,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.status_label.grid(row=0, column=2, sticky="e")

        self.log = ScrolledText(
            log_panel,
            bg=self.COLORS["log"],
            fg=self.COLORS["log_text"],
            insertbackground="white",
            selectbackground="#28536B",
            relief="flat",
            borderwidth=0,
            padx=16,
            pady=14,
            font=("Consolas", 10),
            wrap="word",
            state="disabled",
        )
        self.log.grid(row=1, column=0, sticky="nsew")
        self.log.tag_configure("error", foreground="#FF9B9B")
        self.log.tag_configure("success", foreground="#76D6B6")
        self.log.tag_configure("muted", foreground="#8195A3")

        footer = ttk.Frame(log_panel, style="Surface.TFrame", padding=(14, 12))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Button(
            footer, text="清空日志", style="Secondary.TButton", command=self._clear_log
        ).grid(row=0, column=1, padx=(0, 8))
        self.stop_button = ttk.Button(
            footer,
            text="停止运行",
            style="Danger.TButton",
            command=self._stop,
            state="disabled",
        )
        self.stop_button.grid(row=0, column=2)

        self._append_log("填写课程 URL 后点击“启动浏览器”。\n", "muted")

    def _field(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w")
        line = ttk.Frame(parent, style="Surface.TFrame")
        line.grid(row=row + 1, column=0, sticky="ew", pady=(4, 8))
        line.columnconfigure(0, weight=1)
        ttk.Entry(line, textvariable=variable).grid(row=0, column=0, sticky="ew")
        ttk.Button(
            line,
            text="×",
            width=3,
            style="Icon.TButton",
            command=lambda: variable.set(""),
        ).grid(row=0, column=1, padx=(6, 0))

    def _reset_form(self) -> None:
        defaults = self._default_config()
        self.url_var.set("")
        self.profile_var.set(defaults.profile_dir)
        self.answers_var.set(defaults.answers_path)
        self.driver_var.set("")
        self.wait_var.set(defaults.answer_wait)
        self.force_var.set(defaults.force)
        self.submit_var.set(defaults.submit_answers)

    def _path_field(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        directory: bool = False,
        json_file: bool = False,
    ) -> None:
        ttk.Label(parent, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w")
        line = ttk.Frame(parent, style="Surface.TFrame")
        line.grid(row=row + 1, column=0, sticky="ew", pady=(4, 8))
        line.columnconfigure(0, weight=1)
        ttk.Entry(line, textvariable=variable).grid(row=0, column=0, sticky="ew")

        ttk.Button(
            line,
            text="×",
            width=3,
            style="Icon.TButton",
            command=lambda: variable.set(""),
        ).grid(row=0, column=1, padx=(6, 0))

        def browse() -> None:
            if directory:
                chosen = filedialog.askdirectory(initialdir=variable.get() or APP_ROOT)
            else:
                filetypes = [("JSON 文件", "*.json"), ("所有文件", "*.*")] if json_file else [
                    ("可执行文件", "*.exe"), ("所有文件", "*.*")
                ]
                chosen = filedialog.askopenfilename(
                    initialdir=Path(variable.get()).parent if variable.get() else APP_ROOT,
                    filetypes=filetypes,
                )
            if chosen:
                variable.set(chosen)

        ttk.Button(line, text="浏览", style="Secondary.TButton", command=browse).grid(
            row=0, column=2, padx=(6, 0)
        )

    def _set_status(self, text: str, kind: str = "idle") -> None:
        colors = {
            "idle": ("#E4ECEF", self.COLORS["muted"]),
            "running": ("#DDF2EC", "#176A55"),
            "waiting": ("#FFF0DE", "#94521E"),
            "error": ("#F8E1E1", self.COLORS["danger"]),
        }
        background, foreground = colors[kind]
        self.status_var.set(text)
        self.status_label.configure(bg=background, fg=foreground)

    def _append_log(self, text: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text, tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _launch(self) -> None:
        if self.process and self.process.poll() is None:
            return
        config = self._current_config()
        problems = validate_config(config)
        if problems:
            messagebox.showerror("无法启动", "\n".join(problems), parent=self)
            return

        self._save_config()
        self._clear_log()
        self._append_log("正在启动 Chrome，请稍候...\n", "muted")
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUNBUFFERED"] = "1"
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW
        try:
            self.process = subprocess.Popen(
                build_command(config),
                cwd=APP_ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=environment,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except OSError as exc:
            self.process = None
            self._append_log(f"启动失败: {exc}\n", "error")
            self._set_status("启动失败", "error")
            return

        self._stopping = False
        self.launch_button.configure(state="disabled")
        self.continue_button.configure(state="normal")
        self.stop_button.configure(state="normal")
        self._set_status("浏览器启动中", "running")
        threading.Thread(target=self._read_output, daemon=True).start()
        threading.Thread(target=self._wait_for_exit, daemon=True).start()

    def _read_output(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        for line in iter(process.stdout.readline, ""):
            self.events.put(("output", line))

    def _wait_for_exit(self) -> None:
        process = self.process
        if process:
            self.events.put(("exit", process.wait()))

    def _continue(self) -> None:
        if not self.process or self.process.poll() is not None or not self.process.stdin:
            return
        try:
            self.process.stdin.write("\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            return
        self.continue_button.configure(state="disabled")
        self._append_log("已确认登录，开始扫描课程。\n", "success")
        self._set_status("自动学习中", "running")

    def _stop(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        self._stopping = True
        self._set_status("正在停止", "waiting")
        self._append_log("正在停止当前任务...\n", "muted")
        try:
            self.process.terminate()
        except OSError:
            pass

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "output":
                    line = str(payload)
                    tag = "error" if any(word in line for word in ("失败", "出错", "[停止]")) else None
                    if any(word in line for word in ("已完成", "成功", "恭喜")):
                        tag = "success"
                    self._append_log(line, tag)
                    if "浏览器已打开" in line or "准备就绪后" in line:
                        self._set_status("等待登录确认", "waiting")
                elif event == "exit":
                    self._handle_exit(int(payload))
        except queue.Empty:
            pass
        self.after(80, self._drain_events)

    def _handle_exit(self, return_code: int) -> None:
        stopped = self._stopping
        self.process = None
        self.launch_button.configure(state="normal")
        self.continue_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        if stopped:
            self._set_status("已停止")
            self._append_log("任务已停止。\n", "muted")
        elif return_code == 0:
            self._set_status("运行结束")
            self._append_log("进程已正常结束。\n", "success")
        else:
            self._set_status("异常退出", "error")
            self._append_log(f"进程异常退出，代码: {return_code}\n", "error")

    def _on_close(self) -> None:
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno(
                "退出程序", "当前任务仍在运行。停止任务并退出吗？", parent=self
            ):
                return
            try:
                self.process.terminate()
            except OSError:
                pass
        self._save_config()
        self.destroy()


def main() -> None:
    app = MoocApp()
    app.mainloop()


if __name__ == "__main__":
    main()
