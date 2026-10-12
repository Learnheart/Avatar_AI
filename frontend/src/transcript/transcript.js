/**
 * The lines of the conversation on screen (pure: unit-tested, no DOM, no config).
 *
 * A transcript is a plain value, never changed in place: every function returns the
 * next one, or the same object when nothing changed (the panel redraws on a change).
 *
 *   { conversationId, lines: [{ speaker, text, cut }], running }
 *
 * `running` is true while the last line is the avatar's line of the turn in progress:
 * the next sentence is appended to it. A person line, a cut or a reset ends it.
 */

export const PERSON = 'person'
export const AVATAR = 'avatar'

/** An empty window showing no conversation. */
export function createTranscript() {
  return { conversationId: null, lines: [], running: false }
}

/** What the person said: a new line, and the start of a new turn. */
export function addPersonLine(transcript, text, maxLines) {
  const said = clean(text)
  if (!said) return transcript
  const lines = [...transcript.lines, { speaker: PERSON, text: said, cut: false }]
  return { ...transcript, lines: capLines(lines, maxLines), running: false }
}

/**
 * One sentence of the avatar: appended to its line of the running turn, or opening
 * that line. An empty sentence (an audio chunk without text) adds nothing.
 */
export function appendAvatarSentence(transcript, text, maxLines) {
  const sentence = clean(text)
  if (!sentence) return transcript
  const lines = [...transcript.lines]
  if (transcript.running) {
    const last = lines[lines.length - 1]
    lines[lines.length - 1] = { ...last, text: `${last.text} ${sentence}` }
  } else {
    lines.push({ speaker: AVATAR, text: sentence, cut: false })
  }
  return { ...transcript, lines: capLines(lines, maxLines), running: true }
}

/** The avatar was interrupted: its running line is marked as cut, no other line is. */
export function cutAvatarLine(transcript) {
  if (!transcript.running) return transcript
  const lines = [...transcript.lines]
  lines[lines.length - 1] = { ...lines[lines.length - 1], cut: true }
  return { ...transcript, lines, running: false }
}

/** Empty the window; the conversation it shows stays the same. */
export function resetTranscript(transcript) {
  return { ...transcript, lines: [], running: false }
}

/**
 * The backend named the conversation in progress (`null`: none). Another id than the
 * one on screen empties the lines, the same id keeps them.
 */
export function showConversation(transcript, conversationId) {
  const id = conversationId ?? null
  if (id === transcript.conversationId) return transcript
  return { ...resetTranscript(transcript), conversationId: id }
}

/** The last `maxLines` lines: the oldest are dropped. */
export function capLines(lines, maxLines) {
  const max = Math.max(1, Math.floor(maxLines) || 1)
  return lines.length > max ? lines.slice(lines.length - max) : lines
}

/**
 * True when a scrolled list shows its end, within `slackPx`: only then does a new
 * line scroll it, so a person who scrolled up to read is left where they are.
 */
export function isNearBottom(scrollTop, clientHeight, scrollHeight, slackPx) {
  return scrollHeight - scrollTop - clientHeight <= slackPx
}

function clean(text) {
  return typeof text === 'string' ? text.trim() : ''
}
