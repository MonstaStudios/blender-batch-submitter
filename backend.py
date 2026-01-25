import threading
import tkinter as tk
from tkinter import filedialog

import os
import sys
import subprocess
import tempfile
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
DEFAULT_SCRIPT_NAME = "cgru_submitDEFAULT.py"


class Backend:
    """
    Main backend class for CGRU batch submission operations.
    Handles Blender detection, directory scanning, batch job submission, and logging.
    """
    
    def __init__(self):
        # Ensure logs directory exists
        os.makedirs(LOG_DIR, exist_ok=True)
        self.logs: List[Dict[str, Any]] = self._load_persistent_logs()
        self.status: str = "idle"
        self.cancel_flag = threading.Event()
        self.blender_versions = self.find_blender_installations()
        self.default_blender = self.get_default_blender()

    def find_blender_installations(self) -> List[Dict[str, Any]]:
        """
        Find all Blender installations in common locations.
        Only include folders with numeric names (e.g., 3.3, 4.1, 4.2, 5.0).
        Returns a list of dicts with 'version' and 'path'.
        """
        import re
        search_dirs = [
            Path("D:/App/blender"),
            Path("C:/blender"),
            Path("D:/blender")
        ]
        versions = []
        for base in search_dirs:
            if base.exists():
                for child in base.iterdir():
                    if child.is_dir():
                        # Only accept folders with version-like names (e.g., 3.3, 4.2, 5.0)
                        if re.match(r"^\d+(\.\d+)?$", child.name):
                            blender_exe = child / "blender.exe"
                            if blender_exe.exists():
                                versions.append({
                                    "version": child.name,
                                    "path": str(blender_exe)
                                })
                # Also check for direct blender.exe in base (rare, fallback)
                blender_exe = base / "blender.exe"
                if blender_exe.exists():
                    versions.append({
                        "version": base.name,
                        "path": str(blender_exe)
                    })
        # Sort by version descending (as float)
        def version_key(v):
            try:
                return float(v["version"])
            except Exception:
                return -1.0
        versions = [v for v in versions if re.match(r"^\d+(\.\d+)?$", v["version"])]
        versions.sort(key=version_key, reverse=True)
        return versions

    def find_blender_installations_api(self) -> list:
        """
        Return list of Blender installations for frontend dropdown.
        """
        installs = []
        for v in self.find_blender_installations():
            installs.append({
                "version": v["version"],
                "path": v["path"],
                "full_path": v["path"]
            })
        return installs

    def get_default_blender(self) -> Optional[str]:
        """
        Return path to newest Blender installation, or None if not found.
        """
        if self.blender_versions:
            return self.blender_versions[0]["path"]
        # Fallback: try C:/blender/4.2/blender.exe
        fallback = Path("C:/blender/4.2/blender.exe")
        if fallback.exists():
            return str(fallback)
        return None

    def scan_files(self, target_dir: str, exclude_patterns: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Scan directory for .blend files, excluding patterns. Returns dict for frontend.
        Handles Windows network/UNC paths and logs errors.
        """
        import traceback
        if not target_dir or not os.path.exists(target_dir):
            error_msg = f"Directory does not exist or is not accessible: {target_dir}"
            self.add_log("error", error_msg)
            return {"success": False, "files": [], "count": 0, "error": error_msg}
        if exclude_patterns is None:
            exclude_patterns = [".blend1", ".blend2", ".txt", ".py", ".json", ".md", ".2025"]
        result = {"success": True, "files": [], "count": 0, "error": None}
        try:
            for root, _, files in os.walk(target_dir):
                for fname in files:
                    if fname.lower().endswith(".blend") and not any(fname.lower().endswith(pat) for pat in exclude_patterns):
                        fpath = os.path.join(root, fname)
                        try:
                            stat = os.stat(fpath)
                            result["files"].append({
                                "name": fname,
                                "path": fpath,
                                "size": stat.st_size
                            })
                        except Exception as file_exc:
                            self.add_log("warning", f"Could not stat file: {fpath} ({file_exc})")
                            result["files"].append({
                                "name": fname,
                                "path": fpath,
                                "size": 0
                            })
            result["count"] = len(result["files"])
        except Exception as e:
            tb = traceback.format_exc()
            error_msg = f"Scan failed: {e}\n{tb}"
            self.add_log("error", error_msg)
            result["success"] = False
            result["error"] = error_msg
        if not result["success"] and not result["error"]:
            result["error"] = "Unknown error during scan."
        return result

    def get_default_script_path(self) -> Optional[str]:
        """
        Get path to default submission script (scripts/cgru_submitDEFAULT.py)
        """
        # Try scripts directory first
        script_path = os.path.join(SCRIPTS_DIR, DEFAULT_SCRIPT_NAME)
        if os.path.exists(script_path):
            return script_path
        
        # Fallback to docs directory
        docs_script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docs', DEFAULT_SCRIPT_NAME)
        if os.path.exists(docs_script_path):
            return docs_script_path
            
        return None

    def get_default_script_content(self) -> Dict[str, Any]:
        """
        Always use packaged script as authoritative source
        """
        script_path = self.get_default_script_path()
        if script_path and os.path.exists(script_path):
            try:
                with open(script_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                return {
                    "success": True,
                    "content": content,
                    "source": "packaged",
                    "path": script_path,
                    "line_count": len(content.splitlines())
                }
            except Exception as e:
                self.add_log("error", f"Failed to read default script: {e}")
                return {
                    "success": False,
                    "error": f"Failed to read script: {e}"
                }
        return {
            "success": False,
            "error": "Default script not found. Please check scripts/cgru_submitDEFAULT.py or docs/cgru_submitDEFAULT.py"
        }

    def submit_jobs(self, config: dict) -> dict:
        """
        Accepts config from frontend, runs batch submission, returns status.
        """
        self.status = "submitting"
        self.cancel_flag.clear()
        results = []
        error = None
        files = config.get('files', [])
        blender_path = config.get('blenderPath', self.default_blender)
        use_custom_script = config.get('useCustomScript', False)
        cgru_settings = config.get('cgruSettings', {})
        custom_code = config.get('customCode', '')
        script_content = None
        
        if use_custom_script and custom_code:
            script_content = custom_code
        else:
            script_result = self.get_default_script_content()
            if script_result.get('success'):
                script_content = script_result.get('content', '')
            else:
                error = script_result.get('error', 'Failed to get default script')
                self.add_log("error", error)
                return {"success": False, "results": [], "error": error}
        
        if not script_content:
            error = "No script content available for submission"
            self.add_log("error", error)
            return {"success": False, "results": [], "error": error}
            
        for fileinfo in files:
            if self.cancel_flag.is_set():
                break
            blendfile = fileinfo['path']
            try:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as temp_script:
                    temp_script.write(script_content)
                    temp_script_path = temp_script.name
                if not blender_path or not os.path.exists(blender_path):
                    raise FileNotFoundError(f"Blender executable not found: {blender_path}")
                cmd = [blender_path, '-b', blendfile, '-y', '-P', temp_script_path]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
                results.append({
                    'file': blendfile,
                    'returncode': proc.returncode,
                    'stdout': proc.stdout.decode(errors='ignore'),
                    'stderr': proc.stderr.decode(errors='ignore')
                })
                os.remove(temp_script_path)
            except Exception as e:
                error = str(e)
                results.append({
                    'file': blendfile,
                    'error': error
                })
        self.status = "idle"
        self._persist_logs()
        return {"success": error is None, "results": results, "error": error}

    def cancel_submission(self) -> dict:
        self.cancel_flag.set()
        self.status = "cancelled"
        self.add_log("warning", "Submission cancelled by user.")
        self._persist_logs()
        return {"success": True, "message": "Submission cancelled."}

    def get_status(self) -> dict:
        # For progress bar and logs
        # Dummy progress for now; real implementation would track running jobs
        return {
            "logs": [{"type": log["type"], "timestamp": log["timestamp"], "msg": log["message"]} for log in self.logs],
            "total": 0,
            "progress": 0,
            "current_file": "",
            "is_running": self.status == "submitting"
        }

    def save_logs_to_file(self) -> dict:
        from datetime import datetime
        log_path = os.path.join(LOG_DIR, f"cgru_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                for entry in self.logs:
                    f.write(f"[{entry['timestamp']}] {entry['type'].upper()}: {entry['message']}\n")
            return {"success": True, "file_path": log_path, "error": None}
        except Exception as e:
            return {"success": False, "file_path": None, "error": str(e)}

    def browse_folder(self) -> dict:
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            folder = filedialog.askdirectory()
            root.destroy()
            if folder:
                return {"success": True, "path": folder}
            else:
                return {"success": False, "path": ""}
        except Exception as e:
            return {"success": False, "path": "", "error": str(e)}

    def add_log(self, log_type: str, message: str) -> None:
        """
        Add timestamped log entry with persistence and script source tracking.
        """
        script_source = 'packaged' if self.get_default_script_path() else 'generated'
        self.logs.append({
            "type": log_type,
            "message": message,
            "timestamp": datetime.now().strftime('%H:%M:%S'),
            "script_source": script_source
        })
        # Keep only last 200 logs
        self.logs = self.logs[-200:]
        self._persist_logs()

    def _persist_logs(self):
        """
        Persist logs to logs/temp_logs.txt for crash recovery.
        """
        try:
            with open(os.path.join(LOG_DIR, "temp_logs.txt"), 'w', encoding='utf-8') as f:
                for entry in self.logs:
                    f.write(f"[{entry['timestamp']}] {entry['type'].upper()}: {entry['message']}\n")
        except Exception:
            pass

    def _load_persistent_logs(self) -> List[Dict[str, Any]]:
        """
        Load logs from logs/temp_logs.txt if available.
        """
        log_path = os.path.join(LOG_DIR, "temp_logs.txt")
        logs = []
        if os.path.exists(log_path):
            try:
                with open(log_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        # Parse log line: [timestamp] TYPE: message
                        if line.startswith("[") and ": " in line:
                            ts_end = line.find("]")
                            type_start = ts_end + 2
                            type_end = line.find(":", type_start)
                            if ts_end > 0 and type_end > type_start:
                                logs.append({
                                    "timestamp": line[1:ts_end],
                                    "type": line[type_start:type_end].strip().lower(),
                                    "message": line[type_end+2:].strip()
                                })
            except Exception:
                pass
        return logs[-200:]