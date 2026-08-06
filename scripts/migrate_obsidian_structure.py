"""
迁移 Obsidian Vault 目录结构：
旧结构：
  自动获取信息/
    信息源/<src>/*.md
    日期/YYYY-MM-DD 今日总结.md

新结构：
  自动获取信息/
    YYYY-MM-DD/
      今日总结.md
      信息源/<src>/*.md

用法：
  py scripts/migrate_obsidian_structure.py --vault "D:/Obsidian/自动信息获取"
"""
import argparse
import os
import re
import sys
from pathlib import Path
from datetime import datetime

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass


def parse_frontmatter(text):
    """简单解析 YAML frontmatter，返回 dict。"""
    if not text.startswith('---'):
        return {}
    end = text.find('---', 3)
    if end == -1:
        return {}
    fm_text = text[3:end].strip()
    data = {}
    current_key = None
    for line in fm_text.split('\n'):
        line = line.rstrip()
        if not line:
            continue
        m = re.match(r'^(\S+?):\s*(.*)$', line)
        if m:
            k, v = m.group(1), m.group(2).strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            data[k] = v
            current_key = k
        elif line.startswith('  ') and current_key:
            line = line.strip()
            m2 = re.match(r'^(\S+?):\s*(.*)$', line)
            if m2:
                data[f"{current_key}.{m2.group(1)}"] = m2.group(2).strip().strip('"').strip("'")
    return data


def update_article_links(content, old_relative_root, new_relative_root):
    """
    更新文章中的相对链接路径。
    文章位置：旧：信息源/<src>/xxx.md，新：YYYY-MM-DD/信息源/<src>/xxx.md
    - 今日总结链接：../../日期/{date} 今日总结.md  -> ../../今日总结.md
    - 索引链接：./索引.md（不变，因为和文章同目录）
    """
    # 替换今日总结链接
    # 旧格式：../../日期/YYYY-MM-DD 今日总结.md 或类似
    content = re.sub(
        r'\[(\d{4}-\d{2}-\d{2}) 今日总结\]\([^)]+\)',
        r'[\1 今日总结](../../今日总结.md)',
        content
    )
    return content


def update_daily_summary_links(content, date_str):
    """
    更新每日总结中的相对链接路径。
    位置：旧：日期/YYYY-MM-DD 今日总结.md，新：YYYY-MM-DD/今日总结.md
    - 文章链接：../../信息源/<src>/xxx.md -> ./信息源/<src>/xxx.md
    """
    # 替换按信源分段的列表链接
    content = re.sub(
        r'(\- \[[^\]]+\])\(\.\./\.\./信息源/',
        r'\1(./信息源/',
        content
    )
    # 替换全部文章表中的链接
    content = re.sub(
        r'(\[[^\]]+\])\(\.\./\.\./信息源/',
        r'\1(./信息源/',
        content
    )
    return content


