// WebGS progressive viewer — modules B, C and D of the serving plan.
//
// B: fetch scene.manifest, then base.bin first (first render) and refinement
//    chunks additively after.
// C: greedy chunk scheduler with six policies for the paper's baselines:
//      naive      manifest order, no adaptation
//      bandwidth  order by gain/bytes, serial fetches, no viewport/GPU input
//      viewport   priority = visualGain x viewWeight / bytes, ignores GPU
//      gpu        order by gain only, pauses refinement on frame-time spikes
//      joint      bandwidth + viewport + GPU/memory (default)
//      full       every chunk requested immediately (full-download baseline)
// D: chunks are appended to the worker's buffers and the sort/render loop
//    picks them up; no full-scene materialization ever happens.
//
// URL parameters:
//   url      manifest.json (required)
//   policy   one of the six above (default joint)
//   maxRows  cap on resident rows (GPU-memory stress experiments)
//
// Telemetry: window.__webgsTelemetry() returns the full trace; the HUD and
// the console both expose it so the paper's experiment harness can pull
// TTFR, bytes-over-time, chunk arrivals and frame stats per policy.
// In a deployed setup each load also POSTs one anonymous serving_load event
// (first render, all-chunks-settled, or failure) to /api/telemetry/event.

import { createWorker } from "./progressive-worker.js";

const params = new URLSearchParams(location.search);
const POLICY = (params.get("policy") || "joint").toLowerCase();
const MAX_ROWS = Number(params.get("maxRows") || 0) || Infinity;

const VALID = new Set(["naive", "bandwidth", "viewport", "gpu", "joint", "full"]);
if (!VALID.has(POLICY)) {
  document.getElementById("message").innerText = `Unknown policy: ${POLICY}`;
  throw new Error(`Unknown policy: ${POLICY}`);
}

/* ---------- cameras + matrices (same conventions as megs-viewer) ---------- */

let cameras = [{
    id: 0, img_name: "default", width: 1280, height: 720,
    position: [0.0, 0.0, 4.2],
    rotation: [
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
    ],
    fy: 1160, fx: 1160,
}];

let camera = cameras[0];

function getProjectionMatrix(fx, fy, width, height) {
    const znear = 0.2;
    const zfar = 200;
    return [
        [(2 * fx) / width, 0, 0, 0],
        [0, -(2 * fy) / height, 0, 0],
        [0, 0, zfar / (zfar - znear), 1],
        [0, 0, -(zfar * znear) / (zfar - znear), 0],
    ].flat();
}

function getViewMatrix(camera) {
    const R = camera.rotation.flat();
    const t = camera.position;
    return [
        [R[0], R[1], R[2], 0],
        [R[3], R[4], R[5], 0],
        [R[6], R[7], R[8], 0],
        [
            -t[0] * R[0] - t[1] * R[3] - t[2] * R[6],
            -t[0] * R[1] - t[1] * R[4] - t[2] * R[7],
            -t[0] * R[2] - t[1] * R[5] - t[2] * R[8],
            1,
        ],
    ].flat();
}

function multiply4(a, b) {
    return [
        b[0] * a[0] + b[1] * a[4] + b[2] * a[8] + b[3] * a[12],
        b[0] * a[1] + b[1] * a[5] + b[2] * a[9] + b[3] * a[13],
        b[0] * a[2] + b[1] * a[6] + b[2] * a[10] + b[3] * a[14],
        b[0] * a[3] + b[1] * a[7] + b[2] * a[11] + b[3] * a[15],
        b[4] * a[0] + b[5] * a[4] + b[6] * a[8] + b[7] * a[12],
        b[4] * a[1] + b[5] * a[5] + b[6] * a[9] + b[7] * a[13],
        b[4] * a[2] + b[5] * a[6] + b[6] * a[10] + b[7] * a[14],
        b[4] * a[3] + b[5] * a[7] + b[6] * a[11] + b[7] * a[15],
        b[8] * a[0] + b[9] * a[4] + b[10] * a[8] + b[11] * a[12],
        b[8] * a[1] + b[9] * a[5] + b[10] * a[9] + b[11] * a[13],
        b[8] * a[2] + b[9] * a[6] + b[10] * a[10] + b[11] * a[14],
        b[8] * a[3] + b[9] * a[7] + b[10] * a[11] + b[11] * a[15],
        b[12] * a[0] + b[13] * a[4] + b[14] * a[8] + b[15] * a[12],
        b[12] * a[1] + b[13] * a[5] + b[14] * a[9] + b[15] * a[13],
        b[12] * a[2] + b[13] * a[6] + b[14] * a[10] + b[15] * a[14],
        b[12] * a[3] + b[13] * a[7] + b[14] * a[11] + b[15] * a[15],
    ];
}

