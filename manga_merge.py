'''
Date: 2026-04-26 14:58:03
LastEditors: LevinKai
LastEditTime: 2026-11-12 15:17:32
FilePath: \\macos_script\\manga_merge.py
'''

import os
import re
import shutil
import zipfile
import argparse
from pathlib import Path

def natural_sort_key(s):
    """
    自然排序 Key，确保 2.zip 排在 10.zip 之前。
    """
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', str(s))]

def merge_zips_optimized(input_dir, output_name):
    input_path = Path(input_dir)
    if not input_path.exists() or not input_path.is_dir():
        print(f"错误: 路径 '{input_dir}' 不存在或不是目录。")
        return

    # 1. 扫描并按自然顺序排列所有 zip 文件
    zip_files = sorted([f for f in input_path.glob("*.zip")], key=natural_sort_key)
    
    if not zip_files:
        print("未在指定目录下找到任何 zip 文件。")
        return

    # 创建一个纯净的临时合并目录，避免在原文件夹内操作产生混乱
    merge_temp_dir = input_path / f"temp_{output_name}_merge"
    merge_temp_dir.mkdir(exist_ok=True)
    
    temp_extract_dirs = []
    global_file_count = 0  # 全局文件计数器，从 0 开始

    try:
        for zip_file in zip_files:
            # 2. 解压每个压缩包到独立临时文件夹
            extract_to = input_path / f"extract_{zip_file.stem}"
            print(f"正在读取: {zip_file.name}")
            with zipfile.ZipFile(zip_file, 'r') as zip_ref:
                zip_ref.extractall(extract_to)
            temp_extract_dirs.append(extract_to)

            # 3. 扫描该解压目录下的图片，按自然顺序排序
            # 过滤掉隐藏文件（如 macOS 的 .DS_Store）
            files_in_zip = sorted(
                [f for f in extract_to.iterdir() if f.is_file() and not f.name.startswith('.')], 
                key=natural_sort_key
            )

            # 4. 移动并按 04d 规范重命名
            for file_item in files_in_zip:
                # 生成类似 0000.jpg, 0001.png 的名称
                new_filename = f"{global_file_count:04d}{file_item.suffix.lower()}"
                target_path = merge_temp_dir / new_filename
                
                shutil.move(str(file_item), str(target_path))
                global_file_count += 1

        # 5. 打包合并后的文件夹
        output_zip = input_path / f"{output_name}.zip"
        print(f"正在生成标准化压缩包: {output_zip.name} (共 {global_file_count} 张图片)...")
        
        with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # 获取合并目录下所有重命名后的文件
            merged_files = sorted([f for f in merge_temp_dir.iterdir()], key=natural_sort_key)
            for file_path in merged_files:
                zipf.write(file_path, arcname=file_path.name)

        print(f"\n合并完成！")
        print(f"输出文件: {output_zip}")
        print(f"总页数: {global_file_count}")

    except Exception as e:
        print(f"运行出错: {e}")

    finally:
        # 6. 清理所有临时文件夹
        print("正在清理临时文件...")
        for d in temp_extract_dirs:
            if d.exists():
                shutil.rmtree(d)
        if merge_temp_dir.exists():
            shutil.rmtree(merge_temp_dir)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="漫画 ZIP 统一重命名合并工具 (04d 规范)")
    parser.add_argument("dir", help="包含待合并 zip 的文件夹路径")
    parser.add_argument("name", help="合并后的 zip 文件名 (不含后缀)")
    
    args = parser.parse_args()
    merge_zips_optimized(args.dir, args.name)