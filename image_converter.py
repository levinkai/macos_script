
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
@文件    :image_converter.py
@说明    :
@时间    :2026/11/12 15:17:20
@作者    :LevinKai
@版本    :1.0
'''


from __future__ import annotations

"""
大体积图片批量转换工具

功能：
1. 输入图片目录，默认 ~/Pictures/Screenshots
2. 选择目标格式，默认 HEIC 10-bit Alpha 无损
3. 设置图片大小阈值，默认超过 20 MB 才转换
4. 设置源图处理方式：
   - 删除
   - 保留
   - 转存到指定目录

依赖：
    macOS
    Homebrew libheif
    heif-enc

安装：
    brew install libheif
"""

import shutil
import subprocess
import tempfile

from dataclasses import dataclass
from pathlib import Path


# ============================================================
# 基础配置
# ============================================================

DEFAULT_IMAGE_DIR = Path("/Users/levinkai/Pictures/Screenshots")

DEFAULT_THRESHOLD_MB = 20.0

# 按 MiB 计算：1 MB = 1024 * 1024 Bytes
MB = 1024 * 1024

# 支持的输入格式
SUPPORTED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
}


@dataclass(frozen=True)
class EncodingPreset:
    """HEIC 编码预设。"""

    name: str
    args: tuple[str, ...]


PRESETS = {
    "1": EncodingPreset(
        name="HEIC 10-bit + Alpha + 无损",
        args=("-L", "-b", "10"),
    ),
    "2": EncodingPreset(
        name="HEIC 10-bit + Alpha + 高质量 (Q95)",
        args=("-q", "95", "-b", "10"),
    ),
    "3": EncodingPreset(
        name="HEIC 10-bit + Alpha + 高质量 (Q90)",
        args=("-q", "90", "-b", "10"),
    ),
}


# ============================================================
# 输入处理
# ============================================================

def ask_image_directory() -> Path:
    """询问图片目录。"""

    while True:
        value = input(
            f"\n图片目录 [{DEFAULT_IMAGE_DIR}]: "
        ).strip()

        directory = Path(
            value or str(DEFAULT_IMAGE_DIR)
        ).expanduser()

        if directory.is_dir():
            return directory.resolve()

        print(f"目录不存在或不是有效目录：{directory}")


def ask_target_format() -> EncodingPreset:
    """选择目标编码格式。"""

    print("\n===== 目标格式 =====")
    print("1. HEIC 10-bit + Alpha + 无损（默认）")
    print("2. HEIC 10-bit + Alpha + 高质量 Q95")
    print("3. HEIC 10-bit + Alpha + 高质量 Q90")

    while True:
        choice = input("\n选择目标格式 [1]: ").strip() or "1"

        if choice in PRESETS:
            preset = PRESETS[choice]
            print(f"已选择：{preset.name}")
            return preset

        print("输入无效，请输入 1、2 或 3。")


def ask_threshold() -> float:
    """设置待转换图片的最小大小。"""

    while True:
        value = input(
            f"\n图片大小阈值 MB [{DEFAULT_THRESHOLD_MB}]: "
        ).strip()

        if not value:
            return DEFAULT_THRESHOLD_MB

        try:
            threshold = float(value)

            if threshold <= 0:
                print("大小必须大于 0。")
                continue

            return threshold

        except ValueError:
            print("请输入有效数字。")


def ask_source_action() -> tuple[str, Path | None]:
    """选择转换成功后的源图处理方式。"""

    print("\n===== 源图处理方式 =====")
    print("1. 删除源图（默认）")
    print("2. 不处理，保留源图")
    print("3. 转存源图到指定目录")

    while True:
        choice = input("\n选择处理方式 [1]: ").strip() or "1"

        if choice == "1":
            return "delete", None

        if choice == "2":
            return "keep", None

        if choice == "3":
            while True:
                value = input(
                    "\n请输入源图转存目录（必填）: "
                ).strip()

                if not value:
                    print("转存路径不能为空。")
                    continue

                directory = Path(value).expanduser().resolve()

                try:
                    directory.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                    if not directory.is_dir():
                        print("目标路径不是目录。")
                        continue

                    return "move", directory

                except OSError as exc:
                    print(f"无法创建转存目录：{exc}")

        print("输入无效，请输入 1、2 或 3。")


# ============================================================
# 文件处理
# ============================================================

def format_size(size: int) -> str:
    """格式化文件大小。"""

    return f"{size / MB:.2f} MB"


def get_output_path(source: Path) -> Path:
    """生成不覆盖已有文件的 HEIC 输出路径。"""

    output = source.with_suffix(".heic")

    if not output.exists():
        return output

    index = 1

    while True:
        output = source.with_name(
            f"{source.stem}_converted_{index}.heic"
        )

        if not output.exists():
            return output

        index += 1


def get_archive_path(source: Path, archive_dir: Path) -> Path:
    """生成不覆盖已有文件的转存路径。"""

    target = archive_dir / source.name

    if not target.exists():
        return target

    index = 1

    while True:
        target = archive_dir / (
            f"{source.stem}_{index}{source.suffix}"
        )

        if not target.exists():
            return target

        index += 1


def validate_heic(path: Path) -> bool:
    """
    验证 HEIC 文件。

    优先使用 heif-info，其次使用 macOS sips。
    最后检查 HEIF 文件头。
    """

    if not path.is_file() or path.stat().st_size < 16:
        return False

    try:
        with path.open("rb") as file:
            header = file.read(16)

        if header[4:8] != b"ftyp":
            return False

    except OSError:
        return False

    # 优先使用 libheif 自带验证工具
    heif_info = shutil.which("heif-info")

    if heif_info:
        try:
            result = subprocess.run(
                [heif_info, str(path)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            if result.returncode == 0:
                return True

        except (OSError, subprocess.TimeoutExpired):
            pass

    # 使用 macOS 系统工具验证
    sips = shutil.which("sips")

    if sips:
        try:
            result = subprocess.run(
                [sips, "-g", "format", str(path)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            if result.returncode == 0:
                return True

        except (OSError, subprocess.TimeoutExpired):
            pass

    # 编码器返回成功且文件头正确时，作为最后的检查
    return True


def remove_temp_file(path: Path) -> None:
    """清理临时文件。"""

    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def convert_image(
    source: Path,
    preset: EncodingPreset,
    encoder: str,
    source_action: str,
    archive_dir: Path | None,
) -> tuple[str, int, int]:
    """
    转换单张图片。

    返回：
        状态、源图大小、目标图大小
    """

    source_size = source.stat().st_size
    output = get_output_path(source)

    # 临时文件也使用 .heic 后缀，确保编码器识别输出格式
    fd, temp_name = tempfile.mkstemp(
        prefix=".image-converter-",
        suffix=".heic",
        dir=source.parent,
    )

    import os
    os.close(fd)

    temp_output = Path(temp_name)
    remove_temp_file(temp_output)

    try:
        command = [
            encoder,
            *preset.args,
            str(source),
            "-o",
            str(temp_output),
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )

        if result.returncode != 0:
            error = result.stderr.strip()

            print(f"  转换失败：{error[-1000:]}")

            return "failed", source_size, 0

        if not validate_heic(temp_output):
            print("  转换失败：输出文件验证未通过。")
            return "failed", source_size, 0

        target_size = temp_output.stat().st_size

        # 原子移动临时文件，避免留下不完整的目标文件
        if output.exists():
            print("  目标文件已存在，取消保存。")
            return "failed", source_size, 0

        temp_output.replace(output)

        print(f"  输出文件：{output.name}")
        print(f"  源图大小：{format_size(source_size)}")
        print(f"  目标大小：{format_size(target_size)}")

        difference = source_size - target_size

        if difference > 0:
            ratio = difference / source_size * 100

            print(
                f"  节省空间：{format_size(difference)} "
                f"({ratio:.1f}%)"
            )
        else:
            print(
                f"  文件反而增加：{format_size(-difference)}"
            )

        # ----------------------------------------------------
        # 源图处理
        # ----------------------------------------------------

        if source_action == "delete":

            # 为避免转换后体积反而增加，却删除了原图，
            # 仅当目标文件确实更小时才删除源图。
            if target_size >= source_size:
                print("  保留源图：转换后没有节省空间。")
                return "kept_larger", source_size, target_size

            try:
                move_to_trash(source)
                print("  已删除源图。")

            except OSError as exc:
                print(f"  删除源图失败：{exc}")
                return "converted", source_size, target_size

        elif source_action == "move":

            if archive_dir is None:
                print("  转存目录无效，源图保持不变。")
                return "converted", source_size, target_size

            try:
                archive_path = get_archive_path(
                    source,
                    archive_dir,
                )

                shutil.move(
                    str(source),
                    str(archive_path),
                )

                print(f"  源图已转存：{archive_path}")

            except OSError as exc:
                print(f"  转存源图失败：{exc}")
                return "converted", source_size, target_size

        elif source_action == "keep":
            print("  保留源图。")

        return "success", source_size, target_size

    except subprocess.TimeoutExpired:
        print("  转换超时，源图未处理。")
        return "failed", source_size, 0

    except OSError as exc:
        print(f"  文件处理异常：{exc}")
        return "failed", source_size, 0

    finally:
        remove_temp_file(temp_output)

def move_to_trash(path: Path) -> None:
    """使用 macOS 原生接口将文件移入废纸篓。"""

    from Foundation import NSFileManager, NSURL # type: ignore

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

# ============================================================
# 主程序
# ============================================================

def main() -> None:

    print("=" * 60)
    print("          大体积图片批量转换工具")
    print("=" * 60)

    # 1. 询问图片目录
    image_dir = ask_image_directory()

    # 2. 选择目标格式
    preset = ask_target_format()

    # 3. 设置大小阈值
    threshold_mb = ask_threshold()
    threshold_bytes = threshold_mb * MB

    # 4. 选择源图处理方式
    source_action, archive_dir = ask_source_action()

    # 检查编码器
    encoder = shutil.which("heif-enc")

    if not encoder:
        print("\n未找到 heif-enc。")
        print("请执行：brew install libheif")
        return

    # 扫描当前目录，不递归处理子目录
    files = sorted(
        (
            path
            for path in image_dir.iterdir()
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix.lower() in SUPPORTED_EXTENSIONS
                and path.stat().st_size > threshold_bytes
            )
        ),
        key=lambda path: path.name.lower(),
    )

    total_files = len(files)

    total_source_size = sum(
        path.stat().st_size for path in files
    )

    print("\n" + "=" * 60)
    print("转换任务确认")
    print("=" * 60)

    print(f"图片目录：{image_dir}")
    print(f"目标格式：{preset.name}")
    print(f"大小阈值：超过 {threshold_mb:.2f} MB")
    print(f"源图处理：{source_action}")
    
    if archive_dir:
        print(f"源图转存：{archive_dir}")

    print(f"待转换数量：{total_files}")
    print(f"源图总大小：{format_size(total_source_size)}")

    if total_files == 0:
        print("\n没有找到符合条件的图片。")
        return

    print("\n待转换文件：")

    for index, path in enumerate(files, 1):
        print(
            f"{index:3d}. {path.name:<40} "
            f"{format_size(path.stat().st_size)}"
        )

    confirm = input(
        "\n确认开始转换？[Y/n]: "
    ).strip().lower()

    if confirm not in ("", "y", "yes"):
        print("已取消。")
        return

    # --------------------------------------------------------
    # 开始转换
    # --------------------------------------------------------

    success = 0
    failed = 0
    kept_larger = 0

    total_output_size = 0

    print("\n" + "=" * 60)
    print("开始转换")
    print("=" * 60)

    for index, source in enumerate(files, 1):

        print(
            f"\n[{index}/{total_files}] {source.name}"
        )

        status, source_size, target_size = convert_image(
            source=source,
            preset=preset,
            encoder=encoder,
            source_action=source_action,
            archive_dir=archive_dir,
        )

        if status == "success":
            success += 1
            total_output_size += target_size

        elif status == "kept_larger":
            kept_larger += 1
            total_output_size += target_size

        else:
            failed += 1

    # --------------------------------------------------------
    # 汇总
    # --------------------------------------------------------

    print("\n" + "=" * 60)
    print("转换完成")
    print("=" * 60)

    print(f"待转换文件：{total_files}")
    print(f"成功转换：{success}")
    print(f"转换后体积未减少：{kept_larger}")
    print(f"转换失败：{failed}")

    print(f"\n源图总大小：{format_size(total_source_size)}")
    print(f"目标总大小：{format_size(total_output_size)}")

    difference = total_source_size - total_output_size

    print(f"理论空间差值：{format_size(difference)}")

    if total_source_size > 0:
        ratio = difference / total_source_size * 100
        print(f"体积变化比例：{ratio:.2f}%")

    print("\n处理结束。")


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print("\n\n用户中断操作。")

    except Exception as exc:
        print(f"\n程序发生异常：{exc}")
