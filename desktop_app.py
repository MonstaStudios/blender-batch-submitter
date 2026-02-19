def remove_job_id_txt(filepath: str, job_id: str):
    """Remove the job_id txt file for a given blend file and job_id."""
    import os
    blend_dir = os.path.dirname(filepath)
    job_id_dir = os.path.join(blend_dir, "job_id")
    job_name = os.path.basename(filepath).replace(".blend", "")
    txt_filename = f"{job_name}_jobID_{job_id}.txt"
    txt_path = os.path.join(job_id_dir, txt_filename)
    if os.path.exists(txt_path):
        try:
            os.remove(txt_path)
        except Exception:
            pass

"""
Batch Submitter - Blender to CGRU Afanasy Farm
A standalone PyQt6 desktop application for submitting Blender render jobs
to the Afanasy render farm.
"""
import os
import sys
import json
import subprocess
import math
from dataclasses import dataclass, field
from typing import Optional

import blend_parser

# Initialize CGRU paths before importing af
import config
config.init_cgru()

# Fallback: if CGRU_LOCATION in .env didn't resolve (e.g. Windows path on Linux),
# auto-detect cgru_src relative to this script.
_script_dir = os.path.dirname(os.path.abspath(__file__))
_cgru_src = os.path.join(_script_dir, "cgru_src")
if os.path.isdir(_cgru_src):
    for _sub in ["lib/python", "afanasy/python", "lib", "python"]:
        _p = os.path.join(_cgru_src, _sub)
        if os.path.isdir(_p) and _p not in sys.path:
            sys.path.insert(0, _p)
    if "CGRU_LOCATION" not in os.environ or not os.path.isdir(os.environ.get("CGRU_LOCATION", "")):
        os.environ["CGRU_LOCATION"] = _cgru_src

# Ensure cgruconfig.VARS has required defaults for af.py
import cgruconfig
_CGRU_DEFAULTS = {
    'af_priority': 99,
    'af_task_default_service': 'generic',
    'af_task_default_capacity': 1000,
    'af_cmdprefix': '',
}
for _k, _v in _CGRU_DEFAULTS.items():
    if _k not in cgruconfig.VARS:
        cgruconfig.VARS[_k] = _v

# Re-apply Afanasy server config (init_cgru may have run before cgruconfig was importable)
if config.AFANASY_SERVER and cgruconfig.VARS.get('af_servername', '') != config.AFANASY_SERVER:
    cgruconfig.VARS['af_servername'] = config.AFANASY_SERVER
if config.AFANASY_PORT and cgruconfig.VARS.get('af_serverport', 0) != int(config.AFANASY_PORT):
    cgruconfig.VARS['af_serverport'] = int(config.AFANASY_PORT)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QLabel, QLineEdit, QPushButton, QSpinBox,
    QCheckBox, QComboBox, QTextEdit, QProgressBar, QFileDialog,
    QFormLayout, QListWidget, QListWidgetItem, QAbstractItemView,
    QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QFont, QColor, QPalette, QShortcut, QKeySequence

import af


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BlendFileData:
    """Holds per-file metadata (detected + overrides)."""
    filepath: str = ""
    job_name: str = ""
    inspected: bool = False
    error: str = ""
    # Per-scene data (list of dicts from blend_inspector.py)
    scenes: list = field(default_factory=list)
    # Current user selections / overrides
    selected_scene: str = ""
    selected_layers: list = field(default_factory=list)
    frame_start: int = 1
    frame_end: int = 250
    frame_step: int = 1
    frames_per_task: int = 3
    render_engine: str = ""
    resolution_x: int = 1920
    resolution_y: int = 1080
    output_path: str = ""
    output_format: str = "OPEN_EXR"
    use_nodes: bool = False  # Compositing enabled
    blender_version: str = ""  # Blender version from file header
    # Track which fields have been manually overridden by user
    user_overrides: set = field(default_factory=set)  # Set of field names
    # Render layer submission mode
    render_layers_parallel: bool = False  # True = multi-block, False = single-block (default)


# ---------------------------------------------------------------------------
# Inspector Thread
# ---------------------------------------------------------------------------

class InspectorThread(QThread):
    """Runs Blender headless to inspect .blend files for metadata."""
    file_inspected = pyqtSignal(str, dict)  # filepath, metadata_dict or {"error": msg}
    all_done = pyqtSignal()

    def __init__(self, files: list, blender_path: str, inspector_script: str):
        super().__init__()
        self.files = files
        self.blender_path = blender_path
        self.inspector_script = inspector_script
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        blender_missing = False
        for filepath in self.files:
            if self._stop:
                break
            
            # Try fast binary parsing first (no Blender subprocess needed)
            if blend_parser.is_available():
                try:
                    metadata = blend_parser.parse_blend(filepath)
                    self.file_inspected.emit(filepath, metadata)
                    continue
                except Exception as e:
                    # Binary parsing failed, fall back to Blender headless
                    print(f"Binary parse failed for {filepath}: {e}. Trying Blender headless...")
            
            # Fallback: Blender headless inspection
            if blender_missing:
                self.file_inspected.emit(filepath, {"error": f"Blender not found: {self.blender_path}"})
                continue
            try:
                result = subprocess.run(
                    [self.blender_path, "-b", filepath, "-P", self.inspector_script],
                    capture_output=True, text=True, timeout=120
                )
                metadata = None
                for line in result.stdout.splitlines():
                    if line.startswith("BLEND_INSPECTOR_JSON:"):
                        json_str = line[len("BLEND_INSPECTOR_JSON:"):]
                        metadata = json.loads(json_str)
                        break
                if metadata:
                    self.file_inspected.emit(filepath, metadata)
                else:
                    stderr_short = (result.stderr or "")[:200]
                    self.file_inspected.emit(filepath, {"error": f"No metadata found. {stderr_short}"})
            except subprocess.TimeoutExpired:
                self.file_inspected.emit(filepath, {"error": "Timeout (120s)"})
            except FileNotFoundError:
                blender_missing = True
                self.file_inspected.emit(filepath, {"error": f"Blender not found: {self.blender_path}"})
            except Exception as e:
                self.file_inspected.emit(filepath, {"error": str(e)})
        self.all_done.emit()


# ---------------------------------------------------------------------------
# Submit Thread
# ---------------------------------------------------------------------------

class SubmitThread(QThread):
    """Submits jobs to Afanasy in background."""
    job_submitted = pyqtSignal(str, bool, str)  # filepath, success, message
    all_done = pyqtSignal()

    def __init__(self, jobs: list):
        """jobs: list of (filepath, BlendFileData, settings_dict)"""
        super().__init__()
        self.jobs = jobs

    def run(self):
        for filepath, file_data, settings in self.jobs:
            try:
                result = submit_blend_job(filepath, file_data, settings)
                if result and result[0]:
                    job_id = ""
                    if isinstance(result[1], dict) and "id" in result[1]:
                        job_id = result[1]["id"]
                        # Write job_id txt file
                        import os
                        blend_dir = os.path.dirname(filepath)
                        job_id_dir = os.path.join(blend_dir, "job_id")
                        os.makedirs(job_id_dir, exist_ok=True)
                        job_name = os.path.basename(filepath).replace(".blend", "")
                        txt_filename = f"{job_name}_jobID_{job_id}.txt"
                        txt_path = os.path.join(job_id_dir, txt_filename)
                        with open(txt_path, "w") as f:
                            f.write(f"Job Name: {job_name}\nJob ID: {job_id}\nBlend File: {filepath}\n")
                    self.job_submitted.emit(filepath, True, f"Submitted (ID: {job_id})")
                else:
                    self.job_submitted.emit(filepath, False, "Server rejected job")
            except Exception as e:
                self.job_submitted.emit(filepath, False, str(e))
        self.all_done.emit()


