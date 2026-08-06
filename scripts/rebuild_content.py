"""
批量优化已存在的 Obsidian 内容：
1. 删除所有信息源目录下的 索引.md
2. 更新所有文章的「相关链接」，移除源索引链接行
3. 为每个日期目录重建「今日总结.md」（新格式：每个信源分段含文章总结表格+全部文章表）

用法：
  py scripts/rebuild_content.py --vault "D:/Obsidian/自动信息获取"
"""
import argparse
import os
import re
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 复用 push_to_obsidian 中的工具函数
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.push_to_obsidian import (
    parse_frontmatter,
    get_source_cn,
    get_category,
    CATEGORY_CN,
    clean_summary,
    extract_metrics,
    collect_metric_columns,
    sanitize_filename,
)


def parse_publish_datetime_from_fm(fm, now_dt=None):
    """从 frontmatter 解析发布时间，返回 (date_str, datetime)。"""
    now_dt = now_dt or datetime.now()
    pub_time_full = fm.get('发布时间', '')
    if pub_time_full:
        try:
            dt = datetime.strptime(pub_time_full, '%Y-%m-%d %H:%M')
            return dt.strftime('%Y-%m-%d'), dt
        except Exception:
            pass
    # fallback 用文件名或抓取日期
    return fm.get('抓取日期', now_dt.strftime('%Y-%m-%d')), now_dt


def build_article_filename(fm, pub_date):
    title_zh = fm.get('中文标题', fm.get('原文标题', ''))
    title = sanitize_filename(title_zh, max_len=60)
    return f"{pub_date}-{title}.md"


def extract_article_summary(text):
    """从文章正文中提取「总结」段的内容。"""
    m = re.search(r'##\s*总结\s*\n+([\s\S]*?)(?=\n##\s|\Z)', text)
    if m:
        s = m.group(1).strip()
        s = re.sub(r'\s*https?://\S+$', '', s)
        return clean_summary(s)
    return ''


def update_article_links(md_path):
    """更新文章的「相关链接」段，移除源索引行。返回是否有改动。"""
    try:
        text = md_path.read_text(encoding='utf-8')
    except Exception:
        return False
    # 匹配模式：删除 "- [xxx 源索引](./索引.md)" 那一行
    new_text = re.sub(
        r'\n\s*-\s*\[[^\]]*源索引\]\([^)]*\)\s*(?=\n|\Z)',
        '',
        text
    )
    # 也兼容中文空格
    new_text = re.sub(
        r'\n\s*-\s*\[[^\]]*源[索‌​引]\]\([^)]*\)\s*(?=\n|\Z)',
        '',
        new_text
    )
    if new_text != text:
        md_path.write_text(new_text, encoding='utf-8')
        return True
    return False


