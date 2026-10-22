#!/usr/bin/env bash
# pc-cleanup installer (macOS / Linux / WSL)
#
# 注意：本 skill 的核心逻辑依赖 Windows（winreg、NTFS、PowerShell 回收站），
# 在非 Windows 上安装只保证文件就位，脚本不保证可运行。
# 仅当你需要在非 Windows 环境备份/查看源码时使用。

set -euo pipefail

REPO_OWNER="benben314"
REPO_NAME="pc-cleanup"
BRANCH="main"
SKILL_NAME="pc-cleanup"

DEST="${1:-$HOME/.workbuddy-ai/skills/$SKILL_NAME}"
FORCE="${FORCE:-0}"

info() { printf '\033[36m[..]\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m[OK]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[!]\033[0m  %s\n' "$*"; }
die()  { printf '\033[31m[x]\033[0m  %s\n' "$*" >&2; exit 1; }

echo
echo "  pc-cleanup installer"
echo "  --------------------"
info "目标目录: $DEST"

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ;;
  *) warn "当前不是 Windows。文件会照常复制，但 skill 运行时依赖 Windows API。" ;;
esac

# ---- 1. 定位源 ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP=""
cleanup_tmp() { [ -n "$TMP" ] && [ -d "$TMP" ] && rm -rf "$TMP"; }
trap cleanup_tmp EXIT

if [ -f "$SCRIPT_DIR/SKILL.md" ]; then
  ok "源: 本地仓库目录"
  SRC="$SCRIPT_DIR"
else
  info "源: GitHub ($REPO_OWNER/$REPO_NAME@$BRANCH)"
  TMP="$(mktemp -d)"
  URL="https://github.com/$REPO_OWNER/$REPO_NAME/archive/refs/heads/$BRANCH.tar.gz"
  info "下载: $URL"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$URL" -o "$TMP/repo.tar.gz"
  elif command -v wget >/dev/null 2>&1; then
    wget -q "$URL" -O "$TMP/repo.tar.gz"
  else
    die "需要 curl 或 wget"
  fi
  tar -xzf "$TMP/repo.tar.gz" -C "$TMP"
  SRC="$(find "$TMP" -maxdepth 1 -type d -name "$REPO_NAME-*" | head -1)"
  [ -n "$SRC" ] || die "解压后找不到仓库目录"
  ok "下载完成"
fi

# ---- 2. 处理已存在的目标 ----
if [ -e "$DEST" ]; then
  if [ "$FORCE" != "1" ]; then
    warn "目标已存在: $DEST"
    printf '    覆盖安装? 旧版本会自动备份 [y/N] '
    read -r ans || ans=""
    case "$ans" in
      y|Y) ;;
      *) echo "    已取消，未做任何改动。"; exit 0 ;;
    esac
  fi
  BAK="$DEST.bak-$(date +%Y%m%d-%H%M%S)"
  mv "$DEST" "$BAK"
  warn "旧版本已备份到: $BAK"
fi

# ---- 3. 复制 ----
info "复制文件..."
mkdir -p "$DEST"
for f in SKILL.md README.md LICENSE .gitignore; do
  [ -f "$SRC/$f" ] && cp "$SRC/$f" "$DEST/"
done
for d in references scripts; do
  [ -d "$SRC/$d" ] && cp -R "$SRC/$d" "$DEST/"
done
find "$DEST" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ---- 4. 验证 ----
[ -f "$DEST/SKILL.md" ] || die "安装失败: SKILL.md 未找到"
grep -qE 'name:[[:space:]]*pc-cleanup' "$DEST/SKILL.md" || die "安装失败: SKILL.md 缺少 name 声明"
[ -f "$DEST/scripts/scan.py" ] || die "安装失败: scripts/scan.py 未找到"
[ -f "$DEST/scripts/clean.py" ] || die "安装失败: scripts/clean.py 未找到"

echo
ok "安装完成"
echo "    位置: $DEST"
echo "    文件:"
find "$DEST" -type f | while read -r p; do
  printf '      %-28s %8s B\n' "${p#$DEST/}" "$(wc -c < "$p" | tr -d ' ')"
done
echo
echo "    下一步: 重启 WorkBuddy（或新开一个对话），然后说「清理电脑」试试。"
echo