function invert4(a) {
    let b00 = a[0] * a[5] - a[1] * a[4];
    let b01 = a[0] * a[6] - a[2] * a[4];
    let b02 = a[0] * a[7] - a[3] * a[4];
    let b03 = a[1] * a[6] - a[2] * a[5];
    let b04 = a[1] * a[7] - a[3] * a[5];
    let b05 = a[2] * a[7] - a[3] * a[6];
    let b06 = a[8] * a[13] - a[9] * a[12];
    let b07 = a[8] * a[14] - a[10] * a[12];
    let b08 = a[8] * a[15] - a[11] * a[12];
    let b09 = a[9] * a[14] - a[10] * a[13];
    let b10 = a[9] * a[15] - a[11] * a[13];
    let b11 = a[10] * a[15] - a[11] * a[14];
    let det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
    if (!det) return null;
    return [
        (a[5] * b11 - a[6] * b10 + a[7] * b09) / det,
        (a[2] * b10 - a[1] * b11 - a[3] * b09) / det,
        (a[13] * b05 - a[14] * b04 + a[15] * b03) / det,
        (a[10] * b04 - a[9] * b05 - a[11] * b03) / det,
        (a[6] * b08 - a[4] * b11 - a[7] * b07) / det,
        (a[0] * b11 - a[2] * b08 + a[3] * b07) / det,
        (a[14] * b02 - a[12] * b05 - a[15] * b01) / det,
        (a[8] * b05 - a[10] * b02 + a[11] * b01) / det,
        (a[4] * b10 - a[5] * b08 + a[7] * b06) / det,
        (a[1] * b08 - a[0] * b10 - a[3] * b06) / det,
        (a[12] * b04 - a[13] * b02 + a[15] * b00) / det,
        (a[9] * b02 - a[8] * b04 - a[11] * b00) / det,
        (a[5] * b07 - a[4] * b09 - a[6] * b06) / det,
        (a[0] * b09 - a[1] * b07 + a[2] * b06) / det,
        (a[13] * b01 - a[12] * b03 - a[14] * b00) / det,
        (a[8] * b03 - a[9] * b01 + a[10] * b00) / det,
    ];
}

function rotate4(a, rad, x, y, z) {
    let len = Math.hypot(x, y, z);
    x /= len; y /= len; z /= len;
    let s = Math.sin(rad), c = Math.cos(rad), t = 1 - c;
    let b00 = x * x * t + c, b01 = y * x * t + z * s, b02 = z * x * t - y * s;
    let b10 = x * y * t - z * s, b11 = y * y * t + c, b12 = z * y * t + x * s;
    let b20 = x * z * t + y * s, b21 = y * z * t - x * s, b22 = z * z * t + c;
    return [
        a[0] * b00 + a[4] * b01 + a[8] * b02, a[1] * b00 + a[5] * b01 + a[9] * b02,
        a[2] * b00 + a[6] * b01 + a[10] * b02, a[3] * b00 + a[7] * b01 + a[11] * b02,
        a[0] * b10 + a[4] * b11 + a[8] * b12, a[1] * b10 + a[5] * b11 + a[9] * b12,
        a[2] * b10 + a[6] * b11 + a[10] * b12, a[3] * b10 + a[7] * b11 + a[11] * b12,
        a[0] * b20 + a[4] * b21 + a[8] * b22, a[1] * b20 + a[5] * b21 + a[9] * b22,
        a[2] * b20 + a[6] * b21 + a[10] * b22, a[3] * b20 + a[7] * b21 + a[11] * b22,
        ...a.slice(12, 16),
    ];
}

function translate4(a, x, y, z) {
    return [
        ...a.slice(0, 12),
        a[0] * x + a[4] * y + a[8] * z + a[12],
        a[1] * x + a[5] * y + a[9] * z + a[13],
        a[2] * x + a[6] * y + a[10] * z + a[14],
        a[3] * x + a[7] * y + a[11] * z + a[15],
    ];
}

/* ---------- shaders (SH pipeline, same as megs-viewer) ---------- */

