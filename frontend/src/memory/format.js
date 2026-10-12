/**
 * Pure helpers of the profile page (unit-tested, no DOM, no network).
 */

/** The categories of a memory item, in the order the profile page shows them. */
export const CATEGORIES = ['identity', 'addressing', 'preference', 'personality']

/** `active`: given to the LLM. `pending`: gathered by the avatar, not confirmed yet. */
export const STATUSES = ['active', 'pending']

/** What apiErrorMessage() takes as status when the request never got an answer. */
export const NETWORK_FAILURE = 0

/** The status of a guest: nobody is logged in. */
export const UNAUTHORIZED = 401

const CATEGORY_LABELS = {
  identity: 'Thông tin về bạn',
  addressing: 'Cách xưng hô',
  preference: 'Sở thích',
  personality: 'Tính cách',
}

const SOURCE_LABELS = {
  manual: 'Tự nhập',
  import: 'Từ tệp đã nhập',
  conversation: 'Nghe được khi trò chuyện',
}

/**
 * Items of the API → { active: { identity: [...], ... }, pending: { ... } }.
 *
 * Every status holds every category (an empty list when it has no item), and the
 * order of the API is kept inside a list. An item of an unknown status or category is
 * left out: the page has no place to show it.
 */
export function groupItems(items) {
  const groups = {}
  for (const status of STATUSES) {
    groups[status] = {}
    for (const category of CATEGORIES) groups[status][category] = []
  }
  for (const item of items ?? []) {
    const list = groups[item.status]?.[item.category]
    if (Array.isArray(list)) list.push(item)
  }
  return groups
}

/** The Vietnamese title of a category; an unknown one is shown as it is. */
export function categoryLabel(category) {
  return CATEGORY_LABELS[category] ?? String(category)
}

/** Where an item comes from: typed, imported or heard; an unknown source as it is. */
export function sourceLabel(source) {
  return SOURCE_LABELS[source] ?? String(source)
}

/** The Vietnamese message shown for a failed call to the memory API. */
export function apiErrorMessage(status) {
  switch (status) {
    case NETWORK_FAILURE: return 'Không kết nối được máy chủ'
    case UNAUTHORIZED: return 'Cần đăng nhập để xem và sửa hồ sơ'
    case 404: return 'Mục này không còn tồn tại'
    case 422: return 'Nội dung không hợp lệ, vui lòng nhập lại'
    case 502: return 'Kho hồ sơ đang gặp sự cố, vui lòng thử lại'
    default: return `Lỗi máy chủ (${status})`
  }
}
