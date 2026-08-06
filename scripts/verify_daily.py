"""Verify article counts, translated metadata, and Markdown links for one day."""
import argparse
import re
from pathlib import Path
from urllib.parse import unquote

from push_to_obsidian import clean_summary, parse_frontmatter


TARGET_ENGLISH_SOURCES = {'Dev.to', 'Dev.to React', 'GitHub Trending', 'OpenAI Blog'}


def contains_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fff]', text or ''))


def verify_daily(date_dir, expected_counts=None):
    sources_root = date_dir / '信息源'
    files = list(sources_root.rglob('*.md'))
    counts = {
        source_dir.name: len(list(source_dir.glob('*.md')))
        for source_dir in sources_root.iterdir()
        if source_dir.is_dir()
    }
    if expected_counts and counts != expected_counts:
        raise AssertionError(f'来源数量不匹配: {counts}')

    translated = 0
    untouched_chinese = 0
    invalid_metadata = []
    invalid_summary_noise = []
    for path in files:
        text = path.read_text(encoding='utf-8')
        fm = parse_frontmatter(text)
        summary_match = re.search(r'(?s)## 总结\s*(.*?)(?=\r?\n## |\Z)', text)
        summary = summary_match.group(1).strip() if summary_match else ''
        if clean_summary(summary) != summary:
            invalid_summary_noise.append(str(path))
        if path.parent.name in TARGET_ENGLISH_SOURCES:
            translated += bool(fm.get('翻译状态'))
            if not contains_chinese(fm.get('中文标题', '')) or not contains_chinese(summary):
                invalid_metadata.append(str(path))
        elif not fm.get('翻译状态'):
            untouched_chinese += 1

    daily_path = date_dir / '今日总结.md'
    daily = daily_path.read_text(encoding='utf-8')
    all_relative_links = re.findall(r'\[[^\]]+\]\((\./[^)]+)\)', daily)
    links = [link for link in all_relative_links if unquote(link[2:]).startswith('信息源/')]
    missing_links = [link for link in links if not (date_dir / unquote(link[2:])).exists()]

    malformed_rows = []
    expected_pipes = None
    for line_number, line in enumerate(daily.splitlines(), start=1):
        if not line.startswith('|'):
            expected_pipes = None
            continue
        if expected_pipes is None:
            expected_pipes = line.count('|')
        elif line.count('|') != expected_pipes:
            malformed_rows.append(line_number)

    result = {
        'files': len(files),
        'counts': counts,
        'translated': translated,
        'untouched_chinese': untouched_chinese,
        'links': len(links),
        'unique_links': len(set(links)),
        'missing_links': missing_links,
        'malformed_rows': malformed_rows,
        'invalid_metadata': invalid_metadata,
        'invalid_summary_noise': invalid_summary_noise,
    }
    expected_link_count = len(files) * 2
    if (
        invalid_metadata
        or invalid_summary_noise
        or missing_links
        or malformed_rows
        or len(links) != expected_link_count
        or len(set(links)) != len(files)
    ):
        raise AssertionError(result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--vault', required=True)
    parser.add_argument('--date', required=True)
    args = parser.parse_args()
    date_dir = Path(args.vault) / '自动获取信息' / args.date
    result = verify_daily(date_dir)
    for key, value in result.items():
        print(f'{key}: {value}')


if __name__ == '__main__':
    main()