const vertexShaderSource = `
#version 300 es
precision highp float;
precision highp int;

uniform highp usampler2D u_texture;
uniform highp sampler2D u_sh_texture;
uniform mat4 projection, view;
uniform vec2 focal;
uniform vec2 viewport;
uniform vec3 camPos;

in vec2 position;
in int index;

out vec4 vColor;
out vec2 vPosition;

const float SH_C0 = 0.28209479177387814;
const float SH_C1 = 0.4886025119029199;
const float SH_C2[5] = float[5](1.0925484305920792, -1.0925484305920792, 0.31539156525252005, -1.0925484305920792, 0.5462742152960396);
const float SH_C3[7] = float[7](-0.5900435899266435, 2.890611442640554, -0.4570457994644658, 0.3731763325901154, -0.4570457994644658, 1.445305721320277, -0.5900435899266435);

float computeSHChannel(vec3 dir, int channel, int vertexIndex) {
    int texWidth = 8192;
    float sh[48];
    for (int i = 0; i < 48; i++) {
        int globalPixelIndex = i / 4;
        int texX = (vertexIndex * 12 + globalPixelIndex) % texWidth;
        int texY = (vertexIndex * 12 + globalPixelIndex) / texWidth;
        ivec2 texCoord = ivec2(texX, texY);
        vec4 pixel = texelFetch(u_sh_texture, texCoord, 0);
        int channelInPixel = i % 4;
        sh[i] = pixel[channelInPixel];
    }
    float shCoeffs[16];
    if (channel == 0) {
        shCoeffs[0] = sh[0];
        shCoeffs[1] = sh[3]; shCoeffs[2] = sh[4]; shCoeffs[3] = sh[5];
        shCoeffs[4] = sh[6]; shCoeffs[5] = sh[7]; shCoeffs[6] = sh[8];
        shCoeffs[7] = sh[9]; shCoeffs[8] = sh[10];
        shCoeffs[9] = sh[11]; shCoeffs[10] = sh[12]; shCoeffs[11] = sh[13];
        shCoeffs[12] = sh[14]; shCoeffs[13] = sh[15]; shCoeffs[14] = sh[16]; shCoeffs[15] = sh[17];
    } else if (channel == 1) {
        shCoeffs[0] = sh[1];
        shCoeffs[1] = sh[18]; shCoeffs[2] = sh[19]; shCoeffs[3] = sh[20];
        shCoeffs[4] = sh[21]; shCoeffs[5] = sh[22]; shCoeffs[6] = sh[23];
        shCoeffs[7] = sh[24]; shCoeffs[8] = sh[25];
        shCoeffs[9] = sh[26]; shCoeffs[10] = sh[27]; shCoeffs[11] = sh[28];
        shCoeffs[12] = sh[29]; shCoeffs[13] = sh[30]; shCoeffs[14] = sh[31]; shCoeffs[15] = sh[32];
    } else {
        shCoeffs[0] = sh[2];
        shCoeffs[1] = sh[33]; shCoeffs[2] = sh[34]; shCoeffs[3] = sh[35];
        shCoeffs[4] = sh[36]; shCoeffs[5] = sh[37]; shCoeffs[6] = sh[38];
        shCoeffs[7] = sh[39]; shCoeffs[8] = sh[40];
        shCoeffs[9] = sh[41]; shCoeffs[10] = sh[42]; shCoeffs[11] = sh[43];
        shCoeffs[12] = sh[44]; shCoeffs[13] = sh[45]; shCoeffs[14] = sh[46]; shCoeffs[15] = sh[47];
    }
    float x = dir.x, y = dir.y, z = dir.z;
    float result = SH_C0 * shCoeffs[0];
    result = result - SH_C1 * y * shCoeffs[1] + SH_C1 * z * shCoeffs[2] - SH_C1 * x * shCoeffs[3];
    float xx = x * x, yy = y * y, zz = z * z;
    float xy = x * y, yz = y * z, xz = x * z;
    result = result +
        SH_C2[0] * xy * shCoeffs[4] + SH_C2[1] * yz * shCoeffs[5] +
        SH_C2[2] * (2.0 * zz - xx - yy) * shCoeffs[6] +
        SH_C2[3] * xz * shCoeffs[7] + SH_C2[4] * (xx - yy) * shCoeffs[8];
    result = result +
        SH_C3[0] * y * (3.0 * xx - yy) * shCoeffs[9] +
        SH_C3[1] * xy * z * shCoeffs[10] +
        SH_C3[2] * y * (4.0 * zz - xx - yy) * shCoeffs[11] +
        SH_C3[3] * z * (2.0 * zz - 3.0 * xx - 3.0 * yy) * shCoeffs[12] +
        SH_C3[4] * x * (4.0 * zz - xx - yy) * shCoeffs[13] +
        SH_C3[5] * z * (xx - yy) * shCoeffs[14] +
        SH_C3[6] * x * (xx - 3.0 * yy) * shCoeffs[15];
    return max(result+0.5, 0.0);
}

void main() {
    uvec4 cen = texelFetch(u_texture, ivec2((uint(index) & 0x3ffu) << 1, uint(index) >> 10), 0);
    vec4 cam = view * vec4(uintBitsToFloat(cen.xyz), 1);
    vec4 pos2d = projection * cam;

    float clip = 1.2 * pos2d.w;
    if (pos2d.z < -clip || pos2d.x < -clip || pos2d.x > clip || pos2d.y < -clip || pos2d.y > clip) {
        gl_Position = vec4(0.0, 0.0, 2.0, 1.0);
        return;
    }

    uvec4 cov = texelFetch(u_texture, ivec2(((uint(index) & 0x3ffu) << 1) | 1u, uint(index) >> 10), 0);
    vec2 u1 = unpackHalf2x16(cov.x), u2 = unpackHalf2x16(cov.y), u3 = unpackHalf2x16(cov.z);
    mat3 Vrk = mat3(u1.x, u1.y, u2.x, u1.y, u2.y, u3.x, u2.x, u3.x, u3.y);

    mat3 J = mat3(
        focal.x / cam.z, 0., -(focal.x * cam.x) / (cam.z * cam.z),
        0., -focal.y / cam.z, (focal.y * cam.y) / (cam.z * cam.z),
        0., 0., 0.
    );
    mat3 T = transpose(mat3(view)) * J;
    mat3 cov2d = transpose(T) * Vrk * T;

    float mid = (cov2d[0][0] + cov2d[1][1]) / 2.0;
    float radius = length(vec2((cov2d[0][0] - cov2d[1][1]) / 2.0, cov2d[0][1]));
    float lambda1 = mid + radius, lambda2 = mid - radius;

    if(lambda2 < 0.0) return;
    vec2 diagonalVector = normalize(vec2(cov2d[0][1], lambda1 - cov2d[0][0]));
    vec2 majorAxis = min(sqrt(2.0 * lambda1), 1024.0) * diagonalVector;
    vec2 minorAxis = min(sqrt(2.0 * lambda2), 1024.0) * vec2(diagonalVector.y, -diagonalVector.x);

    vec3 center_world = uintBitsToFloat(cen.xyz);
    vec3 view_dir = normalize(camPos - center_world);

    float r = computeSHChannel(view_dir, 0, index);
    float g = computeSHChannel(view_dir, 1, index);
    float b = computeSHChannel(view_dir, 2, index);

    float alpha = float((cov.w >> 24) & 0xffu) / 255.0;
    vColor = vec4(r, g, b, alpha);
    vPosition = position;

    vec2 vCenter = vec2(pos2d) / pos2d.w;
    gl_Position = vec4(
        vCenter
        + position.x * majorAxis / viewport
        + position.y * minorAxis / viewport, 0.0, 1.0);
}
`.trim();

