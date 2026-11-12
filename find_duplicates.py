'''
Date: 2026-04-25 19:39:04
LastEditors: LevinKai
LastEditTime: 2026-04-25 19:43:22
FilePath: \\Desktop\\find_duplicates.py
'''
import os
import hashlib
import sys

def get_file_hash(file_path, chunk_size=8192):
    """
    计算文件的 MD5 值。分块读取以支持大文件。
    """
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except (PermissionError, OSError):
        # 忽略无权限访问或系统锁定的文件
        return None

def find_duplicates(target_dir):
    """
    扫描目录并返回重复文件的列表
    """
    # key 为文件 hash，value 为包含相同 hash 的文件路径列表
    hash_map = {}
    duplicates = []

    print(f"正在扫描: {target_dir} ...")

    for root, _, files in os.walk(target_dir):
        for filename in files:
            file_path = os.path.join(root, filename)
            
            # 跳过符号链接，避免死循环
            if os.path.islink(file_path):
                continue
                
            file_hash = get_file_hash(file_path)
            
            if file_hash:
                if file_hash in hash_map:
                    hash_map[file_hash].append(file_path)
                else:
                    hash_map[file_hash] = [file_path]

    # 提取存在两个及以上路径的记录
    for paths in hash_map.values():
        if len(paths) > 1:
            duplicates.append(paths)
            
    return duplicates

def main():
    # 获取用户输入
    user_input = input("请输入要扫描的目录路径: ").strip()

    # 路径合法性校验
    if not os.path.exists(user_input):
        print(f"错误: 路径 '{user_input}' 不存在，请重新输入。")
        return

    if not os.path.isdir(user_input):
        print(f"错误: '{user_input}' 是一个文件而非目录。")
        return

    # 执行扫描
    results = find_duplicates(user_input)

    # 结果输出
    if not results:
        print("未发现重复文件。")
    else:
        print(f"\n扫描完成，共发现 {len(results)} 组重复文件：\n")
        for i, group in enumerate(results, 1):
            print(f"组 {i}:")
            for path in group:
                print(f"  - {path}")
            print("-" * 30)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n程序已手动停止。")
        sys.exit(0)