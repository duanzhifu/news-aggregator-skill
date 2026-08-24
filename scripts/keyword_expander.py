import sys
import json
from scripts.llm_client import call_llm, extract_json_block

def expand_keyword(keyword: str) -> list:
    """
    接收用户输入的宏观或模糊关键词（如 "人工智能", "前端工程化"），
    通过大模型自动解构为 3~5 个高价值精准子词组（用于多源交叉检索）。
    """
    if not keyword or not keyword.strip():
        return []
    
    # 如果本身很短或是逗号分隔的多个词，先切分基础词
    base_keywords = [k.strip() for k in keyword.split(',') if k.strip()]
    
    prompt = f"""你是一个智能信息检索专家。用户输入了以下宏观或模糊关键词/主题："{keyword}"。
请将其解构、扩展并生成 3 到 5 个高价值、精准的技术子词组或具体领域术语（包含中英文技术名词，适合在 GitHub、Hacker News、技术博客、RSS 等多源进行交叉检索）。

要求：
1. 输出必须是合法的 JSON 格式。
2. 格式为：{{"sub_keywords": ["子词1", "子词2", "子词3", "子词4"]}}
3. 子词应该具备高区分度，能够精确覆盖该主题的前沿技术、框架、开源项目或核心概念。
"""
    try:
        messages = [
            {"role": "system", "content": "你是一个严谨的信息检索与关键词扩展助手，只返回合法 JSON。"},
            {"role": "user", "content": prompt}
        ]
        response_text = call_llm(messages, temperature=0.3, max_tokens=1000)
        parsed = json.loads(extract_json_block(response_text))
        sub_kw = parsed.get("sub_keywords", [])
        if isinstance(sub_kw, list) and sub_kw:
            # 合并用户原本的关键词和扩展出来的子词，去重保持顺序
            combined = []
            for k in base_keywords + [str(s).strip() for s in sub_kw if str(s).strip()]:
                if k not in combined:
                    combined.append(k)
            return combined
    except Exception as e:
        print(f"[Warning] 模糊关键词智能解构失败: {e}，将回退使用原始关键词", file=sys.stderr)
    
    return base_keywords
