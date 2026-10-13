/**
 * Transcript window — what the person and the avatar said in the conversation in
 * progress, and the "Cuộc trò chuyện mới" button. A drop-down: pressing its header
 * folds the lines away and brings them back.
 *
 * The markup is in index.html (#transcript-panel). The lines are kept by
 * transcript.js; this class only draws them. The texts come from speech-to-text and
 * from the LLM: they are only ever written with textContent.
 */

import { TRANSCRIPT_MAX_LINES, TRANSCRIPT_SCROLL_SLACK_PX } from '../config.js'
import { Collapsible } from '../ui/collapsible.js'
import {
  PERSON,
  addPersonLine,
  appendAvatarSentence,
  createTranscript,
  cutAvatarLine,
  isNearBottom,
  showConversation,
} from './transcript.js'

const SPEAKER_LABELS = { person: 'Bạn', avatar: 'Avatar' }
const EMPTY_TEXT = 'Chưa có lời thoại nào.'
const CUT_TEXT = 'bị ngắt'

export class TranscriptPanel {
  constructor() {
    this._transcript = createTranscript()
    this._list = document.getElementById('transcript-list')
    this._reset = document.getElementById('transcript-reset')
  }

  /**
   * `onReset` is called when the person presses "Cuộc trò chuyện mới". The window is
   * not emptied here: the `conversation` message that follows does it.
   */
  init(onReset) {
    this._reset.addEventListener('click', () => onReset())
    // A drop-down: its header stays, the lines fold away. Opened again, it shows the
    // newest line.
    new Collapsible({
      header: document.getElementById('transcript-toggle'),
      body: this._list,
      key: 'avatar.panel.transcript',
      open: true,
      onChange: (open) => { if (open) this._list.scrollTop = this._list.scrollHeight },
    })
    this._render()
  }

  addPersonLine(text) {
    this._update(addPersonLine(this._transcript, text, TRANSCRIPT_MAX_LINES))
  }

  appendAvatarSentence(text) {
    this._update(appendAvatarSentence(this._transcript, text, TRANSCRIPT_MAX_LINES))
  }

  cutAvatarLine() {
    this._update(cutAvatarLine(this._transcript))
  }

  showConversation(conversationId) {
    this._update(showConversation(this._transcript, conversationId))
  }

  // ── Rendering ───────────────────────────────────────────────────────────

  _update(next) {
    if (next === this._transcript) return
    this._transcript = next
    this._render()
  }

  _render() {
    const list = this._list
    // Read before redrawing: follow the newest line only if the end was in view.
    const follow = isNearBottom(
      list.scrollTop, list.clientHeight, list.scrollHeight, TRANSCRIPT_SCROLL_SLACK_PX,
    )
    const lines = this._transcript.lines
    list.replaceChildren(...(lines.length ? lines.map((line) => this._row(line)) : [this._empty()]))
    if (follow) list.scrollTop = list.scrollHeight
  }

  _row(line) {
    const row = document.createElement('li')
    row.className = `transcript-line ${line.speaker === PERSON ? 'person' : 'avatar'}`
    row.classList.toggle('cut', line.cut)

    const speaker = document.createElement('div')
    speaker.className = 'transcript-speaker'
    speaker.textContent = SPEAKER_LABELS[line.speaker]
    if (line.cut) {
      const mark = document.createElement('span')
      mark.className = 'transcript-cut-mark'
      mark.textContent = CUT_TEXT
      speaker.append(mark)
    }

    const text = document.createElement('div')
    text.className = 'transcript-text'
    text.textContent = line.cut ? `${line.text} …` : line.text

    row.append(speaker, text)
    return row
  }

  _empty() {
    const row = document.createElement('li')
    row.className = 'transcript-empty'
    row.textContent = EMPTY_TEXT
    return row
  }
}
