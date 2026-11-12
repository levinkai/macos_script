#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
@文件    :unmount_disk.py
@说明    :
@时间    :2026/11/12 15:18:19
@作者    :LevinKai
@版本    :1.0
'''

from __future__ import annotations

"""
macOS Force Unmount / Eject — single-authorization helper, improved to avoid Automation prompts.

Key changes:
- Do NOT run AppleScript that controls Finder/Terminal unless absolutely necessary.
  Only use AppleScript once to run the privileged helper (with administrator privileges).
- Avoid refresh_finder() when there are no targets (prevents Finder Automation dialogs).
- Cleanup uses direct process termination (pgrep + os.kill) instead of AppleScript quit,
  so it doesn't trigger System Preferences unlock dialogs for Automation.
- Helper logs to /tmp/force_unmount_helper_<ts>.log; main logs to /tmp/force_unmount_admin.log.
"""



import os
import plistlib
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DISKUTIL = "/usr/sbin/diskutil"
MOUNT = "/sbin/mount"
UMOUNT = "/usr/sbin/umount"
LSOF = "/usr/sbin/lsof"
OSASCRIPT = "/usr/bin/osascript"

NETWORK_FS_TYPES = {"smbfs", "nfs", "afpfs", "webdav", "webdavfs"}
SYSTEM_MOUNT_PREFIXES = ("/System/", "/private/")

SIGTERM_WAIT = 2.0
SIGKILL_WAIT = 1.0
STATE_SETTLE_WAIT = 0.8

ADMIN_LOG = "/tmp/force_unmount_admin.log"

_PRIV_HELPER_RUN = False
# ---------------------------------------------------------------------------
# Script runtime information
# ---------------------------------------------------------------------------

START_TIME = time.time()
START_MONOTONIC = time.monotonic()

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ProcessInfo:
    pid: int
    command: str = ""
    user: str = ""

@dataclass(slots=True)
class Volume:
    device: str
    mount_point: str
    filesystem: str
    volume_type: str
    whole_disk: str = ""
    disk_info: dict = field(default_factory=dict)

    @property
    def is_network(self) -> bool:
        return self.volume_type == "network"

    @property
    def is_external(self) -> bool:
        return self.volume_type == "external"

@dataclass(slots=True)
class PhysicalDisk:
    device: str
    volumes: list[Volume] = field(default_factory=list)

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def log_admin(msg: str) -> None:
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(ADMIN_LOG, "a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass

def run_command(args: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(args=args, returncode=-1, stdout="", stderr=str(exc))

# ---------------------------------------------------------------------------
# Mount / disk info
# ---------------------------------------------------------------------------

def get_mounts() -> list[tuple[str, str, str]]:
    result = run_command([MOUNT])
    if result.returncode != 0:
        return []
    mounts: list[tuple[str, str, str]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if " on " not in line:
            continue
        try:
            device, rest = line.split(" on ", 1)
            mount_point, options = rest.split(" (", 1)
        except ValueError:
            continue
        if not mount_point.startswith("/"):
            continue
        options = options.rstrip(")")
        filesystem = options.split(",", 1)[0].strip()
        mounts.append((device.strip(), mount_point.strip(), filesystem))
    return mounts

def is_mounted(mount_point: str) -> bool:
    for _, current_mount, _ in get_mounts():
        if current_mount == mount_point:
            return True
    return False

def wait_until_unmounted(mount_points: list[str], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        remaining = [mp for mp in mount_points if is_mounted(mp)]
        if not remaining:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)

def get_disk_info(target: str) -> dict:
    result = run_command([DISKUTIL, "info", "-plist", target], timeout=5)
    if result.returncode != 0:
        return {}
    try:
        return plistlib.loads(result.stdout.encode())
    except Exception:
        return {}

def is_system_mount(mount_point: str) -> bool:
    if mount_point == "/":
        return True
    if mount_point.startswith(SYSTEM_MOUNT_PREFIXES):
        return True
    if mount_point.startswith("/System/Volumes/"):
        return True
    return False

def get_whole_disk_from_device(device: str) -> str:
    match = re.match(r"^(.*?/)?(disk\d+)(?:s\d+)?$", device)
    if not match:
        return device
    prefix = "/dev/" if device.startswith("/dev/") else ""
    return f"{prefix}{match.group(2)}"

def get_whole_disk_from_info(info: dict, device: str) -> str:
    for key in ("ParentWholeDisk", "WholeDisk"):
        value = info.get(key)
        if isinstance(value, str):
            if value.startswith("/dev/"):
                return value
            if value.startswith("disk"):
                return f"/dev/{value}"
    return get_whole_disk_from_device(device)

# ---------------------------------------------------------------------------
# Volume identification & grouping
# ---------------------------------------------------------------------------

def identify_volume(device: str, mount_point: str, filesystem: str) -> Volume | None:
    if is_system_mount(mount_point):
        return None
    fs_type = filesystem.lower().strip()
    if fs_type in NETWORK_FS_TYPES:
        return Volume(device=device, mount_point=mount_point, filesystem=fs_type, volume_type="network")
    info = get_disk_info(mount_point)
    if not info:
        return None
    disk_fs = str(info.get("FilesystemType", "")).lower().strip()
    if disk_fs in NETWORK_FS_TYPES:
        return Volume(device=device, mount_point=mount_point, filesystem=disk_fs, volume_type="network", disk_info=info)
    if info.get("Internal") is False:
        whole_disk = get_whole_disk_from_info(info, device)
        return Volume(device=device, mount_point=mount_point, filesystem=(fs_type or disk_fs), volume_type="external", whole_disk=whole_disk, disk_info=info)
    if info.get("Ejectable") is True or info.get("RemovableMedia") is True:
        whole_disk = get_whole_disk_from_info(info, device)
        return Volume(device=device, mount_point=mount_point, filesystem=(fs_type or disk_fs), volume_type="external", whole_disk=whole_disk, disk_info=info)
    return None

def find_target_volumes() -> list[Volume]:
    volumes: list[Volume] = []
    for device, mount_point, filesystem in get_mounts():
        v = identify_volume(device, mount_point, filesystem)
        if v is not None:
            volumes.append(v)
    return volumes

def group_external_disks(volumes: list[Volume]) -> list[PhysicalDisk]:
    disks: dict[str, PhysicalDisk] = {}
    for v in volumes:
        if not v.is_external:
            continue
        whole = v.whole_disk or get_whole_disk_from_device(v.device)
        v.whole_disk = whole
        if whole not in disks:
            disks[whole] = PhysicalDisk(device=whole)
        disks[whole].volumes.append(v)
    return list(disks.values())

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def print_volume_list(volumes: list[Volume]) -> None:
    print()
    print("=" * 70)
    print("发现以下外部/网络磁盘")
    print("=" * 70)
    for i, v in enumerate(volumes, 1):
        type_name = "网络磁盘" if v.is_network else "外置磁盘"
        print(f"{i:2d}. [{type_name}] {v.mount_point}")
        print(f"    device={v.device}")
        if v.whole_disk:
            print(f"    whole_disk={v.whole_disk}")
        print(f"    filesystem={v.filesystem or '?'}")

# ---------------------------------------------------------------------------
# Unmount / eject / lsof / processes
# ---------------------------------------------------------------------------

def normal_unmount(volume: Volume) -> bool:
    mp = volume.mount_point
    if not is_mounted(mp):
        return True
    print(f"  正常卸载：{mp}")
    result = run_command([DISKUTIL, "unmount", mp], timeout=20)
    if not is_mounted(mp):
        print("  ✓ 卸载成功")
        return True
    err = (result.stderr.strip() or result.stdout.strip())
    if err:
        print(f"  ✗ 正常卸载失败：{err}")
    else:
        print("  ✗ 正常卸载失败")
    return False

def normal_eject(disk: PhysicalDisk) -> bool:
    mount_points = [v.mount_point for v in disk.volumes if is_mounted(v.mount_point)]
    if not mount_points:
        return True
    print(f"  正常推出物理磁盘：{disk.device}")
    result = run_command([DISKUTIL, "eject", disk.device], timeout=30)
    wait_until_unmounted(mount_points, timeout=1.5)
    remaining = [mp for mp in mount_points if is_mounted(mp)]
    if not remaining:
        print("  ✓ 物理磁盘已成功推出")
        return True
    err = (result.stderr.strip() or result.stdout.strip())
    if err:
        print(f"  ✗ 正常推出失败：{err}")
    else:
        print("  ✗ 正常推出失败")
    print("  仍然挂载：" + ", ".join(remaining))
    return False

def get_processes(mount_point: str) -> list[ProcessInfo]:
    if not is_mounted(mount_point):
        return []
    result = run_command([LSOF, "-F", "pcu", "--", mount_point], timeout=15)
    if result.returncode not in (0, 1):
        return []
    procs: dict[int, ProcessInfo] = {}
    cur_pid = None
    for line in result.stdout.splitlines():
        if not line:
            continue
        field = line[0]; val = line[1:]
        if field == "p":
            try:
                cur_pid = int(val)
            except ValueError:
                cur_pid = None
                continue
            if cur_pid <= 1:
                cur_pid = None
                continue
            procs[cur_pid] = ProcessInfo(pid=cur_pid)
        elif cur_pid is not None:
            proc = procs.get(cur_pid)
            if proc is None:
                continue
            if field == "c":
                proc.command = val
            elif field == "u":
                proc.user = val
    return list(procs.values())

def get_volume_processes(volumes: list[Volume]) -> list[ProcessInfo]:
    procs: dict[int, ProcessInfo] = {}
    for v in volumes:
        if not is_mounted(v.mount_point):
            continue
        for p in get_processes(v.mount_point):
            procs[p.pid] = p
    return list(procs.values())

def print_processes(processes: list[ProcessInfo]) -> None:
    if not processes:
        print("  未发现占用进程")
        return
    print(f"  发现 {len(processes)} 个占用进程：")
    for p in processes:
        print(f"    PID={p.pid:<7} USER={p.user or '?':<12} COMMAND={p.command or '?'}")

# ---------------------------------------------------------------------------
# GUI app helpers (non-privileged)
# ---------------------------------------------------------------------------

def is_gui_application(process: ProcessInfo) -> bool:
    cmd = process.command.strip()
    if not cmd:
        return False
    if cmd.endswith("d"):
        return False
    candidates = (Path("/Applications") / f"{cmd}.app", Path.home() / "Applications" / f"{cmd}.app")
    return any(p.exists() for p in candidates)

def try_quit_application(process: ProcessInfo) -> bool:
    if not is_gui_application(process):
        return False
    cmd = process.command.strip()
    print(f"  尝试退出应用：{cmd}")
    escaped = cmd.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "{escaped}" to quit'
    result = run_command([OSASCRIPT, "-e", script], timeout=10)
    return result.returncode == 0

def try_quit_gui_applications(processes: list[ProcessInfo]) -> None:
    count = 0
    for p in processes:
        if try_quit_application(p):
            count += 1
    if count:
        print(f"  已向 {count} 个 GUI App 发送退出请求，等待 {SIGTERM_WAIT:.1f} 秒...")
        time.sleep(SIGTERM_WAIT)

def terminate_processes(processes: list[ProcessInfo], sig: signal.Signals) -> None:
    if not processes:
        return
    print(f"  发送 {sig.name}...")
    for p in processes:
        pid = p.pid
        if pid <= 1:
            continue
        try:
            os.kill(pid, sig)
            print(f"    ✓ PID {pid} ({p.command or '?'})")
        except ProcessLookupError:
            print(f"    ✓ PID {pid} 已退出")
        except (PermissionError, OSError) as exc:
            print(f"    ✗ PID {pid}：{exc}")

# ---------------------------------------------------------------------------
# Ordinary preparation steps
# ---------------------------------------------------------------------------

def prepare_network_volume(volume: Volume) -> bool:
    print(); print("=" * 70)
    print(f"网络磁盘：{volume.mount_point}")
    print(f"文件系统：{volume.filesystem}")
    print("=" * 70)
    if not is_mounted(volume.mount_point):
        print("  ✓ 已经卸载"); return True
    if normal_unmount(volume):
        return True
    procs = get_processes(volume.mount_point)
    print_processes(procs)
    if procs:
        try_quit_gui_applications(procs)
    if not is_mounted(volume.mount_point):
        return True
    if normal_unmount(volume):
        return True
    return not is_mounted(volume.mount_point)

def prepare_external_disk(disk: PhysicalDisk) -> bool:
    print(); print("=" * 70)
    print(f"外置物理磁盘：{disk.device}")
    print("=" * 70)
    for v in disk.volumes:
        print(f"  Volume: {v.mount_point} ({v.device})")
    if normal_eject(disk):
        return True
    procs = get_volume_processes(disk.volumes)
    print_processes(procs)
    if procs:
        try_quit_gui_applications(procs)
    if not any(is_mounted(v.mount_point) for v in disk.volumes):
        return True
    if normal_eject(disk):
        return True
    procs = get_volume_processes(disk.volumes)
    if procs:
        print("  当前仍存在占用进程：")
        print_processes(procs)
        terminate_processes(procs, signal.SIGTERM)
        time.sleep(SIGTERM_WAIT)
    if not any(is_mounted(v.mount_point) for v in disk.volumes):
        return True
    if normal_eject(disk):
        return True
    procs = get_volume_processes(disk.volumes)
    if procs:
        print("  仍有占用进程，发送 SIGKILL：")
        print_processes(procs)
        terminate_processes(procs, signal.SIGKILL)
        time.sleep(SIGKILL_WAIT)
    if not any(is_mounted(v.mount_point) for v in disk.volumes):
        return True
    if normal_eject(disk):
        return True
    return any(is_mounted(v.mount_point) for v in disk.volumes)

# ---------------------------------------------------------------------------
# Filter current targets (avoid stale state)
# ---------------------------------------------------------------------------

def filter_current_targets(network_volumes: list[Volume], external_disks: list[PhysicalDisk]) -> tuple[list[Volume], list[PhysicalDisk]]:
    current_volumes = find_target_volumes()
    current_network = [v for v in current_volumes if v.is_network]
    current_external = group_external_disks(current_volumes)
    current_network = [v for v in current_network if is_mounted(v.mount_point)]
    current_external = [d for d in current_external if any(is_mounted(v.mount_point) for v in d.volumes)]
    return current_network, current_external

# ---------------------------------------------------------------------------
# Shell quoting & helper creation
# ---------------------------------------------------------------------------

def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"

def create_privileged_script(network_volumes: list[Volume], external_disks: list[PhysicalDisk], helper_log_path: str) -> str:
    lines = [
        "#!/bin/bash",
        "set +e",
        f"exec >> {shell_quote(helper_log_path)} 2>&1",
        "echo '=== privileged force eject helper started ==='",
        "date",
        "",
        "is_mounted() {",
        "  /sbin/mount | /usr/bin/grep -Fq \" on $1 (\"",
        "}",
        ""
    ]

    # network volumes
    for v in network_volumes:
        q = shell_quote(v.mount_point)
        lines += [
            f"MOUNT_POINT={q}",
            'echo "---- network target ----"',
            'echo "$MOUNT_POINT"',
            'if ! is_mounted "$MOUNT_POINT"; then',
            '  echo "Already unmounted: $MOUNT_POINT"',
            'else',
            '  PIDS=$( /usr/sbin/lsof -t -- "$MOUNT_POINT" 2>/dev/null | sort -u )',
            '  if [ -n "$PIDS" ]; then',
            '    echo "SIGTERM PIDS:"; echo "$PIDS"',
            '    while IFS= read -r pid; do',
            '      if [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" -gt 1 ]; then',
            '        /bin/kill -TERM "$pid" 2>/dev/null || true',
            '      fi',
            '    done <<< "$PIDS"',
            '  fi',
            f"  sleep {SIGTERM_WAIT}",
            '  /usr/sbin/diskutil unmount "$MOUNT_POINT" || true',
            '  if is_mounted "$MOUNT_POINT"; then',
            '    PIDS=$( /usr/sbin/lsof -t -- "$MOUNT_POINT" 2>/dev/null | sort -u )',
            '    if [ -n "$PIDS" ]; then',
            '      echo "SIGKILL PIDS:"; echo "$PIDS"',
            '      while IFS= read -r pid; do',
            '        if [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" -gt 1 ]; then',
            '          /bin/kill -KILL "$pid" 2>/dev/null || true',
            '        fi',
            '      done <<< "$PIDS"',
            '    fi',
            f"    sleep {SIGKILL_WAIT}",
            '    /usr/sbin/diskutil unmount force "$MOUNT_POINT" || true',
            '    if is_mounted "$MOUNT_POINT"; then /usr/sbin/umount -f "$MOUNT_POINT" || true; fi',
            '  fi',
            'fi',
            ''
        ]

    # external disks
    for disk in external_disks:
        dq = shell_quote(disk.device)
        lines += [
            'echo "---- external disk ----"',
            f"DISK={dq}",
            ""
        ]
        # stage 1: term
        for v in disk.volumes:
            mq = shell_quote(v.mount_point)
            lines += [
                f"MOUNT_POINT={mq}",
                'if ! is_mounted "$MOUNT_POINT"; then :; else',
                '  PIDS=$( /usr/sbin/lsof -t -- "$MOUNT_POINT" 2>/dev/null | sort -u )',
                '  if [ -n "$PIDS" ]; then',
                '    echo "SIGTERM for $MOUNT_POINT"; echo "$PIDS"',
                '    while IFS= read -r pid; do',
                '      if [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" -gt 1 ]; then',
                '        /bin/kill -TERM "$pid" 2>/dev/null || true',
                '      fi',
                '    done <<< "$PIDS"',
                '  fi',
                'fi',
            ]
        lines += [f"sleep {SIGTERM_WAIT}", '/usr/sbin/diskutil eject "$DISK" || true', ""]
        # stage 2: kill
        for v in disk.volumes:
            mq = shell_quote(v.mount_point)
            lines += [
                f"MOUNT_POINT={mq}",
                'if ! is_mounted "$MOUNT_POINT"; then :; else',
                '  PIDS=$( /usr/sbin/lsof -t -- "$MOUNT_POINT" 2>/dev/null | sort -u )',
                '  if [ -n "$PIDS" ]; then',
                '    echo "SIGKILL for $MOUNT_POINT"; echo "$PIDS"',
                '    while IFS= read -r pid; do',
                '      if [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" -gt 1 ]; then',
                '        /bin/kill -KILL "$pid" 2>/dev/null || true',
                '      fi',
                '    done <<< "$PIDS"',
                '  fi',
                'fi',
            ]
        lines += [f"sleep {SIGKILL_WAIT}", '/usr/sbin/diskutil eject "$DISK" || true', ""]
        # stage 3: force unmount volumes
        for v in disk.volumes:
            mq = shell_quote(v.mount_point)
            lines += [
                f"MOUNT_POINT={mq}",
                'if is_mounted "$MOUNT_POINT"; then',
                '  echo "Force unmount $MOUNT_POINT"',
                '  /usr/sbin/diskutil unmount force "$MOUNT_POINT" || true',
                '  if is_mounted "$MOUNT_POINT"; then /usr/sbin/umount -f "$MOUNT_POINT" || true; fi',
                'fi',
            ]
        lines += ['/usr/sbin/diskutil eject "$DISK" || true', ""]

    lines += ["echo '=== privileged helper finished ==='", "date", "exit 0", ""]
    fd, path = tempfile.mkstemp(prefix="force_unmount_priv_", suffix=".sh")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        os.chmod(path, 0o700)
        return path
    except Exception:
        try:
            os.unlink(path)
        except Exception:
            pass
        raise

# ---------------------------------------------------------------------------
# Run privileged helper once via AppleScript (single admin prompt)
# ---------------------------------------------------------------------------

def run_privileged_helper(network_volumes: list[Volume], external_disks: list[PhysicalDisk]) -> bool:
    global _PRIV_HELPER_RUN
    if _PRIV_HELPER_RUN:
        log_admin("run_privileged_helper: already run once, skipping")
        return False
    if not network_volumes and not external_disks:
        log_admin("run_privileged_helper: no targets")
        return True

    ts = int(time.time())
    helper_log = f"/tmp/force_unmount_helper_{ts}.log"
    helper_path = create_privileged_script(network_volumes, external_disks, helper_log)

    try:
        current_network, current_external = filter_current_targets(network_volumes, external_disks)
        if not current_network and not current_external:
            log_admin("run_privileged_helper: targets gone before admin stage")
            return True

        log_admin(f"Invoking privileged helper {helper_path} (helper_log={helper_log})")
        helper_escaped = helper_path.replace("'", "'\\''")
        apple_script = f"do shell script \"/bin/bash '{helper_escaped}'\" with administrator privileges"
        result = run_command([OSASCRIPT, "-e", apple_script], timeout=900)
        _PRIV_HELPER_RUN = True
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        log_admin(f"AppleScript exit={result.returncode} stdout=[{out}] stderr=[{err}]")
        return result.returncode == 0
    finally:
        try:
            os.unlink(helper_path)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Finder refresh (ONLY call when we actually changed mounts)
# ---------------------------------------------------------------------------

def refresh_finder() -> None:
    print("Refreshing Finder...")
    return

    # NOTE: calling AppleScript to control Finder can trigger Automation permission dialogs.
    # We keep this function but avoid calling it when no work was done.
    script = """
tell application "Finder"
    try
        update every disk
    end try
end tell
"""
    run_command([OSASCRIPT, "-e", script], timeout=10)

# ---------------------------------------------------------------------------
# Cleanup: do not use AppleScript to control apps; use direct process termination
# ---------------------------------------------------------------------------

def pgrep_once(pattern: str) -> list[int]:
    res = run_command(["/usr/bin/pgrep", "-f", pattern], timeout=2)
    if res.returncode != 0 or not res.stdout.strip():
        return []
    out = res.stdout.strip().splitlines()
    pids = []
    for s in out:
        try:
            pids.append(int(s.strip()))
        except Exception:
            pass
    return pids

def find_parent_chain() -> list[int]:
    chain = [os.getpid()]
    current = os.getpid()
    for _ in range(12):
        res = run_command(["/bin/ps", "-p", str(current), "-o", "ppid="], timeout=2)
        if res.returncode != 0:
            break
        ppid_str = res.stdout.strip()
        if not ppid_str:
            break
        try:
            ppid = int(ppid_str)
        except ValueError:
            break
        if ppid <= 1:
            break
        chain.append(ppid)
        current = ppid
    return chain

def get_process_details(pid: int) -> dict[str, str]:
    """获取指定进程的详细信息。"""
    result = run_command(
        [
            "/bin/ps",
            "-p",
            str(pid),
            "-o",
            "pid=,ppid=,user=,lstart=,etime=,comm=,args=",
        ],
        timeout=2,
    )

    if result.returncode != 0 or not result.stdout.strip():
        return {}

    line = result.stdout.strip()

    # lstart 本身包含空格，例如：
    # Wed Sep 17 11:27:35 2026
    #
    # 因此这里使用固定字段解析：
    # PID PPID USER [lstart 5个字段] ETIME COMMAND ARGS
    parts = line.split(None, 10)

    if len(parts) < 10:
        return {}

    return {
        "pid": parts[0],
        "ppid": parts[1],
        "user": parts[2],
        "start_time": " ".join(parts[3:8]),
        "elapsed": parts[8],
        "command": parts[9],
        "args": parts[10] if len(parts) > 10 else "",
    }

def print_process_chain() -> None:
    """打印当前 Python 进程及其父进程链。"""
    chain = find_parent_chain()

    print()
    print("=" * 70)
    print("命令行 / Python 进程层级")
    print("=" * 70)

    print(
        f"脚本启动时间："
        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(START_TIME))}"
    )

    elapsed = time.monotonic() - START_MONOTONIC
    elapsed_int = int(elapsed)

    days, remain = divmod(elapsed_int, 86400)
    hours, remain = divmod(remain, 3600)
    minutes, seconds = divmod(remain, 60)

    if days:
        runtime = f"{days}天 {hours:02d}:{minutes:02d}:{seconds:02d}"
    else:
        runtime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    print(f"脚本运行时间：{runtime}")
    print()
    print("进程关系：当前 Python → 父进程 → 更上层父进程")
    print()

    for level, pid in enumerate(chain):
        info = get_process_details(pid)

        if not info:
            print(f"{'  ' * level}└─ PID={pid}（进程已经退出）")
            continue

        prefix = "  " * level
        if level == 0:
            relation = "当前 Python"
        else:
            relation = "父进程"

        print(
            f"{prefix}└─ {relation}: "
            f"PID={info['pid']} "
            f"PPID={info['ppid']} "
            f"USER={info['user']}"
        )
        print(
            f"{prefix}   启动时间：{info['start_time']}"
        )
        print(
            f"{prefix}   已运行：{info['elapsed']}"
        )
        print(
            f"{prefix}   COMMAND：{info['command']}"
        )
        print(
            f"{prefix}   ARGS：{info['args']}"
        )
        
def should_cleanup() -> bool:
    if pgrep_once("Python Launcher"):
        return True
    parent = os.getppid()
    res = run_command(["/bin/ps", "-p", str(parent), "-o", "comm="], timeout=2)
    if res.returncode == 0 and res.stdout:
        pn = res.stdout.strip().lower()
        if "launcher" in pn or "python" in pn:
            return True
    return False

def get_process_start_timestamp(pid: int) -> float | None:
    """获取进程启动时间的 Unix 时间戳。"""
    result = run_command(
        [
            "/bin/ps",
            "-p",
            str(pid),
            "-o",
            "lstart=",
        ],
        timeout=2,
    )

    if result.returncode != 0 or not result.stdout.strip():
        return None

    start_text = result.stdout.strip()

    try:
        # 例如：
        # Wed Sep 17 11:27:35 2026
        return time.mktime(
            time.strptime(start_text, "%a %b %d %H:%M:%S %Y")
        )
    except ValueError:
        return None

def format_duration(seconds: float) -> str:
    """将秒数格式化为易读的运行时间。"""
    seconds = max(0, int(seconds))

    days, remain = divmod(seconds, 86400)
    hours, remain = divmod(remain, 3600)
    minutes, seconds = divmod(remain, 60)

    if days:
        return f"{days}天 {hours:02d}:{minutes:02d}:{seconds:02d}"

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def check_process_safety(pid: int) -> bool:
    """
    检查进程是否允许被终止。

    安全规则：
    1. 进程启动时间比脚本启动时间早 1 分钟以上 -> 跳过
    2. 进程运行时间超过 5 分钟 -> 跳过

    返回 True 表示允许终止。
    """
    info = get_process_details(pid)

    if not info:
        print(f"    ⚠ PID={pid} 无法获取进程信息，跳过")
        return False

    print(
        f"    PID={info['pid']} "
        f"PPID={info['ppid']} "
        f"USER={info['user']}"
    )
    print(f"      启动时间：{info['start_time']}")
    print(f"      已运行：{info['elapsed']}")
    print(f"      COMMAND：{info['command']}")
    print(f"      ARGS：{info['args']}")

    # 获取启动时间
    start_timestamp = get_process_start_timestamp(pid)

    if start_timestamp is None:
        print("      ⚠ 无法确定进程启动时间，出于安全考虑跳过")
        return False

    now = time.time()

    # ---------------------------------------------------------------
    # 安全规则 1：
    # 进程启动时间早于脚本启动时间 1 分钟以上
    # ---------------------------------------------------------------
    age_before_script = START_TIME - start_timestamp

    if age_before_script > 60:
        print(
            f"      ⚠ 警告：该进程比脚本早启动 "
            f"{format_duration(age_before_script)}"
        )
        print("      → 跳过，不终止该进程")
        time.sleep(3)
        return False

    # ---------------------------------------------------------------
    # 安全规则 2：
    # 当前进程运行时间超过 5 分钟
    # ---------------------------------------------------------------
    process_runtime = now - start_timestamp

    if process_runtime > 5 * 60:
        print(
            f"      ⚠ 警告：该进程已经运行 "
            f"{format_duration(process_runtime)}"
        )
        print("      → 超过 5 分钟，跳过，不终止该进程")
        time.sleep(3)
        return False

    print("      ✓ 进程满足安全终止条件")
    return True

def cleanup_windows_and_processes() -> None:
    """
    Avoid AppleScript Automation prompts by not using osascript to quit apps.
    Instead:
    - If Python Launcher is present -> kill it (SIGTERM) and kill the parent terminal process chain.
    - Otherwise attempt to close 'empty' terminals conservatively by closing terminals that have no child shell,
      but avoid killing user's unrelated terminals.
    """
    print(); print("="*70); 
    print("清理相关进程和窗口"); 
    print("="*70)
    
    # 在关闭 Python Launcher / Terminal 之前，先记录完整进程信息。
    # 因为后面进程一旦被终止，就无法再通过 ps 获取其启动时间、
    # 运行时间以及父子关系。
    print_process_chain()
    
    launcher_pids = pgrep_once("Python Launcher")
    if launcher_pids:
        for pid in launcher_pids:
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"✓ 终止 Python Launcher PID {pid}")
            except ProcessLookupError:
                pass
            except PermissionError:
                pass
        # find parent chain and kill parent terminal if present
        chain = find_parent_chain()
        # kill any parent processes in chain except PID 1 and our own
        for p in chain[1:]:
            # try to only kill obvious terminal processes (Terminal, iTerm2)
            res = run_command(["/bin/ps", "-p", str(p), "-o", "comm="], timeout=2)
            if res.returncode != 0:
                continue
            comm = (res.stdout or "").strip().lower()
            if "terminal" in comm or "iterm" in comm or "python launcher" in comm or "launcher" in comm:
                try:
                    os.kill(p, signal.SIGTERM)
                    print(f"✓ 终止父进程 PID {p} ({comm})")
                except Exception:
                    pass
        print("✓ 清理完成")
        return

    # conservative cleanup: close empty terminal windows by killing shells
    # that have no children.
    shell_pids = pgrep_once("login|zsh|bash|fish")

    print()
    print(f"发现 {len(shell_pids)} 个 Shell 进程")

    for pid in shell_pids:
        print()
        print("-" * 70)
        print(f"检查 Shell 进程 PID={pid}")

        # ---------------------------------------------------------------
        # 不允许终止当前脚本的直接父进程
        # ---------------------------------------------------------------
        if pid == os.getppid():
            info = get_process_details(pid)

            print("  ⚠ 这是当前 Python 的直接父进程")
            if info:
                print(
                    f"  PID={info['pid']} "
                    f"PPID={info['ppid']} "
                    f"USER={info['user']}"
                )
                print(f"  启动时间：{info['start_time']}")
                print(f"  已运行：{info['elapsed']}")
                print(f"  COMMAND：{info['command']}")
                print(f"  ARGS：{info['args']}")
                time.sleep(3)
            # print("  → 跳过，不终止")
            # continue

        # ---------------------------------------------------------------
        # 检查子进程
        # ---------------------------------------------------------------
        res = run_command(
            ["/bin/ps", "-o", "pid=", "--ppid", str(pid)],
            timeout=2,
        )

        if res.returncode != 0:
            print("  ⚠ 无法查询子进程，跳过")
            continue

        children = res.stdout.strip()

        if children:
            print(f"  存在子进程：{children.replace(chr(10), ', ')}")
            print("  → 不是空 Shell，跳过")
            continue

        # ---------------------------------------------------------------
        # 打印并检查进程安全性
        # ---------------------------------------------------------------
        if not check_process_safety(pid):
            continue

        # ---------------------------------------------------------------
        # 最终终止
        # ---------------------------------------------------------------
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"  ✓ 已发送 SIGTERM：PID={pid}")
        except ProcessLookupError:
            print(f"  ✓ PID={pid} 已经退出")
        except PermissionError as exc:
            print(f"  ✗ PID={pid} 权限不足：{exc}")
        except OSError as exc:
            print(f"  ✗ PID={pid} 终止失败：{exc}")
    print("✓ 清理完成")

# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("macOS Force Unmount")
    print("=" * 70)

    volumes = find_target_volumes()
    if not volumes:
        print()
        print("未发现需要推出的外部/网络磁盘。")
        # IMPORTANT: do not call refresh_finder() here to avoid Automation prompts.
        return 0

    print_volume_list(volumes)

    network_volumes = [v for v in volumes if v.is_network]
    external_disks = group_external_disks(volumes)

    # ordinary attempts
    for v in network_volumes:
        prepare_network_volume(v)
    for d in external_disks:
        prepare_external_disk(d)

    time.sleep(STATE_SETTLE_WAIT)

    remaining_network, remaining_external = filter_current_targets(network_volumes, external_disks)

    if not remaining_network and not remaining_external:
        print()
        print("=" * 70)
        print("处理完成")
        print("=" * 70)
        print("✓ 所有外部/网络磁盘均已成功推出。")
        # We did actual work -> refresh Finder to update UI. This may trigger an Automation prompt
        # on some macOS configurations; if you still see prompts, remove the next line.
        try:
            refresh_finder()
        except Exception:
            pass
        return 0

    print()
    print("=" * 70)
    print("普通权限阶段仍有磁盘未推出")
    print("=" * 70)
    if remaining_network:
        print("网络磁盘：")
        for v in remaining_network:
            print(f"  {v.mount_point}")
    if remaining_external:
        print("外置物理磁盘：")
        for d in remaining_external:
            print(f"  {d.device}")
            for v in d.volumes:
                if is_mounted(v.mount_point):
                    print(f"      {v.mount_point}")

    ok = run_privileged_helper(remaining_network, remaining_external)

    time.sleep(STATE_SETTLE_WAIT)
    final_volumes = find_target_volumes()

    # After privileged action, it's reasonable to refresh Finder (may trigger Automation prompt).
    # If you find that refreshing Finder triggers additional dialogs in your environment,
    # comment out the next try/except block.
    try:
        refresh_finder()
    except Exception:
        pass

    print()
    print("=" * 70)
    print("处理完成")
    print("=" * 70)
    if not final_volumes:
        print("✓ 所有外部/网络磁盘均已成功推出。")
        return 0

    print("✗ 以下磁盘仍然挂载：")
    for v in final_volumes:
        print(f"  [{v.volume_type}] {v.mount_point}")

    if not ok:
        print()
        print("注意：管理员阶段失败或被取消，无法强制停止某些挂载。")

    return 1

# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    exit_code = 0
    try:
        exit_code = main()
    except KeyboardInterrupt:
        print(); print("用户取消操作。"); exit_code = 130
    finally:
        try:
            elapsed = time.monotonic() - START_MONOTONIC
            print(
                f"脚本 PID：{os.getpid()}"
            )
            print(f"脚本启动时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(START_TIME))}"
            )
            print(f"脚本运行时间：{time.strftime('%H:%M:%S', time.gmtime(elapsed))}"
            )
            
            if should_cleanup():
                cleanup_windows_and_processes()
            
            elapsed = time.monotonic() - START_MONOTONIC
            print(f"脚本运行时间：{time.strftime('%H:%M:%S', time.gmtime(elapsed))}"
            )
        except Exception:
            pass
    raise SystemExit(exit_code)