def rebuild_daily_summary(date_dir):
    """为指定日期目录重建今日总结.md（新格式）。"""
    date_str = date_dir.name
    sources_dir = date_dir / '信息源'
    if not sources_dir.exists():
        return False

    # 扫描所有文章
    items_by_source_cn = {}
    source_cn_to_key = {}

    for src_dir in sorted(sources_dir.iterdir()):
        if not src_dir.is_dir():
            continue
        src_cn = src_dir.name
        items_list = []
        for md_file in sorted(src_dir.glob('*.md')):
            if md_file.name == '索引.md':
                continue
            try:
                text = md_file.read_text(encoding='utf-8')
            except Exception:
                continue
            fm = parse_frontmatter(text)
            if not fm:
                continue

            title_orig = fm.get('原文标题', '')
            title_zh = fm.get('中文标题', title_orig)
            source_key = fm.get('来源key', src_cn)
            url = fm.get('链接', '')
            heat = fm.get('热度', 0)
            try:
                heat_val = int(heat) if heat else 0
            except Exception:
                heat_val = 0

            pub_date, pub_dt = parse_publish_datetime_from_fm(fm)
            summary_zh = extract_article_summary(text)

            # 提取指标字段
            metrics = {}
            for k, v in fm.items():
                if k.startswith('指标.'):
                    label = k[len('指标.'):]
                    metrics[label] = str(v)

            # 构造和 push_to_obsidian 一致的 item dict
            item = {
                'title': title_orig,
                'title_zh': title_zh,
                'source': fm.get('来源', src_cn),
                'source_key': source_key,
                'url': url,
                'heat': heat_val,
                'summary': '',
                'summary_zh': summary_zh,
                'time': fm.get('发布时间', ''),
                'published': fm.get('发布时间', ''),
                '_pub_dt': pub_dt,
            }
            # 把指标注入到 item 的 metric_* 字段，供 extract_metrics/collect_metric_columns 使用
            for label, val in metrics.items():
                item[f'metric_{label}'] = val

            items_list.append(item)
            source_cn_to_key[src_cn] = source_key

        if items_list:
            items_by_source_cn[src_cn] = items_list

    if not items_by_source_cn:
        return False

    total = sum(len(v) for v in items_by_source_cn.values())

    # === 生成新格式的今日总结 ===
    lines = [
        '---',
        f'日期: "{date_str}"',
        f'文章总数: {total}',
        f'来源数: {len(items_by_source_cn)}',
        f'来源列表: {json.dumps(list(items_by_source_cn.keys()), ensure_ascii=False)}',
        f'标签: {json.dumps(["今日总结", "新闻", f"抓取日期-{date_str}"], ensure_ascii=False)}',
        '---',
        '',
        f'# {date_str} 今日总结',
        '',
        f'> 共抓取 {len(items_by_source_cn)} 个来源，{total} 篇文章。',
        '',
    ]

    # === 按信源分段（新：每个来源放文章总结表格）===
    lines.append('## 总体概览（按信源）')
    lines.append('')

    for src_cn, src_items in items_by_source_cn.items():
        lines.append(f'### {src_cn}（{len(src_items)} 条）')
        lines.append('')
        # 没有 LLM 的来源总结，放通用描述
        lines.append(f'当日抓取 {len(src_items)} 条内容。')
        lines.append('')
        sorted_src = sorted(src_items, key=lambda x: x.get('_pub_dt') or datetime.now(), reverse=True)
        src_metric_cols = collect_metric_columns(sorted_src)
        src_base_cols = ['发布时间', '中文标题', '文章总结']
        src_extra_cols = [c for c in src_metric_cols if c not in src_base_cols]
        src_headers = src_base_cols + src_extra_cols
        lines.append('| ' + ' | '.join(src_headers) + ' |')
        lines.append('|' + '|'.join([' --- '] * len(src_headers)) + '|')
        for item in sorted_src:
            title_zh = item.get('title_zh', item.get('title', ''))
            title_orig = item.get('title', '')
            pub_dt = item.get('_pub_dt') or datetime.now()
            pub_time_full = pub_dt.strftime('%Y-%m-%d %H:%M')
            pub_date, _ = parse_publish_datetime_from_fm(
                {'发布时间': item.get('time', ''), '抓取日期': date_str},
                pub_dt
            )
            # 用 source_key 辅助找文件名，失败的话用 fm 信息回退
            filename = build_article_filename(
                {'中文标题': title_zh, '原文标题': title_orig},
                pub_date
            )
            rel_path = f"./信息源/{src_cn}/{filename}".replace(' ', '%20')
            summary_zh = item.get('summary_zh', '')
            summary_clean = clean_summary(summary_zh)
            summary_clean = summary_clean.replace('|', '/').replace('\n', ' ').replace('\r', ' ')
            display_title = (title_zh or title_orig).replace('|', '/').replace('\n', ' ').replace('\r', ' ')
            safe_rel_path = rel_path.replace('|', '/').replace(' ', '%20')
            metrics = extract_metrics(item)
            row = [
                pub_time_full,
                f"[{display_title}]({safe_rel_path})",
                summary_clean or '-',
            ]
            for c in src_extra_cols:
                row.append(metrics.get(c, '-'))
            lines.append('| ' + ' | '.join(row) + ' |')
        lines.append('')

    # === 全部文章表（按发布时间倒序）===
    lines.append('## 全部文章（按发布时间倒序）')
    lines.append('')
    all_items = []
    for src_cn, its in items_by_source_cn.items():
        for it in its:
            it2 = dict(it)
            it2['_src_cn'] = src_cn
            all_items.append(it2)
    all_items_sorted = sorted(all_items, key=lambda x: x.get('_pub_dt') or datetime.now(), reverse=True)

    metric_cols = collect_metric_columns(all_items_sorted)
    base_cols = ['发布时间', '中文标题', '来源']
    extra_cols = [c for c in metric_cols if c not in base_cols]
    headers = base_cols + extra_cols
    lines.append('| ' + ' | '.join(headers) + ' |')
    lines.append('|' + '|'.join([' --- '] * len(headers)) + '|')
    for item in all_items_sorted:
        title_zh = item.get('title_zh', item.get('title', ''))
        title_orig = item.get('title', '')
        pub_dt = item.get('_pub_dt') or datetime.now()
        pub_time_full = pub_dt.strftime('%Y-%m-%d %H:%M')
        pub_date, _ = parse_publish_datetime_from_fm(
            {'发布时间': item.get('time', ''), '抓取日期': date_str},
            pub_dt
        )
        filename = build_article_filename(
            {'中文标题': title_zh, '原文标题': title_orig},
            pub_date
        )
        rel_path = f"./信息源/{item['_src_cn']}/{filename}"
        display_title = (title_zh or title_orig).replace('|', '/').replace('\n', ' ').replace('\r', ' ')
        safe_rel_path = rel_path.replace('|', '/').replace(' ', '%20')
        metrics = extract_metrics(item)
        row = [
            pub_time_full,
            f"[{display_title}]({safe_rel_path})",
            item['_src_cn'],
        ]
        for c in extra_cols:
            row.append(metrics.get(c, '-'))
        lines.append('| ' + ' | '.join(row) + ' |')
    lines.append('')

    content = '\n'.join(lines)
    out_path = date_dir / '今日总结.md'
    out_path.write_text(content, encoding='utf-8')
    return True


