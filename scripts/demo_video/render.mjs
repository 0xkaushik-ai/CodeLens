// Renders scripts/demo_video/demo.html to a 30 s, 1080p, 30 fps MP4.
//
//   node scripts/demo_video/render.mjs [data.json] [out.mp4]
//
// Every frame is drawn by calling window.renderAt(t) and photographed with
// Chrome, then piped to ffmpeg, so timing is exact and nothing depends on
// how fast the machine is. Needs Google Chrome and ffmpeg.
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const HERE = dirname(fileURLToPath(import.meta.url));
const dataPath = process.argv[2] ?? join(HERE, 'out', 'data.json');
const outPath = process.argv[3] ?? join(HERE, 'out', 'codelens-demo.mp4');
const FPS = 30, SECONDS = 30, W = 1920, H = 1080;

// JSON inside a <script>: keep "</script>" in code samples from closing the tag
const data = readFileSync(dataPath, 'utf8').replace(/<\//g, '<\\/');
const html = readFileSync(join(HERE, 'demo.html'), 'utf8').replace('/*DATA*/null', data);

const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
await page.setContent(html, { waitUntil: 'load' });

const ffmpeg = spawn('ffmpeg', [
  '-y', '-loglevel', 'error',
  '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-',
  '-c:v', 'libx264', '-preset', 'slow', '-crf', '16', '-pix_fmt', 'yuv420p',
  '-movflags', '+faststart', outPath,
], { stdio: ['pipe', 'inherit', 'inherit'] });

const total = FPS * SECONDS;
for (let i = 0; i < total; i++) {
  await page.evaluate(t => window.renderAt(t), i / FPS);
  const png = await page.screenshot({ type: 'png' });
  if (!ffmpeg.stdin.write(png)) await new Promise(r => ffmpeg.stdin.once('drain', r));
  if (i % 90 === 0) process.stdout.write(`frame ${i}/${total}\n`);
}
ffmpeg.stdin.end();
await new Promise((resolve, reject) => ffmpeg.on('close', code => code === 0 ? resolve() : reject(new Error(`ffmpeg exited ${code}`))));
await browser.close();
console.log(`wrote ${outPath}`);
