// Matches app/services/edf.py's pack_edf_window (WINDOW_MAGIC = "BQEDFW01"):
// 8-byte magic, a fixed scalar header, a UTF-8 JSON block for the one
// variable-length piece (channel names), then a raw channel-major float32
// sample buffer.
const MAGIC = "BQEDFW01";
const HEADER_BYTES = 8 + 8 + 8 + 8 + 1 + 4 + 4 + 4 + 4 + 4; // magic + fs/start/end + filtered + bandLow/bandHigh + counts

export interface ParsedEdfWindow {
  fs: number;
  start: number;
  end: number;
  filtered: boolean;
  bandLow: number | null;
  bandHigh: number | null;
  channels: string[];
  /** data[channelIndex] is that channel's samples for the window. */
  data: Float32Array[];
}

export function parseEdfWindowBinary(buf: ArrayBuffer): ParsedEdfWindow {
  if (buf.byteLength < HEADER_BYTES) {
    throw new Error("EDF window binary too short: missing header");
  }

  const magic = new TextDecoder("ascii").decode(new Uint8Array(buf, 0, 8));
  if (magic !== MAGIC) {
    throw new Error(`EDF window binary has bad magic: expected "${MAGIC}", got "${magic}"`);
  }

  const header = new DataView(buf, 8, HEADER_BYTES - 8);
  let offset = 0;
  const fs = header.getFloat64(offset, true);
  offset += 8;
  const start = header.getFloat64(offset, true);
  offset += 8;
  const end = header.getFloat64(offset, true);
  offset += 8;
  const filtered = header.getUint8(offset) !== 0;
  offset += 1;
  const bandLow = header.getFloat32(offset, true);
  offset += 4;
  const bandHigh = header.getFloat32(offset, true);
  offset += 4;
  const numChannels = header.getUint32(offset, true);
  offset += 4;
  const numSamples = header.getUint32(offset, true);
  offset += 4;
  const channelsJsonLen = header.getUint32(offset, true);

  const channelsJsonOffset = HEADER_BYTES;
  const channelsJsonEnd = channelsJsonOffset + channelsJsonLen;
  if (buf.byteLength < channelsJsonEnd) {
    throw new Error("EDF window binary too short: truncated channel list");
  }
  const channels: string[] = JSON.parse(
    new TextDecoder("utf-8").decode(new Uint8Array(buf, channelsJsonOffset, channelsJsonLen)),
  );

  const dataOffset = channelsJsonEnd;
  const dataBytes = numChannels * numSamples * 4;
  if (buf.byteLength < dataOffset + dataBytes) {
    throw new Error("EDF window binary too short: truncated sample data");
  }

  const flat = new Float32Array(buf.slice(dataOffset, dataOffset + dataBytes));
  const data: Float32Array[] = [];
  for (let c = 0; c < numChannels; c++) {
    data.push(flat.subarray(c * numSamples, (c + 1) * numSamples));
  }

  return {
    fs,
    start,
    end,
    filtered,
    bandLow: filtered ? bandLow : null,
    bandHigh: filtered ? bandHigh : null,
    channels,
    data,
  };
}
