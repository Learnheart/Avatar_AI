/**
 * Unit tests for knowledge/format.js — the pure helpers of the knowledge panel.
 * Run: npm test
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { apiErrorMessage, formatBytes, isSupportedFile } from '../src/knowledge/format.js'

const EXTENSIONS = ['.txt', '.pdf', '.docx']

test('formatBytes picks the unit and keeps one decimal below 100', () => {
  assert.equal(formatBytes(0), '0 B')
  assert.equal(formatBytes(512), '512 B')
  assert.equal(formatBytes(1536), '1.5 KB')
  assert.equal(formatBytes(150 * 1024), '150 KB')
  assert.equal(formatBytes(2.5 * 1024 * 1024), '2.5 MB')
})

test('formatBytes treats a missing or negative size as 0', () => {
  assert.equal(formatBytes(undefined), '0 B')
  assert.equal(formatBytes(-5), '0 B')
})

test('isSupportedFile matches the extension in any case', () => {
  assert.equal(isSupportedFile('Quy-dinh.PDF', EXTENSIONS), true)
  assert.equal(isSupportedFile('ghi chú.txt', EXTENSIONS), true)
  assert.equal(isSupportedFile('anh.png', EXTENSIONS), false)
  assert.equal(isSupportedFile('pdf', EXTENSIONS), false)
})

test('apiErrorMessage explains each refusal of the knowledge API', () => {
  assert.equal(apiErrorMessage(413, 10), 'Tệp vượt quá 10 MB')
  assert.match(apiErrorMessage(415, 10), /không được hỗ trợ/)
  assert.match(apiErrorMessage(422, 10), /Không đọc được/)
  assert.match(apiErrorMessage(502, 10), /chỉ mục/)
  assert.equal(apiErrorMessage(500, 10), 'Lỗi máy chủ (500)')
})