# ---------------------------------------------------------------------------
# Submission engine
# ---------------------------------------------------------------------------

def _create_block_for_layer(
    blend_file: str,
    scene_name: str,
    layer_name: str,
    frame_start: int,
    frame_end: int,
    frames_per_task: int,
    frame_step: int,
    output_path: str,
    output_format: str,
    blender: str
) -> af.Block:
    """Create a single block for one view layer (multi-block mode).

    Relies on blend file's view layer states (vl.use flags).
    Blender automatically renders all enabled layers.
    """
    cmd = f'{blender} -b "{blend_file}" -y'
    if scene_name:
        cmd += f' -S "{scene_name}"'

    # Python expression removed - rely on blend file layer states
    # Blender renders all layers with vl.use=True automatically

    if output_path:
        cmd += f' -o "{output_path}"'
    if output_format:
        cmd += f' -F {output_format}'

    # Use animation rendering for proper frame iteration with Afanasy
    cmd += f' -s @#@ -e @#@ -j {frame_step} -a'

    block_name = scene_name or "render"
    if layer_name:
        block_name = f"{block_name}_{layer_name}"

    block = af.Block(block_name, 'blender')
    block.setCommand(cmd, prefix=False)
    block.setNumeric(frame_start, frame_end, frames_per_task, frame_step)
    block.setWorkingDirectory(os.path.dirname(blend_file))

    return block


def _create_single_block_for_layers(
    blend_file: str,
    scene_name: str,
    layer_names: list,
    frame_start: int,
    frame_end: int,
    frames_per_task: int,
    frame_step: int,
    output_path: str,
    output_format: str,
    blender: str
) -> af.Block:
    """Create a single block for all selected layers (single-block mode).

    Relies on blend file's view layer states (vl.use flags).
    Blender automatically renders all enabled layers and outputs each to its own path.
    """
    cmd = f'{blender} -b "{blend_file}" -y'
    if scene_name:
        cmd += f' -S "{scene_name}"'

    # Python expression removed - rely on blend file layer states
    # Blender renders all layers with vl.use=True automatically
    # User controls layer states by editing the blend file before submission

    if output_path:
        cmd += f' -o "{output_path}"'
    if output_format:
        cmd += f' -F {output_format}'

    # Use animation rendering for proper frame iteration with Afanasy
    cmd += f' -s @#@ -e @#@ -j {frame_step} -a'

    # Block naming
    if len(layer_names) == 1:
        block_name = f"{scene_name}_{layer_names[0]}" if scene_name else layer_names[0]
    else:
        block_name = f"{scene_name}_AllLayers" if scene_name else "AllLayers"

    block = af.Block(block_name, 'blender')
    block.setCommand(cmd, prefix=False)
    # Use normal frames_per_task (workaround no longer needed with animation rendering)
    block.setNumeric(frame_start, frame_end, frames_per_task, frame_step)
    block.setWorkingDirectory(os.path.dirname(blend_file))

    return block


def _submit_single_scene_mode(job: af.Job, blend_file: str, file_data: BlendFileData, blender: str, parallel_mode: bool):
    """Submit a single scene with selected layers."""
    scene_name = file_data.selected_scene
    layers = file_data.selected_layers if file_data.selected_layers else [""]

    # Filter out non-string entries if any (shouldn't happen but be safe)
    layer_names = []
    for layer_item in layers:
        if isinstance(layer_item, str):
            name = layer_item.strip()
            if name:
                layer_names.append(name)

    if not layer_names:
        layer_names = [""]  # Render active layer

    if parallel_mode:
        # Multi-block: Create one block per layer
        for layer_name in layer_names:
            block = _create_block_for_layer(
                blend_file, scene_name, layer_name,
                file_data.frame_start, file_data.frame_end,
                file_data.frames_per_task, file_data.frame_step,
                file_data.output_path, file_data.output_format, blender
            )
            job.blocks.append(block)
    else:
        # Single-block: All layers in one block
        block = _create_single_block_for_layers(
            blend_file, scene_name, layer_names,
            file_data.frame_start, file_data.frame_end,
            file_data.frames_per_task, file_data.frame_step,
            file_data.output_path, file_data.output_format, blender
        )
        job.blocks.append(block)


def _submit_all_scenes_mode(job: af.Job, blend_file: str, file_data: BlendFileData, blender: str, parallel_mode: bool):
    """Submit all scenes with their individual settings."""
    for scene in file_data.scenes:
        scene_name = scene.get("name", "")
        scene_layers = scene.get("view_layers", [])

        # Extract enabled layer names
        layer_names = []
        for layer_data in scene_layers:
            if isinstance(layer_data, dict):
                layer_name = layer_data.get("name", "").strip()
                layer_enabled = layer_data.get("use", True)
                # Skip disabled layers to prevent black frame renders
                if layer_enabled and layer_name:
                    layer_names.append(layer_name)
            elif isinstance(layer_data, str):
                layer_name = layer_data.strip()
                if layer_name:
                    layer_names.append(layer_name)

        if not layer_names:
            layer_names = [""]  # Render active layer

        # Use scene-specific settings
        scene_output = scene.get("output_path", "")
        scene_format = scene.get("output_format", "")
        scene_start = scene.get("frame_start", 1)
        scene_end = scene.get("frame_end", 250)
        scene_step = scene.get("frame_step", 1)

        if parallel_mode:
            # Multi-block: One block per scene+layer combination
            for layer_name in layer_names:
                block = _create_block_for_layer(
                    blend_file, scene_name, layer_name,
                    scene_start, scene_end,
                    file_data.frames_per_task, scene_step,
                    scene_output, scene_format, blender
                )
                job.blocks.append(block)
        else:
            # Single-block: One block per scene (all layers together)
            block = _create_single_block_for_layers(
                blend_file, scene_name, layer_names,
                scene_start, scene_end,
                file_data.frames_per_task, scene_step,
                scene_output, scene_format, blender
            )
            job.blocks.append(block)


def submit_blend_job(blend_file: str, file_data: BlendFileData, settings: dict):
    """Submit a single .blend file as an Afanasy job.

    Supports two submission modes:
    - Multi-block (parallel): Each view layer renders as a separate block
    - Single-block (sequential): All layers render in one block together

    If file_data.selected_scene is '⚡ All Scenes', creates blocks for all scenes.
    Otherwise creates blocks for the selected scene only.
    """
    job = af.Job(file_data.job_name)
    job.setPriority(settings.get("priority", 99))

    if settings.get("start_paused"):
        job.setPaused()
    if settings.get("branch"):
        job.setBranch(settings["branch"])
    if settings.get("max_running_tasks", 0) > 0:
        job.setMaxRunningTasks(settings["max_running_tasks"])
    if settings.get("hosts_mask"):
        job.setHostsMask(settings["hosts_mask"])
    if settings.get("hosts_mask_exclude"):
        job.setHostsMaskExclude(settings["hosts_mask_exclude"])
    if settings.get("depend_mask"):
        job.setDependMask(settings["depend_mask"])

    blender = settings.get("blender_path", "blender")
    render_all_scenes = (file_data.selected_scene == "⚡ All Scenes")
    parallel_mode = file_data.render_layers_parallel

    if render_all_scenes:
        _submit_all_scenes_mode(job, blend_file, file_data, blender, parallel_mode)
    else:
        _submit_single_scene_mode(job, blend_file, file_data, blender, parallel_mode)

    return job.send(verbose=False)


# ---------------------------------------------------------------------------
# File Panel
# ---------------------------------------------------------------------------

