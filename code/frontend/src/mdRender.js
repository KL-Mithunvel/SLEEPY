import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ breaks: true })

export function renderMd(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(text))
}
