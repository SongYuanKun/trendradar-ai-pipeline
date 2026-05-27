"""话题过滤：只处理 AI/科技/互联网/编程相关热点。"""

TECH_KEYWORDS = [
    # AI & 模型
    "AI", "人工智能", "大模型", "ChatGPT", "GPT", "Claude", "Gemini", "DeepSeek",
    "Qwen", "通义", "文心", "Kimi", "豆包", "llm", "LLM", "机器学习", "神经网络",
    "算法", "AGI", "Agent", "智能体", "向量", "RAG", "多模态", "视觉模型",
    # 科技公司 & 产品
    "OpenAI", "Anthropic", "Google", "微软", "苹果", "特斯拉", "英伟达", "NVIDIA",
    "AMD", "Intel", "华为", "字节", "百度", "腾讯", "阿里", "小米", "商汤", "旷视",
    "芯片", "半导体", "量子", "机器人", "无人驾驶", "自动驾驶",
    # 互联网 & 软件
    "编程", "代码", "程序员", "开源", "GitHub", "Docker", "云计算", "API",
    "APP", "应用", "软件", "操作系统", "数据库", "网络安全", "黑客", "漏洞",
    "区块链", "加密", "元宇宙", "VR", "AR", "物联网",
    # 行业动态
    "融资", "上市", "收购", "裁员", "发布会", "新品", "发布", "上线", "内测",
]


def is_tech_topic(item: dict) -> bool:
    """判断热点是否属于 AI/科技/互联网 范畴。
    只匹配标题，不拼接平台名，避免"百度热搜"中的"百度"误触发。
    """
    title = (item.get("title") or "").lower()
    return any(kw.lower() in title for kw in TECH_KEYWORDS)