class FilePanel(QWidget):
    """Top section: directory browse, scan, file table."""
    file_selected = pyqtSignal(str)  # filepath

    COL_CHECK = 0
    COL_FILENAME = 1
    COL_SCENE = 2
    COL_FRAMES = 3
    COL_ENGINE = 4
    COL_STATUS = 5

    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Row 1: directory browse
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Directory:"))
        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText("Select a directory containing .blend files...")
        row1.addWidget(self.dir_edit, 1)
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.clicked.connect(self._browse_directory)
        row1.addWidget(self.browse_btn)
        self.scan_btn = QPushButton("Scan")
        self.scan_btn.clicked.connect(self._scan_directory)
        row1.addWidget(self.scan_btn)
        self.inspect_selected_btn = QPushButton("Inspect Selected")
        self.inspect_selected_btn.setToolTip("Inspect checked files (Ctrl+I)")
        row1.addWidget(self.inspect_selected_btn)
        self.inspect_all_btn = QPushButton("Inspect All")
        row1.addWidget(self.inspect_all_btn)
        layout.addLayout(row1)

        # Row 2: filter + select all/none
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Type to filter files...")
        self.filter_edit.textChanged.connect(self._apply_filter)
        row2.addWidget(self.filter_edit, 1)
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        row2.addWidget(self.select_all_btn)
        self.select_none_btn = QPushButton("Select None")
        self.select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        row2.addWidget(self.select_none_btn)
        layout.addLayout(row2)

        # File table
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "File", "Scene", "Frames", "Engine", "Status"])
        header = self.table.horizontalHeader()
        # All columns user-resizable (Interactive) except checkbox
        header.setSectionResizeMode(self.COL_CHECK, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_FILENAME, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(self.COL_SCENE, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(self.COL_FRAMES, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(self.COL_ENGINE, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeMode.Stretch)
        # Default column widths (proportional)
        header.resizeSection(self.COL_FILENAME, 240)
        header.resizeSection(self.COL_SCENE, 80)
        header.resizeSection(self.COL_FRAMES, 80)
        header.resizeSection(self.COL_ENGINE, 70)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.itemChanged.connect(self._update_inspect_button_text)
        layout.addWidget(self.table)

    def _browse_directory(self):
        path = QFileDialog.getExistingDirectory(self, "Select Directory", self.dir_edit.text())
        if path:
            self.dir_edit.setText(path)
            self._scan_directory()

    def _scan_directory(self):
        directory = self.dir_edit.text()
        if not directory or not os.path.isdir(directory):
            return
        self.table.setRowCount(0)
        blend_files = []
        for root, _, files in os.walk(directory):
            for f in sorted(files):
                if f.endswith(".blend") and not f.endswith(".blend1"):
                    blend_files.append(os.path.join(root, f))

        for filepath in blend_files:
            self._add_file_row(filepath)

    def _add_file_row(self, filepath: str):
        row = self.table.rowCount()
        self.table.insertRow(row)

        # Checkbox
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        chk.setCheckState(Qt.CheckState.Checked)
        self.table.setItem(row, self.COL_CHECK, chk)

        # Filename
        name_item = QTableWidgetItem(os.path.basename(filepath))
        name_item.setToolTip(filepath)
        name_item.setData(Qt.ItemDataRole.UserRole, filepath)
        self.table.setItem(row, self.COL_FILENAME, name_item)

        # Scene, Frames, Engine placeholders
        self.table.setItem(row, self.COL_SCENE, QTableWidgetItem("-"))
        self.table.setItem(row, self.COL_FRAMES, QTableWidgetItem("-"))
        self.table.setItem(row, self.COL_ENGINE, QTableWidgetItem("-"))

        # Status
        status_item = QTableWidgetItem("Not inspected")
        status_item.setForeground(QColor("#777"))
        self.table.setItem(row, self.COL_STATUS, status_item)

    def get_all_filepaths(self) -> list:
        paths = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_FILENAME)
            if item:
                paths.append(item.data(Qt.ItemDataRole.UserRole))
        return paths

    def get_checked_filepaths(self) -> list:
        paths = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, self.COL_CHECK)
            if chk and chk.checkState() == Qt.CheckState.Checked:
                item = self.table.item(row, self.COL_FILENAME)
                if item:
                    paths.append(item.data(Qt.ItemDataRole.UserRole))
        return paths

    def update_file_row(self, filepath: str, scene: str, frames: str, engine: str, status: str,
                       error: bool = False, scene_tooltip: str = "", blender_version: str = ""):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_FILENAME)
            if item and item.data(Qt.ItemDataRole.UserRole) == filepath:
                # Update filename tooltip with version info
                if blender_version:
                    filename_tooltip = f"{filepath}\nBlender {blender_version}"
                    item.setToolTip(filename_tooltip)

                scene_item = self.table.item(row, self.COL_SCENE)
                scene_item.setText(scene)
                if scene_tooltip:
                    scene_item.setToolTip(scene_tooltip)
                self.table.item(row, self.COL_FRAMES).setText(frames)
                self.table.item(row, self.COL_ENGINE).setText(engine)
                status_item = self.table.item(row, self.COL_STATUS)
                status_item.setText(status)
                status_item.setToolTip(status)  # Full text visible on hover
                status_item.setForeground(QColor("#ef5350") if error else QColor("#66bb6a"))
                break

    def set_row_status(self, filepath: str, status: str):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_FILENAME)
            if item and item.data(Qt.ItemDataRole.UserRole) == filepath:
                status_item = self.table.item(row, self.COL_STATUS)
                status_item.setText(status)
                status_item.setForeground(QColor("#64b5f6"))
                break

    def _set_all_checked(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                item = self.table.item(row, self.COL_CHECK)
                if item:
                    item.setCheckState(state)
        self._update_inspect_button_text()

    def _apply_filter(self, text: str):
        text = text.lower()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_FILENAME)
            if item:
                self.table.setRowHidden(row, text not in item.text().lower())

    def _on_cell_clicked(self, row: int, col: int):
        if col == self.COL_CHECK:
            return
        item = self.table.item(row, self.COL_FILENAME)
        if item:
            self.file_selected.emit(item.data(Qt.ItemDataRole.UserRole))

    def _update_inspect_button_text(self):
        """Update Inspect Selected button text with checked file count."""
        checked_count = len(self.get_checked_filepaths())
        if checked_count > 0:
            self.inspect_selected_btn.setText(f"Inspect Selected ({checked_count})")
        else:
            self.inspect_selected_btn.setText("Inspect Selected")


# ---------------------------------------------------------------------------
# Job Settings Tab
# ---------------------------------------------------------------------------

class JobSettingsTab(QWidget):
    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.job_name_edit = QLineEdit()
        self.job_name_edit.setPlaceholderText("Auto from filename")
        layout.addRow("Job Name:", self.job_name_edit)

        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(0, 250)
        self.priority_spin.setValue(99)
        layout.addRow("Priority:", self.priority_spin)

        self.paused_check = QCheckBox("Start job paused")
        layout.addRow("", self.paused_check)

        self.branch_edit = QLineEdit()
        self.branch_edit.setPlaceholderText("Auto from directory")
        layout.addRow("Branch:", self.branch_edit)

        self.max_tasks_spin = QSpinBox()
        self.max_tasks_spin.setRange(0, 9999)
        self.max_tasks_spin.setValue(0)
        self.max_tasks_spin.setSpecialValueText("Unlimited")
        layout.addRow("Max Running Tasks:", self.max_tasks_spin)

        self.depend_mask_edit = QLineEdit()
        self.depend_mask_edit.setPlaceholderText("Regex pattern (optional)")
        layout.addRow("Depend Mask:", self.depend_mask_edit)

        self.hosts_mask_edit = QLineEdit()
        self.hosts_mask_edit.setPlaceholderText("Regex pattern (optional)")
        layout.addRow("Hosts Mask:", self.hosts_mask_edit)

        self.exclude_hosts_edit = QLineEdit()
        self.exclude_hosts_edit.setPlaceholderText("Regex pattern (optional)")
        layout.addRow("Exclude Hosts:", self.exclude_hosts_edit)

    def get_settings(self) -> dict:
        return {
            "priority": self.priority_spin.value(),
            "start_paused": self.paused_check.isChecked(),
            "branch": self.branch_edit.text(),
            "max_running_tasks": self.max_tasks_spin.value(),
            "depend_mask": self.depend_mask_edit.text(),
            "hosts_mask": self.hosts_mask_edit.text(),
            "hosts_mask_exclude": self.exclude_hosts_edit.text(),
        }

    def populate_from_file(self, file_data: BlendFileData):
        self.job_name_edit.setText(file_data.job_name)
        if not self.branch_edit.text():
            self.branch_edit.setText(os.path.dirname(file_data.filepath))


