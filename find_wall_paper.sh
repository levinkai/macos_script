echo "===== 1. macOS 当前用户缓存目录 ====="
###
 # @Date: 2026-10-16 21:53:35
 # @LastEditors: LevinKai
 # @LastEditTime: 2026-10-16 21:53:38
 # @FilePath: \\undefined\\Users\\levinkai\\Desktop\\find_wall_paper.sh
### 
CACHE_ROOT="$(getconf DARWIN_USER_CACHE_DIR 2>/dev/null)"
echo "CACHE_ROOT=$CACHE_ROOT"

if [ -n "$CACHE_ROOT" ]; then
    CACHE_DIR="${CACHE_ROOT%/}/com.apple.wallpaper.caches"
    echo "CACHE_DIR=$CACHE_DIR"

    if [ -d "$CACHE_DIR" ]; then
        du -sh "$CACHE_DIR"
    else
        echo "此路径不存在"
    fi
else
    echo "getconf 未返回缓存目录"
fi

echo
echo "===== 2. 墙纸代理容器缓存目录 ====="
CACHE_DIR="$HOME/Library/Containers/com.apple.wallpaper.agent/Data/Library/Caches/com.apple.wallpaper.caches"

if [ -d "$CACHE_DIR" ]; then
    du -sh "$CACHE_DIR"
else
    echo "此路径不存在"
fi