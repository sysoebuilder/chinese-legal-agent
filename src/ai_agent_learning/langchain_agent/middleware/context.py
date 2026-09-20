"""对话上下文总结中间件。"""

from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models.chat_models import BaseChatModel

CONTEXT_LIMIT = 128_000
COMPACT_THRESHOLD = 0.8
KEEP_RECENT = 6

SUMMARY_PROMPT = """
你负责压缩一段即将从上下文中移除的对话历史。

请保留后续对话仍然需要的全部信息，包括：
- 用户的目标、明确要求、偏好和约束；
- 已经作出的决定及其原因；
- 已完成的操作、修改过的文件和重要结果；
- 尚未完成的任务、阻塞原因和下一步；
- 工具调用中对后续回答仍有价值的事实。

不要添加对话中不存在的事实，不要回答用户问题，也不要保留无意义寒暄。
输出一份可以直接代替原历史的中文上下文摘要。

<messages>
{messages}
</messages>
""".strip()


def build_context_middleware(
    model: BaseChatModel,
) -> SummarizationMiddleware:
    """使用旧版阈值创建官方上下文总结中间件。"""
    return SummarizationMiddleware(
        model=model,
        trigger=("tokens", int(CONTEXT_LIMIT * COMPACT_THRESHOLD)),
        keep=("messages", KEEP_RECENT),
        summary_prompt=SUMMARY_PROMPT,
        # 触发阈值给总结提示词和模型输出预留了约 20% 上下文空间。
        trim_tokens_to_summarize=None,
    )
