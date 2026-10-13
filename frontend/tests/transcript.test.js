/**
 * Unit tests for transcript/transcript.js — the lines of the conversation on screen.
 * Run: npm test
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  AVATAR,
  PERSON,
  addPersonLine,
  appendAvatarSentence,
  createTranscript,
  cutAvatarLine,
  isNearBottom,
  resetTranscript,
  showConversation,
} from '../src/transcript/transcript.js'

const MAX = 6

/** One turn: what the person said, then the sentences of the answer. */
function turn(transcript, said, sentences, max = MAX) {
  let next = addPersonLine(transcript, said, max)
  for (const sentence of sentences) next = appendAvatarSentence(next, sentence, max)
  return next
}

function texts(transcript) {
  return transcript.lines.map((line) => `${line.speaker}: ${line.text}`)
}

test('a new transcript is empty and shows no conversation', () => {
  assert.deepEqual(createTranscript(), { conversationId: null, lines: [], running: false })
})

test('a turn gives a person line and an avatar line', () => {
  const transcript = turn(createTranscript(), 'Xin chào', ['Chào bạn.'])
  assert.deepEqual(transcript.lines, [
    { speaker: PERSON, text: 'Xin chào', cut: false },
    { speaker: AVATAR, text: 'Chào bạn.', cut: false },
  ])
})

test('the sentences of one turn are appended to the same avatar line', () => {
  const transcript = turn(createTranscript(), 'Mấy giờ rồi?', ['Bây giờ là ba giờ.', 'Bạn cần gì nữa không?'])
  assert.deepEqual(texts(transcript), [
    'person: Mấy giờ rồi?',
    'avatar: Bây giờ là ba giờ. Bạn cần gì nữa không?',
  ])
})

test('each turn opens its own avatar line', () => {
  let transcript = turn(createTranscript(), 'Một', ['Câu một.'])
  transcript = turn(transcript, 'Hai', ['Câu hai.', 'Câu ba.'])
  assert.deepEqual(texts(transcript), [
    'person: Một',
    'avatar: Câu một.',
    'person: Hai',
    'avatar: Câu hai. Câu ba.',
  ])
})

test('an empty text_content adds nothing', () => {
  const before = turn(createTranscript(), 'Xin chào', ['Chào bạn.'])
  for (const empty of ['', '   ', null, undefined]) {
    assert.equal(appendAvatarSentence(before, empty, MAX), before)
  }
  // Before the first sentence it opens no line either.
  const asked = addPersonLine(createTranscript(), 'Xin chào', MAX)
  assert.equal(appendAvatarSentence(asked, '', MAX), asked)
  assert.deepEqual(texts(appendAvatarSentence(asked, 'Chào bạn.', MAX)), [
    'person: Xin chào',
    'avatar: Chào bạn.',
  ])
})

test('an empty person line adds nothing and does not end the turn', () => {
  const before = turn(createTranscript(), 'Xin chào', ['Chào bạn.'])
  assert.equal(addPersonLine(before, '  ', MAX), before)
  assert.equal(before.running, true)
})

test('texts are trimmed', () => {
  const transcript = turn(createTranscript(), '  Xin chào ', [' Chào bạn. ', ' Khoẻ không? '])
  assert.deepEqual(texts(transcript), ['person: Xin chào', 'avatar: Chào bạn. Khoẻ không?'])
})

test('a cut marks the running avatar line only', () => {
  let transcript = turn(createTranscript(), 'Một', ['Câu một.'])
  transcript = turn(transcript, 'Hai', ['Câu hai.'])
  transcript = cutAvatarLine(transcript)
  assert.deepEqual(transcript.lines.map((line) => line.cut), [false, false, false, true])
})

test('a cut with no avatar line running marks nothing', () => {
  // Barge-in while the avatar was still thinking: the last line is the person's.
  const thinking = addPersonLine(turn(createTranscript(), 'Một', ['Câu một.']), 'Hai', MAX)
  assert.equal(cutAvatarLine(thinking), thinking)
  assert.equal(cutAvatarLine(createTranscript()).lines.length, 0)
})

