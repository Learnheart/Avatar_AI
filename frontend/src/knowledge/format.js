/**
 * Pure helpers of the knowledge panel (unit-tested, no DOM, no network).
 */

const UNITS = ['B', 'KB', 'MB', 'GB']

/** 1536 → "1.5 KB". */
export function formatBytes(bytes) {
  let value = Math.max(0, Number(bytes) || 0)
  let unit = 0
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  const digits = unit === 0 || value >= 100 ? 0 : 1
  return `${value.toFixed(digits)} ${UNITS[unit]}`
}

/** True if the file name ends with one of the extensions (".pdf", ...), any case. */
export function isSupportedFile(name, extensions) {
  const lower = String(name).toLowerCase()
  return extensions.some((extension) => lower.endsWith(extension))
}

/** The Vietnamese message shown for a failed call to the knowledge API. */
export function apiErrorMessage(status, maxFileMb) {
  switch (status) {
    case 413: return `Tệp vượt quá ${maxFileMb} MB`
    case 415: return 'Định dạng tệp không được hỗ trợ'
    case 422: return 'Không đọc được nội dung văn bản trong tệp'
    case 404: return 'Tài liệu không còn tồn tại'
    case 502: return 'Không tạo được chỉ mục cho tài liệu, vui lòng thử lại'
    default: return `Lỗi máy chủ (${status})`
  }
}
