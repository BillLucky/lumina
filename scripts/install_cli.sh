#!/usr/bin/env bash
# 把 lumina 看板命令软链到 PATH 上的目录，实现「任意目录直接敲 lumina」（类似 npm link）。
# 软链指向仓库里的 lumina 脚本；该脚本会解析软链回真实路径，故 git pull 更新后命令自动跟着更新，
# 无需重装。卸载：删掉软链即可（scripts/install_cli.sh --uninstall）。
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
SRC="$REPO/lumina"
chmod +x "$SRC"

# 选一个「在 PATH 上且可写」的 bin 目录（优先 homebrew，其次 ~/.local/bin）
pick_dir(){
  for d in /opt/homebrew/bin "$HOME/.local/bin" /usr/local/bin; do
    case ":$PATH:" in *":$d:"*) [ -d "$d" ] && [ -w "$d" ] && { echo "$d"; return; };; esac
  done
  # 都不行：用 ~/.local/bin（并提示加 PATH）
  mkdir -p "$HOME/.local/bin"; echo "$HOME/.local/bin"
}

if [ "${1:-}" = "--uninstall" ]; then
  for d in /opt/homebrew/bin "$HOME/.local/bin" /usr/local/bin; do
    [ -L "$d/lumina" ] && { rm -f "$d/lumina"; echo "已移除 $d/lumina"; }
  done
  exit 0
fi

DST="$(pick_dir)/lumina"
ln -sf "$SRC" "$DST"
echo "✓ 已软链: $DST -> $SRC"
case ":$PATH:" in
  *":$(dirname "$DST"):"*) echo "  $(dirname "$DST") 已在 PATH，现在任意目录敲 lumina 即可。";;
  *) echo "  ⚠ $(dirname "$DST") 不在 PATH，请加入：echo 'export PATH=\"$(dirname "$DST"):\$PATH\"' >> ~/.zshrc";;
esac
command -v lumina >/dev/null && echo "  当前解析: $(command -v lumina)"
