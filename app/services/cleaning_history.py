"""
Cleaning history — tracks every clean applied to a dataset so users can
revert. Stored as a JSON sidecar file next to the dataset.

Layout on disk:
  <dataset_path>                       # current file
  <dataset_path>.bak.<entry_id>        # snapshot BEFORE each clean
  <dataset_path>.history.json          # list of entries
"""

import json
import os
import shutil
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional


MAX_BACKUPS = 5  # keep the N most recent backups on disk


def _history_path(file_path: str) -> str:
    return file_path + ".history.json"


def _backup_path(file_path: str, entry_id: str) -> str:
    return f"{file_path}.bak.{entry_id}"


def load_history(file_path: str) -> List[Dict[str, Any]]:
    hp = _history_path(file_path)
    if not os.path.exists(hp):
        return []
    try:
        with open(hp, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("entries", [])
    except Exception:
        return []


def save_history(file_path: str, entries: List[Dict[str, Any]]) -> None:
    hp = _history_path(file_path)
    try:
        with open(hp, "w", encoding="utf-8") as f:
            json.dump({"entries": entries}, f, indent=2)
    except Exception as e:
        print(f"[history] failed to save {hp}: {e}")


def _cleanup_old_backups(file_path: str, entries: List[Dict[str, Any]]) -> None:
    """Delete on-disk backups beyond the most recent MAX_BACKUPS."""
    # Keep only the last MAX_BACKUPS entries that still have backup files
    with_backup = [e for e in entries if e.get("backup_path") and e.get("can_revert")]
    to_delete = with_backup[:-MAX_BACKUPS] if len(with_backup) > MAX_BACKUPS else []
    for e in to_delete:
        bp = e.get("backup_path")
        if bp and os.path.exists(bp):
            try:
                os.remove(bp)
            except Exception:
                pass
        e["can_revert"] = False


def record_clean(
    file_path: str,
    steps: List[Dict[str, Any]],
    rows_before: int,
    rows_after: int,
    cols_before: int,
    cols_after: int,
    health_before: Optional[int],
    health_after: Optional[int],
    replaced: bool,
) -> Dict[str, Any]:
    """
    Called right BEFORE writing the cleaned file.
    - Copies the current file to a timestamped backup
    - Records the entry in the history JSON
    """
    entry_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    backup_path = _backup_path(file_path, entry_id)

    # Snapshot the current file
    try:
        if os.path.exists(file_path):
            shutil.copy2(file_path, backup_path)
    except Exception as e:
        print(f"[history] backup failed: {e}")

    entry = {
        "id": entry_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "steps": steps,
        "step_count": len(steps),
        "rows_before": rows_before,
        "rows_after": rows_after,
        "cols_before": cols_before,
        "cols_after": cols_after,
        "health_before": health_before,
        "health_after": health_after,
        "replaced": replaced,
        "backup_path": backup_path if os.path.exists(backup_path) else None,
        "can_revert": os.path.exists(backup_path),
    }

    entries = load_history(file_path)
    entries.append(entry)
    _cleanup_old_backups(file_path, entries)
    save_history(file_path, entries)
    return entry


def revert_to_entry(file_path: str, entry_id: str) -> Dict[str, Any]:
    """Restore a specific backup. Returns {ok, message}."""
    entries = load_history(file_path)
    target = next((e for e in entries if e["id"] == entry_id), None)
    if not target:
        return {"ok": False, "message": f"Entry {entry_id} not found"}

    bp = target.get("backup_path")
    if not bp or not os.path.exists(bp):
        return {"ok": False, "message": "Backup file missing on disk"}

    # Backup the CURRENT state first so the revert itself can be undone
    revert_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    pre_revert_backup = _backup_path(file_path, f"pre_revert_{revert_id}")
    try:
        shutil.copy2(file_path, pre_revert_backup)
    except Exception as e:
        return {"ok": False, "message": f"Could not snapshot current state: {e}"}

    # Restore the target
    try:
        shutil.copy2(bp, file_path)
    except Exception as e:
        return {"ok": False, "message": f"Restore failed: {e}"}

    # Log the revert itself as a new entry
    revert_entry = {
        "id": f"revert_{revert_id}",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "steps": [{"action": "revert", "to": entry_id}],
        "step_count": 1,
        "rows_before": target.get("rows_after", 0),
        "rows_after": target.get("rows_before", 0),
        "cols_before": target.get("cols_after", 0),
        "cols_after": target.get("cols_before", 0),
        "health_before": target.get("health_after"),
        "health_after": target.get("health_before"),
        "replaced": True,
        "backup_path": pre_revert_backup,
        "can_revert": True,
        "is_revert": True,
    }
    entries.append(revert_entry)
    _cleanup_old_backups(file_path, entries)
    save_history(file_path, entries)

    return {"ok": True, "message": f"Reverted to {entry_id}", "revert_id": revert_entry["id"]}


def clear_history(file_path: str) -> int:
    """Delete all history + backups for a dataset. Returns count removed."""
    entries = load_history(file_path)
    for e in entries:
        bp = e.get("backup_path")
        if bp and os.path.exists(bp):
            try:
                os.remove(bp)
            except Exception:
                pass
    hp = _history_path(file_path)
    if os.path.exists(hp):
        try:
            os.remove(hp)
        except Exception:
            pass
    return len(entries)