---
tags: [dashboard, news]
---

# News Dashboard

> Requires the [Dataview](https://github.com/blacksmithgu/obsidian-dataview) plugin. Place this file in the Vault; `push_to_obsidian.py` writes news to `自动获取信息/YYYY-MM-DD/信息源/`.

## Today's Articles

```dataview
TABLE 中文标题 AS "Title", 来源 AS "Source", 分类 AS "Category", 热度 AS "Heat", 发布时间 AS "Published"
FROM "自动获取信息"
WHERE 抓取日期 = date(today) AND file.name != "今日总结"
SORT 热度 DESC
LIMIT 50
```

## Articles by Category

```dataview
TABLE rows.中文标题 AS "Title", rows.来源 AS "Source", rows.热度 AS "Heat", rows.发布时间 AS "Published"
FROM "自动获取信息"
WHERE 分类 AND file.name != "今日总结"
GROUP BY 分类
SORT key ASC
```

## Daily Summaries

```dataview
TABLE 文章总数 AS "Articles", 来源数 AS "Sources", 来源列表 AS "Source List"
FROM "自动获取信息"
WHERE contains(标签, "今日总结")
SORT file.name DESC
```

## Highest-Heat Articles

```dataview
TABLE 中文标题 AS "Title", 来源 AS "Source", 热度 AS "Heat", 发布时间 AS "Published"
FROM "自动获取信息"
WHERE 热度 AND file.name != "今日总结"
SORT 热度 DESC
LIMIT 20
```
