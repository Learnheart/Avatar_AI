"""Every text the avatar is given or says on its own. Edit here to tune the persona.

This file is the only source of these texts: they are not read from the environment, so
nothing in `.env` can override them.
"""
from dataclasses import dataclass


@dataclass
class Prompts:
    # The persona and the answer style. Sent as the system message of every LLM call.
    SYSTEM_PROMPT: str = (
        "Bạn là trợ lý cá nhân đồng hành cùng người dùng trong công việc hàng ngày. "
        "Bạn giúp họ tìm hiểu, giải đáp những điều họ thắc mắc và sẵn sàng trò chuyện "
        "cùng họ. "
        "Hãy trả lời bằng tiếng Việt, giọng điệu gần gũi, tự nhiên và tôn trọng. "
        "Câu trả lời ngắn gọn, rõ ràng, tối đa 2-3 câu mỗi lượt."
    )

    # The instruction sent with the utterance when a chat model does the STT.
    STT_TRANSCRIBE_PROMPT: str = (
        "Chép lại nguyên văn lời nói trong đoạn audio. Chỉ trả về lời nói, không thêm "
        "gì. Nếu không có lời nói, trả về chuỗi rỗng."
    )

    # Spoken when the LLM fails or answers nothing, instead of staying silent.
    LLM_FALLBACK_TEXT: str = (
        "Xin lỗi, tôi chưa thể trả lời ngay lúc này. Bạn vui lòng thử lại sau nhé."
    )
