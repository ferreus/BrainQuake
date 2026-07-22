// Matches app/services/surface.py's surface_to_binary: 8-byte magic,
// then <II vertex/face counts, then raw little-endian float32 vertex
// and uint32 face buffers.
const MAGIC = "BQSURF01";
const HEADER_BYTES = 8 + 4 + 4;

export interface ParsedSurface {
  vertices: Float32Array;
  faces: Uint32Array;
}

export function parseSurfaceBinary(buf: ArrayBuffer): ParsedSurface {
  if (buf.byteLength < HEADER_BYTES) {
    throw new Error("Surface binary too short: missing header");
  }

  const magic = new TextDecoder("ascii").decode(new Uint8Array(buf, 0, 8));
  if (magic !== MAGIC) {
    throw new Error(`Surface binary has bad magic: expected "${MAGIC}", got "${magic}"`);
  }

  const header = new DataView(buf, 8, 8);
  const vertexCount = header.getUint32(0, true);
  const faceCount = header.getUint32(4, true);

  const verticesOffset = HEADER_BYTES;
  const verticesBytes = vertexCount * 3 * 4;
  const facesOffset = verticesOffset + verticesBytes;
  const facesBytes = faceCount * 3 * 4;

  if (buf.byteLength < facesOffset + facesBytes) {
    throw new Error("Surface binary too short: truncated vertex/face data");
  }

  const vertices = new Float32Array(buf.slice(verticesOffset, verticesOffset + verticesBytes));
  const faces = new Uint32Array(buf.slice(facesOffset, facesOffset + facesBytes));

  return { vertices, faces };
}