def main():
    parser = argparse.ArgumentParser(description='批量优化 Obsidian 内容（移除索引.md + 更新文章链接 + 重建新格式今日总结）')
    parser.add_argument('--vault', help='Obsidian Vault 根目录')
    args = parser.parse_args()

    vault_path = args.vault or os.environ.get('OBSIDIAN_VAULT_PATH')
    if not vault_path:
        print("错误：请通过 --vault 参数或 OBSIDIAN_VAULT_PATH 环境变量指定 Obsidian Vault 根目录。",
              file=sys.stderr)
        sys.exit(1)

    vault = Path(vault_path)
    root = vault / '自动获取信息'
    if not root.exists():
        print(f"目录不存在: {root}")
        sys.exit(1)

    print(f"开始处理: {root}\n")

    # ===== 1. 清理所有索引.md =====
    print("=" * 50)
    print("【步骤 1/3】清理所有 索引.md 文件")
    print("=" * 50)
    idx_deleted = 0
    for idx_path in root.rglob('索引.md'):
        try:
            idx_path.unlink()
            idx_deleted += 1
            print(f"  删除: {idx_path.relative_to(root)}")
        except Exception as e:
            print(f"  [错误] {idx_path}: {e}")
    print(f"✅ 共删除 {idx_deleted} 个索引.md 文件\n")

    # ===== 2. 更新所有文章的相关链接 =====
    print("=" * 50)
    print("【步骤 2/3】更新所有文章的相关链接（移除源索引行）")
    print("=" * 50)
    link_updated = 0
    art_total = 0
    for md_path in root.rglob('*.md'):
        if md_path.name in ('今日总结.md', '索引.md'):
            continue
        art_total += 1
        if update_article_links(md_path):
            link_updated += 1
            print(f"  更新: {md_path.relative_to(root)}")
    print(f"✅ 共扫描 {art_total} 篇文章，更新 {link_updated} 篇\n")

    # ===== 3. 为每个日期目录重建今日总结 =====
    print("=" * 50)
    print("【步骤 3/3】重建今日总结.md（新格式：信源分段含文章总结表格）")
    print("=" * 50)
    sum_rebuilt = 0
    date_dirs = sorted([d for d in root.iterdir() if d.is_dir() and re.match(r'^\d{4}-\d{2}-\d{2}$', d.name)])
    for date_dir in date_dirs:
        try:
            ok = rebuild_daily_summary(date_dir)
            status = "✅ 重建" if ok else "⚠️  无文章跳过"
            if ok:
                sum_rebuilt += 1
            print(f"  {status}: {date_dir.name}/今日总结.md")
        except Exception as e:
            print(f"  [错误] {date_dir.name}: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 50)
    print("处理完成！")
    print(f"  删除索引.md:     {idx_deleted} 个")
    print(f"  更新文章链接:     {link_updated} / {art_total} 篇")
    print(f"  重建今日总结:     {sum_rebuilt} / {len(date_dirs)} 个日期目录")
    print("=" * 50)


if __name__ == '__main__':
    main()
