export const DUP_BITS = 6;
export const SIMILAR_BITS = 12;
export const ASPECT_TOL = 0.02;

const N = 32;
// Match the Python reference's 15-decimal cosine coefficients exactly.
const COS = Array.from({ length: N }, (_, k) =>
  Float64Array.from({ length: N }, (_, n) => Number(((k === 0 ? Math.sqrt(1 / 32) : Math.sqrt(2 / 32) * Math.cos(Math.PI * (2 * n + 1) * k / 64))).toFixed(15))));

export function phashGray(gray, w, h) {
  const x = Array.from({ length: N }, () => new Float64Array(N));
  for (let i = 0; i < N; i++) {
    const y0 = Math.floor(i * h / N);
    const y1 = Math.max(Math.floor((i + 1) * h / N), y0 + 1);
    for (let j = 0; j < N; j++) {
      const x0 = Math.floor(j * w / N);
      const x1 = Math.max(Math.floor((j + 1) * w / N), x0 + 1);
      let sum = 0, count = 0;
      for (let y = y0; y < y1; y++) for (let xx = x0; xx < x1; xx++) {
        sum += gray[y * w + xx];
        count++;
      }
      x[i][j] = sum / count;
    }
  }
  const temp = Array.from({ length: N }, () => new Float64Array(N));
  const dct = Array.from({ length: N }, () => new Float64Array(N));
  for (let k = 0; k < N; k++) for (let j = 0; j < N; j++) {
    let sum = 0;
    for (let n = 0; n < N; n++) sum += COS[k][n] * x[n][j];
    temp[k][j] = sum;
  }
  for (let k = 0; k < N; k++) for (let l = 0; l < N; l++) {
    let sum = 0;
    for (let n = 0; n < N; n++) sum += temp[k][n] * COS[l][n];
    dct[k][l] = sum;
  }
  const band = [];
  for (let i = 0; i < 8; i++) for (let j = 0; j < 8; j++) band.push(dct[i][j]);
  const sorted = band.slice().sort((a, b) => a - b);
  const median = (sorted[31] + sorted[32]) / 2;
  let hash = 0n;
  for (const value of band) hash = (hash << 1n) | (value > median ? 1n : 0n);
  return hash.toString(16).padStart(16, '0');
}

export function hamming(a, b) {
  let value = BigInt(`0x${a}`) ^ BigInt(`0x${b}`);
  let count = 0;
  while (value) { value &= value - 1n; count++; }
  return count;
}

export function classify(a, b) {
  const sameSha = Boolean(a.sha) && Boolean(b.sha) && a.sha === b.sha;
  const distance = hamming(a.ph, b.ph);
  const aspectOk = a.h > 0 && b.h > 0 && Math.abs(a.w / a.h - b.w / b.h) / (b.w / b.h) <= ASPECT_TOL;
  if (sameSha || (distance <= DUP_BITS && aspectOk)) return 'duplicate';
  if (distance <= SIMILAR_BITS) return 'similar';
  return null;
}

export function findMatch(item, index) {
  let best = null;
  for (const entry of index) {
    const kind = classify(item, entry);
    if (kind === null) continue;
    const distance = hamming(item.ph, entry.ph);
    if (!best || (kind === 'duplicate' && best.kind !== 'duplicate') || (kind === best.kind && distance < best.distance)) {
      best = { kind, distance, id: entry.id };
    }
  }
  return best ? [best.kind, best.id] : null;
}

export async function imageInfo(file) {
  const bytes = await file.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const sha = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  try {
    const w = bitmap.width, h = bitmap.height;
    const scale = Math.min(1, 512 / Math.max(w, h));
    const sw = Math.max(1, Math.round(w * scale)), sh = Math.max(1, Math.round(h * scale));
    let canvas;
    if (typeof OffscreenCanvas !== 'undefined') canvas = new OffscreenCanvas(sw, sh);
    else { canvas = document.createElement('canvas'); canvas.width = sw; canvas.height = sh; }
    const context = canvas.getContext('2d', { willReadFrequently: true });
    context.drawImage(bitmap, 0, 0, sw, sh);
    const pixels = context.getImageData(0, 0, sw, sh).data;
    const gray = new Float64Array(sw * sh);
    for (let i = 0; i < gray.length; i++) gray[i] = 0.299 * pixels[i * 4] + 0.587 * pixels[i * 4 + 1] + 0.114 * pixels[i * 4 + 2];
    return { sha, ph: phashGray(gray, sw, sh), w, h };
  } finally {
    bitmap.close();
  }
}
