"""Every text the avatar is given or says on its own. Edit here to tune the persona.

This file is the only source of these texts: they are not read from the environment, so
nothing in `.env` can override them.
"""
from dataclasses import dataclass


@dataclass
class Prompts:
    # The persona and the answer style. Sent as the system message of every LLM call.
    SYSTEM_PROMPT: str = (
        "Bạn là Chuyên viên tài chính VIP tại ngân hàng. "
        "Hãy trả lời bằng tiếng Việt, phong cách chuyên nghiệp, trang trọng và thân thiện. "
        "Câu trả lời ngắn gọn, rõ ràng, tối đa 2-3 câu mỗi lượt."
    )

    # Spoken when the LLM fails or answers nothing, instead of staying silent.
    LLM_FALLBACK_TEXT: str = (
        "Xin lỗi, tôi chưa thể trả lời ngay lúc này. Quý khách vui lòng thử lại sau."
    )
