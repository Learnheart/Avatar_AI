/**
 * Unit tests for memory/format.js — the pure helpers of the profile page.
 * Run: npm test
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  CATEGORIES,
  NETWORK_FAILURE,
  STATUSES,
  UNAUTHORIZED,
  apiErrorMessage,
  categoryLabel,
  groupItems,
  sourceLabel,
} from '../src/memory/format.js'

function item(id, category, status = 'active', source = 'manual') {
  return { id, category, status, source, text: `item ${id}` }
}

function ids(list) {
  return list.map((entry) => entry.id)
}

test('groupItems puts each item under its status and its category', () => {
  const groups = groupItems([
    item('a', 'identity'),
    item('b', 'preference'),
    item('c', 'preference', 'pending', 'conversation'),
    item('d', 'addressing'),
    item('e', 'personality', 'pending', 'conversation'),
  ])

  assert.deepEqual(ids(groups.active.identity), ['a'])
  assert.deepEqual(ids(groups.active.addressing), ['d'])
  assert.deepEqual(ids(groups.active.preference), ['b'])
  assert.deepEqual(ids(groups.active.personality), [])
  assert.deepEqual(ids(groups.pending.preference), ['c'])
  assert.deepEqual(ids(groups.pending.personality), ['e'])
  assert.deepEqual(ids(groups.pending.identity), [])
})

test('groupItems keeps the order of the API inside a list', () => {
  const groups = groupItems([
    item('3', 'preference'),
    item('1', 'preference'),
    item('2', 'preference'),
  ])

  assert.deepEqual(ids(groups.active.preference), ['3', '1', '2'])
})

test('groupItems returns every status and category when there is no item', () => {
  for (const items of [[], undefined, null]) {
    const groups = groupItems(items)

    assert.deepEqual(Object.keys(groups), STATUSES)
    for (const status of STATUSES) {
      assert.deepEqual(Object.keys(groups[status]), CATEGORIES)
      for (const category of CATEGORIES) assert.deepEqual(groups[status][category], [])
    }
  }
})

test('groupItems leaves out an item of an unknown status or category', () => {
  const groups = groupItems([
    item('a', 'identity'),
    item('b', 'identity', 'rejected'),
    item('c', 'hobby'),
    item('d', 'constructor'),
    item('e', 'identity', 'constructor'),
  ])

  assert.deepEqual(ids(groups.active.identity), ['a'])
  const kept = STATUSES.flatMap((status) => CATEGORIES.flatMap((c) => groups[status][c]))
  assert.equal(kept.length, 1)
})

test('categoryLabel names the four categories in Vietnamese', () => {
  assert.equal(categoryLabel('identity'), 'Thông tin về bạn')
  assert.equal(categoryLabel('addressing'), 'Cách xưng hô')
  assert.equal(categoryLabel('preference'), 'Sở thích')
  assert.equal(categoryLabel('personality'), 'Tính cách')
})

test('categoryLabel shows an unknown category as it is', () => {
  assert.equal(categoryLabel('hobby'), 'hobby')
})

test('sourceLabel tells typed, imported and heard apart', () => {
  const labels = ['manual', 'import', 'conversation'].map(sourceLabel)

  assert.equal(labels[0], 'Tự nhập')
  assert.match(labels[1], /nhập/)
  assert.match(labels[2], /trò chuyện/)
  assert.equal(new Set(labels).size, 3)
  assert.equal(sourceLabel('other'), 'other')
})

test('apiErrorMessage explains each refusal of the memory API', () => {
  assert.match(apiErrorMessage(UNAUTHORIZED), /đăng nhập/)
  assert.match(apiErrorMessage(404), /không còn tồn tại/)
  assert.match(apiErrorMessage(422), /không hợp lệ/)
  assert.match(apiErrorMessage(502), /thử lại/)
  assert.equal(UNAUTHORIZED, 401)
})

test('apiErrorMessage has a message for a request that got no answer', () => {
  assert.equal(apiErrorMessage(NETWORK_FAILURE), 'Không kết nối được máy chủ')
})

test('apiErrorMessage falls back to the status for any other failure', () => {
  assert.equal(apiErrorMessage(500), 'Lỗi máy chủ (500)')
})

test('apiErrorMessage gives a different message for each known failure', () => {
  const messages = [NETWORK_FAILURE, UNAUTHORIZED, 404, 422, 502, 500].map(apiErrorMessage)

  assert.equal(new Set(messages).size, messages.length)
})
