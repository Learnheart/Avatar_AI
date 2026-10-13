/**
 * Pure helpers of the "Tính cách của avatar" block (unit-tested, no DOM, no network).
 */

import { NETWORK_FAILURE, UNAUTHORIZED } from '../memory/format.js'

/**
 * The fields of the persona, in the order the block shows them. The keys are the ones
 * of the backend (PERSONA_FIELDS in backend/models.py).
 */
export const PERSONA_FIELDS = [
  { key: 'name', label: 'Tên', placeholder: 'Ví dụ: Mai' },
  { key: 'role', label: 'Vai trò', placeholder: 'Ví dụ: Trợ lý công việc, người bạn đồng hành' },
  { key: 'addressing', label: 'Cách xưng hô', placeholder: 'Ví dụ: Xưng em, gọi bạn là anh' },
  { key: 'personality', label: 'Tính cách', placeholder: 'Ví dụ: Vui vẻ, điềm đạm, hay pha trò' },
  {
    key: 'answer_style',
    label: 'Định hướng trả lời',
    placeholder: 'Ví dụ: Ngắn gọn, đi thẳng vào ý chính, có ví dụ',
  },
  { key: 'avoid', label: 'Điều cần tránh', placeholder: 'Ví dụ: Không bàn chuyện chính trị' },
]

/**
 * What is typed in the form ({ key: text }) → the fields to save: the known keys in
 * their order, each text trimmed, the empty ones left out.
 */
export function formValues(raw) {
  const values = {}
  for (const { key } of PERSONA_FIELDS) {
    const text = typeof raw?.[key] === 'string' ? raw[key].trim() : ''
    if (text) values[key] = text
  }
  return values
}

/**
 * The form after a description was read: the fields the reading found take their new
 * text, the others stay as they were typed.
 */
export function mergeRead(form, read) {
  return formValues({ ...formValues(form), ...formValues(read) })
}

/** True when two sets of fields hold the same texts. */
export function sameFields(a, b) {
  const [left, right] = [formValues(a), formValues(b)]
  return PERSONA_FIELDS.every(({ key }) => left[key] === right[key])
}

/** What the block says after a description was read into `count` fields (at least one). */
export function readMessage(count) {
  return `Đã điền ${count} trường từ mô tả. Kiểm tra lại rồi bấm "Lưu" để avatar dùng.`
}

/** What the block says after a save that left `count` fields set. */
export function savedMessage(count) {
  return count > 0
    ? 'Đã lưu, avatar dùng tính cách này từ câu trả lời tiếp theo'
    : 'Không có trường nào được điền: avatar dùng tính cách mặc định'
}

/** What the block says after the persona was removed. */
export function clearedMessage() {
  return 'Đã xoá, avatar trở về tính cách mặc định'
}

/**
 * The Vietnamese message shown for a failed call to the persona API. `maxChars` is the
 * length limit of one field. 413 only comes from the reading of a description.
 */
export function personaErrorMessage(status, maxChars) {
  switch (status) {
    case NETWORK_FAILURE: return 'Không kết nối được máy chủ'
    case UNAUTHORIZED: return 'Cần đăng nhập để đặt tính cách cho avatar'
    case 413: return 'Bản mô tả quá dài, vui lòng rút gọn'
    case 422: return `Nội dung không hợp lệ: mỗi trường tối đa ${maxChars} ký tự`
    case 502: return 'Máy chủ đang gặp sự cố, vui lòng thử lại'
    default: return `Lỗi máy chủ (${status})`
  }
}

/**
 * The message for a refused reading of a description: 422 and 502 mean something else
 * there (nothing to read; the reading failed), the rest is the same.
 */
export function readErrorMessage(status, maxChars) {
  switch (status) {
    case 422: return 'Không thấy điều gì về tính cách của avatar trong mô tả này'
    case 502: return 'Chưa đọc được bản mô tả, vui lòng thử lại'
    default: return personaErrorMessage(status, maxChars)
  }
}
