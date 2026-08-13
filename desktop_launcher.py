"""Native PySide6 desktop launcher for agentchattr.

The UI talks to Launcher from a dedicated QThread that owns its asyncio event
loop, keeping process supervision and status polling off the Qt main thread.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback
import webbrowser
from pathlib import Path
from typing import Any, Callable, Coroutine

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from config_loader import load_config
except Exception:
    load_config = None

try:
    from launcher_supervisor import Launcher
except ImportError:
    # Compatibility fallback while launcher_supervisor is not present yet.
    from launcher import Launcher


AsyncFactory = Callable[[Any], Coroutine[Any, Any, Any]]
INTERNAL_ARG = "--agentchattr-internal"
_STREAM_FALLBACKS: list[Any] = []
APP_USER_MODEL_ID = "agentchattr.desktop.launcher"


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def app_icon() -> QIcon:
    root = runtime_root()
    icon = QIcon(str(root / "static" / "agentchattr-icon.svg"))
    if not icon.isNull():
        return icon
    return QIcon(str(root / "agentchattr-icon.ico"))


def set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _ensure_internal_standard_streams() -> None:
    if sys.stdin is None:
        stream = open(os.devnull, "r", encoding="utf-8")
        _STREAM_FALLBACKS.append(stream)
        sys.stdin = stream
    if sys.stdout is None:
        stream = open(os.devnull, "w", encoding="utf-8")
        _STREAM_FALLBACKS.append(stream)
        sys.stdout = stream
    if sys.stderr is None:
        stream = open(os.devnull, "w", encoding="utf-8")
        _STREAM_FALLBACKS.append(stream)
        sys.stderr = stream


def _run_internal_subcommand() -> int | None:
    if len(sys.argv) < 3 or sys.argv[1] != INTERNAL_ARG:
        return None

    _ensure_internal_standard_streams()
    command = sys.argv[2]
    sys.argv = [sys.argv[0], *sys.argv[3:]]
    try:
        if command == "server":
            import run

            run.main()
            return 0
        if command == "wrapper":
            import wrapper

            wrapper.main()
            return 0
        print(f"Unknown internal command: {command}", file=sys.stderr)
        return 2
    except Exception:
        trace_path = os.environ.get("AGENTCHATTR_INTERNAL_TRACE")
        if trace_path:
            try:
                path = Path(trace_path)
                if not path.is_absolute():
                    path = Path(sys.executable).resolve().parent / path
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(traceback.format_exc())
            except Exception:
                pass
        raise


class LauncherThread(QThread):
    ready = Signal()
    status_updated = Signal(dict)
    logs_updated = Signal(str, list)
    operation_finished = Signal(str, object)
    operation_failed = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.launcher: Launcher | None = None
        self._closing = False
        self._poll_task: asyncio.Task | None = None

    def run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.launcher = Launcher()
            self._poll_task = self.loop.create_task(self._poll_status())
            self.ready.emit()
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                self.loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self.loop.close()

    def close(self) -> None:
        self._closing = True
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

    def submit(
        self,
        name: str,
        factory: AsyncFactory,
        refresh: bool = True,
        notify: bool = True,
    ) -> None:
        if not self.loop or not self.loop.is_running() or not self.launcher:
            self.operation_failed.emit(name, "Launcher thread is not ready yet.")
            return

        def schedule() -> None:
            async def runner() -> None:
                try:
                    result = await factory(self.launcher)
                    if notify:
                        self.operation_finished.emit(name, result)
                    if refresh:
                        await self._emit_status()
                except Exception as exc:
                    detail = f"{exc}\n{traceback.format_exc()}"
                    self.operation_failed.emit(name, detail)

            self.loop.create_task(runner())

        self.loop.call_soon_threadsafe(schedule)

    async def _poll_status(self) -> None:
        while not self._closing:
            try:
                await self._emit_status()
            except Exception as exc:
                self.operation_failed.emit("status refresh", str(exc))
            await asyncio.sleep(1.0)

    async def _emit_status(self) -> None:
        if not self.launcher:
            return
        status = await self.launcher.get_status()
        self.status_updated.emit(status)
        keys = ["server"]
        seen: set[str] = set()
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            logs = self.launcher.get_logs(key, 500)
            if logs:
                self.logs_updated.emit(key, logs)

    def start_server(self) -> None:
        self.submit("Start Server", lambda launcher: launcher.start_server())

    def stop_server(self) -> None:
        self.submit("Stop Server", lambda launcher: launcher.stop_server())

    def restart_server(self) -> None:
        self.submit("Restart Server", lambda launcher: launcher.restart_process("server"))

    def start_agent(
        self,
        base: str,
        mode: str = "normal",
        role: str | None = None,
        custom_role: str | None = None,
        cwd: str | None = None,
    ) -> None:
        async def run(launcher: Launcher) -> dict:
            return await launcher.start_agent(
                base=base,
                mode=mode,
                role=role,
                custom_role=custom_role,
                cwd=cwd or None,
            )

        self.submit(f"Start Agent {base}", run)

    def stop_process(self, key: str) -> None:
        self.submit(f"Stop {key}", lambda launcher: launcher.stop_process(key))

    def start_existing_agent(self, key: str) -> None:
        self.submit(f"Start {key}", lambda launcher: launcher.start_existing_agent(key))

    def restart_process(self, key: str) -> None:
        self.submit(f"Restart {key}", lambda launcher: launcher.restart_process(key))

    def send_input(self, key: str, text: str) -> None:
        self.submit(
            f"Send input to {key}",
            lambda launcher: launcher.send_input(key, text),
            refresh=False,
        )

    def start_all(self) -> None:
        async def run(launcher: Launcher) -> dict:
            server_result = await self._ensure_server_running(launcher)
            results: dict[str, Any] = {
                "server": server_result,
                "agents": [],
            }
            status = await launcher.get_status()
            if not status.get("server", {}).get("running"):
                results["agents_error"] = "Skipped agent startup because server is not running."
                return results
            for base in status.get("templates", {}):
                results["agents"].append(await launcher.start_agent(base=base))
            return results

        self.submit("Start All", run)

    def stop_all(self) -> None:
        async def run(launcher: Launcher) -> dict:
            status = await launcher.get_status()
            results: dict[str, Any] = {"agents": [], "server": None}
            for key, process in status.get("processes", {}).items():
                if process.get("kind") == "agent" and process.get("started_by_launcher"):
                    results["agents"].append(await launcher.stop_process(key))
            results["server"] = await launcher.stop_server()
            return results

        self.submit("Stop All", run)

    def restart_all(self) -> None:
        async def run(launcher: Launcher) -> dict:
            stopped = await self._stop_all_for_restart(launcher)
            server_result = await self._ensure_server_running(launcher)
            started: dict[str, Any] = {
                "server": server_result,
                "agents": [],
            }
            status = await launcher.get_status()
            if not status.get("server", {}).get("running"):
                started["agents_error"] = "Skipped agent startup because server is not running."
                return {"stopped": stopped, "started": started}
            for base in status.get("templates", {}):
                started["agents"].append(await launcher.start_agent(base=base))
            return {"stopped": stopped, "started": started}

        self.submit("Restart All", run)

    async def _ensure_server_running(self, launcher: Launcher) -> dict:
        status = await launcher.get_status()
        if status.get("server", {}).get("running"):
            return {"status": "running"}

        result = await launcher.start_server()
        for _ in range(30):
            await asyncio.sleep(0.5)
            status = await launcher.get_status()
            if status.get("server", {}).get("running"):
                return result
        return {
            "error": "Server did not report running before agent startup timeout",
            "start_result": result,
        }

    async def _stop_all_for_restart(self, launcher: Launcher) -> dict:
        status = await launcher.get_status()
        results: dict[str, Any] = {"agents": [], "server": None}
        for key, process in status.get("processes", {}).items():
            if process.get("kind") == "agent" and process.get("started_by_launcher"):
                results["agents"].append(await launcher.stop_process(key))
        results["server"] = await launcher.stop_server()
        await asyncio.sleep(0.5)
        return results


APP_QSS = """
QMainWindow, QWidget#root {
    background: #f4f7fb;
    color: #182230;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
    font-size: 13px;
}
QFrame#topBar, QFrame#pageNav {
    background: #ffffff;
    border-bottom: 1px solid #d8e0ea;
}
QLabel#logoImage {
    background: transparent;
    border: 0;
}
QLabel#topTitle {
    color: #0c111d;
    font-size: 16px;
    font-weight: 700;
}
QPushButton {
    border: 1px solid #d8e0ea;
    border-radius: 6px;
    background: #ffffff;
    color: #667085;
    padding: 7px 13px;
    font-weight: 600;
}
QPushButton:hover {
    background: #f8fafc;
    border-color: #b8c4d4;
    color: #182230;
}
QPushButton[variant="primary"] {
    background: #2563eb;
    border-color: #2563eb;
    color: #ffffff;
}
QPushButton[variant="primary"]:hover { background: #1d4ed8; }
QPushButton[variant="success"] {
    background: #16a34a;
    border-color: #16a34a;
    color: #ffffff;
}
QPushButton[variant="success"]:hover { background: #15803d; }
QPushButton[variant="danger"] {
    background: #dc2626;
    border-color: #dc2626;
    color: #ffffff;
}
QPushButton[variant="danger"]:hover { background: #b91c1c; }
QPushButton[variant="outline-success"] {
    background: #ecfdf3;
    border-color: rgba(22, 163, 74, 80);
    color: #16a34a;
}
QPushButton[variant="outline-danger"] {
    background: #fef2f2;
    border-color: rgba(220, 38, 38, 80);
    color: #dc2626;
}
QPushButton[variant="ghost"] {
    background: transparent;
    border-color: transparent;
}
QPushButton[nav="true"] {
    border: 0;
    border-radius: 0;
    background: #ffffff;
    padding: 12px 22px;
    color: #667085;
}
QPushButton[nav="true"]:checked {
    color: #2563eb;
    border-bottom: 3px solid #2563eb;
    font-weight: 800;
}
QFrame[card="true"] {
    background: #ffffff;
    border: 1px solid #d8e0ea;
    border-radius: 10px;
}
QFrame[agentCard="true"] {
    background: #f8fafc;
    border: 1px solid #d8e0ea;
    border-radius: 10px;
}
QFrame[metric="true"], QFrame[infoRow="true"], QFrame[summaryRow="true"] {
    background: #f8fafc;
    border: 1px solid #d8e0ea;
    border-radius: 8px;
}
QLabel[heading="true"] {
    color: #0c111d;
    font-weight: 800;
    font-size: 14px;
}
QLabel[muted="true"] {
    color: #667085;
}
QLabel[pill="online"] {
    color: #16a34a;
    background: #ecfdf3;
    border-radius: 13px;
    padding: 4px 10px;
    font-weight: 800;
}
QLabel[pill="working"] {
    color: #d97706;
    background: #fffbeb;
    border-radius: 13px;
    padding: 4px 10px;
    font-weight: 800;
}
QLabel[pill="offline"] {
    color: #667085;
    background: #f8f9fb;
    border-radius: 13px;
    padding: 4px 10px;
    font-weight: 800;
}
QLabel[pill="error"] {
    color: #dc2626;
    background: #fef2f2;
    border-radius: 13px;
    padding: 4px 10px;
    font-weight: 800;
}
QLabel[avatar="true"] {
    color: #ffffff;
    border-radius: 9px;
    font-size: 15px;
    font-weight: 900;
}
QLabel#terminalTitle {
    color: #0c111d;
    font-weight: 800;
}
QScrollArea {
    border: 0;
    background: transparent;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QTabWidget::pane {
    border: 1px solid #d8e0ea;
    border-radius: 8px;
    background: #ffffff;
}
QTabBar::tab {
    background: #f8fafc;
    border: 1px solid #d8e0ea;
    padding: 8px 14px;
    color: #667085;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #2563eb;
    font-weight: 800;
}
QPlainTextEdit, QLineEdit, QComboBox {
    background: #f8fafc;
    border: 1px solid #d8e0ea;
    border-radius: 8px;
    padding: 7px;
    color: #182230;
}
QStatusBar {
    background: #ffffff;
    border-top: 1px solid #d8e0ea;
    color: #667085;
}
"""


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def button(text: str, variant: str = "", *, fixed_width: int | None = None) -> QPushButton:
    btn = QPushButton(text)
    if variant:
        btn.setProperty("variant", variant)
    if fixed_width:
        btn.setFixedWidth(fixed_width)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def label(text: str = "", *, heading: bool = False, muted: bool = False) -> QLabel:
    lbl = QLabel(text)
    if heading:
        lbl.setProperty("heading", True)
    if muted:
        lbl.setProperty("muted", True)
    return lbl


def card() -> QFrame:
    frame = QFrame()
    frame.setProperty("card", True)
    return frame


def status_kind(status: str | None) -> str:
    value = (status or "").lower()
    if value in {"running", "active", "online"}:
        return "online"
    if value in {"starting", "stopping", "working", "pending"}:
        return "working"
    if value in {"error", "occupied"}:
        return "error"
    return "offline"


def agent_display_status(proc: dict[str, Any]) -> str:
    if proc.get("busy"):
        return "working"
    if proc.get("available"):
        return "active"
    status = str(proc.get("status") or "unknown").lower()
    if status == "online":
        return "active"
    return status


def summarize_agents(agent_processes: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = {"online": 0, "working": 0, "offline": 0, "error": 0}
    for proc in agent_processes.values():
        status = agent_display_status(proc)
        if status == "working":
            counts["working"] += 1
        elif status in {"running", "active", "online"}:
            counts["online"] += 1
        elif status == "error":
            counts["error"] += 1
        else:
            counts["offline"] += 1
    return counts


def status_text(status: str | None, external: bool = False) -> str:
    if external and status in {"running", "active", "online"}:
        return "外部运行"
    return {
        "running": "运行中",
        "active": "在线",
        "working": "工作中",
        "starting": "启动中",
        "stopping": "停止中",
        "stopped": "离线",
        "error": "异常",
        "occupied": "端口占用",
        "pending": "连接中",
    }.get(status or "", status or "未知")


def make_pill(text: str, kind: str) -> QLabel:
    pill = QLabel(text)
    pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
    pill.setProperty("pill", kind)
    return pill


def short_name(value: str | None) -> str:
    raw = (value or "??").replace("-", " ")
    parts = [part for part in raw.split() if part]
    if not parts:
        return "??"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return "".join(part[0] for part in parts[:2]).upper()


class AddAgentDialog(QDialog):
    def __init__(self, templates: dict[str, dict[str, Any]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新增代理")
        self.setModal(True)
        self.setMinimumWidth(430)
        self.templates = templates
        self.setStyleSheet(APP_QSS)

        self.base = QComboBox()
        for key, template in sorted(templates.items()):
            label_text = template.get("label") or key
            self.base.addItem(f"{label_text} ({key})", key)

        self.mode = QComboBox()
        self.mode.addItem("普通模式", "normal")
        self.mode.addItem("Yolo 模式", "yolo")
        self.role = QComboBox()
        self.role.addItem("无", None)
        self.role.addItem("Planner - 规划者", "planner")
        self.role.addItem("Builder - 构建者", "builder")
        self.role.addItem("Reviewer - 审查者", "reviewer")
        self.role.addItem("Researcher - 研究者", "researcher")
        self.role.addItem("自定义", "custom")

        self.custom_role = QLineEdit()
        self.custom_role.setPlaceholderText("仅在角色为自定义时使用")

        self.cwd = QLineEdit()
        self.cwd.setReadOnly(True)
        self.cwd.setPlaceholderText("请选择项目工作目录")
        self.choose_cwd = QPushButton("选择文件夹…")
        self.choose_cwd.clicked.connect(self._choose_workdir)
        cwd_row = QWidget()
        cwd_layout = QHBoxLayout(cwd_row)
        cwd_layout.setContentsMargins(0, 0, 0, 0)
        cwd_layout.addWidget(self.cwd, 1)
        cwd_layout.addWidget(self.choose_cwd)

        self.start_immediately = QCheckBox("保存后立即启动")
        self.start_immediately.setChecked(True)
        self.start_immediately.setEnabled(False)

        form = QFormLayout()
        form.setSpacing(12)
        form.addRow("代理类型", self.base)
        form.addRow("启动模式", self.mode)
        form.addRow("角色", self.role)
        form.addRow("自定义角色", self.custom_role)
        form.addRow("工作目录", cwd_row)
        form.addRow("", self.start_immediately)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("启动")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        title = label("新增代理", heading=True)
        hint = label("实例名称由 wrapper.py / registry 自动分配", muted=True)
        self.workdir_hint = label("工作目录是 Agent 的项目上下文，不限制其访问目录外的文件。", muted=True)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.workdir_hint)
        layout.addSpacing(10)
        layout.addLayout(form)
        layout.addSpacing(8)
        layout.addWidget(buttons)
        self.base.currentIndexChanged.connect(self._sync_mode)
        self.base.currentIndexChanged.connect(self._sync_workdir)
        self.mode.currentIndexChanged.connect(self._sync_mode)
        self._sync_mode()
        self._sync_workdir()

    def _mode_label_for(self, base: str) -> str:
        template = self.templates.get(base, {})
        return template.get("mode_label") or "Yolo"

    def _mode_desc_for(self, base: str) -> str:
        template = self.templates.get(base, {})
        return template.get("mode_desc") or ""

    def _sync_mode(self, *_args: object) -> None:
        base = self.base.currentData()
        supports_yolo = bool(self.templates.get(base, {}).get("supports_yolo"))
        mode_label = self._mode_label_for(base)
        mode_desc = self._mode_desc_for(base)
        self.mode.setItemText(1, f"{mode_label} 模式")
        self.mode.model().item(1).setEnabled(supports_yolo)
        if not supports_yolo and self.mode.currentData() == "yolo":
            self.mode.setCurrentIndex(0)
        if self.mode.currentData() == "yolo":
            if mode_desc:
                self.workdir_hint.setText(mode_desc)
            else:
                self.workdir_hint.setText("Yolo 模式：自动执行，适合可信本地任务")
        else:
            self.workdir_hint.setText("工作目录是 Agent 的项目上下文，不限制其访问目录外的文件。")

    def _sync_workdir(self, *_args: object) -> None:
        template = self.templates.get(self.base.currentData(), {})
        self.cwd.setText(template.get("remembered_cwd") or "")

    def _choose_workdir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择 Agent 工作目录", self.cwd.text() or str(Path.home())
        )
        if selected:
            self.cwd.setText(selected)

    def accept(self) -> None:
        if not self.cwd.text().strip():
            QMessageBox.warning(self, "请选择工作目录", "启动 Agent 前，请先选择一个项目工作目录。")
            return
        super().accept()

    def values(self) -> dict[str, Any]:
        return {
            "base": self.base.currentData(),
            "mode": self.mode.currentData(),
            "role": self.role.currentData(),
            "custom_role": self.custom_role.text().strip() or None,
            "cwd": self.cwd.text().strip() or None,
        }


class DesktopLauncher(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("agentchattr 控制台")
        self.setWindowIcon(app_icon())
        self.resize(1320, 820)
        self.setMinimumSize(980, 650)
        self.setStyleSheet(APP_QSS)
        self.latest_status: dict[str, Any] = {}
        self.templates: dict[str, dict[str, Any]] = {}
        self.agent_rows: list[tuple[str, dict[str, Any]]] = []
        self.log_editors: dict[str, QPlainTextEdit] = {}
        self.log_clear_after: dict[str, float] = {}
        self.recent_events: list[str] = []

        self.worker = LauncherThread(self)
        self.worker.ready.connect(lambda: self.statusBar().showMessage("Launcher ready", 2500))
        self.worker.status_updated.connect(self.apply_status)
        self.worker.logs_updated.connect(self.apply_logs)
        self.worker.operation_finished.connect(self.operation_finished)
        self.worker.operation_failed.connect(self.operation_failed)

        self._build_shell()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Starting launcher thread...")
        self.worker.start()

    def closeEvent(self, event) -> None:
        self.worker.close()
        self.worker.wait(2500)
        super().closeEvent(event)

    def _build_shell(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_top_bar())
        layout.addWidget(self._build_nav())
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_overview_page())
        self.stack.addWidget(self._build_agents_page())
        self.stack.addWidget(self._build_logs_page())
        self.stack.addWidget(self._build_settings_page())
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

    def _build_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topBar")
        bar.setFixedHeight(62)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(12)

        logo = QLabel()
        logo.setObjectName("logoImage")
        logo.setFixedSize(30, 30)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_pixmap = app_icon().pixmap(30, 30)
        if icon_pixmap.isNull():
            logo.setText("A")
        else:
            logo.setPixmap(icon_pixmap)
        title = QLabel("agentchattr 控制台")
        title.setObjectName("topTitle")
        layout.addWidget(logo)
        layout.addWidget(title)
        layout.addStretch(1)

        actions = [
            ("■ 打开聊天", "primary", self.open_chat),
            ("▶ 全部启动", "success", self.worker.start_all),
            ("■ 全部停止", "danger", self.worker.stop_all),
            ("↻ 全部重启", "", self.worker.restart_all),
        ]
        for text, variant, callback in actions:
            btn = button(text, variant)
            btn.clicked.connect(lambda _checked=False, cb=callback: cb())
            layout.addWidget(btn)
        return bar

    def _build_nav(self) -> QFrame:
        nav = QFrame()
        nav.setObjectName("pageNav")
        nav.setFixedHeight(44)
        layout = QHBoxLayout(nav)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(0)
        self.nav_buttons: list[QPushButton] = []
        for index, text in enumerate(["■ 概览", "● 代理", "■ 终端", "⚙ 设置"]):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setProperty("nav", True)
            btn.clicked.connect(lambda _checked=False, i=index: self._set_page(i))
            layout.addWidget(btn)
            self.nav_buttons.append(btn)
        layout.addStretch(1)
        self.nav_buttons[0].setChecked(True)
        return nav

    def _set_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)

    def _page_scroll(self, content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll

    def _build_overview_page(self) -> QWidget:
        page = QWidget()
        page_layout = QHBoxLayout(page)
        page_layout.setContentsMargins(20, 16, 20, 16)
        page_layout.setSpacing(16)

        left = self._build_server_card()
        left.setFixedWidth(280)
        center = self._build_dashboard_agents_card()
        right = self._build_right_column()
        right.setFixedWidth(220)

        page_layout.addWidget(left)
        page_layout.addWidget(center, 1)
        page_layout.addWidget(right)
        return page

    def _build_server_card(self) -> QFrame:
        panel = card()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(label("服务", heading=True))
        self.server_status_pill = make_pill("检测中", "working")
        header.addWidget(self.server_status_pill)
        layout.addLayout(header)

        controls = QGridLayout()
        self.btn_start_server = button("▶ 启动服务", "success")
        self.btn_stop_server = button("■ 停止服务", "danger")
        self.btn_restart_server = button("↻ 重启服务")
        self.btn_open_chat = button("■ 打开聊天", "primary")
        self.btn_start_server.clicked.connect(lambda _checked=False: self.worker.start_server())
        self.btn_stop_server.clicked.connect(lambda _checked=False: self.worker.stop_server())
        self.btn_restart_server.clicked.connect(lambda _checked=False: self.worker.restart_server())
        self.btn_open_chat.clicked.connect(lambda _checked=False: self.open_chat())
        controls.addWidget(self.btn_start_server, 0, 0)
        controls.addWidget(self.btn_stop_server, 0, 1)
        controls.addWidget(self.btn_restart_server, 1, 0, 1, 2)
        controls.addWidget(self.btn_open_chat, 2, 0, 1, 2)
        layout.addLayout(controls)

        layout.addWidget(label("服务信息", heading=True))
        self.info_rows: dict[str, QLabel] = {}
        for key, title in [
            ("port", "HTTP 端口"),
            ("mcp_sse", "MCP SSE"),
            ("mcp_http", "MCP HTTP"),
            ("data_dir", "数据目录"),
        ]:
            row, value = self._info_row(title, "--")
            self.info_rows[key] = value
            layout.addWidget(row)

        layout.addWidget(label("资源", heading=True))
        metrics = QGridLayout()
        self.metric_online = self._metric("0", "在线")
        self.metric_working = self._metric("0", "工作中")
        self.metric_memory = self._metric("--", "内存占用")
        self.metric_errors = self._metric("0", "近1小时错误")
        metrics.addWidget(self.metric_online, 0, 0)
        metrics.addWidget(self.metric_working, 0, 1)
        metrics.addWidget(self.metric_memory, 1, 0)
        metrics.addWidget(self.metric_errors, 1, 1)
        layout.addLayout(metrics)

        layout.addStretch(1)
        return panel

    def _info_row(self, title: str, value: str) -> tuple[QFrame, QLabel]:
        row = QFrame()
        row.setProperty("infoRow", True)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 9, 12, 9)
        title_label = label(title, muted=True)
        value_label = QLabel(value)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(value_label, 1)
        return row, value_label

    def _metric(self, value: str, caption: str) -> QFrame:
        frame = QFrame()
        frame.setProperty("metric", True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 12, 8, 12)
        val = QLabel(value)
        val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val.setStyleSheet("font-size: 24px; font-weight: 900; color: #182230;")
        cap = label(caption, muted=True)
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(val)
        layout.addWidget(cap)
        frame.value_label = val  # type: ignore[attr-defined]
        return frame

    def _build_dashboard_agents_card(self) -> QFrame:
        panel = card()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(16, 14, 16, 12)
        header.addWidget(label("代理实例", heading=True))
        self.agent_summary_label = label("0 个实例", muted=True)
        header.addStretch(1)
        header.addWidget(self.agent_summary_label)
        layout.addLayout(header)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(16, 0, 16, 12)
        self.filter_label = label("全部 (0)", muted=True)
        self.filter_label.setStyleSheet(
            "background:#ffffff;border:1px solid #d8e0ea;border-radius:8px;"
            "padding:8px 14px;color:#182230;font-weight:700;"
        )
        add_btn = button("+ 新增代理", "primary")
        add_btn.clicked.connect(self.add_agent)
        toolbar.addWidget(self.filter_label)
        toolbar.addStretch(1)
        toolbar.addWidget(add_btn)
        layout.addLayout(toolbar)

        self.dashboard_agent_list = QVBoxLayout()
        self.dashboard_agent_list.setContentsMargins(16, 0, 16, 16)
        self.dashboard_agent_list.setSpacing(10)
        list_host = QWidget()
        list_host.setLayout(self.dashboard_agent_list)
        scroll = self._page_scroll(list_host)
        layout.addWidget(scroll, 1)
        return panel

    def _build_right_column(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._build_summary_card())
        layout.addWidget(self._build_events_card(), 1)
        return host

    def _build_summary_card(self) -> QFrame:
        panel = card()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(label("运行摘要", heading=True))
        self.summary_values: dict[str, QLabel] = {}
        for key, title, color in [
            ("online", "在线代理", "#16a34a"),
            ("working", "工作中", "#d97706"),
            ("offline", "离线代理", "#667085"),
            ("error", "异常", "#dc2626"),
        ]:
            row = QFrame()
            row.setProperty("summaryRow", True)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            row_layout.addWidget(label(title, muted=True))
            val = QLabel("0")
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            val.setStyleSheet(f"color:{color};font-size:18px;font-weight:900;")
            row_layout.addWidget(val, 1)
            self.summary_values[key] = val
            layout.addWidget(row)
        return panel

    def _build_events_card(self) -> QFrame:
        panel = card()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.setContentsMargins(16, 14, 16, 12)
        header.addWidget(label("最近事件", heading=True))
        header.addStretch(1)
        view_logs = button("查看全部", "ghost")
        view_logs.clicked.connect(lambda: self._set_page(2))
        header.addWidget(view_logs)
        layout.addLayout(header)
        self.events_list = QVBoxLayout()
        self.events_list.setContentsMargins(16, 0, 16, 16)
        self.events_list.setSpacing(8)
        host = QWidget()
        host.setLayout(self.events_list)
        layout.addWidget(host)
        return panel

    def _build_agents_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(0)
        panel = card()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.setContentsMargins(16, 14, 16, 12)
        header.addWidget(label("代理管理", heading=True))
        header.addStretch(1)
        add_btn = button("+ 新增代理", "primary")
        add_btn.clicked.connect(self.add_agent)
        header.addWidget(add_btn)
        panel_layout.addLayout(header)
        self.agents_page_list = QVBoxLayout()
        self.agents_page_list.setContentsMargins(16, 0, 16, 16)
        self.agents_page_list.setSpacing(10)
        host = QWidget()
        host.setLayout(self.agents_page_list)
        panel_layout.addWidget(self._page_scroll(host), 1)
        layout.addWidget(panel)
        return page

    def _build_logs_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        panel = card()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 14, 16, 16)
        toolbar = QHBoxLayout()
        title = QLabel("服务终端 - Server stdout/stderr")
        title.setObjectName("terminalTitle")
        toolbar.addWidget(title)
        toolbar.addStretch(1)
        clear_button = button("清空", "ghost")
        copy_button = button("复制", "ghost")
        clear_button.clicked.connect(self.clear_current_log)
        copy_button.clicked.connect(self.copy_current_log)
        toolbar.addWidget(clear_button)
        toolbar.addWidget(copy_button)
        panel_layout.addLayout(toolbar)
        self.logs_tabs = QTabWidget()
        self.logs_tabs.currentChanged.connect(self._update_terminal_input_state)
        panel_layout.addWidget(self.logs_tabs, 1)
        self._log_editor("server")

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.terminal_input = QLineEdit()
        self.terminal_input.setPlaceholderText("服务日志只读；agent CLI 请在 Windows Terminal 中操作")
        self.terminal_input.returnPressed.connect(self.send_current_input)
        self.terminal_input.textChanged.connect(self._update_terminal_input_state)
        self.terminal_send_button = button("发送", "primary", fixed_width=82)
        self.terminal_send_button.clicked.connect(self.send_current_input)
        input_row.addWidget(self.terminal_input, 1)
        input_row.addWidget(self.terminal_send_button)
        panel_layout.addLayout(input_row)

        self.terminal_status_label = label(
            "这里仅显示 launcher 启动的 server 日志；Claude/Codex/Kimi 等 CLI 在 Windows Terminal 中操作。",
            muted=True,
        )
        self.terminal_status_label.setWordWrap(True)
        panel_layout.addWidget(self.terminal_status_label)
        self._update_terminal_input_state()

        layout.addWidget(panel)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        header = QVBoxLayout()
        header.setSpacing(4)
        header.addWidget(label("设置", heading=True))
        header.addWidget(label("配置仍以 config.toml 为准；这里展示桌面启动器实际读取到的运行信息。", muted=True))
        layout.addLayout(header)

        self.settings_values: dict[str, QLabel] = {}

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)

        service_card, service_form = self._settings_card("服务配置", "Web UI 和 server 运行参数")
        self._add_setting_row(service_form, "服务状态", "server_status")
        self._add_setting_row(service_form, "访问地址", "server_url")
        self._add_setting_row(service_form, "监听端口", "server_port")
        self._add_setting_row(service_form, "管理方式", "server_managed")
        content_layout.addWidget(service_card)

        mcp_card, mcp_form = self._settings_card("MCP 端口", "供各 agent CLI 连接 agentchattr")
        self._add_setting_row(mcp_form, "Streamable HTTP", "mcp_http")
        self._add_setting_row(mcp_form, "SSE", "mcp_sse")
        content_layout.addWidget(mcp_card)

        data_card, data_form = self._settings_card("数据目录", "运行数据、上传文件和本地配置位置")
        self._add_setting_row(data_form, "数据目录", "data_dir")
        self._add_setting_row(data_form, "上传目录", "upload_dir")
        self._add_setting_row(data_form, "配置文件", "config_file")
        content_layout.addWidget(data_card)

        templates_card, templates_layout = self._settings_card("Agent templates", "可由启动器创建的 agent 类型")
        self.settings_templates_layout = QVBoxLayout()
        self.settings_templates_layout.setContentsMargins(0, 4, 0, 0)
        self.settings_templates_layout.setSpacing(8)
        templates_layout.addRow(self.settings_templates_layout)
        content_layout.addWidget(templates_card)

        help_card, help_layout = self._settings_card("操作说明", "状态和按钮的含义")
        for text in [
            "运行摘要以 server /api/status 的 busy/available 为准；busy 会显示为“工作中”。",
            "外部启动的 agent 可以展示状态，但不能由桌面启动器停止或重启。",
            "修改端口、数据目录或 agent 命令后，需要重启 server 才会生效。",
            "新增 agent 会先启动 wrapper.py，实例名称由 registry 分配并回填。",
        ]:
            item = label(text, muted=True)
            item.setWordWrap(True)
            help_layout.addRow(item)
        content_layout.addWidget(help_card)
        content_layout.addStretch(1)

        layout.addWidget(self._page_scroll(content), 1)
        self._update_settings_summary({})
        return page

    def _load_config_summary(self) -> dict[str, Any]:
        if not load_config:
            return {}
        try:
            loaded = load_config()
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    def _settings_card(self, title: str, subtitle: str = "") -> tuple[QFrame, QFormLayout]:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(8)
        layout.addWidget(label(title, heading=True))
        if subtitle:
            hint = label(subtitle, muted=True)
            hint.setWordWrap(True)
            layout.addWidget(hint)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(9)
        layout.addLayout(form)
        return frame, form

    def _add_setting_row(self, form: QFormLayout, title: str, key: str) -> QLabel:
        value = QLabel("--")
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(label(title, muted=True), value)
        self.settings_values[key] = value
        return value

    def apply_status(self, status: dict) -> None:
        self.latest_status = status
        self.templates = status.get("templates", {})
        server = status.get("server", {})
        processes = status.get("processes", {})
        agent_processes = {
            key: proc for key, proc in processes.items() if proc.get("kind") == "agent"
        }

        running = server.get("status") or ("running" if server.get("running") else "stopped")
        host = server.get("host", "127.0.0.1")
        port = server.get("port", 8300)
        status_label = status_text(running)
        status_label = "运行中" if server.get("running") else status_label
        kind = status_kind("running" if server.get("running") else running)
        self.server_status_pill.setText(f"● {status_label}")
        self.server_status_pill.setProperty("pill", kind)
        repolish(self.server_status_pill)
        server_running = bool(server.get("running"))
        self.btn_start_server.setEnabled(not server_running)
        self.btn_stop_server.setEnabled(server_running)
        self.btn_restart_server.setEnabled(server_running)

        self.info_rows["port"].setText(str(port))
        self.info_rows["mcp_sse"].setText(f"端口 {server.get('mcp_sse_port', '--')}")
        self.info_rows["mcp_http"].setText(f"端口 {server.get('mcp_http_port', '--')}")
        self.info_rows["data_dir"].setText(str(server.get("data_dir", "--")))

        counts = summarize_agents(agent_processes)
        online = counts["online"]
        working = counts["working"]
        errors = counts["error"]
        offline = counts["offline"]
        self.metric_online.value_label.setText(str(online))  # type: ignore[attr-defined]
        self.metric_working.value_label.setText(str(working))  # type: ignore[attr-defined]
        self.metric_memory.value_label.setText("--")  # type: ignore[attr-defined]
        self.metric_errors.value_label.setText(str(errors))  # type: ignore[attr-defined]
        self.summary_values["online"].setText(str(online))
        self.summary_values["working"].setText(str(working))
        self.summary_values["offline"].setText(str(offline))
        self.summary_values["error"].setText(str(errors))

        self.agent_summary_label.setText(
            f"{len(agent_processes)} 个实例 · {online} 在线 · {working} 工作中"
        )
        self.filter_label.setText(f"全部 ({len(agent_processes)})")
        self.agent_rows = self._sorted_agents(agent_processes)
        self._render_agent_lists()
        self._render_events()
        self._update_settings_summary(status)
        self._update_terminal_input_state()

    def _sorted_agents(self, agents: dict[str, dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
        return sorted(
            agents.items(),
            key=lambda item: (
                item[1].get("base") or "",
                item[1].get("assigned_name") or item[0],
            ),
        )

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_agent_lists(self) -> None:
        for layout in (self.dashboard_agent_list, self.agents_page_list):
            self._clear_layout(layout)
            if not self.agent_rows:
                empty = label("暂无代理实例", muted=True)
                empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(empty)
            else:
                for key, proc in self.agent_rows:
                    layout.addWidget(self._agent_card(key, proc))
            layout.addStretch(1)

    def _agent_card(self, key: str, proc: dict[str, Any]) -> QFrame:
        base = proc.get("base") or "agent"
        name = proc.get("assigned_name") or base
        tmpl = self.templates.get(base, {})
        status = agent_display_status(proc)
        external = not bool(proc.get("started_by_launcher"))
        role = proc.get("role") or ""
        mode = proc.get("mode") or ""

        frame = QFrame()
        frame.setProperty("agentCard", True)
        frame.setMinimumHeight(82)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(14)

        avatar = QLabel(short_name(name))
        avatar.setProperty("avatar", True)
        avatar.setFixedSize(40, 40)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(f"background:{tmpl.get('color', '#2563eb')};")
        layout.addWidget(avatar)

        body = QVBoxLayout()
        body.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(8)
        name_label = QLabel(str(name))
        name_label.setStyleSheet("font-size:15px;font-weight:900;color:#0c111d;")
        base_label = make_pill(str(base), "offline")
        state = make_pill(
            f"● {status_text(status, external)}",
            status_kind(status),
        )
        top.addWidget(name_label)
        top.addWidget(base_label)
        top.addWidget(state)
        if mode == "yolo":
            mode_label = tmpl.get("mode_label") or "Yolo"
            top.addWidget(make_pill(mode_label, "working"))
        top.addStretch(1)
        body.addLayout(top)

        subtitle = f"{tmpl.get('label', base.capitalize())} 代理"
        if role:
            subtitle = f"{role} · {subtitle}"
        body.addWidget(label(subtitle, muted=True))

        terminal_capability = str(proc.get("terminal_capability") or "")
        if external:
            ownership = "外部进程"
        elif terminal_capability == "windows_terminal":
            ownership = "由启动器管理 · Windows Terminal"
        else:
            ownership = "由启动器管理"
        meta = QLabel(f"● PID: {proc.get('pid') or '未运行'}    ■ {ownership}")
        meta.setProperty("muted", True)
        body.addWidget(meta)
        layout.addLayout(body, 1)

        launcher_owned = bool(proc.get("started_by_launcher"))
        can_stop = launcher_owned and status in {"running", "active", "working", "starting"}
        can_restart = launcher_owned and status in {"running", "active", "working"}
        can_start = launcher_owned and status in {"stopped", "error"}
        if can_stop:
            stop = button("停止", "outline-danger", fixed_width=72)
            stop.clicked.connect(lambda _checked=False, k=key: self.worker.stop_process(k))
            layout.addWidget(stop)
        elif can_start:
            start = button("启动", "outline-success", fixed_width=72)
            start.clicked.connect(lambda _checked=False, k=key: self.worker.start_existing_agent(k))
            layout.addWidget(start)
        else:
            disabled = button("--", fixed_width=72)
            disabled.setEnabled(False)
            layout.addWidget(disabled)

        restart = button("↻", "ghost", fixed_width=34)
        restart.setEnabled(bool(can_restart))
        restart.clicked.connect(lambda _checked=False, k=key: self.worker.restart_process(k))
        layout.addWidget(restart)
        return frame

    def _render_events(self) -> None:
        self._clear_layout(self.events_list)
        if not self.recent_events:
            self.recent_events = ["服务状态已同步", "桌面控制台已连接"]
        for text in self.recent_events[:6]:
            row = QLabel(text)
            row.setProperty("muted", True)
            row.setWordWrap(True)
            row.setStyleSheet("border-bottom:1px solid #eef2f6;padding:6px 0;")
            self.events_list.addWidget(row)
        self.events_list.addStretch(1)

    def _update_settings_summary(self, status: dict[str, Any]) -> None:
        if not hasattr(self, "settings_values"):
            return

        cfg = self._load_config_summary()
        server = status.get("server", {}) if status else {}
        cfg_server = cfg.get("server", {}) if isinstance(cfg.get("server"), dict) else {}
        cfg_mcp = cfg.get("mcp", {}) if isinstance(cfg.get("mcp"), dict) else {}
        cfg_images = cfg.get("images", {}) if isinstance(cfg.get("images"), dict) else {}

        host = server.get("host") or cfg_server.get("host", "127.0.0.1")
        port = server.get("port") or cfg_server.get("port", 8300)
        status_value = server.get("status") or ("running" if server.get("running") else "stopped")
        managed = "桌面启动器管理" if server.get("managed_by_launcher") else "外部或未启动"

        values = {
            "server_status": status_text(status_value),
            "server_url": f"http://{host}:{port}",
            "server_port": str(port),
            "server_managed": managed,
            "mcp_http": f"http://{host}:{server.get('mcp_http_port') or cfg_mcp.get('http_port', 8200)}/mcp",
            "mcp_sse": f"http://{host}:{server.get('mcp_sse_port') or cfg_mcp.get('sse_port', 8201)}/sse",
            "data_dir": str(server.get("data_dir") or cfg_server.get("data_dir", "./data")),
            "upload_dir": str(cfg_images.get("upload_dir", "./uploads")),
            "config_file": "config.toml",
        }
        for key, value in values.items():
            if key in self.settings_values:
                self.settings_values[key].setText(value)

        templates = status.get("templates") or self.templates or {}
        self._render_settings_templates(templates)

    def _render_settings_templates(self, templates: dict[str, dict[str, Any]]) -> None:
        if not hasattr(self, "settings_templates_layout"):
            return
        self._clear_layout(self.settings_templates_layout)
        if not templates:
            self.settings_templates_layout.addWidget(label("暂无 agent template 配置", muted=True))
            return
        for base, template in sorted(templates.items()):
            row = QFrame()
            row.setProperty("infoRow", True)
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(12, 9, 12, 9)
            title = QLabel(f"{template.get('label') or base} ({base})")
            title.setStyleSheet("font-weight:800;color:#0c111d;")
            command = template.get("command") or base
            cwd = template.get("cwd") or "--"
            if template.get("supports_yolo"):
                mode_label = template.get("mode_label") or "Yolo"
                mode_desc = template.get("mode_desc") or ""
                yolo = f"支持 {mode_label} 模式"
                if mode_desc:
                    yolo += f" · {mode_desc}"
            else:
                yolo = "普通模式"
            detail = label(f"命令: {command} · 工作目录: {cwd} · {yolo}", muted=True)
            detail.setWordWrap(True)
            row_layout.addWidget(title)
            row_layout.addWidget(detail)
            self.settings_templates_layout.addWidget(row)

    def add_agent(self) -> None:
        if not self.templates:
            QMessageBox.warning(self, "Add Agent", "Agent templates are not loaded yet.")
            return
        dialog = AddAgentDialog(self.templates, self)
        if dialog.exec() == QDialog.Accepted:
            values = dialog.values()
            self.worker.start_agent(**values)

    def open_chat(self) -> None:
        server = self.latest_status.get("server", {})
        host = server.get("host", "127.0.0.1")
        port = server.get("port", 8300)
        webbrowser.open(f"http://{host}:{port}")

    def apply_logs(self, key: str, logs: list[dict[str, Any]]) -> None:
        if key != "server":
            return
        editor = self._log_editor(key)
        threshold = self.log_clear_after.get(key, 0.0)
        lines = []
        for event in logs:
            if event.get("timestamp", 0) <= threshold:
                continue
            timestamp = time.strftime(
                "%H:%M:%S", time.localtime(float(event.get("timestamp", 0) or 0))
            )
            stream = event.get("stream", "log")
            text = event.get("text", "")
            lines.append(f"[{timestamp}] {stream}: {text}")
        next_text = "\n".join(lines)
        if editor.toPlainText() != next_text:
            scrollbar = editor.verticalScrollBar()
            at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
            editor.setPlainText(next_text)
            if at_bottom:
                scrollbar.setValue(scrollbar.maximum())

    def _log_editor(self, key: str) -> QPlainTextEdit:
        if key != "server":
            raise ValueError("Desktop logs only render launcher-owned server output")
        if key in self.log_editors:
            return self.log_editors[key]
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_editors[key] = editor
        self.logs_tabs.addTab(editor, key)
        self._update_terminal_input_state()
        return editor

    def current_log_key(self) -> str | None:
        index = self.logs_tabs.currentIndex()
        if index < 0:
            return None
        return self.logs_tabs.tabText(index)

    def clear_current_log(self) -> None:
        key = self.current_log_key()
        if not key:
            return
        self.log_clear_after[key] = time.time()
        if key in self.log_editors:
            self.log_editors[key].clear()

    def _current_terminal_process(self) -> dict[str, Any] | None:
        key = self.current_log_key()
        if not key:
            return None
        return self.latest_status.get("processes", {}).get(key)

    def _update_terminal_input_state(self, *_args: object) -> None:
        if not hasattr(self, "terminal_send_button"):
            return
        self.terminal_send_button.setEnabled(False)
        self.terminal_input.setEnabled(False)
        self.terminal_status_label.setText(
            "这里只显示 launcher 启动的 server stdout/stderr；agent CLI 请在 Windows Terminal 中操作。"
        )

    def send_current_input(self) -> None:
        QMessageBox.information(self, "服务日志", self.terminal_status_label.text())

    def copy_current_log(self) -> None:
        key = self.current_log_key()
        if not key:
            return
        editor = self.log_editors[key]
        text = editor.textCursor().selectedText() or editor.toPlainText()
        QGuiApplication.clipboard().setText(text)
        self.statusBar().showMessage(f"Copied log: {key}", 2000)

    def operation_finished(self, name: str, result: object) -> None:
        if isinstance(result, dict) and result.get("error"):
            QMessageBox.warning(self, name, str(result.get("error")))
            return
        self.statusBar().showMessage(f"{name} complete", 2500)

    def operation_failed(self, name: str, detail: str) -> None:
        QMessageBox.critical(self, name, detail)


def main() -> int:
    internal_result = _run_internal_subcommand()
    if internal_result is not None:
        return internal_result

    set_windows_app_user_model_id()
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    app.setFont(QFont("Microsoft YaHei UI", 9))
    window = DesktopLauncher()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
