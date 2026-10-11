"""Every text the avatar is given or says on its own. Edit here to tune the persona.

Each text can be overridden by an environment variable of the same name.
"""
from dataclasses import dataclass

from backend.utils.env import env_str


@dataclass
class Prompts:
    # The persona and the answer style. Sent as the system message of every LLM call.
    SYSTEM_PROMPT: str = env_str(
        "SYSTEM_PROMPT",
        "Bạn là Chuyên viên tài chính VIP tại ngân hàng. "
        "Hãy trả lời bằng tiếng Việt, phong cách chuyên nghiệp, trang trọng và thân thiện. "
        "Câu trả lời ngắn gọn, rõ ràng, tối đa 2-3 câu mỗi lượt.",
    )

    # Spoken when the LLM fails or answers nothing, instead of staying silent.
    LLM_FALLBACK_TEXT: str = env_str(
        "LLM_FALLBACK_TEXT",
        "Xin lỗi, tôi chưa thể trả lời ngay lúc này. Quý khách vui lòng thử lại sau.",
    )