const fragmentShaderSource = `
#version 300 es
precision highp float;
in vec4 vColor;
in vec2 vPosition;
out vec4 fragColor;
void main () {
    float A = -dot(vPosition, vPosition);
    if (A < -4.0) discard;
    float B = exp(A) * vColor.a;
    fragColor = vec4(B * vColor.rgb, B);
}
`.trim();

/* ---------- telemetry ---------- */

const telemetry = {
    policy: POLICY,
    startedAt: 0,
    ttfrMs: null,               // time to first rendered frame
    manifestBytes: 0,
    chunkEvents: [],            // {id, startMs, endMs, bytes, rows}
    samples: [],                // {tMs, bytes, rows, fps} sampled periodically
    frameTimes: [],             // last N frame durations (ms)
    maxRowsCap: Number.isFinite(MAX_ROWS) ? MAX_ROWS : null,
    totalBytes: 0,
    totalRows: 0,
    sceneRows: 0,
    chunkFailures: 0,
};
window.__webgsTelemetry = () => JSON.parse(JSON.stringify(telemetry));

function fmtBytes(b) {
    if (!Number.isFinite(b) || b <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0, v = b;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

/* ---------- deployment telemetry beacon (serving plan P1) ----------
 * Reports one anonymous serving_load event to /api/telemetry/event at
 * first render, when all chunks settle, and on failure. Fire-and-forget:
 * telemetry problems must never affect the viewer. */

let clientLoadedBeaconed = false;
let clientFinishedBeaconed = false;

function p95FrameMs() {
    const sorted = [...telemetry.frameTimes].sort((a, b) => a - b);
    return sorted.length ? sorted[Math.floor(sorted.length * 0.95)] : null;
}

function sendLoadBeacon(extra) {
    const payload = {
        type: "serving_load",
        client_id: clientId(),
        policy: POLICY,
        ttfr_ms: telemetry.ttfrMs,
        load_ms: Math.round(performance.now() - telemetry.startedAt),
        bytes: telemetry.totalBytes,
        budget_bytes: telemetry.totalBudgetBytes || null,
        rows: telemetry.totalRows,
        scene_rows: telemetry.sceneRows,
        chunks_done: telemetry.chunkEvents.length,
        chunk_failures: telemetry.chunkFailures,
        p95_frame_ms: p95FrameMs(),
        ...extra,
    };
    try {
        fetch("/api/telemetry/event", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            keepalive: true,
        }).catch(() => {});
    } catch { /* ignore */ }
}

function clientId() {
    try {
        let id = localStorage.getItem("webgs_client_id");
        if (!id) {
            id = crypto.randomUUID().replaceAll("-", "").slice(0, 24);
            localStorage.setItem("webgs_client_id", id);
        }
        return id;
    } catch {
        return "anon";
    }
}

/* ---------- main ---------- */

let viewMatrix = getViewMatrix(camera);
let defaultViewMatrix = viewMatrix.slice();
let projectionMatrix;
let vertexCount = 0;
let downsample = 1;

const hud = document.getElementById("hud");

async function main() {
    const manifestUrl = params.get("url");
    if (!manifestUrl) throw new Error("Missing ?url=<manifest.json>");

    telemetry.startedAt = performance.now();

    const manifestResp = await fetch(manifestUrl);
    if (manifestResp.status !== 200) throw new Error(`${manifestResp.status} unable to load manifest`);
    const manifest = await manifestResp.json();
    telemetry.manifestBytes = Number(manifestResp.headers.get("content-length") || 0);
    telemetry.sceneRows = manifest.vertexCount;
    // Chunk URLs are relative to the manifest's directory, not the document.
    const manifestDir = new URL(".", new URL(manifestUrl, location.href)).href;
    for (const chunk of manifest.chunks) {
      chunk.url = new URL(chunk.url, manifestDir).href;
      if (chunk.shUrl) chunk.shUrl = new URL(chunk.shUrl, manifestDir).href;
    }

    if (manifest.format !== "splat-rows-v1") throw new Error(`Unsupported manifest format: ${manifest.format}`);
    if (manifest.representation !== "sh") {
        throw new Error(`v1 progressive viewer supports SH scenes only (got: ${manifest.representation})`);
    }

    const worker = new Worker(
        URL.createObjectURL(
            new Blob(["(", createWorker.toString(), ")(self)"], { type: "application/javascript" }),
        ),
    );
    worker.addEventListener("error", (e) => {
      window.__errors.push("worker error: " + e.message + " @" + e.lineno + ":" + e.colno);
      console.error("worker error:", e.message, e.lineno, e.filename);
    });
    worker.postMessage({ init: { total: manifest.vertexCount, sh: Boolean(manifest.shRowBytes) } });

    const canvas = document.getElementById("canvas");
    const gl = canvas.getContext("webgl2", { antialias: false });

    const compile = (type, source) => {
        const shader = gl.createShader(type);
        gl.shaderSource(shader, source);
        gl.compileShader(shader);
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
            throw new Error(gl.getShaderInfoLog(shader));
        }
        return shader;
    };
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, vertexShaderSource));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, fragmentShaderSource));
    gl.linkProgram(program);
    gl.useProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));

    gl.disable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    gl.blendFuncSeparate(gl.ONE_MINUS_DST_ALPHA, gl.ONE, gl.ONE_MINUS_DST_ALPHA, gl.ONE);
    gl.blendEquationSeparate(gl.FUNC_ADD, gl.FUNC_ADD);

    const u = {};
    for (const name of ["projection", "viewport", "focal", "view", "camPos", "u_texture", "u_sh_texture"]) {
        u[name] = gl.getUniformLocation(program, name);
    }
    gl.uniform1i(u.u_texture, 0);
    gl.uniform1i(u.u_sh_texture, 1);

    const triangleVertices = new Float32Array([-2, -2, 2, -2, 2, 2, -2, 2]);
    const vertexBuffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vertexBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, triangleVertices, gl.STATIC_DRAW);
    const a_position = gl.getAttribLocation(program, "position");
    gl.enableVertexAttribArray(a_position);
    gl.vertexAttribPointer(a_position, 2, gl.FLOAT, false, 0, 0);

    const mainTexture = gl.createTexture();
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, mainTexture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32UI, 1, 1, 0, gl.RGBA_INTEGER, gl.UNSIGNED_INT, new Uint32Array([0, 0, 0, 0]));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);

    const shTexture = gl.createTexture();
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, shTexture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 1, 1, 0, gl.RGBA, gl.FLOAT, new Float32Array([0, 0, 0, 0]));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);

    const indexBuffer = gl.createBuffer();
    const a_index = gl.getAttribLocation(program, "index");
    gl.enableVertexAttribArray(a_index);
    gl.bindBuffer(gl.ARRAY_BUFFER, indexBuffer);
    gl.vertexAttribIPointer(a_index, 1, gl.INT, false, 0, 0);
    gl.vertexAttribDivisor(a_index, 1);

    const resize = () => {
        gl.uniform2fv(u.focal, new Float32Array([camera.fx, camera.fy]));
        projectionMatrix = getProjectionMatrix(camera.fx, camera.fy, innerWidth, innerHeight);
        gl.uniform2fv(u.viewport, new Float32Array([innerWidth, innerHeight]));
        canvas.width = Math.round(innerWidth / downsample);
        canvas.height = Math.round(innerHeight / downsample);
        gl.viewport(0, 0, canvas.width, canvas.height);
        gl.uniformMatrix4fv(u.projection, false, projectionMatrix);
    };
    window.addEventListener("resize", resize);
    resize();

    let firstFrameAt = null;
    let smoothFrameMs = 16.7;
    let lastFrame = performance.now();
    let gpuGuardUntil = 0;
    let fps = 0;

    worker.onmessage = (e) => {
      window.__workerRecv = (window.__workerRecv || 0) + 1;
      (window.__workerMsgs = window.__workerMsgs || []).push(Object.keys(e.data || {}).join("+"));
      try {
        if (e.data.texdata) {
            const { texdata, texwidth, texheight } = e.data;
            gl.activeTexture(gl.TEXTURE0);
            gl.bindTexture(gl.TEXTURE_2D, mainTexture);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32UI, texwidth, texheight, 0, gl.RGBA_INTEGER, gl.UNSIGNED_INT, texdata);
        } else if (e.data.texdata_sh) {
            const { texdata_sh, texwidth_sh, texheight_sh } = e.data;
            gl.activeTexture(gl.TEXTURE1);
            gl.bindTexture(gl.TEXTURE_2D, shTexture);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, texwidth_sh, texheight_sh, 0, gl.RGBA, gl.FLOAT, texdata_sh);
        } else if (e.data.depthIndex) {
            gl.bindBuffer(gl.ARRAY_BUFFER, indexBuffer);
            gl.bufferData(gl.ARRAY_BUFFER, e.data.depthIndex, gl.DYNAMIC_DRAW);
            const fresh = e.data.vertexCount > vertexCount;
            vertexCount = e.data.vertexCount;
            if (fresh && firstFrameAt === null) {
                firstFrameAt = performance.now() - telemetry.startedAt;
                telemetry.ttfrMs = Math.round(firstFrameAt);
                document.getElementById("spinner").style.display = "none";
                if (!clientLoadedBeaconed) {
                    clientLoadedBeaconed = true;
                    sendLoadBeacon({ failed: false, chunks_total: chunkState.length });
                }
            }
        }
      } catch (err) {
        console.error("worker message handler failed:", err);
      }
    };

    /* ---------- interaction (orbit / zoom / pan) ---------- */

    let activeKeys = [];
    window.addEventListener("keydown", (e) => {
        if (!activeKeys.includes(e.code)) activeKeys.push(e.code);
    });
    window.addEventListener("keyup", (e) => {
        activeKeys = activeKeys.filter((k) => k !== e.code);
    });
    window.addEventListener("blur", () => { activeKeys = []; });

    let startX, startY, down;
    canvas.addEventListener("mousedown", (e) => {
        e.preventDefault();
        startX = e.clientX; startY = e.clientY;
        down = e.ctrlKey || e.metaKey ? 2 : 1;
    });
    canvas.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        startX = e.clientX; startY = e.clientY; down = 2;
    });
    canvas.addEventListener("mousemove", (e) => {
        if (!down) return;
        e.preventDefault();
        let inv = invert4(viewMatrix);
        if (down === 1) {
            const dx = (5 * (e.clientX - startX)) / innerWidth;
            const dy = (5 * (e.clientY - startY)) / innerHeight;
            const d = 4;
            inv = translate4(inv, 0, 0, d);
            inv = rotate4(inv, dx, 0, 1, 0);
            inv = rotate4(inv, -dy, 1, 0, 0);
            inv = translate4(inv, 0, 0, -d);
        } else {
            inv = translate4(
                inv,
                (-10 * (e.clientX - startX)) / innerWidth,
                0,
                (10 * (e.clientY - startY)) / innerHeight,
            );
        }
        viewMatrix = invert4(inv);
        startX = e.clientX; startY = e.clientY;
    });
    canvas.addEventListener("mouseup", () => { down = false; });
    window.addEventListener("wheel", (e) => {
        e.preventDefault();
        const scale = e.deltaMode === 1 ? 10 : e.deltaMode === 2 ? innerHeight : 1;
        let inv = invert4(viewMatrix);
        if (e.shiftKey) {
            inv = translate4(inv, (e.deltaX * scale) / innerWidth, (e.deltaY * scale) / innerHeight, 0);
        } else if (e.ctrlKey || e.metaKey) {
            inv = translate4(inv, 0, 0, (-10 * (e.deltaY * scale)) / innerHeight);
        } else {
            const d = 4;
            inv = translate4(inv, 0, 0, d);
            inv = rotate4(inv, -(e.deltaX * scale) / innerWidth, 0, 1, 0);
            inv = rotate4(inv, (e.deltaY * scale) / innerHeight, 1, 0, 0);
            inv = translate4(inv, 0, 0, -d);
        }
        viewMatrix = invert4(inv);
    }, { passive: false });

    /* ---------- scheduler (module C) ---------- */

    const chunkState = manifest.chunks.map((chunk) => ({
        ...chunk,
        status: "pending",      // pending | fetching | done
    }));
    const bytesPerRow = manifest.rowBytes + (manifest.shRowBytes || 0);
    const totalBytes = chunkState.reduce((sum, c) => sum + c.bytes + (c.shBytes || 0), 0);
    telemetry.totalBudgetBytes = totalBytes;

    // bandwidth estimate: exponential moving average over completed fetches
    let bandwidthBps = 0;         // bytes per second
    const queue = new Set();      // chunks currently fetching
    let lastSchedule = 0;

    function viewWeight(chunk) {
        // 1.0 for non-spatial chunks; alignment of the region center with the
        // camera forward direction for region chunks.
        if (!chunk.region) return 1.0;
        const viewInv = invert4(viewMatrix);
        const camPos = [viewInv[12], viewInv[13], viewInv[14]];
        const forward = [-viewInv[8], -viewInv[9], -viewInv[10]];
        const toChunk = [
            chunk.region.center[0] - camPos[0],
            chunk.region.center[1] - camPos[1],
            chunk.region.center[2] - camPos[2],
        ];
        const len = Math.hypot(...toChunk) || 1;
        const cos = (toChunk[0] * forward[0] + toChunk[1] * forward[1] + toChunk[2] * forward[2]) / (len * (Math.hypot(...forward) || 1));
        return Math.max(0.05, (cos + 1) / 2);
    }

    function score(chunk) {
        const bytes = chunk.bytes + (chunk.shBytes || 0);
        const gain = chunk.gain || chunk.rows;
        const decodeCost = chunk.rows * 0.002;   // heuristic ms per row
        const gpuCost = chunk.rows * 0.0004;
        let value;
        if (POLICY === "naive") value = -chunkIndex(chunk);
        else if (POLICY === "bandwidth") value = gain / bytes;
        else if (POLICY === "viewport") value = (gain * viewWeight(chunk)) / bytes;
        else if (POLICY === "gpu") value = gain / bytes;
        else value = (gain * viewWeight(chunk)) / (bytes + 8 * decodeCost + 2 * gpuCost);
        return value;
    }

    function chunkIndex(chunk) {
        return chunkState.indexOf(chunk);
    }

    function gpuGuarded() {
        if (POLICY !== "gpu" && POLICY !== "joint") return true;
        return performance.now() >= gpuGuardUntil;
    }

    function bandwidthLimit() {
        // serial fetches for the bandwidth-only policy; small concurrency for
        // everyone else, scaled down while the bandwidth estimate is unknown.
        if (POLICY === "bandwidth") return 1;
        if (POLICY === "naive" || POLICY === "full") return 6;
        return bandwidthBps > 0 ? 4 : 2;
    }

    function schedule() {
        const pending = chunkState.filter((c) => c.status === "pending");
        if (!pending.length) {
            if (!clientFinishedBeaconed && telemetry.chunkEvents.length && queue.size === 0) {
                clientFinishedBeaconed = true;
                sendLoadBeacon({ failed: false, chunks_total: chunkState.length });
            }
            return;
        }
        if (POLICY === "full") {
            for (const chunk of pending) startFetch(chunk);
            return;
        }
        const now = performance.now();
        if (now - lastSchedule < 120) return;
        lastSchedule = now;
        if (!gpuGuarded()) return;
        const inFlight = queue.size;
        const residentCap = Number.isFinite(MAX_ROWS) ? MAX_ROWS : Infinity;
        if (telemetry.totalRows >= Math.min(manifest.vertexCount, residentCap)) return;
        const concurrency = Math.max(1, bandwidthLimit() - inFlight);
        const candidates = pending
            .sort((a, b) => score(b) - score(a))
            .slice(0, concurrency);
        for (const chunk of candidates) startFetch(chunk);
    }

    async function startFetch(chunk) {
        chunk.status = "fetching";
        queue.add(chunk);
        const startedMs = performance.now() - telemetry.startedAt;
        try {
            const [rowsResp, shResp] = await Promise.all([
                fetch(chunk.url),
                chunk.shUrl ? fetch(chunk.shUrl) : Promise.resolve(null),
            ]);
            if (rowsResp.status !== 200) throw new Error(`chunk ${chunk.id}: ${rowsResp.status}`);
            const rowsBuf = await rowsResp.arrayBuffer();
            const shBuf = shResp ? await shResp.arrayBuffer() : null;
            const bytes = rowsBuf.byteLength + (shBuf ? shBuf.byteLength : 0);
            const durationMs = Math.max(1, performance.now() - telemetry.startedAt - startedMs);
            // bandwidth EMA (bytes/second), biased to the newest sample
            const sample = bytes / (durationMs / 1000);
            bandwidthBps = bandwidthBps === 0 ? sample : bandwidthBps * 0.7 + sample * 0.3;
            telemetry.totalBytes += bytes;
            telemetry.totalRows += chunk.rows;
            telemetry.chunkEvents.push({
                id: chunk.id, startMs: Math.round(startedMs),
                endMs: Math.round(performance.now() - telemetry.startedAt),
                bytes, rows: chunk.rows,
            });
            worker.postMessage(
                { append: { rows: rowsBuf, sh: shBuf } },
                [rowsBuf, ...(shBuf ? [shBuf] : [])],
            );
        } catch (err) {
            console.error("chunk fetch failed:", chunk.id, err);
            telemetry.chunkFailures += 1;
            chunk.status = "pending"; // retry on the next scheduling pass
        } finally {
            queue.delete(chunk);
        }
    }

    /* ---------- frame loop ---------- */

    let framesDone = 0;
    let sampleAccum = 0;
    const frame = (now) => {
        let inv = invert4(viewMatrix);
        if (activeKeys.includes("ArrowLeft")) inv = translate4(inv, -0.03, 0, 0);
        if (activeKeys.includes("ArrowRight")) inv = translate4(inv, 0.03, 0, 0);
        if (activeKeys.includes("KeyA")) inv = rotate4(inv, -0.01, 0, 1, 0);
        if (activeKeys.includes("KeyD")) inv = rotate4(inv, 0.01, 0, 1, 0);
        if (activeKeys.includes("KeyW")) inv = rotate4(inv, 0.005, 1, 0, 0);
        if (activeKeys.includes("KeyS")) inv = rotate4(inv, -0.005, 1, 0, 0);
        viewMatrix = invert4(inv);

        // slow auto-orbit until the user interacts
        if (!down && !activeKeys.length) {
            const t = (now - telemetry.startedAt) / 9000;
            let auto = invert4(defaultViewMatrix);
            auto = translate4(auto, 2.5 * Math.sin(t), 0, 6 * (1 - Math.cos(t)));
            auto = rotate4(auto, -0.5 * t, 0, 1, 0);
            viewMatrix = invert4(auto);
        }

        const cameraPos = [viewMatrix[12], viewMatrix[13], viewMatrix[14]];
        gl.uniform3fv(u.camPos, cameraPos);
        const viewProj = multiply4(projectionMatrix, viewMatrix);
        worker.postMessage({ view: viewProj });

        // Clamp: after occlusion (rAF paused) the first delta is huge and
        // would spike the smooth frame time and trip the GPU guard once.
        const frameMs = Math.min(now - lastFrame, 250);
        lastFrame = now;
        telemetry.frameTimes.push(Math.round(frameMs));
        if (telemetry.frameTimes.length > 240) telemetry.frameTimes.shift();
        smoothFrameMs = smoothFrameMs * 0.9 + frameMs * 0.1;
        fps = 1000 / (smoothFrameMs || 16.7);

        // GPU guard: pause refinement when interaction would stutter
        if ((POLICY === "gpu" || POLICY === "joint") && smoothFrameMs > 34 && queue.size === 0) {
            gpuGuardUntil = performance.now() + 1500;
        }

        if (vertexCount > 0) {
            document.getElementById("spinner").style.display = "none";
            gl.uniformMatrix4fv(u.view, false, viewMatrix);
            gl.clear(gl.COLOR_BUFFER_BIT);
            gl.activeTexture(gl.TEXTURE0);
            gl.bindTexture(gl.TEXTURE_2D, mainTexture);
            gl.activeTexture(gl.TEXTURE1);
            gl.bindTexture(gl.TEXTURE_2D, shTexture);
            gl.drawArraysInstanced(gl.TRIANGLE_FAN, 0, 4, vertexCount);
        } else {
            gl.clear(gl.COLOR_BUFFER_BIT);
        }

        // telemetry sampling ~4 Hz
        framesDone += 1;
        sampleAccum += frameMs;
        if (sampleAccum > 250) {
            sampleAccum = 0;
            telemetry.samples.push({
                tMs: Math.round(now - telemetry.startedAt),
                bytes: telemetry.totalBytes,
                rows: vertexCount,
                fps: Math.round(fps * 10) / 10,
            });
        }

        // HUD
        const pct = telemetry.sceneRows ? Math.min(100, (100 * telemetry.totalRows) / telemetry.sceneRows) : 0;
        const p95 = (() => {
            const sorted = [...telemetry.frameTimes].sort((a, b) => a - b);
            return sorted.length ? sorted[Math.floor(sorted.length * 0.95)] : 0;
        })();
        hud.innerHTML =
            `<div><span class="k">policy</span><b>${POLICY}</b></div>` +
            `<div><span class="k">time</span><b>${((now - telemetry.startedAt) / 1000).toFixed(1)}s</b></div>` +
            `<div><span class="k">TTFR</span><b>${telemetry.ttfrMs !== null ? telemetry.ttfrMs + " ms" : "…"}</b></div>` +
            `<div><span class="k">vertices</span><b>${vertexCount} / ${telemetry.sceneRows}</b></div>` +
            `<div><span class="k">downloaded</span><b>${fmtBytes(telemetry.totalBytes)} / ${fmtBytes(totalBytes)}</b></div>` +
            `<div><span class="k">bandwidth est.</span><b>${fmtBytes(bandwidthBps)}/s</b></div>` +
            `<div><span class="k">fps · p95</span><b>${fps.toFixed(0)} · ${p95} ms</b></div>` +
            `<div class="bar"><div style="width:${pct}%"></div></div>`;

        requestAnimationFrame(frame);
    };

    requestAnimationFrame(frame);

    // Scheduling and view updates must not depend on rAF: background tabs
    // pause rAF entirely, which would stall chunk fetching and TTFR measurement.
    const tick = () => {
        // rAF can be fully paused for occluded/hidden tabs; the scheduler and
        // the worker's sort must keep running so TTFR stays honest. Posting a
        // view is nearly free when it is unchanged (dot-product early return).
        const viewProj = multiply4(projectionMatrix, viewMatrix);
        worker.postMessage({ view: viewProj });
        schedule();
    };
    setInterval(tick, 300);
}

main().catch((err) => {
    document.getElementById("spinner").style.display = "none";
    document.getElementById("message").innerText = err.toString();
    console.error(err);
    // Failure-case evidence for the deployment telemetry (P1): which stage
    // broke, with the viewer's own words as the stage hint. Skip early URL
    // errors where the load never started (startedAt is still 0).
    if (telemetry.startedAt > 0) {
        sendLoadBeacon({ failed: true, fail_stage: String(err && err.message || err).slice(0, 40) });
    }
});
