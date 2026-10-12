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

    # The user message when the knowledge base has passages for the question: the
    # passages (each written with KNOWLEDGE_PASSAGE), then the question.
    KNOWLEDGE_PROMPT: str = (
        "Dưới đây là các đoạn trích từ tài liệu nội bộ có thể liên quan đến câu hỏi:\n\n"
        "{passages}\n\n"
        "Hãy ưu tiên dùng thông tin trong các đoạn trích trên để trả lời. Nếu chúng không "
        "liên quan đến câu hỏi, hãy bỏ qua và trả lời như bình thường. Nếu câu hỏi cần "
        "thông tin nội bộ mà các đoạn trích không có, hãy nói rằng bạn chưa có thông tin "
        "đó, đừng tự đoán. Không đọc số thứ tự hay tên tài liệu của đoạn trích.\n\n"
        "Câu hỏi: {question}"
    )
    KNOWLEDGE_PASSAGE: str = "[{index}] (tài liệu: {source})\n{text}"

    # Spoken when the LLM fails or answers nothing, instead of staying silent.
    LLM_FALLBACK_TEXT: str = (
        "Xin lỗi, tôi chưa thể trả lời ngay lúc này. Bạn vui lòng thử lại sau nhé."
    )
