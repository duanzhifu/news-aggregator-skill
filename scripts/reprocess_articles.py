"""
批量重处理已存在的 Obsidian 文章：
1. 重新生成 frontmatter（热度、指标字段按新逻辑）
2. 清洗正文噪音
3. 清理文件名中的 HTML 实体 / URL 编码
4. 同步更新所有今日总结.md 中的链接

用法：
  py scripts/reprocess_articles.py --vault "D:/Obsidian/自动信息获取"
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from push_to_obsidian import (
    parse_frontmatter,
    parse_heat,
    extract_metrics,
    clean_body_paragraphs,
    split_paragraphs,
    sanitize_filename,
    clean_summary,
    yaml_escape,
    CATEGORY_CN,
    get_category,
    HEAT_METRIC_HINTS,
    SOURCE_KEY_ALIAS,
    build_daily_summary_markdown,
    group_items_by_source,
    load_articles_for_date,
    normalize_article_path,
    parse_daily_read_states,
    parse_daily_saved_states,
)


def contains_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fff]', text or ''))


def extract_section(text, heading):
    match = re.search(rf'(?s)##\s*{re.escape(heading)}\s*\n(.*?)(?=\n##\s|\Z)', text)
    return match.group(1).strip() if match else ''


def replace_section(text, heading, value):
    pattern = rf'(?s)(##\s*{re.escape(heading)}\s*\n+)(.*?)(?=\n##\s|\Z)'
    replacement = lambda match: match.group(1) + value.strip() + '\n'
    updated, count = re.subn(pattern, replacement, text, count=1)
    if count != 1:
        raise ValueError(f'文章缺少“## {heading}”段落')
    return updated


def set_frontmatter_value(text, key, value):
    escaped = yaml_escape(value)
    pattern = rf'(?m)^{re.escape(key)}:\s*.*$'
    line = f'{key}: "{escaped}"'
    if re.search(pattern, text):
        return re.sub(pattern, line, text, count=1)
    end = text.find('\n---', 3)
    if end == -1:
        raise ValueError('无效的 YAML frontmatter')
    return text[:end] + '\n' + line + text[end:]


def _article_key_relative_to(date_dir, abs_path):
    """把绝对路径换算成今日总结勾选 key 格式（./信息源/<来源>/<文件名>）。"""
    try:
        rel = Path(abs_path).relative_to(date_dir)
    except ValueError:
        return None
    return normalize_article_path('./' + rel.as_posix())


def translate_metadata_for_date(root, date_value):
    """Translate failed English metadata for one date without changing article bodies."""
    from scripts.llm_summarize import translate_items, TRANSLATION_FAILED

    date_dir = root / date_value
    sources_root = date_dir / '信息源'
    if not sources_root.exists():
        raise FileNotFoundError(f'日期目录不存在: {sources_root}')

    records = []
    target_sources = {'Dev.to', 'Dev.to React', 'GitHub Trending', 'OpenAI Blog'}
    for path in sorted(sources_root.rglob('*.md')):
        if path.name == '索引.md':
            continue
        text = path.read_text(encoding='utf-8')
        fm = parse_frontmatter(text)
        if fm.get('来源', '') not in target_sources:
            continue
        title = fm.get('原文标题', '').replace('\\"', '"')
        title_zh = fm.get('中文标题', '')
        summary = clean_summary(extract_section(text, '总结'))
        if not title or (title_zh != title and contains_chinese(summary)):
            continue
        body = extract_section(text, '文章正文')
        records.append({
            'path': path,
            'text': text,
            'body': body,
            'item': {
                'source': fm.get('来源', ''),
                'title': title,
                'summary': summary,
                'content': body[:3000],
            },
        })

    translated = [None] * len(records)
    llm_indexes = []
    llm_items = []
    for index, record in enumerate(records):
        llm_indexes.append(index)
        llm_items.append(record['item'])
    if llm_items:
        llm_results = translate_items(llm_items, translate_content=False)
        for index, result in zip(llm_indexes, llm_results):
            translated[index] = result

    print(f'发现 {len(records)} 篇需要翻译元数据的文章。')
    rename_map = {}
    success_count = 0
    failed_count = 0
    for record, result in zip(records, translated):
        path = record['path']
        text = record['text']
        status = result.get('translation_status', TRANSLATION_FAILED)
        if status == TRANSLATION_FAILED:
            updated = set_frontmatter_value(text, '翻译状态', status)
            path.write_text(updated, encoding='utf-8')
            failed_count += 1
            print(f'  [翻译失败] {path.name}')
            continue

        title_zh = result['title_zh'].strip()
        summary_zh = result['summary_zh'].strip()
        if not contains_chinese(title_zh) or not contains_chinese(summary_zh):
            updated = set_frontmatter_value(text, '翻译状态', TRANSLATION_FAILED)
            path.write_text(updated, encoding='utf-8')
            failed_count += 1
            print(f'  [翻译无效] {path.name}')
            continue

        updated = set_frontmatter_value(text, '中文标题', title_zh)
        updated = set_frontmatter_value(updated, '翻译状态', status)
        updated = replace_section(updated, '总结', summary_zh)
        if extract_section(updated, '文章正文') != record['body']:
            raise RuntimeError(f'正文发生意外变化: {path}')

        fm = parse_frontmatter(updated)
        pub_date, _ = parse_publish_datetime_from_fm(fm)
        new_path = path.with_name(f'{pub_date}-{sanitize_filename(title_zh, max_len=60)}.md')
        if new_path.exists() and new_path.resolve() != path.resolve():
            raise FileExistsError(f'目标文件已存在: {new_path}')
        path.write_text(updated, encoding='utf-8')
        if new_path.resolve() != path.resolve():
            path.rename(new_path)
            rename_map[str(path)] = str(new_path)
        success_count += 1
        print(f'  [已翻译] {new_path.name}')

    # 重建今日总结前保留已读/已收藏勾选：
    # 翻译会重命名文章（旧文件名 → 新文件名），勾选 key 跟随文章路径，需同步换 key。
    daily_path = date_dir / '今日总结.md'
    read_states = {}
    saved_states = {}
    if daily_path.exists():
        old_text = daily_path.read_text(encoding='utf-8')
        read_states = parse_daily_read_states(old_text)
        saved_states = parse_daily_saved_states(old_text)
        for old_abs, new_abs in rename_map.items():
            old_key = _article_key_relative_to(date_dir, old_abs)
            new_key = _article_key_relative_to(date_dir, new_abs)
            if old_key and old_key in read_states:
                read_states[new_key] = read_states.pop(old_key)
            if old_key and old_key in saved_states:
                saved_states[new_key] = saved_states.pop(old_key)

    daily_items = load_articles_for_date(date_dir)
    by_source, _ = group_items_by_source(daily_items)
    source_summaries = {source: f'{source} 今日收录 {len(items)} 篇文章。' for source, items in by_source.items()}
    daily_text = build_daily_summary_markdown(
        source_summaries,
        by_source,
        report_date=date_value,
        read_states=read_states,
        saved_states=saved_states,
    )
    (date_dir / '今日总结.md').write_text(daily_text, encoding='utf-8')
    print(f'元数据翻译完成：成功 {success_count} 篇，失败 {failed_count} 篇；日报收录 {len(daily_items)} 篇。')
    return success_count, failed_count, len(daily_items)


def parse_publish_datetime_from_fm(fm):
    pub_time_full = fm.get('发布时间', '')
    if pub_time_full:
        try:
            dt = datetime.strptime(pub_time_full, '%Y-%m-%d %H:%M')
            return dt.strftime('%Y-%m-%d'), dt
        except Exception:
            pass
    return fm.get('抓取日期', datetime.now().strftime('%Y-%m-%d')), datetime.now()


def rebuild_frontmatter(fm, metrics, heat):
    title_orig = fm.get('原文标题', '')
    title_zh = fm.get('中文标题', title_orig)
    source_cn = fm.get('来源', '')
    source_key = fm.get('来源key', source_cn)
    category = fm.get('分类', CATEGORY_CN.get(get_category(source_key), '其他'))
    url = fm.get('链接', '')
    pub_time_full = fm.get('发布时间', '')
    fetch_date = fm.get('抓取日期', datetime.now().strftime('%Y-%m-%d'))
    tags_list = fm.get('标签', ['新闻', get_category(source_key), f'来源-{source_cn}', f'抓取日期-{fetch_date}'])

    yaml_lines = ['---']
    yaml_lines.append(f'原文标题: "{yaml_escape(title_orig)}"')
    yaml_lines.append(f'中文标题: "{yaml_escape(title_zh)}"')
    yaml_lines.append(f'来源: "{yaml_escape(source_cn)}"')
    yaml_lines.append(f'来源key: "{yaml_escape(source_key)}"')
    yaml_lines.append(f'分类: "{yaml_escape(category)}"')
    yaml_lines.append(f'链接: "{yaml_escape(url)}"')
    yaml_lines.append(f'热度: {heat}')
    yaml_lines.append(f'发布时间: "{pub_time_full}"')
    yaml_lines.append(f'抓取日期: "{fetch_date}"')
    if fm.get('翻译状态'):
        yaml_lines.append(f'翻译状态: "{yaml_escape(fm["翻译状态"])}"')
    yaml_lines.append(f'标签: {json.dumps(tags_list, ensure_ascii=False)}')
    if metrics:
        yaml_lines.append('指标:')
        for label, val in metrics.items():
            yaml_lines.append(f'  {label}: {val}')
    yaml_lines.append('---')
    return '\n'.join(yaml_lines)


def clean_article_body(text):
    """清洗文章正文段。"""
    # 找到 ## 文章正文 和下一个 ## 之间的内容
    m = re.search(r'##\s*文章正文\s*\n+([\s\S]*?)(?=\n##\s|\Z)', text)
    if not m:
        return text, False
    body_text = m.group(1).strip()
    if not body_text:
        return text, False

    # 处理异常提示块：如果已经是异常提示，跳过清洗
    if body_text.startswith('> ⚠️ 正文获取异常'):
        return text, False

    paragraphs = split_paragraphs(body_text)
    cleaned = clean_body_paragraphs(paragraphs)
    substantial = [p for p in cleaned if len(p) >= 80]
    total_text = '\n'.join(cleaned)
    has_substance = bool(substantial) and len(total_text) >= 300
    if cleaned and has_substance:
        new_body = '\n\n'.join(cleaned)
        new_text = text[:m.start()] + f"## 文章正文\n\n{new_body}\n" + text[m.end():]
        return new_text, True
    else:
        # 正文清洗后为空或实质内容过少，生成异常提示
        # 提取 URL
        fm = parse_frontmatter(text)
        url = fm.get('链接', '')
        placeholder = (
            "## 文章正文\n\n"
            "> ⚠️ 正文获取异常或清洗后无实质内容，建议查看原文："
            f"[{url}]({url})\n"
        )
        new_text = text[:m.start()] + placeholder + text[m.end():]
        return new_text, True


def remove_index_link(text):
    """移除相关链接段中的源索引行（兼容旧格式）。"""
    return re.sub(
        r'\n\s*-\s*\[[^\]]*源索引\]\([^)]*\)\s*(?=\n|\Z)',
        '',
        text
    )


def clean_summary_document(text):
    """Clean one article summary and remove its redundant related-links section."""
    summary = extract_section(text, '总结')
    if not summary:
        return text, False

    updated = replace_section(text, '总结', clean_summary(summary))
    updated = re.sub(
        r'\n*##\s*相关链接\s*\n+.*?(?=\n##\s|\Z)',
        '',
        updated,
        count=1,
        flags=re.DOTALL,
    ).rstrip() + '\n'
    return updated, updated != text


def clean_daily_summary_cells(text):
    """Clean only the article-summary column in Markdown tables."""
    lines = text.splitlines()
    summary_column = None
    changed = False

    for index, line in enumerate(lines):
        if not line.startswith('|'):
            summary_column = None
            continue

        parts = line.split('|')
        cells = [cell.strip() for cell in parts[1:-1]]
        if '文章总结' in cells:
            summary_column = cells.index('文章总结') + 1
            continue
        if summary_column is None or summary_column >= len(parts) - 1:
            continue
        if all(re.fullmatch(r'\s*:?-{3,}:?\s*', cell or '') for cell in cells):
            continue

        old_value = parts[summary_column].strip()
        new_value = clean_summary(old_value) or '-'
        if new_value != old_value:
            parts[summary_column] = f' {new_value} '
            lines[index] = '|'.join(parts)
            changed = True

    trailing_newline = '\n' if text.endswith('\n') else ''
    return '\n'.join(lines) + trailing_newline, changed


def clean_summaries(root, date_value=None):
    """Clean article and daily summaries without touching metadata or article bodies."""
    scan_root = root / date_value if date_value else root
    if not scan_root.exists():
        raise FileNotFoundError(f'日期目录不存在: {scan_root}')

    articles_updated = 0
    daily_updated = 0
    for path in sorted(scan_root.rglob('*.md')):
        if path.name in ('今日总结.md', '索引.md'):
            continue
        text = path.read_text(encoding='utf-8')
        updated, changed = clean_summary_document(text)
        if changed:
            path.write_text(updated, encoding='utf-8')
            articles_updated += 1

    for path in sorted(scan_root.rglob('今日总结.md')):
        text = path.read_text(encoding='utf-8')
        updated, changed = clean_daily_summary_cells(text)
        if changed:
            path.write_text(updated, encoding='utf-8')
            daily_updated += 1

    print(f'总结清理完成：文章 {articles_updated} 篇，今日总结 {daily_updated} 篇。')
    return articles_updated, daily_updated


def main():
    parser = argparse.ArgumentParser(description='批量重处理 Obsidian 文章')
    parser.add_argument('--vault', help='Obsidian Vault 根目录')
    parser.add_argument('--date', help='仅处理指定日期（YYYY-MM-DD）')
    parser.add_argument('--translate-metadata', action='store_true',
                        help='仅重做中文标题和总结，保留正文及其他日期数据')
    parser.add_argument('--clean-summaries', action='store_true',
                        help='仅清理总结中的链接/阅读引导并删除相关链接区块')
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

    if args.translate_metadata:
        if not args.date or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', args.date):
            parser.error('--translate-metadata 必须同时提供有效的 --date YYYY-MM-DD')
        translate_metadata_for_date(root, args.date)
        return

    if args.clean_summaries:
        if args.date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', args.date):
            parser.error('--date 必须是 YYYY-MM-DD 格式')
        clean_summaries(root, args.date)
        return

    print(f"开始重处理: {root}\n")

    # === 1. 处理每篇文章 ===
    rename_map = {}  # old_relative -> new_relative
    updated_count = 0
    renamed_count = 0
    skipped_count = 0

    scan_root = root / args.date if args.date else root
    if not scan_root.exists():
        parser.error(f'日期目录不存在: {scan_root}')
    for md_path in list(scan_root.rglob('*.md')):
        if md_path.name in ('今日总结.md', '索引.md'):
            continue
        try:
            text = md_path.read_text(encoding='utf-8')
        except Exception as e:
            print(f"  [错误] 无法读取 {md_path}: {e}")
            continue

        fm = parse_frontmatter(text)
        if not fm:
            skipped_count += 1
            continue

        # 构造 item 用于新逻辑
        title_orig = fm.get('原文标题', '')
        title_zh = fm.get('中文标题', title_orig)
        source_key = fm.get('来源key', '')
        pub_date, pub_dt = parse_publish_datetime_from_fm(fm)

        item = {
            'title': title_orig,
            'title_zh': title_zh,
            'source_key': source_key,
            'heat': fm.get('热度', 0),
        }
        # 把现有指标字段还原为 metric_ / raw 字段
        metrics_in_fm = fm.get('指标', {})
        if isinstance(metrics_in_fm, dict):
            for k, v in metrics_in_fm.items():
                # 反向映射回 raw key（简化：直接用 metric_ 前缀）
                item[f'metric_{k}'] = v

        # 重新计算热度与指标
        heat = parse_heat(item['heat'])
        metrics = extract_metrics(item)

        # 历史文章 frontmatter 中的热度已被 parse_heat 解析为整数，
        # 原始 heat 字符串已丢失；这里根据来源 key 把热度值映射回对应指标
        norm_key = source_key.lower().replace(' ', '').replace("'", '')
        if norm_key in SOURCE_KEY_ALIAS:
            norm_key = SOURCE_KEY_ALIAS[norm_key]
        if heat and norm_key in HEAT_METRIC_HINTS:
            _, cn_label = HEAT_METRIC_HINTS[norm_key]
            if cn_label not in metrics:
                metrics[cn_label] = str(heat)

        # 重新生成 frontmatter
        new_fm_block = rebuild_frontmatter(fm, metrics, heat)

        # 替换原文 frontmatter
        old_fm_end = text.find('---', 3)
        if old_fm_end == -1:
            skipped_count += 1
            continue
        body_text = text[old_fm_end + 3:].lstrip('\n')

        # 清洗正文
        cleaned_body_text, body_changed = clean_article_body(body_text)
        cleaned_body_text = remove_index_link(cleaned_body_text)

        new_text = new_fm_block + '\n\n' + cleaned_body_text

        # 计算新文件名
        new_filename = f"{pub_date}-{sanitize_filename(title_zh, max_len=60)}.md"
        src_dir = md_path.parent
        new_path = src_dir / new_filename

        # 如果新文件名已存在且不是当前文件，加后缀
        if new_path.exists() and new_path.resolve() != md_path.resolve():
            base = new_path.stem
            suffix = 2
            while (src_dir / f"{base}-{suffix}.md").exists():
                suffix += 1
            new_path = src_dir / f"{base}-{suffix}.md"
            new_filename = new_path.name

        # 写入（重命名或覆盖）
        rel_old = str(md_path.relative_to(root))
        rel_new = str(new_path.relative_to(root))

        if new_path.resolve() != md_path.resolve():
            md_path.write_text(new_text, encoding='utf-8')
            md_path.rename(new_path)
            renamed_count += 1
            rename_map[rel_old] = rel_new
            print(f"  [重命名+更新] {rel_old} -> {rel_new}")
        else:
            md_path.write_text(new_text, encoding='utf-8')
            updated_count += 1
            if body_changed:
                print(f"  [更新正文] {rel_old}")

    print(f"\n文章处理完成：")
    print(f"  更新 frontmatter/正文：{updated_count} 篇")
    print(f"  重命名文件：{renamed_count} 篇")
    print(f"  跳过：{skipped_count} 篇")

    # === 2. 同步更新今日总结.md 中的链接 ===
    if rename_map:
        print(f"\n同步更新今日总结中的 {len(rename_map)} 个链接...")
        renamed = 0
        for summary_path in root.rglob('今日总结.md'):
            try:
                text = summary_path.read_text(encoding='utf-8')
            except Exception:
                continue
            new_text = text
            changed = False
            for old_rel, new_rel in rename_map.items():
                # 今日总结中链接形式：./信息源/<src>/文件名.md
                # old_rel 形式：YYYY-MM-DD/信息源/<src>/文件名.md
                old_link = './' + old_rel.split('/', 1)[1].replace(' ', '%20')
                new_link = './' + new_rel.split('/', 1)[1].replace(' ', '%20')
                if old_link in new_text:
                    new_text = new_text.replace(old_link, new_link)
                    changed = True
            if changed:
                summary_path.write_text(new_text, encoding='utf-8')
                renamed += 1
                print(f"  更新链接: {summary_path.relative_to(root)}")
        print(f"  共更新 {renamed} 个今日总结文件")

    print("\n全部完成！")


if __name__ == '__main__':
    main()
