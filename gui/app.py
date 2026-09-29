"""ChickenRice desktop workbench, using the locally installed PyQt5 runtime."""
from __future__ import annotations

import sys
from pathlib import Path

# Resolve sibling modules from this file's directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import time
import traceback
from collections import Counter
from dataclasses import replace

from PyQt5.QtCore import QLockFile, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextBrowser, QVBoxLayout, QWidget,
)

from backend import (
    DATA, ENGINE, PRESETS, ROOT, SUFFIXES, Runner, Settings, discover_files,
    expected_outputs, load_settings, output_dir, read_cues, save_settings, validate,
)
from desktop_instance import APP_TITLE, activate_window, instance_lock_path

STYLE = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #223147; }
QMainWindow, QWidget#shell, QWidget#page { background: #f4f6fa; }
QWidget#sidebar { background: #edf1f7; border-right: 1px solid #dfe5ef; }
QFrame#card { background: white; border: 1px solid #e0e6ef; border-radius: 12px; }
QLabel { background: transparent; border: none; }
QLabel#title { font-size: 25px; font-weight: 600; color: #18283e; }
QLabel#section { font-size: 15px; font-weight: 600; }
QLabel#muted { color: #66758a; font-size: 12px; }
QLabel#brand { background: #2667d5; color: white; border-radius: 12px; font-size: 22px; font-weight: 600; }
QLabel#pill { color: #2359aa; background: #eaf2ff; border-radius: 9px; padding: 5px 10px; }
QPushButton { background: white; border: 1px solid #d8e0eb; border-radius: 7px; padding: 8px 14px; }
QPushButton:hover { background: #f0f5ff; border-color: #88ace6; }
QPushButton:pressed { background: #e2edff; }
QPushButton:disabled { color: #9aa7b8; background: #f4f6f9; border-color: #e5e9ef; }
QPushButton#primary { background: #2667d5; color: white; border: 1px solid #2667d5; font-weight: 600; }
QPushButton#primary:hover { background: #1d58bc; }
QPushButton#primary:disabled { background: #abc4ed; border-color: #abc4ed; }
QPushButton#nav { text-align: left; background: transparent; border: none; padding: 13px 15px; }
QPushButton#nav:checked { background: #dce8fa; color: #205cb7; font-weight: 600; }
QPushButton#nav:hover { background: #e2eaf6; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: white; border: 1px solid #d5dfea; border-radius: 6px; padding: 7px; min-height: 18px; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #397de0; }
QLineEdit:disabled, QComboBox:disabled { background: #f1f4f8; color: #8896a8; }
QComboBox::drop-down { border: none; width: 25px; }
QCheckBox { spacing: 8px; padding: 4px 0; }
QCheckBox::indicator { width: 17px; height: 17px; }
QTableWidget { background: white; border: none; gridline-color: #edf1f6; selection-background-color: #eaf2ff; selection-color: #203651; }
QTableWidget::item { padding: 8px; border-bottom: 1px solid #edf1f6; }
QHeaderView::section { background: #f7f9fc; border: none; border-bottom: 1px solid #e4eaf2; padding: 8px; color: #6a7b91; }
QTabWidget::pane { border: none; background: white; }
QTabBar::tab { background: transparent; border-bottom: 2px solid transparent; padding: 9px 16px; color: #708096; }
QTabBar::tab:selected { color: #245fbf; border-bottom: 2px solid #2667d5; }
QPlainTextEdit, QTextBrowser { background: white; border: none; padding: 8px; }
QProgressBar { background: #e8eef7; border: none; border-radius: 4px; height: 7px; text-align: center; }
QProgressBar::chunk { background: #3777dc; border-radius: 4px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: #c4cfde; border-radius: 4px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QSplitter::handle { background: transparent; width: 12px; height: 10px; }
QSplitter::handle:vertical { background: #dfe5ef; height: 6px; border-radius: 3px; }
"""


def label(text, name="", wrap=False):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text)
    widget.setCursor(Qt.PointingHandCursor)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(callback)
    return widget


def card(title=None):
    widget = QFrame()
    widget.setObjectName("card")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(12)
    if title:
        layout.addWidget(label(title, "section"))
    return widget, layout


def scroll(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


class Worker(QThread):
    event = pyqtSignal(str, object)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, sources, settings, parent=None, runner_factory=Runner):
        super().__init__(parent)
        self.sources, self.settings = sources, settings
        self.runner = runner_factory(self.event.emit)

    def run(self):
        try:
            self.completed.emit(self.runner.run(self.sources, self.settings))
        except Exception:
            self.failed.emit(traceback.format_exc())


class Window(QMainWindow):
    def __init__(self, settings=None, settings_path=DATA / "settings.json"):
        super().__init__()
        self.settings_path = settings_path
        self.load_error = ""
        try:
            self.settings = settings or load_settings(settings_path)
        except (ValueError, TypeError, OSError) as error:
            self.settings = Settings()
            self.load_error = f"上次设置未能读取，已载入默认值：{error}"
        self.sources, self.records = [], {}
        self.worker = None
        self.run_dir = None
        self.current_file = None
        self.preview_path = None
        self.started_at = 0
        self.busy = False
        self.pending_close = False
        self.setWindowTitle(APP_TITLE)
        self.resize(1250, 860)
        self.setMinimumSize(1020, 720)
        self.setAcceptDrops(True)
        self.setStyleSheet(STYLE + '\nQComboBox::down-arrow { image: url("' +
                          (Path(__file__).parent / "chevron.svg").as_posix() + '"); width: 12px; height: 8px; }')
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor("#2667d5"))
        painter = QPainter(pixmap)
        painter.setPen(Qt.white)
        painter.setFont(QFont("Segoe UI", 24, QFont.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "CR")
        painter.end()
        self.setWindowIcon(QIcon(pixmap))
        shell = QWidget()
        shell.setObjectName("shell")
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.pages = QStackedWidget()
        layout.addWidget(self.make_sidebar())
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(shell)
        self.pages.addWidget(self.make_workbench())
        self.pages.addWidget(self.make_settings())
        self.pages.addWidget(self.make_help())
        self.apply_settings()
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.timeout.connect(self.update_elapsed)
        self.elapsed_timer.start(1000)
        if self.load_error:
            self.log.appendPlainText(self.load_error)
            self.status.setText("上次设置读取失败，请检查参数设置；详情见运行日志。")

    def make_sidebar(self):
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(174)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(15, 26, 15, 20)
        logo = label("CR", "brand")
        logo.setFixedSize(48, 48)
        logo.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo)
        layout.addSpacing(4)
        name = label("ChickenRice", "section")
        name.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(name)
        layout.addWidget(label("日语音声 · 中文字幕", "muted"))
        layout.addSpacing(28)
        self.nav = []
        for index, text in enumerate(("字幕工作台", "参数设置", "功能说明")):
            btn = button(text, lambda checked=False, i=index: self.navigate(i))
            btn.setObjectName("nav")
            btn.setCheckable(True)
            btn.setChecked(index == 0)
            self.nav.append(btn)
            layout.addWidget(btn)
        layout.addStretch()
        layout.addWidget(label("本地推理", "pill"))
        self.engine_badge = label("引擎已就绪" if ENGINE.is_file() else "引擎未找到", "muted")
        layout.addWidget(self.engine_badge)
        layout.addWidget(label("Faster Whisper\nTransWithAI", "muted"))
        return side

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        for i, btn in enumerate(self.nav):
            btn.setChecked(i == index)

    def page(self, title, subtitle):
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 25, 26, 22)
        layout.setSpacing(16)
        layout.addWidget(label(title, "title"))
        layout.addWidget(label(subtitle, "muted", True))
        return page, layout

    def make_workbench(self):
        page, layout = self.page("字幕工作台", "音视频文件管理、字幕生成与结果预览。")
        split = QSplitter(Qt.Horizontal)
        left = QSplitter(Qt.Vertical)
        left.setMinimumWidth(420)
        left.setChildrenCollapsible(False)
        left.setHandleWidth(8)
        self.content_split = left
        files_card, files_layout = card()
        self.files_card = files_card
        files_card.setMinimumHeight(200)
        files_layout.setContentsMargins(14, 12, 14, 12)
        files_layout.setSpacing(8)
        row = QHBoxLayout()
        self.queue_title = label("文件队列 · 0", "section")
        row.addWidget(self.queue_title)
        row.addStretch()
        self.add_button = button("添加文件", self.choose_files)
        self.folder_button = button("添加文件夹", self.choose_folder)
        self.remove_button = button("移除", self.remove_selected)
        self.clear_button = button("清空", self.clear_files)
        row.addWidget(self.add_button)
        row.addWidget(self.folder_button)
        row.addWidget(self.remove_button)
        row.addWidget(self.clear_button)
        files_layout.addLayout(row)
        drop = label("支持拖入音视频文件或文件夹", "muted")
        drop.setToolTip("支持 WAV、FLAC、MP3 及常见视频格式；文件夹包含子目录。")
        files_layout.addWidget(drop)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["文件", "大小", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 78)
        self.table.setColumnWidth(2, 144)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setMinimumHeight(80)
        self.table.setAcceptDrops(False)
        self.table.itemSelectionChanged.connect(self.preview_selected)
        files_layout.addWidget(self.table)
        self.import_hint = label("递归导入子文件夹；相同文件路径自动去重。", "muted", True)
        files_layout.addWidget(self.import_hint)
        left.addWidget(files_card)

        result_card, result_layout = card()
        result_card.setMinimumHeight(240)
        self.tabs = QTabWidget()
        self.preview_expanded = False
        self.expand_preview_button = button("展开预览", self.toggle_preview)
        preview_actions = QWidget()
        actions_layout = QHBoxLayout(preview_actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(6)
        self.open_output_button = button("打开目录", self.open_output)
        self.open_subtitle_button = button("打开字幕", self.open_subtitle)
        actions_layout.addWidget(self.open_output_button)
        actions_layout.addWidget(self.open_subtitle_button)
        actions_layout.addWidget(self.expand_preview_button)
        self.tabs.setCornerWidget(preview_actions)
        preview = QWidget()
        pv = QVBoxLayout(preview)
        pv.setContentsMargins(0, 10, 0, 0)
        self.preview_hint = label("显示所选文件的字幕内容与时间轴。", "muted", True)
        pv.addWidget(self.preview_hint)
        self.cues = QTableWidget(0, 3)
        self.cues.setHorizontalHeaderLabels(["开始", "结束", "字幕内容"])
        self.cues.setColumnWidth(0, 105)
        self.cues.setColumnWidth(1, 105)
        self.cues.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.cues.horizontalHeader().sectionResized.connect(
            lambda: QTimer.singleShot(0, self.resize_cue_rows))
        self.cues.verticalHeader().hide()
        self.cues.setShowGrid(False)
        self.cues.setWordWrap(True)
        self.cues.setTextElideMode(Qt.ElideNone)
        self.cues.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.cues.setMinimumHeight(100)
        self.cues.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cues.setSelectionBehavior(QAbstractItemView.SelectRows)
        pv.addWidget(self.cues, 1)
        self.tabs.addTab(preview, "字幕预览")
        log_page = QWidget()
        lv = QVBoxLayout(log_page)
        lv.setContentsMargins(0, 6, 0, 0)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2500)
        self.log.setFont(QFont("Consolas", 10))
        lv.addWidget(self.log)
        lv.addWidget(button("打开本次日志文件夹", self.open_logs), 0, Qt.AlignLeft)
        self.tabs.addTab(log_page, "运行日志")
        result_layout.addWidget(self.tabs)
        left.addWidget(result_card)
        left.setSizes([210, 404])
        left.setStretchFactor(0, 0)
        left.setStretchFactor(1, 1)
        left.handle(1).setToolTip("文件队列与字幕预览高度调节")
        split.addWidget(left)

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 2, 0)
        panel_layout.setSpacing(14)
        task_card, task_layout = card("处理方式")
        self.task = QComboBox()
        self.task.addItem("日语音频 → 中文字幕", "translate")
        self.task.addItem("日语音频 → 日文转录", "transcribe")
        task_layout.addWidget(self.task)
        self.model_hint = label("", "muted", True)
        task_layout.addWidget(self.model_hint)
        self.device = QComboBox()
        self.device.addItem("NVIDIA GPU", "cuda")
        self.device.addItem("CPU", "cpu")
        task_layout.addWidget(self.device)
        self.compute = QComboBox()
        task_layout.addWidget(self.compute)
        panel_layout.addWidget(task_card)
        preset_card, preset_layout = card("音声预设")
        self.preset = QComboBox()
        self.preset.addItems(["标准", "轻声 / 耳语", "自定义"])
        preset_layout.addWidget(self.preset)
        self.preset_hint = label("", "muted", True)
        preset_layout.addWidget(self.preset_hint)
        self.parameters_button = button("调整详细参数  →", lambda: self.navigate(1))
        preset_layout.addWidget(self.parameters_button)
        panel_layout.addWidget(preset_card)
        output_card, output_layout = card("字幕输出")
        row = QHBoxLayout()
        self.formats = {}
        for fmt in ("srt", "vtt", "lrc"):
            box = QCheckBox(fmt.upper())
            self.formats[fmt] = box
            row.addWidget(box)
        output_layout.addLayout(row)
        self.output_mode = QComboBox()
        self.output_mode.addItems(["源文件旁 · 独立字幕目录", "选择输出文件夹"])
        output_layout.addWidget(self.output_mode)
        output_row = QHBoxLayout()
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("选择保存位置")
        self.browse_output = button("浏览", self.choose_output)
        output_row.addWidget(self.output_path, 1)
        output_row.addWidget(self.browse_output)
        output_layout.addLayout(output_row)
        self.output_hint = label("", "muted", True)
        output_layout.addWidget(self.output_hint)
        self.overwrite = QCheckBox("覆盖已有字幕")
        self.overwrite.setToolTip("开启后会替换本次输出目录中的同名字幕，包括手工修改的内容。")
        output_layout.addWidget(self.overwrite)
        panel_layout.addWidget(output_card)
        for compact in (task_layout, preset_layout, output_layout):
            compact.setContentsMargins(16, 14, 16, 14)
            compact.setSpacing(8)
        panel_layout.addStretch()
        right = scroll(panel)
        self.settings_panel = right
        right.setMinimumWidth(295)
        split.addWidget(right)
        split.setSizes([670, 330])
        split.setChildrenCollapsible(False)
        self.main_split = split
        layout.addWidget(split, 1)
        footer, footer_layout = card()
        row = QHBoxLayout()
        text = QVBoxLayout()
        self.status = label("队列为空", "section", True)
        self.elapsed = label("本地处理 · 默认保留已有字幕", "muted")
        text.addWidget(self.status)
        text.addWidget(self.elapsed)
        row.addLayout(text, 1)
        self.stop_button = button("停止", self.stop_run)
        self.stop_button.setEnabled(False)
        self.start_button = button("开始生成字幕", self.start_run, True)
        row.addWidget(self.stop_button)
        row.addWidget(self.start_button)
        footer_layout.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(7)
        self.progress.setValue(0)
        self.progress.setToolTip("按文件与当前音频块显示进度，不代表剩余时间。")
        footer_layout.addWidget(self.progress)
        layout.addWidget(footer)
        self.task.currentIndexChanged.connect(self.update_hints)
        self.device.currentIndexChanged.connect(self.update_compute)
        self.preset.activated.connect(self.apply_preset)
        self.output_mode.currentIndexChanged.connect(self.update_hints)
        self.panel = panel
        return page

    def spin(self, low, high, step=1, suffix="", decimals=False):
        box = QDoubleSpinBox() if decimals else QSpinBox()
        box.setRange(low, high)
        box.setSingleStep(step)
        box.setSuffix(suffix)
        if decimals:
            box.setDecimals(2)
        box.valueChanged.connect(self.update_preset_label)
        return box

    def make_settings(self):
        page, layout = self.page("参数设置", "语音检测、分段、字幕合并与模型路径配置。")
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 5, 0)
        content_layout.setSpacing(16)
        vad_card, vad_layout = card("语音检测")
        vad_layout.addWidget(label("筛选进入识别流程的语音片段。", "muted", True))
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.threshold = self.spin(0.1, 0.9, 0.05, decimals=True)
        self.min_speech = self.spin(0, 3000, 50, " 毫秒")
        self.min_silence = self.spin(0, 5000, 50, " 毫秒")
        self.padding = self.spin(0, 2000, 50, " 毫秒")
        for title, control, tip in (
            ("检测阈值", self.threshold, "语音检测的置信度阈值。标准预设为 0.50，轻声预设为 0.35。"),
            ("最短语音", self.min_speech, "过滤持续时间低于此值的语音片段。"),
            ("切分所需静音", self.min_silence, "相邻语音之间达到此静音长度时，允许切分。"),
            ("语音前后保留", self.padding, "在语音片段两端保留指定时长的音频。"),
        ):
            control.setToolTip(tip)
            form.addRow(title, control)
        vad_layout.addLayout(form)
        content_layout.addWidget(vad_card)
        timing_card, timing_layout = card("时间轴与字幕合并")
        self.smart_split = QCheckBox("智能分段")
        self.merge = QCheckBox("合并重复、相邻片段")
        self.chunk_seconds = self.spin(5, 30, 1, " 秒")
        self.merge_gap = self.spin(0, 5000, 100, " 毫秒")
        self.merge_duration = self.spin(1000, 60000, 1000, " 毫秒")
        form = QFormLayout()
        form.setVerticalSpacing(12)
        form.addRow(self.smart_split)
        form.addRow("目标分段长度", self.chunk_seconds)
        form.addRow(self.merge)
        form.addRow("允许合并的间隔", self.merge_gap)
        form.addRow("字幕段时长上限", self.merge_duration)
        timing_layout.addLayout(form)
        timing_layout.addWidget(label("智能分段按目标时长切分音频；字幕段时长上限应用于合并前后的字幕片段。", "muted", True))
        content_layout.addWidget(timing_card)
        models_card, models_layout = card("本地模型")
        models_layout.addWidget(label("翻译和转录分别保存本地 CTranslate2 模型路径，随任务类型切换。", "muted", True))
        self.model_edits = {}
        for task, title in (("translate", "中文字幕 · 海南鸡翻译模型"), ("transcribe", "日文字幕 · 日语转录模型")):
            models_layout.addWidget(label(title, "muted"))
            row = QHBoxLayout()
            edit = QLineEdit()
            edit.textChanged.connect(self.update_hints)
            self.model_edits[task] = edit
            row.addWidget(edit, 1)
            row.addWidget(button("浏览", lambda checked=False, t=task: self.choose_model(t)))
            models_layout.addLayout(row)
        content_layout.addWidget(models_card)
        content_layout.addStretch()
        self.settings_content = content
        layout.addWidget(scroll(content), 1)
        self.settings_actions = QWidget()
        row = QHBoxLayout(self.settings_actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(button("恢复标准语音检测", lambda: self.set_preset("标准")))
        row.addStretch()
        row.addWidget(button("保存设置并返回", self.save_and_return, True))
        layout.addWidget(self.settings_actions)
        return page

    def make_help(self):
        page, layout = self.page("功能说明", "文件导入、字幕生成、预览与输出功能。")
        text = QTextBrowser()
        text.setOpenExternalLinks(True)
        text.setHtml("""<style>body{font-family:'Microsoft YaHei UI';color:#304058;line-height:1.7}h3{color:#1f416e}p{margin-bottom:18px}</style>
        <h3>文件导入</h3><p>支持拖入音视频文件或文件夹，以及文件选择对话框。文件夹导入包含子目录，相同文件路径自动去重。</p>
        <h3>处理方式与预设</h3><p>中文字幕使用海南鸡翻译模型，日文转录使用日语模型。运行设备支持 NVIDIA GPU 和 CPU。标准、轻声与自定义预设控制语音检测参数。</p>
        <h3>字幕预览</h3><p>显示所选文件的字幕内容、开始时间和结束时间。队列与预览区的高度通过分隔条调节。“展开预览”将预览区扩展至工作区宽度，“还原布局”恢复队列与参数面板。</p>
        <h3>输出管理</h3><p>支持 SRT、VTT 和 LRC。默认保存至音频旁的 ChickenRice字幕/中文 或 ChickenRice字幕/日文；自定义输出目录也按语言区分。默认跳过已有字幕，开启覆盖后替换同名字幕。</p>
        <h3>任务与日志</h3><p>显示当前文件、音频块进度和处理用时。停止操作结束当前推理及后续队列，已完成字幕保留。运行日志记录本次参数、引擎输出和错误信息。</p>
        <h3>本地运行</h3><p>音频识别、翻译、设置保存与日志记录均在本机完成。模型路径按任务类型分别保存。</p>
        <p><a href="https://github.com/TransWithAI/Faster-Whisper-TransWithAI-ChickenRice">ChickenRice 项目</a> · <a href="https://github.com/WEIFENG2333/VideoCaptioner">界面交互参考：VideoCaptioner</a></p>""")
        layout.addWidget(text, 1)
        return page

    def toggle_preview(self):
        self.preview_expanded = not self.preview_expanded
        if self.preview_expanded:
            self.preview_split_sizes = (self.main_split.sizes(), self.content_split.sizes())
        self.files_card.setVisible(not self.preview_expanded)
        self.settings_panel.setVisible(not self.preview_expanded)
        self.expand_preview_button.setText("还原布局" if self.preview_expanded else "展开预览")
        self.tabs.setCurrentIndex(0)
        if not self.preview_expanded:
            self.main_split.setSizes(self.preview_split_sizes[0])
            self.content_split.setSizes(self.preview_split_sizes[1])

    def apply_settings(self):
        s = self.settings
        self.task.setCurrentIndex(max(0, self.task.findData(s.task)))
        self.device.setCurrentIndex(max(0, self.device.findData(s.device)))
        self.update_compute()
        self.compute.setCurrentIndex(max(0, self.compute.findData(s.compute)))
        self.model_edits["translate"].setText(s.translate_model)
        self.model_edits["transcribe"].setText(s.transcribe_model)
        self.output_path.setText(s.output)
        self.output_mode.setCurrentIndex(1 if s.output else 0)
        self.overwrite.setChecked(s.overwrite)
        for key, checkbox in self.formats.items():
            checkbox.setChecked(key in s.formats.split(","))
        for name in ("threshold", "min_speech", "min_silence", "padding", "chunk_seconds", "merge_gap", "merge_duration"):
            getattr(self, name).setValue(getattr(s, name))
        self.smart_split.setChecked(s.smart_split)
        self.merge.setChecked(s.merge)
        self.update_hints()
        self.update_preset_label()

    def collect_settings(self):
        values = {name: getattr(self, name).value() for name in
                  ("threshold", "min_speech", "min_silence", "padding", "chunk_seconds", "merge_gap", "merge_duration")}
        return replace(self.settings, task=self.task.currentData(), device=self.device.currentData(),
                       compute=self.compute.currentData(),
                       translate_model=self.model_edits["translate"].text().strip(),
                       transcribe_model=self.model_edits["transcribe"].text().strip(),
                       formats=",".join(k for k, v in self.formats.items() if v.isChecked()),
                       output=self.output_path.text().strip() if self.output_mode.currentIndex() else "",
                       overwrite=self.overwrite.isChecked(), smart_split=self.smart_split.isChecked(),
                       merge=self.merge.isChecked(), **values)

    def update_compute(self):
        self.compute.clear()
        choices = [("节省显存 · int8_float16", "int8_float16"), ("标准精度 · float16", "float16"),
                   ("完整精度 · float32", "float32")] if self.device.currentData() == "cuda" else [
                       ("CPU 量化 · int8", "int8"), ("CPU 完整精度 · float32", "float32")]
        for title, data in choices:
            self.compute.addItem(title, data)

    def update_hints(self):
        if not hasattr(self, "model_edits") or len(self.model_edits) < 2:
            return
        task = self.task.currentData()
        model = Path(self.model_edits[task].text())
        ready = all((model / f).is_file() for f in ("model.bin", "config.json", "tokenizer.json"))
        name = "海南鸡翻译模型" if task == "translate" else "日语转录模型"
        self.model_hint.setText(f"{name} · {'本地已就绪' if ready else '模型文件缺失'}")
        self.model_hint.setStyleSheet("color: #248263;" if ready else "color: #b05b35;")
        self.model_hint.setToolTip(str(model))
        custom = self.output_mode.currentIndex() == 1
        self.output_path.setVisible(custom)
        self.browse_output.setVisible(custom)
        language = "中文" if task == "translate" else "日文"
        self.output_hint.setText(f"保存到：{'所选目录' if custom else '音频所在目录 / ChickenRice字幕'} / {language}")

    def apply_preset(self):
        self.set_preset(self.preset.currentText())

    def set_preset(self, name):
        if name in PRESETS:
            for key, value in zip(("threshold", "min_speech", "min_silence", "padding"), PRESETS[name]):
                getattr(self, key).setValue(value)
        self.update_preset_label()

    def update_preset_label(self):
        if not hasattr(self, "padding"):
            return
        values = tuple(getattr(self, k).value() for k in ("threshold", "min_speech", "min_silence", "padding"))
        name = next((name for name, preset in PRESETS.items() if values == preset), "自定义")
        self.preset.setCurrentText(name)
        self.preset_hint.setText(f"检测阈值 {values[0]:.2f} · 最短语音 {values[1]} ms\n"
                                f"切分静音 {values[2]} ms · 前后保留 {values[3]} ms")

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "添加音频或视频", "", "音视频 (" + " ".join("*." + s for s in SUFFIXES.split(",")) + ")")
        self.add_paths(paths)

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "添加包含音频的文件夹")
        if path:
            self.add_paths([path])

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择字幕输出文件夹", self.output_path.text())
        if path:
            self.output_path.setText(path)

    def choose_model(self, task):
        path = QFileDialog.getExistingDirectory(self, "选择本地模型目录", self.model_edits[task].text())
        if path:
            self.model_edits[task].setText(path)

    def add_paths(self, paths):
        if self.busy or not paths:
            return
        try:
            found, ignored = discover_files(paths)
            known = {str(p).casefold() for p in self.sources}
            added = [p for p in found if str(p).casefold() not in known]
            self.sources.extend(added)
            self.refresh_table()
            self.import_hint.setText(f"新增 {len(added)} 个文件；已自动去重。" + (f"忽略 {ignored} 个非音视频文件。" if ignored else ""))
            self.status.setText(f"已准备 {len(self.sources)} 个文件")
        except (OSError, ValueError) as error:
            self.show_error(str(error))

    def refresh_table(self):
        self.table.setRowCount(len(self.sources))
        for row, source in enumerate(self.sources):
            record = self.records.get(str(source), {})
            size = f"{source.stat().st_size / 1048576:.1f} MB" if source.exists() else "已失效"
            for col, text in enumerate((source.name, size, record.get("message", "等待处理"))):
                item = QTableWidgetItem(text)
                item.setToolTip(str(source) if col == 0 else text)
                if col == 2:
                    color = {"done": "#218068", "empty": "#a07027", "failed": "#c24c47",
                             "running": "#2667d5"}.get(record.get("status"), "#7a8799")
                    item.setForeground(QColor(color))
                self.table.setItem(row, col, item)
        self.queue_title.setText(f"文件队列 · {len(self.sources)}")

    def remove_selected(self):
        if not self.busy:
            rows = {i.row() for i in self.table.selectionModel().selectedRows()}
            self.sources = [p for i, p in enumerate(self.sources) if i not in rows]
            self.refresh_table()
            self.preview_selected()

    def clear_files(self):
        if not self.busy:
            self.sources.clear()
            self.records.clear()
            self.refresh_table()
            self.preview_selected()
            self.status.setText("队列为空")

    def dragEnterEvent(self, event):
        if not self.busy and event.mimeData().hasUrls() and all(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()

    def set_busy(self, busy):
        self.busy = busy
        for control in (self.add_button, self.folder_button, self.remove_button, self.clear_button,
                        self.panel, self.settings_content, self.settings_actions, self.start_button):
            control.setEnabled(not busy)
        self.stop_button.setEnabled(busy)

    def start_run(self):
        if self.busy:
            return
        try:
            settings = self.collect_settings()
            if self.output_mode.currentIndex() and not settings.output:
                raise ValueError("请选择字幕输出文件夹。")
            validate(settings, self.sources)
            save_settings(settings, self.settings_path)
        except (OSError, ValueError) as error:
            self.show_error(str(error))
            return
        self.settings = settings
        self.records.clear()
        self.refresh_table()
        self.log.clear()
        self.run_dir = None
        self.current_file = None
        self.preview_selected()
        self.progress.setRange(0, 0)
        self.status.setText("正在准备本地模型…")
        self.started_at = time.monotonic()
        self.set_busy(True)
        self.worker = Worker(list(self.sources), replace(settings), self)
        self.worker.event.connect(self.on_event)
        self.worker.completed.connect(self.on_completed)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

    def on_event(self, kind, data):
        if kind == "log":
            self.log.appendPlainText(data)
        elif kind == "run_dir":
            self.run_dir = Path(data)
        elif kind == "stage":
            self.status.setText(data)
        elif kind == "file":
            self.current_file = data
            self.records[data] = {"status": "running", "message": "正在识别…"}
            self.refresh_table()
            self.status.setText("正在处理：" + Path(data).name)
            self.update_progress()
        elif kind == "chunk":
            self.update_progress((data[0] - 1) / max(1, data[1]))
            if self.current_file:
                self.records[self.current_file] = {"status": "running", "message": f"音频块 {data[0]} / {data[1]}"}
                self.refresh_table()
        elif kind == "result":
            self.records[data["source"]] = data
            self.refresh_table()
            self.update_progress()
            self.preview_selected()

    def update_progress(self, fraction=0):
        finished = sum(r.get("status") not in {"running", "cancelled"} for r in self.records.values())
        self.progress.setRange(0, max(1, len(self.sources)) * 100)
        self.progress.setValue(int((finished + fraction) * 100))

    def on_completed(self, manifest):
        counts = Counter(r["status"] for r in manifest["results"])
        prefix = "已停止" if manifest["cancelled"] else "处理结束"
        self.status.setText(f"{prefix} · 生成 {counts['done']} · 跳过 {counts['skipped']} · 无对白 {counts['empty']} · 失败 {counts['failed']}")
        if manifest.get("engine_errors"):
            self.status.setText(self.status.text() + " · 引擎报错，详见日志")
        if counts["failed"] or manifest.get("engine_errors"):
            self.tabs.setCurrentIndex(1)
        else:
            for i, source in enumerate(self.sources):
                if self.records.get(str(source), {}).get("outputs"):
                    self.table.selectRow(i)
                    break
        self.update_progress()

    def on_failed(self, text):
        self.log.appendPlainText(text)
        if self.run_dir:
            try:
                with (self.run_dir / "engine.log").open("a", encoding="utf-8") as log:
                    log.write("\nGUI runner error:\n" + text)
            except OSError:
                pass  # The error remains visible in the GUI even if the log disk is unavailable.
        self.tabs.setCurrentIndex(1)
        self.status.setText("处理失败，详情见运行日志")
        for source in self.sources:
            if self.records.get(str(source), {}).get("status") not in {"done", "skipped", "empty"}:
                self.records[str(source)] = {"status": "failed", "message": "失败，见日志"}
        self.refresh_table()
        self.update_progress()

    def on_worker_finished(self):
        self.set_busy(False)
        self.worker.deleteLater()
        self.worker = None
        if self.pending_close:
            self.close()

    def stop_run(self):
        if self.worker:
            self.worker.runner.cancel()
            self.stop_button.setEnabled(False)
            self.status.setText("正在停止当前推理，已完成的字幕将保留…")

    def update_elapsed(self):
        if self.busy:
            seconds = int(time.monotonic() - self.started_at)
            finished = sum(r.get("status") in {"done", "empty", "skipped", "failed"} for r in self.records.values())
            self.elapsed.setText(f"已用时 {seconds // 60:02d}:{seconds % 60:02d} · 已处理 {finished} / {len(self.sources)} 个文件")

    def selected_source(self):
        row = self.table.currentRow()
        return self.sources[row] if 0 <= row < len(self.sources) else None

    def resize_cue_rows(self):
        self.cues.resizeRowsToContents()
        width = max(1, self.cues.columnWidth(2) - 24)
        metrics = self.cues.fontMetrics()
        for row in range(self.cues.rowCount()):
            item = self.cues.item(row, 2)
            if item:
                height = metrics.boundingRect(0, 0, width, 100000, Qt.TextWordWrap, item.text()).height()
                self.cues.setRowHeight(row, max(self.cues.rowHeight(row), height + 20))

    def preview_selected(self):
        self.preview_path = None
        self.cues.setRowCount(0)
        source = self.selected_source()
        outputs = self.records.get(str(source), {}).get("outputs", [])
        if not outputs:
            self.preview_hint.setText("显示所选文件的字幕内容与时间轴。")
            return
        try:
            path = next((Path(p) for p in outputs if Path(p).suffix == ".srt"), Path(outputs[0]))
            cues = read_cues(path)
            self.preview_path = path
            self.preview_hint.setText(f"{path.name} · {len(cues)} 条字幕" + (" · 未检测到对白" if not cues else ""))
            self.preview_hint.setToolTip(str(path))
            self.cues.setRowCount(len(cues))
            for row, cue in enumerate(cues):
                for col, value in enumerate(cue):
                    self.cues.setItem(row, col, QTableWidgetItem(value))
            self.resize_cue_rows()
        except (OSError, ValueError, UnicodeError) as error:
            self.preview_hint.setText(str(error))

    def open_output(self):
        source = self.selected_source()
        path = self.preview_path.parent if self.preview_path else (output_dir(source, self.collect_settings()) if source else None)
        if path and path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self.show_error("请先选择已有输出的文件。")

    def open_subtitle(self):
        if self.preview_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.preview_path)))

    def open_logs(self):
        if self.run_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.run_dir)))
        else:
            self.show_error("开始处理后会创建本次日志。")

    def save_and_return(self):
        try:
            self.settings = self.collect_settings()
            save_settings(self.settings, self.settings_path)
            self.navigate(0)
        except OSError as error:
            self.show_error(f"无法保存设置：{error}")

    def show_error(self, text):
        QMessageBox.warning(self, "请检查", text)

    def closeEvent(self, event):
        if self.busy:
            answer = QMessageBox.question(self, "任务仍在运行", "停止当前推理并退出？已完成的字幕会保留。",
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer == QMessageBox.Yes:
                self.pending_close = True
                self.stop_run()
            event.ignore()
            return
        try:
            save_settings(self.collect_settings(), self.settings_path)
        except OSError as error:
            QMessageBox.warning(self, "设置未保存", str(error))
        event.accept()


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    DATA.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(instance_lock_path(DATA)))
    lock.setStaleLockTime(0)
    if not lock.tryLock():
        valid, pid, _, _ = lock.getLockInfo()
        if valid:
            for _ in range(20):
                if activate_window(pid):
                    return 0
                time.sleep(0.1)  # A first launch may still be constructing its main window.
        QMessageBox.warning(None, "ChickenRice", "现有窗口尚未就绪或未响应，请稍后重试。\n"
                            "如仍无法打开，可结束任务管理器中的该工作台进程后重启。")
        return 1
    window = Window()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        DATA.mkdir(parents=True, exist_ok=True)
        error = traceback.format_exc()
        (DATA / "startup-error.log").write_text(error, encoding="utf-8")
        if QApplication.instance():
            QMessageBox.critical(None, "启动失败", "详情已保存至 .gui/startup-error.log\n" + error[-1600:])
        raise
