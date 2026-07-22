import FFT from "fft.js";

export interface Spectrogram {
  times: number[];
  freqs: number[];
  /** values[freqIndex][timeIndex] */
  values: number[][];
}

interface ComputeSpectrogramOptions {
  maxFreqHz?: number;
}

/** Symmetric (M[n] == M[M-1-n]) Tukey window, matching
 * scipy.signal.windows.tukey's default alpha=0.25. */
function tukeyWindow(length: number, alpha: number): number[] {
  if (length <= 1) return new Array(Math.max(length, 0)).fill(1);
  if (alpha <= 0) return new Array(length).fill(1);
  if (alpha >= 1) return hannWindow(length);

  const w = new Array(length).fill(1);
  const width = Math.floor((alpha * (length - 1)) / 2);
  for (let n = 0; n <= width; n++) {
    const taper = 0.5 * (1 + Math.cos(Math.PI * (-1 + (2 * n) / (alpha * (length - 1)))));
    w[n] = taper;
    w[length - 1 - n] = taper;
  }
  return w;
}

function hannWindow(length: number): number[] {
  if (length <= 1) return new Array(Math.max(length, 0)).fill(1);
  const w = new Array(length);
  for (let n = 0; n < length; n++) {
    w[n] = 0.5 - 0.5 * Math.cos((2 * Math.PI * n) / (length - 1));
  }
  return w;
}

function linspace(start: number, stop: number, num: number): number[] {
  if (num <= 0) return [];
  if (num === 1) return [start];
  const step = (stop - start) / (num - 1);
  return Array.from({ length: num }, (_, i) => start + step * i);
}

// Python's `%` is a floor-mod (always non-negative for a positive divisor);
// JS's `%` keeps the sign of the dividend, so this mirrors it explicitly.
function pymod(a: number, b: number): number {
  return ((a % b) + b) % b;
}

function zScoreRows(values: number[][]): void {
  for (const row of values) {
    const n = row.length;
    let mean = 0;
    for (const v of row) mean += v;
    mean /= n;
    let variance = 0;
    for (const v of row) variance += (v - mean) * (v - mean);
    variance /= n;
    const std = Math.sqrt(variance);
    for (let i = 0; i < n; i++) {
      row[i] = std > 0 ? (row[i] - mean) / std : 0;
    }
  }
}

function gaussianKernel1d(sigma: number): number[] {
  const radius = Math.floor(4 * sigma + 0.5); // scipy's default truncate=4.0
  const kernel = new Array(2 * radius + 1);
  let sum = 0;
  for (let i = -radius; i <= radius; i++) {
    const w = Math.exp(-0.5 * (i / sigma) * (i / sigma));
    kernel[i + radius] = w;
    sum += w;
  }
  for (let i = 0; i < kernel.length; i++) kernel[i] /= sum;
  return kernel;
}

// scipy.ndimage's default boundary mode ("reflect" == half-sample symmetric:
// ...c b a | a b c... -- the edge sample is mirrored, not repeated once and
// dropped).
function reflectIndex(i: number, n: number): number {
  if (n === 1) return 0;
  while (i < 0 || i >= n) {
    if (i < 0) i = -i - 1;
    if (i >= n) i = 2 * n - 1 - i;
  }
  return i;
}

/** In-place separable 2D Gaussian blur, matching scipy.ndimage.gaussian_filter
 * with a scalar sigma (applied to both axes). */
function gaussianBlur2d(values: number[][], sigma: number): void {
  const kernel = gaussianKernel1d(sigma);
  const radius = (kernel.length - 1) / 2;
  const rows = values.length;
  const cols = rows > 0 ? values[0].length : 0;

  const tmp: number[][] = Array.from({ length: rows }, () => new Array(cols).fill(0));
  for (let c = 0; c < cols; c++) {
    for (let r = 0; r < rows; r++) {
      let acc = 0;
      for (let k = -radius; k <= radius; k++) {
        acc += values[reflectIndex(r + k, rows)][c] * kernel[k + radius];
      }
      tmp[r][c] = acc;
    }
  }

  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      let acc = 0;
      for (let k = -radius; k <= radius; k++) {
        acc += tmp[r][reflectIndex(c + k, cols)] * kernel[k + radius];
      }
      values[r][c] = acc;
    }
  }
}

/** Ports client_ictal.py's per-channel spectrogram panel (ei_press_func /
 * hfer_press_func / fullband_press_func all share this exact recipe):
 *   scipy.signal.spectrogram(x, fs, nperseg=0.5*fs, noverlap=0.9*0.5*fs,
 *     nfft=1024, mode='magnitude')
 *   -> per-frequency-row z-score
 *   -> gaussian_filter(sigma=2)
 *   -> crop to the first `spec_f_nums = len(f) * maxFreqHz / f.max()` rows.
 *
 * The STFT's window-normalization scale factor (scipy's default
 * scaling='density') is a positive constant per bin and is dropped here: it
 * cancels out exactly under the following z-score, so skipping it saves a
 * pass without changing the output. */
export function computeSpectrogram(
  samples: Float32Array | number[],
  fs: number,
  options: ComputeSpectrogramOptions = {},
): Spectrogram {
  const maxFreqHz = options.maxFreqHz ?? 300;
  const nperseg = Math.floor(0.5 * fs);
  const noverlap = Math.floor(0.9 * 0.5 * fs);
  const nstep = nperseg - noverlap;
  const nfft = 1024;

  const x = Array.from(samples);
  const n = x.length;

  if (n < nperseg || nstep <= 0) {
    return { times: [], freqs: [], values: [] };
  }

  // scipy.signal.spectrogram pads with trailing zeros so every segment is a
  // full nperseg window (padded=True, boundary=None).
  const nadd = pymod(pymod(-(n - nperseg), nstep), nperseg);
  const padded = nadd > 0 ? x.concat(new Array(nadd).fill(0)) : x;

  const numSegments = 1 + Math.floor((padded.length - nperseg) / nstep);
  const window = tukeyWindow(nperseg, 0.25);

  const fft = new FFT(nfft);
  const numFreqBins = nfft / 2 + 1;
  const values: number[][] = Array.from({ length: numFreqBins }, () => new Array(numSegments).fill(0));
  const times: number[] = new Array(numSegments);

  const complexIn = fft.createComplexArray();
  const complexOut = fft.createComplexArray();

  for (let seg = 0; seg < numSegments; seg++) {
    const start = seg * nstep;
    times[seg] = (nperseg / 2 + seg * nstep) / fs;

    let mean = 0;
    for (let i = 0; i < nperseg; i++) mean += padded[start + i];
    mean /= nperseg;

    for (let i = 0; i < nfft; i++) {
      const sample = i < nperseg ? (padded[start + i] - mean) * window[i] : 0;
      complexIn[i * 2] = sample;
      complexIn[i * 2 + 1] = 0;
    }

    fft.transform(complexOut, complexIn);

    for (let f = 0; f < numFreqBins; f++) {
      const re = complexOut[f * 2];
      const im = complexOut[f * 2 + 1];
      values[f][seg] = Math.sqrt(re * re + im * im);
    }
  }

  zScoreRows(values);
  gaussianBlur2d(values, 2);

  const freqMax = fs / 2; // f.max() from scipy's rfftfreq(nfft, 1/fs)
  const specFreqNums = Math.min(numFreqBins, Math.floor((numFreqBins * maxFreqHz) / freqMax));

  return {
    times,
    freqs: linspace(0, maxFreqHz, specFreqNums),
    values: values.slice(0, specFreqNums),
  };
}
