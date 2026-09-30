// WebGS progressive viewer worker (module B+D).
// Same splat-row layout and sort machinery as megs-viewer, but the scene
// GROWS: the main thread posts {append:{rows,sh}} chunks and the buffers are
// reallocated in batches. vertexCount always equals the rows resident on the
// GPU, so the sort/render loop exposes partial scenes immediately.

function createWorker(self) {

    let buffer;               // 32-byte splat rows (pos/scale/rgba/rot)
    let shBuffer;             // 48 float32 per row, aligned with buffer rows
    let vertexCount = 0;
    let capacity = 0;         // rows allocated in buffer/shBuffer
    let viewProj;
    const rowLength = 3 * 4 + 3 * 4 + 4 + 4; // 32
    let lastProj = [];
    let depthIndex = new Uint32Array();
    let lastVertexCount = -1;
    let lastTextureRows = -1;

    var _floatView = new Float32Array(1);
    var _int32View = new Int32Array(_floatView.buffer);

    function floatToHalf(float) {
        _floatView[0] = float;
        var f = _int32View[0];
        var sign = (f >> 31) & 0x0001;
        var exp = (f >> 23) & 0x00ff;
        var frac = f & 0x007fffff;
        var newExp;
        if (exp == 0) {
            newExp = 0;
        } else if (exp < 113) {
            newExp = 0;
            frac |= 0x00800000;
            frac = frac >> (113 - exp);
            if (frac & 0x01000000) {
                newExp = 1;
                frac = 0;
            }
        } else if (exp < 142) {
            newExp = exp - 112;
        } else {
            newExp = 31;
            frac = 0;
        }
        return (sign << 15) | (newExp << 10) | (frac >> 13);
    }

    function packHalf2x16(x, y) {
        return (floatToHalf(x) | (floatToHalf(y) << 16)) >>> 0;
    }

    function generateTexture() {
        if (!buffer || vertexCount === 0) return;
        const f_buffer = new Float32Array(buffer, 0, vertexCount * 8);
        const u_buffer = new Uint8Array(buffer);

        var texwidth = 1024 * 2;
        var texheight = Math.ceil((2 * vertexCount) / texwidth);
        var texdata = new Uint32Array(texwidth * texheight * 4);
        var texdata_c = new Uint8Array(texdata.buffer);
        var texdata_f = new Float32Array(texdata.buffer);

        let texdata_sh = null, texwidth_sh = 1, texheight_sh = 1;
        if (shBuffer) {
            texwidth_sh = 8192;
            texheight_sh = Math.ceil((vertexCount * 12) / texwidth_sh);
            texdata_sh = new Float32Array(texwidth_sh * texheight_sh * 4);
            const sh_f_buffer = new Float32Array(shBuffer);
            for (let i = 0; i < vertexCount; i++) {
                for (let j = 0; j < 12; j++) {
                    const texX = (i * 12 + j) % texwidth_sh;
                    const texY = Math.floor((i * 12 + j) / texwidth_sh);
                    const texIndex = (texY * texwidth_sh + texX) * 4;
                    if (texIndex < texdata_sh.length) {
                        texdata_sh[texIndex + 0] = sh_f_buffer[i * 48 + j * 4 + 0];
                        texdata_sh[texIndex + 1] = sh_f_buffer[i * 48 + j * 4 + 1];
                        texdata_sh[texIndex + 2] = sh_f_buffer[i * 48 + j * 4 + 2];
                        texdata_sh[texIndex + 3] = sh_f_buffer[i * 48 + j * 4 + 3];
                    }
                }
            }
        }

        for (let i = 0; i < vertexCount; i++) {
            texdata_f[8 * i + 0] = f_buffer[8 * i + 0];
            texdata_f[8 * i + 1] = f_buffer[8 * i + 1];
            texdata_f[8 * i + 2] = f_buffer[8 * i + 2];
            texdata_c[4 * (8 * i + 7) + 0] = u_buffer[32 * i + 24 + 0];
            texdata_c[4 * (8 * i + 7) + 1] = u_buffer[32 * i + 24 + 1];
            texdata_c[4 * (8 * i + 7) + 2] = u_buffer[32 * i + 24 + 2];
            texdata_c[4 * (8 * i + 7) + 3] = u_buffer[32 * i + 24 + 3];

            let scale = [f_buffer[8 * i + 3], f_buffer[8 * i + 4], f_buffer[8 * i + 5]];
            let rot = [
                (u_buffer[32 * i + 28 + 0] - 128) / 128,
                (u_buffer[32 * i + 28 + 1] - 128) / 128,
                (u_buffer[32 * i + 28 + 2] - 128) / 128,
                (u_buffer[32 * i + 28 + 3] - 128) / 128,
            ];
            const M = [
                1.0 - 2.0 * (rot[2] * rot[2] + rot[3] * rot[3]),
                2.0 * (rot[1] * rot[2] + rot[0] * rot[3]),
                2.0 * (rot[1] * rot[3] - rot[0] * rot[2]),
                2.0 * (rot[1] * rot[2] - rot[0] * rot[3]),
                1.0 - 2.0 * (rot[1] * rot[1] + rot[3] * rot[3]),
                2.0 * (rot[2] * rot[3] + rot[0] * rot[1]),
                2.0 * (rot[1] * rot[3] + rot[0] * rot[2]),
                2.0 * (rot[2] * rot[3] - rot[0] * rot[1]),
                1.0 - 2.0 * (rot[1] * rot[1] + rot[2] * rot[2]),
            ].map((k, i) => k * scale[Math.floor(i / 3)]);
            const sigma = [
                M[0] * M[0] + M[3] * M[3] + M[6] * M[6],
                M[0] * M[1] + M[3] * M[4] + M[6] * M[7],
                M[0] * M[2] + M[3] * M[5] + M[6] * M[8],
                M[1] * M[1] + M[4] * M[4] + M[7] * M[7],
                M[1] * M[2] + M[4] * M[5] + M[7] * M[8],
                M[2] * M[2] + M[5] * M[5] + M[8] * M[8],
            ];
            texdata[8 * i + 4] = packHalf2x16(4 * sigma[0], 4 * sigma[1]);
            texdata[8 * i + 5] = packHalf2x16(4 * sigma[2], 4 * sigma[3]);
            texdata[8 * i + 6] = packHalf2x16(4 * sigma[4], 4 * sigma[5]);
        }

        self.postMessage({ texdata, texwidth, texheight }, [texdata.buffer]);
        if (texdata_sh) {
            self.postMessage({ texdata_sh, texwidth_sh, texheight_sh }, [texdata_sh.buffer]);
        }
    }

    function runSort(viewProj) {
        if (!buffer || vertexCount === 0 || !viewProj) return;
        const f_buffer = new Float32Array(buffer, 0, vertexCount * 8);
        if (lastVertexCount == vertexCount) {
            let dot =
                lastProj[2] * viewProj[2] +
                lastProj[6] * viewProj[6] +
                lastProj[10] * viewProj[10];
            if (Math.abs(dot - 1) < 0.01) {
                return;
            }
        } else {
            // Rebuild the texture only when the row count changed (an append)
            // or on the very first sort.
            if (lastVertexCount === -1 || lastTextureRows !== vertexCount) {
                generateTexture();
                lastTextureRows = vertexCount;
            }
            lastVertexCount = vertexCount;
        }

        let maxDepth = -Infinity;
        let minDepth = Infinity;
        let sizeList = new Int32Array(vertexCount);
        for (let i = 0; i < vertexCount; i++) {
            let depth =
                ((viewProj[2] * f_buffer[8 * i + 0] +
                    viewProj[6] * f_buffer[8 * i + 1] +
                    viewProj[10] * f_buffer[8 * i + 2]) *
                    4096) |
                0;
            sizeList[i] = depth;
            if (depth > maxDepth) maxDepth = depth;
            if (depth < minDepth) minDepth = depth;
        }

        let depthInv = (256 * 256 - 1) / (maxDepth - minDepth || 1);
        let counts0 = new Uint32Array(256 * 256);
        for (let i = 0; i < vertexCount; i++) {
            sizeList[i] = ((sizeList[i] - minDepth) * depthInv) | 0;
            counts0[sizeList[i]]++;
        }
        let starts0 = new Uint32Array(256 * 256);
        for (let i = 1; i < 256 * 256; i++)
            starts0[i] = starts0[i - 1] + counts0[i - 1];
        depthIndex = new Uint32Array(vertexCount);
        for (let i = 0; i < vertexCount; i++)
            depthIndex[starts0[sizeList[i]]++] = i;

        lastProj = viewProj;
        self.postMessage({ depthIndex, viewProj, vertexCount }, [depthIndex.buffer]);
    }

    function ensureCapacity(rows) {
        if (capacity >= rows) return;
        let next = Math.max(1024, capacity * 2);
        while (next < rows) next *= 2;
        const bigger = new ArrayBuffer(next * rowLength);
        if (buffer) new Uint8Array(bigger).set(new Uint8Array(buffer, 0, vertexCount * rowLength));
        buffer = bigger;
        if (shBuffer) {
            const biggerSh = new ArrayBuffer(next * 192);
            new Uint8Array(biggerSh).set(new Uint8Array(shBuffer, 0, vertexCount * 192));
            shBuffer = biggerSh;
        }
        capacity = next;
    }

    self.onmessage = (e) => {
        if (e.data.init) {
            capacity = 0;
            vertexCount = 0;
            buffer = undefined;
            shBuffer = undefined;
            ensureCapacity(e.data.init.total);
            buffer = new ArrayBuffer(e.data.init.total * rowLength);
            capacity = e.data.init.total;
            if (e.data.init.sh) shBuffer = new ArrayBuffer(e.data.init.total * 192);
            self.postMessage({ initialized: true, capacity });
        } else if (e.data.append) {
            // rows: ArrayBuffer of 32-byte splat rows; sh: matching 192-byte
            // SH rows (optional). Rows are appended at the tail; vertexCount
            // grows so the next sort renders the larger scene.
            const part = new Uint8Array(e.data.append.rows);
            const partRows = Math.floor(part.length / rowLength);
            if (partRows === 0) return;
            ensureCapacity(vertexCount + partRows);
            new Uint8Array(buffer, vertexCount * rowLength, part.length).set(part);
            if (shBuffer && e.data.append.sh) {
                const shPart = new Uint8Array(e.data.append.sh);
                new Uint8Array(shBuffer, vertexCount * 192, shPart.length).set(shPart);
            }
            vertexCount += partRows;
            lastVertexCount = -1; // force texture regeneration on next sort
            runSort(viewProj);
        } else if (e.data.view) {
            viewProj = e.data.view;
            runSort(viewProj);
        }
    };

}

export { createWorker };