# ---------------------------------------------------------------------------
# Render Settings Tab
# ---------------------------------------------------------------------------

class RenderSettingsTab(QWidget):
    def __init__(self):
        super().__init__()
        self._updating = False
        self._current_file_data = None  # Reference to current file being edited
        self._build_ui()
        self._connect_override_tracking()

    def _build_ui(self):
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        # Scene selector
        self.scene_combo = QComboBox()
        self.scene_combo.currentTextChanged.connect(self._on_scene_changed)
        layout.addRow("Scene:", self.scene_combo)

        # View layers with check/uncheck buttons
        layers_container = QWidget()
        layers_layout = QVBoxLayout(layers_container)
        layers_layout.setContentsMargins(0, 0, 0, 0)

        # Button row
        layers_buttons = QHBoxLayout()
        self.check_all_layers_btn = QPushButton("Check All")
        self.check_all_layers_btn.clicked.connect(self._check_all_layers)
        self.uncheck_all_layers_btn = QPushButton("Uncheck All")
        self.uncheck_all_layers_btn.clicked.connect(self._uncheck_all_layers)
        layers_buttons.addWidget(self.check_all_layers_btn)
        layers_buttons.addWidget(self.uncheck_all_layers_btn)
        layers_buttons.addStretch()
        layers_layout.addLayout(layers_buttons)

        # Layers list
        self.layers_list = QListWidget()
        self.layers_list.setMaximumHeight(100)
        layers_layout.addWidget(self.layers_list)

        layout.addRow("View Layers:", layers_container)

        # Parallel rendering mode
        self.parallel_layers_check = QCheckBox("Render layers in separate blocks (parallel)")
        self.parallel_layers_check.setChecked(False)  # Default to unchecked
        self.parallel_layers_check.setToolTip(
            "ON (default): Each view layer renders as a separate block, allowing parallel rendering on different farm nodes.\n"
            "OFF: All selected layers render in one block sequentially, reducing job tree complexity."
        )
        self.parallel_layers_check.stateChanged.connect(self._on_parallel_mode_changed)
        layout.addRow("", self.parallel_layers_check)

        # Frame range
        frame_group = QHBoxLayout()
        self.frame_start_spin = QSpinBox()
        self.frame_start_spin.setRange(0, 999999)
        self.frame_start_spin.setValue(1)
        self.frame_start_spin.valueChanged.connect(self._update_calculated)
        frame_group.addWidget(QLabel("Start:"))
        frame_group.addWidget(self.frame_start_spin)

        self.frame_end_spin = QSpinBox()
        self.frame_end_spin.setRange(0, 999999)
        self.frame_end_spin.setValue(250)
        self.frame_end_spin.valueChanged.connect(self._update_calculated)
        frame_group.addWidget(QLabel("End:"))
        frame_group.addWidget(self.frame_end_spin)

        self.frame_step_spin = QSpinBox()
        self.frame_step_spin.setRange(1, 999)
        self.frame_step_spin.setValue(1)
        self.frame_step_spin.valueChanged.connect(self._update_calculated)
        frame_group.addWidget(QLabel("Step:"))
        frame_group.addWidget(self.frame_step_spin)
        layout.addRow("Frame Range:", frame_group)

        self.fpt_spin = QSpinBox()
        self.fpt_spin.setRange(1, 9999)
        self.fpt_spin.setValue(3)
        self.fpt_spin.valueChanged.connect(self._update_calculated)
        layout.addRow("Frames Per Task:", self.fpt_spin)

        # Render engine (read-only)
        self.engine_label = QLabel("-")
        layout.addRow("Render Engine:", self.engine_label)

        # Note: Compositing (scene.use_nodes) is controlled by the blend file
        # No command line flag exists to override it

        # Output
        out_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("From scene settings")
        out_row.addWidget(self.output_edit, 1)
        self.output_browse_btn = QPushButton("Browse")
        self.output_browse_btn.clicked.connect(self._browse_output)
        out_row.addWidget(self.output_browse_btn)
        layout.addRow("Output Path:", out_row)

        self.format_combo = QComboBox()
        self.format_combo.addItems([
            "OPEN_EXR", "OPEN_EXR_MULTILAYER", "PNG", "JPEG",
            "TIFF", "BMP", "HDR", "TARGA"
        ])
        layout.addRow("Output Format:", self.format_combo)

        # Reset overrides button
        reset_row = QHBoxLayout()
        self.reset_btn = QPushButton("Reset to File Settings")
        self.reset_btn.setToolTip("Clear manual overrides and reload settings from blend file")
        self.reset_btn.clicked.connect(self._reset_overrides)
        self.reset_btn.setEnabled(False)  # Enabled when overrides exist
        reset_row.addWidget(self.reset_btn)
        reset_row.addStretch()
        layout.addRow("", reset_row)

        # Calculated info
        self.resolution_label = QLabel("-")
        layout.addRow("Resolution:", self.resolution_label)
        self.total_frames_label = QLabel("-")
        layout.addRow("Total Frames:", self.total_frames_label)
        self.total_tasks_label = QLabel("-")
        layout.addRow("Total Tasks:", self.total_tasks_label)

        # Store scenes data for switching
        self._scenes_data = []

    def _connect_override_tracking(self):
        """Connect widget signals to track user overrides."""
        # Frame range overrides
        self.frame_start_spin.valueChanged.connect(lambda: self._mark_override("frame_start"))
        self.frame_end_spin.valueChanged.connect(lambda: self._mark_override("frame_end"))
        self.frame_step_spin.valueChanged.connect(lambda: self._mark_override("frame_step"))
        self.fpt_spin.valueChanged.connect(lambda: self._mark_override("frames_per_task"))

        # Output overrides
        self.output_edit.textChanged.connect(lambda: self._mark_override("output_path"))
        self.format_combo.currentTextChanged.connect(lambda: self._mark_override("output_format"))

        # Note: selected_scene and selected_layers are handled separately as they're
        # user selections by nature, not overrides

    def _mark_override(self, field_name: str):
        """Mark a field as manually overridden by user."""
        # Only mark as override if not during programmatic update
        if not self._updating and self._current_file_data:
            self._current_file_data.user_overrides.add(field_name)
            # Enable reset button when overrides exist
            self.reset_btn.setEnabled(True)
            self.reset_btn.setStyleSheet("background-color: #ff9800; color: white;")  # Orange highlight

    def _reset_overrides(self):
        """Clear all user overrides and reload settings from file."""
        if not self._current_file_data:
            return

        # Clear overrides
        self._current_file_data.user_overrides.clear()

        # Reload from file data (this will now update all fields)
        self.populate_from_file(self._current_file_data)

        # Disable reset button
        self.reset_btn.setEnabled(False)
        self.reset_btn.setStyleSheet("")  # Reset style

    def populate_from_file(self, file_data: BlendFileData):
        self._updating = True
        self._current_file_data = file_data  # Store reference for override tracking
        self._scenes_data = file_data.scenes
        overrides = file_data.user_overrides

        # Populate scene combo (always update - scene selection is not overrideable)
        self.scene_combo.clear()
        if file_data.scenes:
            # Add "All Scenes" option if multiple scenes exist
            if len(file_data.scenes) > 1:
                self.scene_combo.addItem("⚡ All Scenes")

            for s in file_data.scenes:
                self.scene_combo.addItem(s["name"])

            if file_data.selected_scene:
                idx = self.scene_combo.findText(file_data.selected_scene)
                if idx >= 0:
                    self.scene_combo.setCurrentIndex(idx)
        else:
            self.scene_combo.addItem("(no scenes detected)")

        # Only update fields that haven't been manually overridden
        if "frame_start" not in overrides:
            self.frame_start_spin.setValue(file_data.frame_start)
        if "frame_end" not in overrides:
            self.frame_end_spin.setValue(file_data.frame_end)
        if "frame_step" not in overrides:
            self.frame_step_spin.setValue(file_data.frame_step)
        if "frames_per_task" not in overrides:
            self.fpt_spin.setValue(file_data.frames_per_task)

        # Always update read-only fields
        self.engine_label.setText(file_data.render_engine or "-")
        self.resolution_label.setText(f"{file_data.resolution_x} x {file_data.resolution_y}")

        # Only update output fields if not overridden
        if "output_path" not in overrides:
            self.output_edit.setText(file_data.output_path)
        if "output_format" not in overrides:
            idx = self.format_combo.findText(file_data.output_format)
            if idx >= 0:
                self.format_combo.setCurrentIndex(idx)

        # Populate layers (always update - layer selection is not overrideable)
        self._populate_layers(file_data.selected_layers)

        # Update reset button state
        if overrides:
            self.reset_btn.setEnabled(True)
            self.reset_btn.setStyleSheet("background-color: #ff9800; color: white;")
        else:
            self.reset_btn.setEnabled(False)
            self.reset_btn.setStyleSheet("")

        # Set parallel rendering checkbox
        self.parallel_layers_check.setChecked(file_data.render_layers_parallel)

        self._updating = False
        self._update_calculated()

    def _populate_layers(self, selected_layers: list):
        self.layers_list.clear()
        scene_name = self.scene_combo.currentText()

        # Special handling for "All Scenes"
        if scene_name == "⚡ All Scenes":
            # Show note that all scene layers will be rendered
            item = QListWidgetItem("(All scenes & layers will be rendered)")
            item.setFlags(Qt.ItemFlag.NoItemFlags)  # Not selectable
            self.layers_list.addItem(item)
            self._update_layer_button_state()
            return

        scene_data = next((s for s in self._scenes_data if s["name"] == scene_name), None)
        if scene_data and scene_data.get("view_layers"):
            for layer_data in scene_data["view_layers"]:
                # Handle both old string format and new dict format
                if isinstance(layer_data, dict):
                    layer_name = layer_data.get("name", "")
                    layer_use = layer_data.get("use", True)  # Is this layer enabled for rendering?
                else:
                    layer_name = layer_data
                    layer_use = True  # Default to enabled for old format

                item = QListWidgetItem(layer_name)

                if layer_use:
                    # Layer is enabled: make it checkable
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    # Auto-check if in selected_layers or no layers selected yet
                    should_check = layer_name in selected_layers or not selected_layers
                    item.setCheckState(
                        Qt.CheckState.Checked if should_check else Qt.CheckState.Unchecked
                    )
                else:
                    # Layer is DISABLED: make it NOT checkable to prevent black frame renders
                    # Remove checkable flag so user cannot accidentally enable it
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # Can see but not interact
                    item.setForeground(QColor("#888"))  # Gray out disabled layers
                    item.setToolTip(
                        f"⚠️ {layer_name} is disabled for rendering in blend file\n"
                        f"(Enabling it could cause black frames with compositor nodes)"
                    )

                self.layers_list.addItem(item)

        # Update button state at the end
        self._update_layer_button_state()

    def _check_all_layers(self):
        """Check all view layers in the list."""
        for i in range(self.layers_list.count()):
            item = self.layers_list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Checked)

    def _uncheck_all_layers(self):
        """Uncheck all view layers in the list."""
        for i in range(self.layers_list.count()):
            item = self.layers_list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Unchecked)

    def _update_layer_button_state(self):
        """Enable/disable layer buttons based on list contents."""
        # Count checkable items (exclude "All Scenes" placeholder)
        checkable_count = 0
        for i in range(self.layers_list.count()):
            item = self.layers_list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                checkable_count += 1

        # Enable buttons only if there are checkable items
        enabled = checkable_count > 0
        self.check_all_layers_btn.setEnabled(enabled)
        self.uncheck_all_layers_btn.setEnabled(enabled)

    def _on_scene_changed(self, scene_name: str):
        if self._updating:
            return
        
        # Handle "All Scenes" option
        if scene_name == "⚡ All Scenes":
            # Show combined info from all scenes
            if self._scenes_data:
                # Use first scene's settings as default
                first_scene = self._scenes_data[0]
                self.frame_start_spin.setValue(first_scene["frame_start"])
                self.frame_end_spin.setValue(first_scene["frame_end"])
                self.frame_step_spin.setValue(first_scene.get("frame_step", 1))
                self.engine_label.setText("Multiple")
                self.output_edit.setText("(per-scene settings)")
                self.resolution_label.setText("(varies)")
                self.format_combo.setCurrentIndex(0)
            self._populate_layers([])
            self._update_calculated()
            return
        
        scene_data = next((s for s in self._scenes_data if s["name"] == scene_name), None)
        if scene_data:
            self.frame_start_spin.setValue(scene_data["frame_start"])
            self.frame_end_spin.setValue(scene_data["frame_end"])
            self.frame_step_spin.setValue(scene_data.get("frame_step", 1))
            self.engine_label.setText(scene_data.get("render_engine", "-"))
            # Compositing controlled by blend file (no UI override)
            self.output_edit.setText(scene_data.get("output_path", ""))
            self.resolution_label.setText(
                f"{scene_data.get('resolution_x', '?')} x {scene_data.get('resolution_y', '?')}"
            )
            fmt = scene_data.get("output_format", "")
            idx = self.format_combo.findText(fmt)
            if idx >= 0:
                self.format_combo.setCurrentIndex(idx)
            self._populate_layers(scene_data.get("view_layers", []))
            self._update_calculated()

    def _on_parallel_mode_changed(self, _state):
        """Handle changes to parallel rendering mode checkbox."""
        if not self._updating and self._current_file_data:
            self._current_file_data.render_layers_parallel = self.parallel_layers_check.isChecked()

    def _update_calculated(self):
        scene_name = self.scene_combo.currentText()
        
        # Special calculation for All Scenes
        if scene_name == "⚡ All Scenes":
            total_frames_all = 0
            total_tasks_all = 0
            fpt = self.fpt_spin.value()
            
            for scene in self._scenes_data:
                start = scene.get("frame_start", 1)
                end = scene.get("frame_end", 250)
                step = scene.get("frame_step", 1)
                frames = max(0, (end - start) // step + 1) if step > 0 else 0
                tasks = math.ceil(frames / fpt) if fpt > 0 and frames > 0 else 0
                total_frames_all += frames
                total_tasks_all += tasks
            
            self.total_frames_label.setText(f"{total_frames_all} (all scenes)")
            self.total_tasks_label.setText(f"{total_tasks_all} (all scenes)")
        else:
            # Single scene calculation
            start = self.frame_start_spin.value()
            end = self.frame_end_spin.value()
            step = self.frame_step_spin.value()
            fpt = self.fpt_spin.value()
            total_frames = max(0, (end - start) // step + 1) if step > 0 else 0
            total_tasks = math.ceil(total_frames / fpt) if fpt > 0 else 0
            self.total_frames_label.setText(str(total_frames))
            self.total_tasks_label.setText(str(total_tasks))

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "Select Output Directory", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def get_selected_scene(self) -> str:
        return self.scene_combo.currentText()

    def get_selected_layers(self) -> list:
        layers = []
        for i in range(self.layers_list.count()):
            item = self.layers_list.item(i)
            # Only check state if item is checkable
            if (item.flags() & Qt.ItemFlag.ItemIsUserCheckable) and \
               (item.checkState() == Qt.CheckState.Checked):
                layer_text = item.text().strip()
                if layer_text:  # Only add non-empty layer names
                    layers.append(layer_text)
        return layers

    def apply_to_file_data(self, file_data: BlendFileData):
        file_data.selected_scene = self.scene_combo.currentText()
        file_data.selected_layers = self.get_selected_layers()
        file_data.frame_start = self.frame_start_spin.value()
        file_data.frame_end = self.frame_end_spin.value()
        file_data.frame_step = self.frame_step_spin.value()
        file_data.frames_per_task = self.fpt_spin.value()
        file_data.output_path = self.output_edit.text()
        file_data.output_format = self.format_combo.currentText()
        file_data.render_layers_parallel = self.parallel_layers_check.isChecked()
        # Compositing (use_nodes) controlled by blend file, not user-editable


# ---------------------------------------------------------------------------
# App Settings Tab
# ---------------------------------------------------------------------------

class AppSettingsTab(QWidget):
    def __init__(self):
        super().__init__()
        # Pre-create jobid combo before UI build so it always exists
        self.cleanup_jobid_combo = QComboBox()
        self.cleanup_jobid_combo.setEditable(True)
        self.cleanup_jobid_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.cleanup_jobid_combo.setMinimumWidth(100)
        self.cleanup_jobid_combo.setPlaceholderText("Job ID")
        self._build_ui()
        # Connect signal for auto job_id detection (only once, after UI is built)
        self.cleanup_blend_combo.currentTextChanged.connect(self._auto_detect_job_ids)

    def _build_ui(self):
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        # Blender path
        blender_row = QHBoxLayout()
        self.blender_edit = QLineEdit()
        self.blender_edit.setPlaceholderText("Path to Blender executable")
        blender_row.addWidget(self.blender_edit, 1)
        self.blender_browse_btn = QPushButton("Browse")
        self.blender_browse_btn.clicked.connect(self._browse_blender)
        blender_row.addWidget(self.blender_browse_btn)
        layout.addRow("Blender Path:", blender_row)

        # --- Job ID txt cleanup UI ---
        cleanup_row = QHBoxLayout()
        self.cleanup_blend_combo = QComboBox()
        self.cleanup_blend_combo.setEditable(True)
        self.cleanup_blend_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.cleanup_blend_combo.setMinimumWidth(220)
        self.cleanup_blend_combo.setPlaceholderText("Select or browse .blend file...")
        cleanup_row.addWidget(self.cleanup_blend_combo, 1)
        self.cleanup_blend_btn = QPushButton("Browse")
        self.cleanup_blend_btn.clicked.connect(self._browse_cleanup_blend)
        cleanup_row.addWidget(self.cleanup_blend_btn)
        self.reload_jobid_btn = QPushButton("Reload")
        self.reload_jobid_btn.setToolTip("Reload job IDs from job_id folder for selected blend file")
        self.reload_jobid_btn.setFixedWidth(64)
        self.reload_jobid_btn.clicked.connect(self._reload_job_ids)
        cleanup_row.addWidget(self.reload_jobid_btn)
        cleanup_row.addWidget(self.cleanup_jobid_combo)
        self.cleanup_btn = QPushButton("Delete job_id txt")
        self.cleanup_btn.clicked.connect(self._cleanup_job_id_txt)
        cleanup_row.addWidget(self.cleanup_btn)
        layout.addRow("Cleanup job_id txt:", cleanup_row)

    def _reload_job_ids(self):
        blend_path = self.cleanup_blend_combo.currentText().strip()
        self._auto_detect_job_ids(blend_path)
        
    def update_cleanup_blend_list(self, blend_filepaths: list):
        self.cleanup_blend_combo.clear()
        self.cleanup_blend_combo.addItems(blend_filepaths)
        self.cleanup_blend_combo.setCurrentIndex(-1)
        if hasattr(self, 'cleanup_jobid_combo'):
            self.cleanup_jobid_combo.clear()

    def _auto_detect_job_ids(self, blend_path):
        import os, re
        if not hasattr(self, 'cleanup_jobid_combo'):
            return
        self.cleanup_jobid_combo.clear()
        blend_path = blend_path.strip()
        if not blend_path or not os.path.isfile(blend_path):
            self.cleanup_jobid_combo.addItem("No job_id found")
            return
        blend_dir = os.path.dirname(blend_path)
        job_id_dir = os.path.join(blend_dir, "job_id")
        job_name = os.path.basename(blend_path).replace(".blend", "")
        if not os.path.isdir(job_id_dir):
            self.cleanup_jobid_combo.addItem("No job_id found")
            return
        job_ids = []
        for fname in os.listdir(job_id_dir):
            if fname.startswith(job_name + "_jobID_") and fname.endswith(".txt"):
                m = re.match(rf"{re.escape(job_name)}_jobID_(.+)\.txt", fname)
                if m:
                    job_ids.append(m.group(1))
        if job_ids:
            self.cleanup_jobid_combo.clear()
            self.cleanup_jobid_combo.addItems(sorted(job_ids, key=lambda x: int(x) if x.isdigit() else x))
            self.cleanup_jobid_combo.setCurrentIndex(0)
        else:
            self.cleanup_jobid_combo.clear()
            self.cleanup_jobid_combo.addItem("No job_id found")
        self.cleanup_jobid_combo.update()
    def _browse_cleanup_blend(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Blend File",
            self.cleanup_blend_combo.currentText(),
            "Blender Files (*.blend);;All Files (*)"
        )
        if path:
            idx = self.cleanup_blend_combo.findText(path)
            if idx == -1:
                self.cleanup_blend_combo.addItem(path)
            self.cleanup_blend_combo.setCurrentText(path)
            self._auto_detect_job_ids(path)

    def _cleanup_job_id_txt(self):
        filepath = self.cleanup_blend_combo.currentText().strip()
        job_id = self.cleanup_jobid_combo.currentText().strip()
        if not filepath or not job_id:
            QMessageBox.warning(self, "Missing Info", "Please select a .blend file and enter a Job ID.")
            return
        try:
            remove_job_id_txt(filepath, job_id)
            QMessageBox.information(self, "Cleanup Complete", f"job_id txt for Job ID {job_id} removed (if existed).")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to remove job_id txt: {e}")


    # (UI built earlier in __init__ - duplicate method removed)

    def _browse_blender(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Blender Executable",
            self.blender_edit.text(),
            "Executables (*);;All Files (*)"
        )
        if path:
            self.blender_edit.setText(path)


# ---------------------------------------------------------------------------
# Submission Log
# ---------------------------------------------------------------------------

class SubmissionPanel(QWidget):
    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Log
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        font = QFont("Monospace", 9)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.log.setFont(font)
        layout.addWidget(self.log)

        # Bottom row: progress + submit
        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        bottom.addWidget(self.progress, 1)
        self.submit_btn = QPushButton("Submit Selected")
        self.submit_btn.setMinimumHeight(36)
        self.submit_btn.setStyleSheet(
            "QPushButton { background-color: #1976d2; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 8px 24px; }"
            "QPushButton:hover { background-color: #2196f3; }"
            "QPushButton:disabled { background-color: #444; color: #777; }"
        )
        bottom.addWidget(self.submit_btn)
        layout.addLayout(bottom)

    def log_message(self, msg: str, error: bool = False):
        color = "#ef5350" if error else "#b0b0b0"
        self.log.append(f'<span style="color:{color}">&gt; {msg}</span>')
        # Scroll to bottom
        scrollbar = self.log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_log(self):
        self.log.clear()


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def update_cleanup_blend_list(self):
        if hasattr(self, 'file_panel') and hasattr(self, 'app_tab'):
            blend_files = self.file_panel.get_all_filepaths()
            self.app_tab.update_cleanup_blend_list(blend_files)

    def cleanup_job_id_txt(self, filepath, job_id):
        """Public method to remove job_id txt file after job deletion."""
        remove_job_id_txt(filepath, job_id)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Batch Submitter - Blender to Afanasy")
        self.setMinimumSize(900, 700)

        self.file_data = {}
        self.current_filepath = ""
        self.inspector_thread = None
        self.submit_thread = None

        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # Splitter: file panel (top) | settings tabs (middle) | submission log (bottom)
        splitter = QSplitter(Qt.Orientation.Vertical)

        # File panel
        self.file_panel = FilePanel()
        self.file_panel.file_selected.connect(self._on_file_selected)
        self.file_panel.inspect_selected_btn.clicked.connect(self._inspect_selected)
        self.file_panel.inspect_all_btn.clicked.connect(self._inspect_all)
        self.file_panel.scan_btn.clicked.connect(self.update_cleanup_blend_list)
        splitter.addWidget(self.file_panel)

        # Settings tabs
        self.tabs = QTabWidget()
        self.job_tab = JobSettingsTab()
        self.render_tab = RenderSettingsTab()
        self.app_tab = AppSettingsTab()
        self.tabs.addTab(self.job_tab, "Job Settings")
        self.tabs.addTab(self.render_tab, "Render Settings")
        self.tabs.addTab(self.app_tab, "App Settings")
        splitter.addWidget(self.tabs)

        # Submission panel
        self.submission_panel = SubmissionPanel()
        self.submission_panel.submit_btn.clicked.connect(self._submit_selected)
        splitter.addWidget(self.submission_panel)

        # Set splitter proportions
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 1)

        main_layout.addWidget(splitter)

        # Keyboard shortcuts
        inspect_shortcut = QShortcut(QKeySequence("Ctrl+I"), self)
        inspect_shortcut.activated.connect(self._inspect_selected)

    # --- Settings persistence ---

    def _load_settings(self):
        settings = QSettings("MonstaStudios", "BatchSubmitter")
        self.file_panel.dir_edit.setText(settings.value("last_directory", ""))
        self.app_tab.blender_edit.setText(
            settings.value("blender_path", config.SETTINGS.get("BLENDER_PATH", ""))
        )
        self.job_tab.priority_spin.setValue(int(settings.value("default_priority", 99)))
        self.render_tab.fpt_spin.setValue(int(settings.value("default_fpt", 3)))

        geom = settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)

    def _save_settings(self):
        settings = QSettings("MonstaStudios", "BatchSubmitter")
        settings.setValue("last_directory", self.file_panel.dir_edit.text())
        settings.setValue("blender_path", self.app_tab.blender_edit.text())
        settings.setValue("default_priority", self.job_tab.priority_spin.value())
        settings.setValue("default_fpt", self.render_tab.fpt_spin.value())
        settings.setValue("geometry", self.saveGeometry())

    def closeEvent(self, event):
        self._save_settings()
        if self.inspector_thread and self.inspector_thread.isRunning():
            self.inspector_thread.stop()
            self.inspector_thread.wait(3000)
        super().closeEvent(event)

    # --- File selection ---

    def _on_file_selected(self, filepath: str):
        # Save current file's overrides before switching
        if self.current_filepath and self.current_filepath in self.file_data:
            self.render_tab.apply_to_file_data(self.file_data[self.current_filepath])
            self.file_data[self.current_filepath].job_name = self.job_tab.job_name_edit.text()

        self.current_filepath = filepath

        # Get or create file data
        if filepath not in self.file_data:
            self.file_data[filepath] = BlendFileData(
                filepath=filepath,
                job_name=os.path.basename(filepath).replace(".blend", ""),
            )

        fd = self.file_data[filepath]
        self.job_tab.populate_from_file(fd)
        self.render_tab.populate_from_file(fd)

    # --- Inspect ---

    def _format_scene_display(self, scenes: list, selected_scene: str) -> str:
        """Format scene name for table display.

        Shows:
        - Single scene: "Scene"
        - Multiple scenes: "_Main +2" or "Scene (3)"
        """
        if not scenes:
            return "-"

        if len(scenes) == 1:
            return scenes[0]["name"]

        # Multiple scenes: show first/selected scene + count
        first_scene = selected_scene if selected_scene else scenes[0]["name"]
        extra_count = len(scenes) - 1
        return f"{first_scene} +{extra_count}"

    def _format_scene_tooltip(self, scenes: list) -> str:
        """Format tooltip showing all scene names."""
        if not scenes:
            return ""
        return "\n".join(s["name"] for s in scenes)

    def _get_blender_path(self) -> str:
        return self.app_tab.blender_edit.text().strip()

    def _inspect_selected(self):
        blender = self._get_blender_path()
        if not blender:
            QMessageBox.warning(self, "Blender Path", "Set the Blender executable path in App Settings first.")
            self.tabs.setCurrentWidget(self.app_tab)
            return

        files = self.file_panel.get_checked_filepaths()
        if not files:
            QMessageBox.information(self, "No Files Selected", "Please check at least one file to inspect.")
            return

        self._run_inspection(files)

    def _inspect_all(self):
        blender = self._get_blender_path()
        if not blender:
            QMessageBox.warning(self, "Blender Path", "Set the Blender executable path in App Settings first.")
            self.tabs.setCurrentWidget(self.app_tab)
            return

        files = self.file_panel.get_all_filepaths()
        if not files:
            return

        self._run_inspection(files)

    def _run_inspection(self, files: list):
        """Common inspection logic for both Inspect Selected and Inspect All."""
        blender = self._get_blender_path()
        inspector_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blend_inspector.py")

        # Mark all as inspecting
        for f in files:
            self.file_panel.set_row_status(f, "Inspecting...")

        # Disable both buttons during inspection
        self.file_panel.inspect_selected_btn.setEnabled(False)
        self.file_panel.inspect_selected_btn.setText("Inspecting...")
        self.file_panel.inspect_all_btn.setEnabled(False)
        self.file_panel.inspect_all_btn.setText("Inspecting...")

        self.inspector_thread = InspectorThread(files, blender, inspector_script)
        self.inspector_thread.file_inspected.connect(self._on_file_inspected)
        self.inspector_thread.all_done.connect(self._on_inspect_done)
        self.inspector_thread.start()

    def _on_file_inspected(self, filepath: str, metadata: dict):
        if "error" in metadata:
            # Create minimal file data with error
            if filepath not in self.file_data:
                self.file_data[filepath] = BlendFileData(filepath=filepath)
            self.file_data[filepath].error = metadata["error"]
            short_name = os.path.basename(filepath)
            full_error = metadata["error"]
            self.file_panel.update_file_row(filepath, "-", "-", "-", "Error", error=True)
            self.submission_panel.log_message(f"{short_name}: {full_error}", error=True)
            return

        scenes = metadata.get("scenes", [])
        first_scene = scenes[0] if scenes else {}

        # Extract layer names from view_layers (which may be dicts or strings)
        view_layers_data = first_scene.get("view_layers", [])
        enabled_layer_names = []
        for layer_data in view_layers_data:
            if isinstance(layer_data, dict):
                # Only include layers that are enabled for rendering
                if layer_data.get("use", True):
                    enabled_layer_names.append(layer_data.get("name", ""))
            else:
                # Old string format
                enabled_layer_names.append(layer_data)

        fd = BlendFileData(
            filepath=filepath,
            job_name=metadata.get("job_name", os.path.basename(filepath).replace(".blend", "")),
            inspected=True,
            scenes=scenes,
            selected_scene=first_scene.get("name", ""),
            selected_layers=enabled_layer_names,
            frame_start=first_scene.get("frame_start", 1),
            frame_end=first_scene.get("frame_end", 250),
            frame_step=first_scene.get("frame_step", 1),
            frames_per_task=self.render_tab.fpt_spin.value(),
            render_engine=first_scene.get("render_engine", ""),
            resolution_x=first_scene.get("resolution_x", 1920),
            resolution_y=first_scene.get("resolution_y", 1080),
            output_path=first_scene.get("output_path", ""),
            output_format=first_scene.get("output_format", "OPEN_EXR"),
            use_nodes=first_scene.get("use_nodes", False),
            blender_version=metadata.get("blender_version", "Unknown"),
        )
        self.file_data[filepath] = fd

        frames_str = f"{fd.frame_start}-{fd.frame_end}"
        engine_short = fd.render_engine.replace("BLENDER_", "").replace("_NEXT", "+")

        # Format scene display and tooltip
        scene_display = self._format_scene_display(fd.scenes, fd.selected_scene)
        scene_tooltip = self._format_scene_tooltip(fd.scenes)

        self.file_panel.update_file_row(filepath, scene_display, frames_str, engine_short, "Inspected",
                                       error=False, scene_tooltip=scene_tooltip,
                                       blender_version=fd.blender_version)

        # If this is the currently selected file, refresh the tabs
        if filepath == self.current_filepath:
            self.job_tab.populate_from_file(fd)
            self.render_tab.populate_from_file(fd)

    def _on_inspect_done(self):
        self.file_panel.inspect_selected_btn.setEnabled(True)
        self.file_panel.inspect_all_btn.setEnabled(True)
        self.file_panel.inspect_all_btn.setText("Inspect All")
        # Update Inspect Selected button text with current checked count
        self.file_panel._update_inspect_button_text()
        # Update cleanup blend dropdown after inspection
        self.update_cleanup_blend_list()

    # --- Submit ---

    def _submit_selected(self):
        # Save current file overrides
        if self.current_filepath and self.current_filepath in self.file_data:
            self.render_tab.apply_to_file_data(self.file_data[self.current_filepath])
            self.file_data[self.current_filepath].job_name = self.job_tab.job_name_edit.text()

        blender = self._get_blender_path()
        if not blender:
            QMessageBox.warning(self, "Blender Path", "Set the Blender executable path in App Settings first.")
            return

        checked = self.file_panel.get_checked_filepaths()
        if not checked:
            QMessageBox.information(self, "No Files", "No files are selected for submission.")
            return

        # Validate layer selection
        validation_errors = []
        for filepath in checked:
            if filepath in self.file_data:
                fd = self.file_data[filepath]
                # Skip validation if "All Scenes" is selected (renders all layers automatically)
                if fd.selected_scene != "⚡ All Scenes":
                    # Check if at least one layer is selected
                    if not fd.selected_layers or len(fd.selected_layers) == 0:
                        filename = os.path.basename(filepath)
                        validation_errors.append(f"{filename}: No view layers selected")

        if validation_errors:
            error_msg = "Cannot submit - please select at least one view layer for each file:\n\n"
            error_msg += "\n".join(validation_errors)
            QMessageBox.warning(self, "Layer Selection Required", error_msg)
            return

        # Build job list
        job_settings = self.job_tab.get_settings()
        job_settings["blender_path"] = blender

        jobs = []
        for filepath in checked:
            if filepath not in self.file_data:
                self.file_data[filepath] = BlendFileData(
                    filepath=filepath,
                    job_name=os.path.basename(filepath).replace(".blend", ""),
                )
            fd = self.file_data[filepath]
            # If no job name override, use the file-based one
            if not fd.job_name:
                fd.job_name = os.path.basename(filepath).replace(".blend", "")
            jobs.append((filepath, fd, job_settings))

        # Show submission summary
        self.submission_panel.log_message(f"═══ Submitting {len(jobs)} job(s) ═══")
        for filepath in checked:
            if filepath in self.file_data:
                fd = self.file_data[filepath]
                filename = os.path.basename(filepath)
                scene = fd.selected_scene if fd.selected_scene else "(default)"
                layers_text = "all" if fd.selected_scene == "⚡ All Scenes" else str(len(fd.selected_layers)) if fd.selected_layers else "1"
                self.submission_panel.log_message(f"  • {filename} - Scene: {scene}, Layers: {layers_text}")

        self.submission_panel.progress.setVisible(True)
        self.submission_panel.progress.setMaximum(len(jobs))
        self.submission_panel.progress.setValue(0)
        self.submission_panel.submit_btn.setEnabled(False)
        self._submitted_count = 0

        self.submit_thread = SubmitThread(jobs)
        self.submit_thread.job_submitted.connect(self._on_job_submitted)
        self.submit_thread.all_done.connect(self._on_submit_done)
        self.submit_thread.start()

    def _on_job_submitted(self, filepath: str, success: bool, message: str):
        filename = os.path.basename(filepath)
        self._submitted_count += 1
        self.submission_panel.progress.setValue(self._submitted_count)

        if success:
            # Show detailed submission info
            if filepath in self.file_data:
                fd = self.file_data[filepath]
                scene_info = fd.selected_scene if fd.selected_scene else "(default)"

                # Layer info
                if fd.selected_scene == "⚡ All Scenes":
                    layer_info = "all scenes & layers"
                elif fd.selected_layers:
                    layer_count = len(fd.selected_layers)
                    if layer_count == 1:
                        layer_info = fd.selected_layers[0]
                    else:
                        layer_info = f"{layer_count} layers"
                else:
                    layer_info = "(active layer)"

                # Mode info
                mode = "parallel" if fd.render_layers_parallel else "sequential"

                # Frame info
                frame_info = f"{fd.frame_start}-{fd.frame_end}"

                self.submission_panel.log_message(
                    f"✓ {filename} - {message}\n"
                    f"  Scene: {scene_info} | Layers: {layer_info} | Mode: {mode} | Frames: {frame_info}"
                )
            else:
                self.submission_panel.log_message(f"✓ {filename} - {message}")
        else:
            self.submission_panel.log_message(f"✗ {filename} - FAILED: {message}", error=True)

    def _on_submit_done(self):
        self.submission_panel.submit_btn.setEnabled(True)
        self.submission_panel.progress.setVisible(False)
        self.submission_panel.log_message("═══ All submissions complete ═══\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _apply_dark_palette(app: QApplication):
    """Apply a dark Fusion palette matching common DCC/farm tool aesthetics."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(45, 45, 48))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(45, 45, 48))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.Text, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.Button, QColor(55, 55, 58))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Link, QColor(85, 170, 255))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 112, 210))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(120, 120, 120))
    # Disabled state
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(100, 100, 100))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(100, 100, 100))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(100, 100, 100))
    app.setPalette(palette)
    app.setStyleSheet(
        "QToolTip { color: #d4d4d4; background-color: #1e1e1e; border: 1px solid #555; }"
    )


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    _apply_dark_palette(app)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
