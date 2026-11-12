#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
@文件    :video_convert.py
@说明    :
@时间    :2026/11/12 15:16:33
@作者    :LevinKai
@版本    :1.0
'''
from __future__ import annotations

"""
macOS 视频转换工具

视频编码：
    hevc_videotoolbox

音频编码：
    copy（不重新编码）

质量参数：
    -q:v 65

源视频处理：
    1. 移入 macOS 废纸篓（默认）
    2. 永久删除
    3. 保留
"""


import shutil
import subprocess
import sys
from pathlib import Path


# ============================================================
# 基础配置
# ============================================================

DEFAULT_QUALITY = 65
DEFAULT_SOURCE_ACTION = "1"

SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".mkv",
    ".avi",
    ".ts",
    ".mts",
    ".m2ts",
    ".wmv",
    ".flv",
    ".webm",
}


# ============================================================
# 文件处理
# ============================================================

def format_size(size: int) -> str:
    """格式化文件大小。"""

    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)

    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"

        value /= 1024

    return f"{value:.2f} TB"


def move_to_trash(path: Path) -> None:
    """使用 macOS 原生接口将文件移入废纸篓。"""

    from Foundation import NSFileManager, NSURL  # type: ignore

    url = NSURL.fileURLWithPath_(str(path))

    success, resulting_url, error = (
        NSFileManager.defaultManager()
        .trashItemAtURL_resultingItemURL_error_(
            url,
            None,
            None,
        )
    )

    if not success:
        message = str(error) if error else "未知错误"
        raise RuntimeError(f"移入废纸篓失败：{message}")


def delete_source(path: Path, action: str) -> str:
    """根据用户选择处理源视频。"""

    if action == "1":
        move_to_trash(path)
        return "源视频已移入废纸篓"

    if action == "2":
        path.unlink()
        return "源视频已永久删除"

    return "保留源视频"


# ============================================================
# 用户输入
# ============================================================

def ask_input_video() -> Path:
    """输入源视频路径。"""

    while True:
        value = input("\n请输入输入视频路径：").strip().strip('"')

        if not value:
            print("输入路径不能为空。")
            continue

        path = Path(value).expanduser()

        if not path.is_file():
            print(f"文件不存在：{path}")
            continue

        if path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            print(f"暂不支持此视频扩展名：{path.suffix}")
            continue

        return path.resolve()


def ask_output_video(source: Path) -> Path:
    """输入输出视频路径。"""

    default_path = source.with_name(
        f"{source.stem}_output{source.suffix}"
    )

    while True:
        value = input(
            f"\n请输入输出视频路径 [{default_path}]: "
        ).strip().strip('"')

        output = (
            Path(value).expanduser()
            if value
            else default_path
        )

        output = output.resolve()

        if output == source:
            print("输出路径不能与输入视频相同。")
            continue

        if output.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            print("输出文件扩展名不受支持。")
            continue

        return output


def ask_quality() -> int:
    """输入视频质量等级。"""

    print("\n视频质量等级：")
    print("  建议范围：50 ~ 70")
    print("  数值越大，通常质量越高，文件也可能越大。")
    print(f"  默认值：{DEFAULT_QUALITY}")

    while True:
        value = input(f"请输入质量等级 [{DEFAULT_QUALITY}]: ").strip()

        if not value:
            return DEFAULT_QUALITY

        try:
            quality = int(value)

            if 1 <= quality <= 100:
                return quality

            print("请输入 1 ~ 100 之间的整数。")

        except ValueError:
            print("请输入有效整数。")


def ask_source_action() -> str:
    """选择源视频处理方式。"""

    print("\n转换完成后如何处理源视频？")
    print("  1. 删除至 macOS 废纸篓（默认）")
    print("  2. 永久删除")
    print("  3. 保留源视频")

    while True:
        value = input("请选择 [1]: ").strip()

        if not value:
            return DEFAULT_SOURCE_ACTION

        if value in {"1", "2", "3"}:
            return value

        print("输入无效，请选择 1、2 或 3。")


# ============================================================
# 视频转换
# ============================================================

def convert_video(
    source: Path,
    destination: Path,
    quality: int,
) -> None:
    """调用 FFmpeg 使用 VideoToolbox 转换视频。"""

    ffmpeg = shutil.which("ffmpeg")

    if not ffmpeg:
        raise RuntimeError(
            "找不到 ffmpeg，请先安装：\n"
            "brew install ffmpeg"
        )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    command = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i", str(source),

        "-map", "0:v:0",
        "-map", "0:a?",

        "-c:v", "hevc_videotoolbox",
        "-q:v", str(quality),

        "-c:a", "copy",

        "-map_metadata", "0",
        "-movflags", "+faststart",

        str(destination),
    ]

    print("\n执行 FFmpeg：")
    print(subprocess.list2cmdline(command))
    print("\n开始转换，请稍候...\n")

    result = subprocess.run(
        command,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg 转换失败，返回码：{result.returncode}"
        )

    if not destination.is_file():
        raise RuntimeError("转换结束，但没有生成输出文件。")

    if destination.stat().st_size <= 0:
        raise RuntimeError("输出视频文件大小为 0。")

def handle_conversion_result(
    source: Path,
    destination: Path,
    source_action: str,
) -> None:
    """根据转换前后的文件大小决定后续处理方式。"""

    source_size = source.stat().st_size
    output_size = destination.stat().st_size

    saved_size = source_size - output_size
    saved_ratio = saved_size / source_size * 100

    print("\n" + "=" * 64)
    print("视频转换结果")
    print("=" * 64)

    print(f"源视频大小：{format_size(source_size)}")
    print(f"输出大小：  {format_size(output_size)}")

    # 情况一：输出文件不小于源文件，判定转换失败。
    if output_size >= source_size:
        print("\n[转换失败] 输出视频体积不小于源视频。")
        print(f"体积增加：{format_size(output_size - source_size)}")

        move_to_trash(destination)
        print("转换后的视频已移入废纸篓。")
        print("原视频保留。")
        return

    print(f"体积减少：{format_size(saved_size)}")
    print(f"压缩比例：{saved_ratio:.2f}%")

    # 情况二：体积减少不足 5%，由用户决定保留哪个文件。
    if saved_ratio < 5:
        print("\n[警告] 视频体积减少不足 5%，压缩收益较低。")
        print("请选择需要保留的视频：")
        print("  1. 保留原视频，转换后的视频移入废纸篓")
        print("  2. 保留转换后的视频，原视频移入废纸篓")

        while True:
            choice = input("请选择 [1/2]: ").strip()

            if choice == "1":
                move_to_trash(destination)
                print("转换后的视频已移入废纸篓。")
                print("原视频已保留。")
                return

            if choice == "2":
                try:
                    delete_source(source, "1")
                    print("原视频已移入废纸篓。")
                    print("转换后的视频已保留。")
                except Exception as exc:
                    print(f"原视频移入废纸篓失败：{exc}")
                    print("两份视频均保留，请手动处理。")
                return

            print("输入无效，请输入 1 或 2。")

    # 情况三：体积减少达到 5%，按照原有策略处理源视频。
    print("\n[转换成功] 视频体积减少达到 5%。")

    if source_action != "3":
        try:
            message = delete_source(source, source_action)
            print(message)
        except Exception as exc:
            print(f"源视频处理失败：{exc}")
            print("转换后的视频已经生成，请检查源文件。")
    else:
        print("源视频已保留。")

# ============================================================
# 主程序
# ============================================================

def main() -> None:
    if sys.platform != "darwin":
        print("此脚本专为 macOS 设计。")
        return

    if not shutil.which("ffmpeg"):
        print("未找到 FFmpeg，请先执行：brew install ffmpeg")
        return

    while True:
        print("\n" + "=" * 64)
        print("macOS HEVC 视频转换工具")
        print("=" * 64)

        try:
            source = ask_input_video()
            destination = ask_output_video(source)
            quality = ask_quality()
            source_action = ask_source_action()

            print("\n" + "=" * 64)
            print("转换参数")
            print("=" * 64)

            print(f"输入视频：{source}")
            print(f"输出视频：{destination}")
            print(f"视频质量：{quality}")
            print("视频编码：HEVC VideoToolbox")
            print("音频处理：直接复制")
            print(
                "源视频处理："
                + {
                    "1": "移入废纸篓",
                    "2": "永久删除",
                    "3": "保留",
                }[source_action]
            )

            print(f"输入文件大小：{format_size(source.stat().st_size)}")

            if destination.exists():
                confirm = input(
                    "\n输出文件已经存在，是否覆盖？[y/N]: "
                ).strip().lower()

                if confirm not in {"y", "yes"}:
                    print("已取消本次转换。")
                    raise InterruptedError

            confirm = input(
                "\n确认开始转换？[Y/n]: "
            ).strip().lower()

            if confirm not in {"", "y", "yes"}:
                print("已取消本次转换。")
                raise InterruptedError

            convert_video(
                source=source,
                destination=destination,
                quality=quality,
            )

            handle_conversion_result(
                source=source,
                destination=destination,
                source_action=source_action,
            )

        except InterruptedError:
            pass

        except KeyboardInterrupt:
            print("\n\n用户中断操作。")

        except Exception as exc:
            print(f"\n转换失败：{exc}")
            print("源视频未被主动删除。")

            # 转换失败时清理可能不完整的输出文件。
            if "destination" in locals() and destination.exists():
                try:
                    destination.unlink()
                    print("已清理失败的输出文件。")
                except OSError:
                    print("无法清理输出文件，请手动检查。")

        print("\n" + "-" * 64)
        choice = input("按 R 重新转换，按其他任意键退出：").strip().lower()

        if choice != "r":
            print("程序结束。")
            break


if __name__ == "__main__":
    main()