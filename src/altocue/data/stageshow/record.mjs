// Record a Stageshow stage to video, one fixed frame at a time.
//
// Stageshow's kit reads every clock through the page's globals, and its
// installVirtualClock() replaces them with a clock that moves only when it is
// stepped. This script injects that clock before the page loads, then for each
// frame flushes layout, screenshots the stage and steps exactly 1/fps. Every
// render is the same, beats land on known frames, nothing drops or doubles.
//
// It needs a Stageshow folder (for kit/clock.ts, esbuild and playwright-core,
// all installed by `npm install` there) and ffmpeg on PATH. Altocue calls it
// from `altocue stageshow record`; it can also run by hand:
//
//   node record.mjs --stageshow <dir> --url <page> --out ui.mp4
//        [--select '#example'] [--seconds 12] [--fps 30] [--width 1280]
//        [--scale 2] [--start 0] [--dark] [--still] [--chrome <path>]
//        [--background '#ffffff']
import { spawn } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'

const argv = process.argv.slice(2)
const opt = (name, fallback) => {
  const at = argv.indexOf(`--${name}`)
  return at >= 0 && at + 1 < argv.length ? argv[at + 1] : fallback
}
const has = (name) => argv.includes(`--${name}`)
const fail = (message) => { console.error(`record: ${message}`); process.exit(2) }

const dir = opt('stageshow')
const url = opt('url')
const out = opt('out')
if (!dir || !url || !out) fail('needs --stageshow, --url and --out')
const select = opt('select', '#example')
const fps = Number(opt('fps', 30))
const seconds = Number(opt('seconds', 12))
const width = Number(opt('width', 1280))
const scale = Number(opt('scale', 2))
const start = Number(opt('start', 0))
const background = opt('background', '')
const still = has('still')
for (const [name, value, low] of [['fps', fps, 1], ['seconds', seconds, 0.04], ['width', width, 200], ['scale', scale, 1], ['start', start, 0]]) {
  if (!Number.isFinite(value) || value < low) fail(`--${name} must be a number of at least ${low}`)
}
const clockFile = resolve(dir, 'kit/clock.ts')
if (!existsSync(clockFile)) fail(`no kit/clock.ts in ${dir}. Point --stageshow at the stageshow folder`)

const require = createRequire(resolve(dir, 'package.json'))
let esbuild, chromium
try { esbuild = require('esbuild') } catch { fail(`esbuild is missing. Run npm install in ${dir}`) }
try { ({ chromium } = require('playwright-core')) } catch { fail(`playwright-core is missing. Run npm install in ${dir}`) }

// The clock source is self-contained by design, so it can run as an init script.
const { code } = await esbuild.transform(readFileSync(clockFile, 'utf8'), { loader: 'ts', format: 'iife', globalName: '__altocueClock', target: 'es2020' })
// Playwright may wrap an init script in a function, so the bundle is called by its local name.
const init = `${code}\n;__altocueClock.installVirtualClock({ fps: ${fps}, epoch: 1767225600000 });`

const executablePath = opt('chrome', process.env.CHROME_PATH || undefined)
const browser = await chromium.launch({ executablePath, headless: true, args: ['--no-sandbox', '--hide-scrollbars', '--force-color-profile=srgb'] })
const page = await browser.newPage({ viewport: { width, height: 900 }, deviceScaleFactor: scale, colorScheme: has('dark') ? 'dark' : 'light', reducedMotion: still ? 'reduce' : 'no-preference' })
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
await page.addInitScript({ content: init })
await page.goto(url, { waitUntil: 'load' })
const stage = await page.waitForSelector(select, { state: 'visible', timeout: 30000 }).catch(() => null)
if (!stage) { await browser.close(); fail(`nothing matched ${select} at ${url}`) }
if (background) await page.evaluate((bg) => { document.documentElement.style.background = bg; document.body.style.background = bg }, background)

const clock = () => page.evaluate(async (frames) => {
  const c = window.__stageshow?.clock
  if (!c) throw new Error('the virtual clock did not install; is this a Stageshow page?')
  await c.flushLayout()
  if (frames) await c.step(frames)
  await c.settle()
  await c.flushLayout()
  return c.frame
}, 0)
const step = (frames) => page.evaluate(async (n) => {
  const c = window.__stageshow.clock
  await c.step(n)
  await c.flushLayout()
}, frames)

await page.evaluate(() => document.fonts?.ready)
await clock()
if (start > 0) await step(Math.round(start * fps))

const box = await stage.boundingBox()
const even = (n) => Math.max(2, Math.round(n * scale / 2) * 2)
const size = `${even(box.width)}x${even(box.height)}`
const frames = still ? 1 : Math.max(1, Math.round(seconds * fps))
const ffmpegArgs = still
  ? ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-c:v', 'png', '-i', '-', '-frames:v', '1', out]
  : ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-c:v', 'png', '-framerate', String(fps), '-i', '-',
     '-vf', `scale=${size.replace('x', ':')}:flags=lanczos,format=yuv420p`, '-c:v', 'libx264', '-preset', 'medium', '-crf', '14',
     '-r', String(fps), '-movflags', '+faststart', '-an', out]
const ffmpeg = spawn('ffmpeg', ffmpegArgs, { stdio: ['pipe', 'inherit', 'inherit'] })
const done = new Promise((ok, no) => ffmpeg.on('close', (status) => status === 0 ? ok() : no(new Error(`ffmpeg exited ${status}`))))
ffmpeg.on('error', () => fail('ffmpeg is not on PATH'))

const write = (buffer) => new Promise((ok) => (ffmpeg.stdin.write(buffer) ? ok() : ffmpeg.stdin.once('drain', ok)))
for (let i = 0; i < frames; i++) {
  await write(await stage.screenshot({ type: 'png', animations: 'allow', caret: 'initial' }))
  if (i < frames - 1) await step(1)
}
ffmpeg.stdin.end()
await done
await browser.close()
console.log(JSON.stringify({ out: resolve(out), frames, fps, seconds: frames / fps, size, errors }))
if (errors.length) process.exitCode = 1