test('a second cut changes nothing and a later sentence opens a new line', () => {
  const cut = cutAvatarLine(turn(createTranscript(), 'Một', ['Câu một.']))
  assert.equal(cutAvatarLine(cut), cut)
  const late = appendAvatarSentence(cut, 'Câu hai.', MAX)
  assert.deepEqual(late.lines.map((line) => [line.text, line.cut]), [
    ['Một', false],
    ['Câu một.', true],
    ['Câu hai.', false],
  ])
})

test('past the maximum the oldest lines are dropped', () => {
  let transcript = createTranscript()
  for (let i = 1; i <= 4; i += 1) transcript = turn(transcript, `Hỏi ${i}`, [`Đáp ${i}.`], 4)
  assert.deepEqual(texts(transcript), [
    'person: Hỏi 3',
    'avatar: Đáp 3.',
    'person: Hỏi 4',
    'avatar: Đáp 4.',
  ])
})

test('appending to the running line drops nothing and still finds it after a cap', () => {
  let transcript = turn(createTranscript(), 'Một', ['Câu một.'], 2)
  transcript = turn(transcript, 'Hai', ['Câu hai.', 'Câu ba.'], 2)
  assert.deepEqual(texts(transcript), ['person: Hai', 'avatar: Câu hai. Câu ba.'])
  assert.equal(cutAvatarLine(transcript).lines[1].cut, true)
})

test('a conversation with another id empties the lines', () => {
  let transcript = showConversation(createTranscript(), 'c1')
  transcript = turn(transcript, 'Xin chào', ['Chào bạn.'])
  const next = showConversation(transcript, 'c2')
  assert.deepEqual(next, { conversationId: 'c2', lines: [], running: false })
})

test('a closed conversation (null) empties the lines', () => {
  const transcript = turn(showConversation(createTranscript(), 'c1'), 'Xin chào', ['Chào bạn.'])
  assert.deepEqual(showConversation(transcript, null), {
    conversationId: null,
    lines: [],
    running: false,
  })
})

test('the same conversation id keeps the lines', () => {
  const transcript = turn(showConversation(createTranscript(), 'c1'), 'Xin chào', ['Chào bạn.'])
  assert.equal(showConversation(transcript, 'c1'), transcript)
  // No conversation, told again that there is none (the message after `connected`).
  const empty = createTranscript()
  assert.equal(showConversation(empty, null), empty)
  assert.equal(showConversation(empty, undefined), empty)
})

test('after a new conversation the next sentence opens a new avatar line', () => {
  const transcript = turn(showConversation(createTranscript(), 'c1'), 'Xin chào', ['Chào bạn.'])
  const next = appendAvatarSentence(showConversation(transcript, 'c2'), 'Câu mới.', MAX)
  assert.deepEqual(texts(next), ['avatar: Câu mới.'])
})

test('reset empties the lines and keeps the conversation id', () => {
  const transcript = turn(showConversation(createTranscript(), 'c1'), 'Xin chào', ['Chào bạn.'])
  assert.deepEqual(resetTranscript(transcript), { conversationId: 'c1', lines: [], running: false })
})

test('the functions do not change the transcript they are given', () => {
  const before = turn(createTranscript(), 'Xin chào', ['Chào bạn.'])
  const snapshot = JSON.parse(JSON.stringify(before))
  appendAvatarSentence(before, 'Thêm.', MAX)
  cutAvatarLine(before)
  addPersonLine(before, 'Nữa', MAX)
  showConversation(before, 'c9')
  assert.deepEqual(before, snapshot)
})

test('isNearBottom tells whether the list shows its end', () => {
  assert.equal(isNearBottom(300, 200, 500, 24), true)    // at the end
  assert.equal(isNearBottom(280, 200, 500, 24), true)    // within the slack
  assert.equal(isNearBottom(100, 200, 500, 24), false)   // scrolled up to read
  assert.equal(isNearBottom(0, 200, 150, 24), true)      // shorter than the window
})