def migrate(vault_path):
    vault = Path(vault_path)
    if not vault.exists():
        raise RuntimeError(f"Vault 路径不存在: {vault}")

    root = vault / '自动获取信息'
    if not root.exists():
        print("自动获取信息目录不存在，无需迁移。")
        return

    old_sources_root = root / '信息源'
    old_summary_dir = root / '日期'

    if not old_sources_root.exists() and not old_summary_dir.exists():
        print("旧结构目录不存在，可能已迁移完成。")
        return

    print(f"开始迁移：{root}")
    print(f"  旧信息源目录：{old_sources_root}")
    print(f"  旧总结目录：{old_summary_dir}")

    # ===== 1. 迁移文章：按抓取日期分组 =====
    migrated_articles = 0
    skipped_articles = 0
    date_sources_map = {}  # date_str -> {src_cn: [articles]}

    if old_sources_root.exists():
        for src_dir in old_sources_root.iterdir():
            if not src_dir.is_dir():
                continue
            src_cn = src_dir.name
            print(f"\n处理来源：{src_cn}")

            for md_file in src_dir.glob('*.md'):
                if md_file.name == '索引.md':
                    # 索引文件后面单独处理
                    continue

                try:
                    text = md_file.read_text(encoding='utf-8')
                    fm = parse_frontmatter(text)
                    fetch_date = fm.get('抓取日期', '')

                    # 如果没有抓取日期，用文件名中的日期
                    if not fetch_date:
                        m = re.match(r'^(\d{4}-\d{2}-\d{2})-', md_file.name)
                        if m:
                            fetch_date = m.group(1)
                        else:
                            fetch_date = datetime.now().strftime('%Y-%m-%d')

                    # 目标目录
                    new_date_dir = root / fetch_date
                    new_sources_dir = new_date_dir / '信息源' / src_cn
                    new_sources_dir.mkdir(parents=True, exist_ok=True)

                    # 更新链接
                    new_text = update_article_links(text, '', '')

                    # 写入新位置
                    new_file = new_sources_dir / md_file.name
                    if new_file.exists():
                        # 如果目标已存在，保留两者（加后缀）
                        base = new_file.stem
                        suffix = 2
                        while (new_sources_dir / f"{base}-{suffix}.md").exists():
                            suffix += 1
                        new_file = new_sources_dir / f"{base}-{suffix}.md"

                    new_file.write_text(new_text, encoding='utf-8')

                    # 记录映射
                    date_sources_map.setdefault(fetch_date, {}).setdefault(src_cn, []).append(md_file)

                    # 删除旧文件
                    md_file.unlink()
                    migrated_articles += 1
                    print(f"  [迁移] {md_file.name} -> {fetch_date}/信息源/{src_cn}/")

                except Exception as e:
                    print(f"  [错误] {md_file.name}: {e}")
                    skipped_articles += 1

    # ===== 2. 迁移来源索引（每个日期目录下的来源需要一个索引）=====
    # 先读取旧索引，然后拆分到各日期目录
    # 这里简化处理：我们不重新生成索引，让用户下次运行 push_to_obsidian 时重新生成
    # 但把旧索引文件做个备份
    if old_sources_root.exists():
        for src_dir in old_sources_root.iterdir():
            if not src_dir.is_dir():
                continue
            old_idx = src_dir / '索引.md'
            if old_idx.exists():
                # 移到一个备份位置，避免干扰
                backup_path = root / '_旧结构备份' / '信息源索引备份'
                backup_path.mkdir(parents=True, exist_ok=True)
                old_idx.rename(backup_path / f"{src_dir.name}_索引.md")
                print(f"\n[备份索引] {src_dir.name}/索引.md -> _旧结构备份/信息源索引备份/")

    # ===== 3. 迁移每日总结 =====
    migrated_summaries = 0
    if old_summary_dir.exists():
        for summary_file in old_summary_dir.glob('*.md'):
            m = re.match(r'^(\d{4}-\d{2}-\d{2}) 今日总结\.md$', summary_file.name)
            if not m:
                continue
            date_str = m.group(1)
            print(f"\n处理每日总结：{date_str}")

            try:
                text = summary_file.read_text(encoding='utf-8')
                # 更新链接
                new_text = update_daily_summary_links(text, date_str)

                # 目标目录
                new_date_dir = root / date_str
                new_date_dir.mkdir(parents=True, exist_ok=True)
                new_file = new_date_dir / '今日总结.md'

                if new_file.exists():
                    # 备份旧的
                    backup = new_date_dir / '今日总结_迁移前备份.md'
                    new_file.rename(backup)
                    print(f"  [备份] 已有今日总结.md -> 今日总结_迁移前备份.md")

                new_file.write_text(new_text, encoding='utf-8')
                summary_file.unlink()
                migrated_summaries += 1
                print(f"  [迁移] {summary_file.name} -> {date_str}/今日总结.md")

            except Exception as e:
                print(f"  [错误] {summary_file.name}: {e}")

    # ===== 4. 清理旧空目录 =====
    print("\n清理旧目录...")

    # 清理旧信息源子目录（如果空了）
    if old_sources_root.exists():
        for src_dir in old_sources_root.iterdir():
            if src_dir.is_dir():
                try:
                    src_dir.rmdir()  # 只删除空目录
                    print(f"  [删除空目录] 信息源/{src_dir.name}")
                except OSError:
                    print(f"  [保留非空目录] 信息源/{src_dir.name}")
        try:
            old_sources_root.rmdir()
            print(f"  [删除空目录] 信息源/")
        except OSError:
            print(f"  [保留非空目录] 信息源/（仍有文件）")

    # 清理旧日期目录
    if old_summary_dir.exists():
        try:
            old_summary_dir.rmdir()
            print(f"  [删除空目录] 日期/")
        except OSError:
            print(f"  [保留非空目录] 日期/（仍有文件）")

    print(f"\n{'='*50}")
    print(f"迁移完成！")
    print(f"  迁移文章数：{migrated_articles}")
    print(f"  跳过文章数：{skipped_articles}")
    print(f"  迁移总结数：{migrated_summaries}")
    print(f"\n建议：下次运行 push_to_obsidian.py 时，会自动为各日期目录的信息源重新生成索引.md。")


def main():
    parser = argparse.ArgumentParser(description='迁移 Obsidian Vault 目录结构到按日期组织')
    parser.add_argument('--vault', help='Obsidian Vault 根目录')
    args = parser.parse_args()

    vault_path = args.vault or os.environ.get('OBSIDIAN_VAULT_PATH')
    if not vault_path:
        print("错误：请通过 --vault 参数或 OBSIDIAN_VAULT_PATH 环境变量指定 Obsidian Vault 根目录。",
              file=sys.stderr)
        sys.exit(1)

    migrate(vault_path)


if __name__ == '__main__':
    main()
