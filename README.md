
# 国科大慕课学习

一个专为中国科学院大学在线慕课平台设计的自动化学习工具，支持按目录顺序自动观看视频、阅读 PPT、填写章节测验，并智能跳过已完成内容。
[参考自](https://github.com/HangboZhu/ucas-mooc-automate)

如果你觉得我这个项目有用，给个star🌟吧～

可以直接从release下载打包好的程序，不需要配置环境

或者可以直接运行gui.py来跑代码

<img width="1680" height="1109" alt="b5eaae119870f2fb585f54a806951a12" src="https://github.com/user-attachments/assets/cc415ce4-8f0b-4715-a6f8-daecaa6ac32c" />


### 步骤1：环境准备

确保你的计算机上已经安装了 Python (3.7+) 和 pip ，谷歌浏览器等工具。

### 步骤2：安装依赖包

在虚拟环境中运行以下命令安装代码所需的依赖包：

```bash
pip install -r requirements.txt
```

**主要依赖：**
- `selenium==4.15.2` - 网页自动化框架
- `PyAutoGUI==0.9.54` - 鼠标键盘控制
- `tqdm==4.66.1` - 进度条显示

### 步骤3：获取课程URL

> **重要：需要手动配置课程URL到代码中**

1. 进入国科大在线网站 [中国科学院大学网络教学平台](http://mooc.ucas.edu.cn/portal)
2. 点击右上角"校内登录"
3. 选择使用手机号登录（Mooc绑定的手机号与密码）
4. 登录成功后点击右上角"个人空间"
5. 在"我学的课"中找到目标课程（如"硕士学位英语（慕课学习）"）
6. 进入课程后点击任意小节，复制浏览器地址栏的完整网址

8. **重要：** 将获取的网址替换到 `main.py` 第305行的 `url` 变量

### 步骤4：运行脚本

推荐使用桌面界面：

```bash
python gui.py
```

在界面中填写课程 URL，点击“启动浏览器”；登录并进入课程章节页后，点击“登录完成，开始学习”。运行日志、答案提交开关、登录配置目录和 ChromeDriver 路径均可在界面中管理。

每个文本框右侧的 `×` 可以单独清空内容；“重置表单”会恢复默认配置。

### Windows 打包

安装 PyInstaller 后执行：

```bash
python -m PyInstaller --clean --noconfirm ucas-mooc-aq.spec
```

打包结果位于 `dist`，主程序为 `UCAS-MOOC-Assistant.exe`。请保留同目录下的 `UCAS-MOOC-Worker.exe`，它负责后台执行浏览器自动化。

也可以继续使用命令行：

```bash

# --force 即使没有获取到这个进度条到终止时间，也要进行观看
python main.py -url XXXX --force
```

项目内置 `answers.json` 中课程的完整连续处理命令：

```bash
python main.py --url "课程章节 URL" --force --submit-answers
```

程序只在传入 `--submit-answers` 时提交测验并继续下一章节。未传入时会填写并暂存当前测验，然后停在该页供人工检查。也可以用 `--answers` 指定其他同格式答案文件。

可用 `--profile-dir .chrome-profile` 保留独立浏览器登录状态；如果无法自动找到浏览器驱动，可用 `--driver "C:\path\to\chromedriver.exe"` 指定。



---

**免责声明：** 本工具仅供学习和技术研究使用，请遵守平台使用规则和学术诚信要求。
