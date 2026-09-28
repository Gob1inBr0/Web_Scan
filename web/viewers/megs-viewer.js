// Merged Gaussian splat viewer for both representations:
//   - "sh": spherical-harmonics Gaussians (f_dc_*/f_rest_* properties)
//   - "sg": spherical-Gaussian Gaussians (rgb_base_*/sg_dir_*/sg_rgb_* properties)
// sh.html loads it with ?fmt=sh and sg.html with ?fmt=sg (script src query).
//
// PLY files are parsed incrementally in the worker: the page streams raw
// chunks over as they download, the worker locates the header, allocates the
// output buffers, and converts complete rows on the fly so the scene appears
// before the download finishes. Only binary_little_endian PLY is supported.
const FORMAT = (() => {
    try {
        const src = document.currentScript && document.currentScript.src;
        if (src) {
            const fmt = new URL(src).searchParams.get("fmt");
            if (fmt) return fmt.toLowerCase() === "sg" ? "sg" : "sh";
        }
    } catch (err) { /* fall through to default */ }
    return "sh";
})();

let cameras = [
    {
        id: 0,
        img_name: "00001",
        width: 1959,
        height: 1090,
        position: [
            -3.0089893469241797, -0.11086489695181866, -3.7527640949141428,
        ],
        rotation: [
            [0.876134201218856, 0.06925962026449776, 0.47706599800804744],
            [-0.04747421839895102, 0.9972110940209488, -0.057586739349882114],
            [-0.4797239414934443, 0.027805376500959853, 0.8769787916452908],
        ],
        fy: 1164.6601287484507,
        fx: 1159.5880733038064,
    },
    {
        id: 1,
        img_name: "00009",
        width: 1959,
        height: 1090,
        position: [
            -2.5199776022057296, -0.09704735754873686, -3.6247725540304545,
        ],
        rotation: [
            [0.9982731285632193, -0.011928707708098955, -0.05751927260507243],
            [0.0065061360949636325, 0.9955928229282383, -0.09355533724430458],
            [0.058381769258182864, 0.09301955098900708, 0.9939511719154457],
        ],
        fy: 1164.6601287484507,
        fx: 1159.5880733038064,
    },
    {
        id: 2,
        img_name: "00017",
        width: 1959,
        height: 1090,
        position: [
            -0.7737533667465242, -0.3364271945329695, -2.9358969417573753,
        ],
        rotation: [
            [0.9998813418672372, 0.013742375651625236, -0.0069605529394208224],
            [-0.014268370388586709, 0.996512943252834, -0.08220929105659476],
            [0.00580653013657589, 0.08229885200307129, 0.9965907801935302],
        ],
        fy: 1164.6601287484507,
        fx: 1159.5880733038064,
    },
    {
        id: 3,
        img_name: "00025",
        width: 1959,
        height: 1090,
        position: [
            1.2198221749590001, -0.2196687861401182, -2.3183162007028453,
        ],
        rotation: [
            [0.9208648867765482, 0.0012010625395201253, 0.389880004297208],
            [-0.06298104172269357, 0.987319521752825, 0.14571693239364383],
            [-0.3847611242348369, -0.1587410451475895, 0.9092635249821667],
        ],
        fy: 1164.6601287484507,
        fx: 1159.5880733038064,
    },
    {
        id: 4,
        img_name: "00033",
        width: 1959,
        height: 1090,
        position: [
            1.742387858893817, -0.13848225198886954, -2.0566370113193146,
        ],
        rotation: [
            [0.24669889292141334, -0.08370189346592856, -0.9654706879349405],
            [0.11343747891376445, 0.9919082664242816, -0.05700815184573074],
            [0.9624300466054861, -0.09545671285663988, 0.2541976029815521],
        ],
        fy: 1164.6601287484507,
        fx: 1159.5880733038064,
    },
    {
        id: 5,
        img_name: "00041",
        width: 1959,
        height: 1090,
        position: [
            3.6567309419223925, -0.16470990600750707, -1.3458085590422042,
        ],
        rotation: [
            [0.2341293058324528, -0.029683304577558845, -0.9717522161434825],
            [0.10270823606832301, 0.99469554638321, -0.005638106875665722],
            [0.9667649592295676, -0.0984869096675676, 0.2359360976431732],
        ],
        fy: 1164.6601287484507,
        fx: 1159.5880733038064,
    },
    {
        id: 6,
        img_name: "00049",
        width: 1959,
        height: 1090,
        position: [
            3.9013554243203497, -0.2597500978038105, -0.8106154188297828,
        ],
        rotation: [
            [0.6717235545638952, -0.015718162115524837, -0.7406351366386528],
            [0.055627354673906296, 0.9980224478387622, 0.029270992841185218],
            [0.7387104058127439, -0.060861588786650656, 0.6712695459756353],
        ],
        fy: 1164.6601287484507,
        fx: 1159.5880733038064,
    },
    {
        id: 7,
        img_name: "00057",
        width: 1959,
        height: 1090,
        position: [4.742994605467533, -0.05591660945412069, 0.9500365976084458],
        rotation: [
            [-0.17042655709210375, 0.01207080756938, -0.9852964448542146],
            [0.1165090336695526, 0.9931575292530063, -0.00798543433078162],
            [0.9784581921120181, -0.1161568667478904, -0.1706667764862097],
        ],
        fy: 1164.6601287484507,
        fx: 1159.5880733038064,
    },
    {
        id: 8,
        img_name: "00065",
        width: 1959,
        height: 1090,
        position: [4.34676307626522, 0.08168160516967145, 1.0876221470355405],
        rotation: [
            [-0.003575447631888379, -0.044792503246552894, -0.9989899137764799],
            [0.10770152645126597, 0.9931680875192705, -0.04491693593046672],
            [0.9941768441149182, -0.10775333677534978, 0.0012732004866391048],
        ],
        fy: 1164.6601287484507,
        fx: 1159.5880733038064,
    },
    {
        id: 9,
        img_name: "00073",
        width: 1959,
        height: 1090,
        position: [3.264984351114202, 0.078974937336732, 1.0117200284114904],
        rotation: [
            [-0.026919994628162257, -0.1565891128261527, -0.9872968974090509],
            [0.08444552208239385, 0.983768234577625, -0.15833197540970964],
            [0.9960643893290491, -0.0876350978794554, -0.013259786205163005],
        ],
        fy: 1164.6601287484507,
        fx: 1159.5880733038064,
    },
];

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
    const camToWorld = [
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
    return camToWorld;
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
    let det =
        b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
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
    x /= len;
    y /= len;
    z /= len;
    let s = Math.sin(rad);
    let c = Math.cos(rad);
    let t = 1 - c;
    let b00 = x * x * t + c;
    let b01 = y * x * t + z * s;
    let b02 = z * x * t - y * s;
    let b10 = x * y * t - z * s;
    let b11 = y * y * t + c;
    let b12 = z * y * t + x * s;
    let b20 = x * z * t + y * s;
    let b21 = y * z * t - x * s;
    let b22 = z * z * t + c;
    return [
        a[0] * b00 + a[4] * b01 + a[8] * b02,
        a[1] * b00 + a[5] * b01 + a[9] * b02,
        a[2] * b00 + a[6] * b01 + a[10] * b02,
        a[3] * b00 + a[7] * b01 + a[11] * b02,
        a[0] * b10 + a[4] * b11 + a[8] * b12,
        a[1] * b10 + a[5] * b11 + a[9] * b12,
        a[2] * b10 + a[6] * b11 + a[10] * b12,
        a[3] * b10 + a[7] * b11 + a[11] * b12,
        a[0] * b20 + a[4] * b21 + a[8] * b22,
        a[1] * b20 + a[5] * b21 + a[9] * b22,
        a[2] * b20 + a[6] * b21 + a[10] * b22,
        a[3] * b20 + a[7] * b21 + a[11] * b22,
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

function createWorker(self) {

    let buffer;
    let vertexCount = 0;
    let viewProj;
    // 6*4 + 4 + 4 = 8*4
    // XYZ - Position (Float32)
    // XYZ - Scale (Float32)
    // RGBA - colors (uint8)
    // IJKL - quaternion/rot (uint8)
    const rowLength = 3 * 4 + 3 * 4 + 4 + 4; // 32 bytes per vertex
    let lastProj = [];
    let depthIndex = new Uint32Array();
    let lastVertexCount = 0;

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

    // ---- format-specific extra state (SH coefficient buffer / SG axis rows) ----
    let shBuffer = null;
    let sgRows = [];
    let sgStarts = new Uint32Array(0);
    let axisCounts = new Uint32Array(0);
    let sgRowCursor = 0;

    function generateTexture() {
        if (!buffer) return;
        const floatCount = Math.floor(buffer.byteLength / 4);
        if (floatCount <= 0) return;
        const f_buffer = new Float32Array(buffer, 0, floatCount);
        const u_buffer = new Uint8Array(buffer);

        var texwidth = 1024 * 2; // 2048 pixels wide
        var texheight = Math.ceil((2 * vertexCount) / texwidth); // 2 pixels per vertex
        var texdata = new Uint32Array(texwidth * texheight * 4);
        var texdata_c = new Uint8Array(texdata.buffer);
        var texdata_f = new Float32Array(texdata.buffer);

        let texdata_extra = null, texwidth_extra = 1, texheight_extra = 1;
        let texdata_index = null, texwidth_index = 1, texheight_index = 1;

        if (FORMAT === "sh" && shBuffer) {
            texwidth_extra = 8192;
            const shRowLength = 48;
            texheight_extra = Math.ceil((vertexCount * 12) / texwidth_extra);
            texdata_extra = new Float32Array(texwidth_extra * texheight_extra * 4);

            const sh_f_buffer = new Float32Array(shBuffer);

            for (let i = 0; i < vertexCount; i++) {
                for (let j = 0; j < 12; j++) {
                    const texX = (i * 12 + j) % texwidth_extra;
                    const texY = Math.floor((i * 12 + j) / texwidth_extra);
                    const texIndex = (texY * texwidth_extra + texX) * 4;

                    if (texIndex < texdata_extra.length) {
                        texdata_extra[texIndex + 0] = sh_f_buffer[i * 48 + j * 4 + 0];
                        texdata_extra[texIndex + 1] = sh_f_buffer[i * 48 + j * 4 + 1];
                        texdata_extra[texIndex + 2] = sh_f_buffer[i * 48 + j * 4 + 2];
                        texdata_extra[texIndex + 3] = sh_f_buffer[i * 48 + j * 4 + 3];
                    }
                }
            }
        }

        if (FORMAT === "sg" && sgStarts.length > 0 && sgRowCursor > 0) {
            texwidth_extra = 2048;
            texheight_extra = Math.ceil(sgRowCursor / texwidth_extra) * 2;
            texdata_extra = new Float32Array(texwidth_extra * texheight_extra * 4);

            for (let i = 0; i < sgRowCursor; i++) {
                const row0 = sgRows[2 * i + 0];
                const row1 = sgRows[2 * i + 1];

                const texX = i % texwidth_extra;
                const texY = Math.floor(i / texwidth_extra) * 2;

                texdata_extra[(texY * texwidth_extra + texX) * 4 + 0] = row0[0];
                texdata_extra[(texY * texwidth_extra + texX) * 4 + 1] = row0[1];
                texdata_extra[(texY * texwidth_extra + texX) * 4 + 2] = row0[2];
                texdata_extra[(texY * texwidth_extra + texX) * 4 + 3] = row0[3];

                texdata_extra[((texY + 1) * texwidth_extra + texX) * 4 + 0] = row1[0];
                texdata_extra[((texY + 1) * texwidth_extra + texX) * 4 + 1] = row1[1];
                texdata_extra[((texY + 1) * texwidth_extra + texX) * 4 + 2] = row1[2];
                texdata_extra[((texY + 1) * texwidth_extra + texX) * 4 + 3] = row1[3];
            }

            texwidth_index = 2048;
            texheight_index = Math.ceil(vertexCount / texwidth_index);
            texdata_index = new Uint32Array(texwidth_index * texheight_index * 4);

            for (let i = 0; i < vertexCount; i++) {
                const texX = i % texwidth_index;
                const texY = Math.floor(i / texwidth_index);
                const texIndex = (texY * texwidth_index + texX) * 4;

                texdata_index[texIndex + 0] = sgStarts[i] || 0;
                texdata_index[texIndex + 1] = axisCounts[i] || 0;
                texdata_index[texIndex + 2] = 0;
                texdata_index[texIndex + 3] = 0;
            }
        }

        // Here we convert from a .splat file buffer into a texture
        // With a little bit more foresight perhaps this texture file
        // should have been the native format as it'd be very easy to
        // load it into webgl.
        for (let i = 0; i < vertexCount; i++) {
            // x, y, z
            texdata_f[8 * i + 0] = f_buffer[8 * i + 0];
            texdata_f[8 * i + 1] = f_buffer[8 * i + 1];
            texdata_f[8 * i + 2] = f_buffer[8 * i + 2];

            // r, g, b, a (color data at offset 24-27 in buffer)
            texdata_c[4 * (8 * i + 7) + 0] = u_buffer[32 * i + 24 + 0];
            texdata_c[4 * (8 * i + 7) + 1] = u_buffer[32 * i + 24 + 1];
            texdata_c[4 * (8 * i + 7) + 2] = u_buffer[32 * i + 24 + 2];
            texdata_c[4 * (8 * i + 7) + 3] = u_buffer[32 * i + 24 + 3];

            // Scale data at offset 12-23 in buffer (3 float32 values)
            let scale = [
                f_buffer[8 * i + 3],
                f_buffer[8 * i + 4],
                f_buffer[8 * i + 5],
            ];

            // Rotation data at offset 28-31 in buffer (4 uint8 values)
            let rot = [
                (u_buffer[32 * i + 28 + 0] - 128) / 128,
                (u_buffer[32 * i + 28 + 1] - 128) / 128,
                (u_buffer[32 * i + 28 + 2] - 128) / 128,
                (u_buffer[32 * i + 28 + 3] - 128) / 128,
            ];

            // Compute the matrix product of S and R (M = S * R)
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

            // Store covariance data in pixel 1
            texdata[8 * i + 4] = packHalf2x16(4 * sigma[0], 4 * sigma[1]);
            texdata[8 * i + 5] = packHalf2x16(4 * sigma[2], 4 * sigma[3]);
            texdata[8 * i + 6] = packHalf2x16(4 * sigma[4], 4 * sigma[5]);
        }

        self.postMessage({ texdata, texwidth, texheight }, [texdata.buffer]);
        if (texdata_extra) {
            if (FORMAT === "sg") {
                self.postMessage({ texdata_sg: texdata_extra, texwidth_sg: texwidth_extra, texheight_sg: texheight_extra }, [texdata_extra.buffer]);
                self.postMessage({ texdata_index, texwidth_index, texheight_index }, [texdata_index.buffer]);
            } else {
                self.postMessage({ texdata_sh: texdata_extra, texwidth_sh: texwidth_extra, texheight_sh: texheight_extra }, [texdata_extra.buffer]);
            }
        }
    }

    function runSort(viewProj) {
        if (!buffer) return;
        const floatCount = Math.floor(buffer.byteLength / 4);
        if (floatCount <= 0) return;
        const f_buffer = new Float32Array(buffer, 0, floatCount);
        if (lastVertexCount == vertexCount) {
            let dot =
                lastProj[2] * viewProj[2] +
                lastProj[6] * viewProj[6] +
                lastProj[10] * viewProj[10];
            if (Math.abs(dot - 1) < 0.01) {
                return;
            }
        } else {
            generateTexture();
            lastVertexCount = vertexCount;
        }

        console.time("sort");
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

        // This is a 16 bit single-pass counting sort
        let depthInv = (256 * 256 - 1) / (maxDepth - minDepth);
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

        console.timeEnd("sort");

        lastProj = viewProj;
        self.postMessage({ depthIndex, viewProj, vertexCount }, [
            depthIndex.buffer,
        ]);
    }

    // ---- PLY incremental ingest ----
    // The page streams raw PLY bytes over as they download. The worker
    // accumulates them until "end_header" is found, parses the element
    // layout once, then converts every complete row immediately so the
    // scene can be rendered while the download is still in flight.
    let plyJob = null;
    let plyPreHeader = new Uint8Array(0);
    const PLY_TYPE_MAP = {
        double: "getFloat64",
        int: "getInt32",
        uint: "getUint32",
        float: "getFloat32",
        short: "getInt16",
        ushort: "getUint16",
        uchar: "getUint8",
    };

    function parsePlyLayout(headerText) {
        const headerEnd = "end_header\n";
        const endIdx = headerText.indexOf(headerEnd);
        if (endIdx < 0) return { status: "incomplete" };
        const fmtMatch = /format\s+(\S+)\s+1\.0/.exec(headerText);
        if (!fmtMatch) return { status: "unsupported", reason: "Missing PLY format line" };
        if (fmtMatch[1] !== "binary_little_endian") {
            return { status: "unsupported", reason: `Unsupported PLY format: ${fmtMatch[1]} (only binary_little_endian)` };
        }
        const lines = headerText.slice(0, endIdx).split("\n");
        let elements = [];
        let current = null;
        for (const ln of lines) {
            if (ln.startsWith("element ")) {
                const parts = ln.split(/\s+/);
                current = {
                    name: parts[1],
                    count: parseInt(parts[2]),
                    props: [],
                    types: {},
                    offsets: {},
                    stride: 0,
                };
                if (/^vertex(_\d+)?$/.test(current.name)) elements.push(current);
            } else if (ln.startsWith("property ") && current && /^vertex(_\d+)?$/.test(current.name)) {
                const [p, type, ...rest] = ln.split(/\s+/);
                const name = rest.join(" ");
                const arrayType = PLY_TYPE_MAP[type] || "getInt8";
                current.types[name] = arrayType;
                current.offsets[name] = current.stride;
                current.stride += parseInt(arrayType.replace(/[^\d]/g, "")) / 8;
                current.props.push(name);
            }
        }
        if (elements.length === 0) {
            return { status: "unsupported", reason: "No vertex elements found in PLY" };
        }
        const total = elements.reduce((s, e) => s + e.count, 0);
        if (!Number.isFinite(total) || total <= 0) {
            return { status: "unsupported", reason: "PLY vertex element has no rows" };
        }
        return { status: "ok", total, elements, dataStart: endIdx + headerEnd.length };
    }

    function plyAttr(elem, dv, base, name) {
        const t = elem.types[name];
        if (!t) return undefined;
        return dv[t](base + elem.offsets[name], true);
    }

    function plyConvertRow(elem, dv, base, outRow) {
        const position = new Float32Array(buffer, outRow * rowLength, 3);
        const scales = new Float32Array(buffer, outRow * rowLength + 12, 3);
        const rgba = new Uint8ClampedArray(buffer, outRow * rowLength + 24, 4);
        const rot = new Uint8ClampedArray(buffer, outRow * rowLength + 28, 4);
        const SH_C0 = 0.28209479177387814;

        position[0] = plyAttr(elem, dv, base, "x") ?? 0;
        position[1] = plyAttr(elem, dv, base, "y") ?? 0;
        position[2] = plyAttr(elem, dv, base, "z") ?? 0;

        if (elem.types["scale_0"]) {
            const q0 = plyAttr(elem, dv, base, "rot_0") ?? 1;
            const q1 = plyAttr(elem, dv, base, "rot_1") ?? 0;
            const q2 = plyAttr(elem, dv, base, "rot_2") ?? 0;
            const q3 = plyAttr(elem, dv, base, "rot_3") ?? 0;
            const qlen = Math.sqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3) || 1;
            rot[0] = (q0 / qlen) * 128 + 128;
            rot[1] = (q1 / qlen) * 128 + 128;
            rot[2] = (q2 / qlen) * 128 + 128;
            rot[3] = (q3 / qlen) * 128 + 128;
            scales[0] = Math.exp(plyAttr(elem, dv, base, "scale_0"));
            scales[1] = Math.exp(plyAttr(elem, dv, base, "scale_1"));
            scales[2] = Math.exp(plyAttr(elem, dv, base, "scale_2"));
        } else {
            scales[0] = 0.01;
            scales[1] = 0.01;
            scales[2] = 0.01;
            rot[0] = 255;
            rot[1] = 0;
            rot[2] = 0;
            rot[3] = 0;
        }

        if (FORMAT === "sh") {
            if (elem.types["f_dc_0"]) {
                rgba[0] = (0.5 + SH_C0 * plyAttr(elem, dv, base, "f_dc_0")) * 255;
                rgba[1] = (0.5 + SH_C0 * plyAttr(elem, dv, base, "f_dc_1")) * 255;
                rgba[2] = (0.5 + SH_C0 * plyAttr(elem, dv, base, "f_dc_2")) * 255;
            } else {
                rgba[0] = plyAttr(elem, dv, base, "red") ?? plyAttr(elem, dv, base, "r") ?? 220;
                rgba[1] = plyAttr(elem, dv, base, "green") ?? plyAttr(elem, dv, base, "g") ?? 220;
                rgba[2] = plyAttr(elem, dv, base, "blue") ?? plyAttr(elem, dv, base, "b") ?? 220;
            }
            rgba[3] = elem.types["opacity"]
                ? (1 / (1 + Math.exp(-plyAttr(elem, dv, base, "opacity")))) * 255
                : 255;

            if (elem.types["f_dc_0"]) {
                const shOffset = outRow * 48 * 4;
                for (let i = 0; i < 45; i++) {
                    const value = elem.types[`f_rest_${i}`]
                        ? plyAttr(elem, dv, base, `f_rest_${i}`)
                        : 0;
                    if (i < 15) {
                        plyJob.shView.setFloat32(shOffset + 12 + i * 4, value, true);
                    } else if (i < 30) {
                        plyJob.shView.setFloat32(shOffset + 12 + (i - 15 + 15) * 4, value, true);
                    } else {
                        plyJob.shView.setFloat32(shOffset + 12 + (i - 30 + 30) * 4, value, true);
                    }
                }
                plyJob.shView.setFloat32(shOffset + 0, plyAttr(elem, dv, base, "f_dc_0"), true);
                plyJob.shView.setFloat32(shOffset + 4, plyAttr(elem, dv, base, "f_dc_1"), true);
                plyJob.shView.setFloat32(shOffset + 8, plyAttr(elem, dv, base, "f_dc_2"), true);
            }
        } else { // sg
            const opacityRaw = plyAttr(elem, dv, base, "opacity");
            const alpha = opacityRaw !== undefined ? 1 / (1 + Math.exp(-opacityRaw)) : 1.0;
            const br = plyAttr(elem, dv, base, "rgb_base_0") ?? 0;
            const bg = plyAttr(elem, dv, base, "rgb_base_1") ?? 0;
            const bb = plyAttr(elem, dv, base, "rgb_base_2") ?? 0;
            rgba[0] = (0.5 + SH_C0 * br) * 255;
            rgba[1] = (0.5 + SH_C0 * bg) * 255;
            rgba[2] = (0.5 + SH_C0 * bb) * 255;
            rgba[3] = Math.max(0, Math.min(255, Math.round(alpha * 255)));

            let inferred = 0;
            const m = elem.name.match(/^vertex_(\d+)$/);
            if (m) inferred = parseInt(m[1]);
            const ac = Math.max(0, Math.min(3, ((plyAttr(elem, dv, base, "sg_axis_count") ?? inferred) | 0)));
            axisCounts[outRow] = ac;
            sgStarts[outRow] = sgRowCursor;

            for (let a = 0; a < ac; a++) {
                const dirx = plyAttr(elem, dv, base, `sg_dir_${a}_0`) ?? 0;
                const diry = plyAttr(elem, dv, base, `sg_dir_${a}_1`) ?? 0;
                const dirz = plyAttr(elem, dv, base, `sg_dir_${a}_2`) ?? 0;
                const sharp = plyAttr(elem, dv, base, `sg_sharp_${a}`) ?? 0;
                const rgbr = plyAttr(elem, dv, base, `sg_rgb_${a}_0`) ?? 0;
                const rgbg = plyAttr(elem, dv, base, `sg_rgb_${a}_1`) ?? 0;
                const rgbb = plyAttr(elem, dv, base, `sg_rgb_${a}_2`) ?? 0;
                sgRows.push([dirx, diry, dirz, sharp]);
                sgRows.push([rgbr, rgbg, rgbb, 0]);
                sgRowCursor += 1;
            }
        }
    }

    function plyInit(layout) {
        plyJob = {
            total: layout.total,
            elements: layout.elements,
            elemIdx: 0,
            rowInElem: 0,
            outRow: 0,
            save: false,
            carry: new Uint8Array(1 << 20),
            carryLen: 0,
            shView: null,
        };
        buffer = new ArrayBuffer(rowLength * layout.total);
        vertexCount = 0;
        lastVertexCount = 0;
        if (FORMAT === "sh") {
            shBuffer = new ArrayBuffer(48 * 4 * layout.total);
            plyJob.shView = new DataView(shBuffer);
        } else {
            sgRows = [];
            sgStarts = new Uint32Array(layout.total);
            axisCounts = new Uint32Array(layout.total);
            sgRowCursor = 0;
        }
        self.postMessage({ plyLayout: { total: layout.total } });
    }

    function plyIngest(rawBuffer) {
        if (!plyJob) return;
        const needed = plyJob.carryLen + rawBuffer.byteLength;
        if (plyJob.carry.length < needed) {
            let cap = plyJob.carry.length * 2;
            while (cap < needed) cap *= 2;
            const bigger = new Uint8Array(cap);
            bigger.set(plyJob.carry.subarray(0, plyJob.carryLen), 0);
            plyJob.carry = bigger;
        }
        plyJob.carry.set(new Uint8Array(rawBuffer), plyJob.carryLen);
        plyJob.carryLen = needed;

        const dv = new DataView(plyJob.carry.buffer);
        let cursor = 0;
        while (plyJob.elemIdx < plyJob.elements.length) {
            const elem = plyJob.elements[plyJob.elemIdx];
            if (plyJob.rowInElem >= elem.count) {
                plyJob.elemIdx += 1;
                plyJob.rowInElem = 0;
                continue;
            }
            if (plyJob.carryLen - cursor < elem.stride) break;
            plyConvertRow(elem, dv, cursor, plyJob.outRow);
            cursor += elem.stride;
            plyJob.rowInElem += 1;
            plyJob.outRow += 1;
        }
        plyJob.carry.copyWithin(0, cursor, plyJob.carryLen);
        plyJob.carryLen -= cursor;
        vertexCount = plyJob.outRow;
        self.postMessage({ plyParsed: plyJob.outRow, plyTotal: plyJob.total });
    }

    function plyFinalize() {
        if (!plyJob) return;
        const save = plyJob.save;
        vertexCount = plyJob.outRow;
        plyJob = null;
        runSort(viewProj);
        postMessage({ buffer: buffer, save: !!save });
    }

    function plyFeed(rawBuffer) {
        if (plyJob) {
            plyIngest(rawBuffer);
            return;
        }
        // Accumulate bytes until the header is complete, then initialize.
        const merged = new Uint8Array(plyPreHeader.length + rawBuffer.byteLength);
        merged.set(plyPreHeader, 0);
        merged.set(new Uint8Array(rawBuffer), plyPreHeader.length);
        plyPreHeader = merged;
        if (plyPreHeader.length > 4 * 1024 * 1024) {
            self.postMessage({ plyError: "PLY header is too large" });
            plyPreHeader = new Uint8Array(0);
            return;
        }
        const headText = new TextDecoder().decode(
            plyPreHeader.subarray(0, Math.min(plyPreHeader.length, 1024 * 10)),
        );
        const parsed = parsePlyLayout(headText);
        if (parsed.status === "incomplete") return;
        if (parsed.status === "unsupported") {
            self.postMessage({ plyError: parsed.reason });
            plyPreHeader = new Uint8Array(0);
            return;
        }
        const dataStart = parsed.dataStart;
        plyInit({ total: parsed.total, elements: parsed.elements });
        if (plyPreHeader.length > dataStart) {
            plyIngest(plyPreHeader.buffer.slice(dataStart, plyPreHeader.length));
        }
        plyPreHeader = new Uint8Array(0);
    }

    const throttledSort = () => {
        if (!sortRunning) {
            sortRunning = true;
            let lastView = viewProj;
            runSort(lastView);
            setTimeout(() => {
                sortRunning = false;
                if (lastView !== viewProj) {
                    throttledSort();
                }
            }, 0);
        }
    };

    let sortRunning;

    self.onmessage = (e) => {
        if (e.data.plyRaw) {
            plyFeed(e.data.plyRaw);
        } else if (e.data.plyDone) {
            plyFinalize();
        } else if (e.data.buffer) {
            const rawBuffer = e.data.buffer;
            const completeBytes = Math.floor(rawBuffer.byteLength / rowLength) * rowLength;
            buffer = completeBytes > 0 ? rawBuffer.slice(0, completeBytes) : rawBuffer;
            const safeVertexCount = Math.floor((buffer?.byteLength ?? 0) / rowLength);
            vertexCount = Math.min(e.data.vertexCount ?? safeVertexCount, safeVertexCount);
        } else if (e.data.vertexCount) {
            vertexCount = e.data.vertexCount;
        } else if (e.data.view) {
            viewProj = e.data.view;
            throttledSort();
        }
    };

}

const vertexShaderSource = FORMAT === "sg" ? `
#version 300 es
precision highp float;
precision highp int;

uniform highp usampler2D u_texture;
uniform highp sampler2D u_texture_sg;
uniform highp usampler2D u_texture_index;
uniform mat4 projection, view;
uniform vec2 focal;
uniform vec2 viewport;
uniform vec3 camPos;

in vec2 position;
in int index;

out vec4 vColor;
out vec2 vPosition;

void main () {
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

    int tex_width_index = 2048;
    int tex_x_index = index % tex_width_index;
    int tex_y_index = index / tex_width_index;
    uvec4 index_info = texelFetch(u_texture_index, ivec2(tex_x_index, tex_y_index), 0);
    int sg_start = int(index_info.x);
    int sg_count = int(index_info.y);

    vec3 center_world = uintBitsToFloat(cen.xyz);
    vec3 view_dir = normalize(camPos - center_world);

    vec3 sg_contrib = vec3(0.0);
    int count = sg_count;
    for (int i = 0; i < 16; ++i) {
        if (i >= count) break;
        int sg_global_index = sg_start + i;
        int tex_width = 2048;
        int tex_x = sg_global_index % tex_width;
        int tex_y = (sg_global_index / tex_width) * 2;

        vec4 sg_dir_sharp = texelFetch(u_texture_sg, ivec2(tex_x, tex_y), 0);
        vec3 sg_dir = sg_dir_sharp.xyz;
        float sg_sharp = sg_dir_sharp.w;
        vec3 sg_rgb = texelFetch(u_texture_sg, ivec2(tex_x, tex_y + 1), 0).xyz;
        float cos_theta = dot(normalize(sg_dir), view_dir);
        float directional_scale = exp(abs(sg_sharp) * (cos_theta - 1.0));
        sg_contrib += sg_rgb * directional_scale;
    }

    vec3 base_rgb = vec3(
        float((cov.w) & 0xffu),
        float((cov.w >> 8) & 0xffu),
        float((cov.w >> 16) & 0xffu)
    ) / 255.0;
    float alpha = float((cov.w >> 24) & 0xffu) / 255.0;
    vec3 final_rgb = base_rgb + sg_contrib;
    vColor = vec4(final_rgb, alpha);
    vPosition = position;

    vec2 vCenter = vec2(pos2d) / pos2d.w;
    gl_Position = vec4(
        vCenter
        + position.x * majorAxis / viewport
        + position.y * minorAxis / viewport, 0.0, 1.0);
}
`.trim() : `
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

        shCoeffs[1] = sh[3];
        shCoeffs[2] = sh[4];
        shCoeffs[3] = sh[5];

        shCoeffs[4] = sh[6];
        shCoeffs[5] = sh[7];
        shCoeffs[6] = sh[8];
        shCoeffs[7] = sh[9];
        shCoeffs[8] = sh[10];

        shCoeffs[9] = sh[11];
        shCoeffs[10] = sh[12];
        shCoeffs[11] = sh[13];
        shCoeffs[12] = sh[14];
        shCoeffs[13] = sh[15];
        shCoeffs[14] = sh[16];
        shCoeffs[15] = sh[17];
    } else if (channel == 1) {
        shCoeffs[0] = sh[1];

        shCoeffs[1] = sh[18];
        shCoeffs[2] = sh[19];
        shCoeffs[3] = sh[20];

        shCoeffs[4] = sh[21];
        shCoeffs[5] = sh[22];
        shCoeffs[6] = sh[23];
        shCoeffs[7] = sh[24];
        shCoeffs[8] = sh[25];

        shCoeffs[9] = sh[26];
        shCoeffs[10] = sh[27];
        shCoeffs[11] = sh[28];
        shCoeffs[12] = sh[29];
        shCoeffs[13] = sh[30];
        shCoeffs[14] = sh[31];
        shCoeffs[15] = sh[32];
    } else {
        shCoeffs[0] = sh[2];

        shCoeffs[1] = sh[33];
        shCoeffs[2] = sh[34];
        shCoeffs[3] = sh[35];

        shCoeffs[4] = sh[36];
        shCoeffs[5] = sh[37];
        shCoeffs[6] = sh[38];
        shCoeffs[7] = sh[39];
        shCoeffs[8] = sh[40];

        shCoeffs[9] = sh[41];
        shCoeffs[10] = sh[42];
        shCoeffs[11] = sh[43];
        shCoeffs[12] = sh[44];
        shCoeffs[13] = sh[45];
        shCoeffs[14] = sh[46];
        shCoeffs[15] = sh[47];
    }

    float x = dir.x, y = dir.y, z = dir.z;
    float result = SH_C0 * shCoeffs[0];

    result = result - SH_C1 * y * shCoeffs[1] + SH_C1 * z * shCoeffs[2] - SH_C1 * x * shCoeffs[3];

    float xx = x * x, yy = y * y, zz = z * z;
    float xy = x * y, yz = y * z, xz = x * z;
    result = result +
        SH_C2[0] * xy * shCoeffs[4] +
        SH_C2[1] * yz * shCoeffs[5] +
        SH_C2[2] * (2.0 * zz - xx - yy) * shCoeffs[6] +
        SH_C2[3] * xz * shCoeffs[7] +
        SH_C2[4] * (xx - yy) * shCoeffs[8];

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

let defaultViewMatrix = [
    0.47, 0.04, 0.88, 0, -0.11, 0.99, 0.02, 0, -0.88, -0.11, 0.47, 0, 0.07,
    0.03, 6.55, 1,
];
let viewMatrix = defaultViewMatrix;
async function main() {
    let carousel = true;
    const params = new URLSearchParams(location.search);
    try {
        viewMatrix = JSON.parse(decodeURIComponent(location.hash.slice(1)));
        carousel = false;
    } catch (err) { }
    const url = new URL(
        params.get("url") || (FORMAT === "sg" ? "./point_cloud.ply" : "./bicycle_30000.ply"),
        location.href
    );

    const req = await fetch(url, {
        mode: "cors",
        credentials: "omit",
    });
    if (req.status != 200)
        throw new Error(req.status + " Unable to load " + req.url);

    const rowLength = 3 * 4 + 3 * 4 + 4 + 4;
    const reader = req.body.getReader();
    // Content-Length may be absent (chunked responses); the buffer grows on demand.
    const contentLength = parseInt(req.headers.get("content-length") || "0") || 0;
    let splatData = new Uint8Array(contentLength || (1 << 20));
    let bytesRead = 0;

    let downsample = contentLength
        ? (contentLength / rowLength > 500000 ? 1 : 1 / devicePixelRatio)
        : 1;

    let plyExpectedRows = 0;
    let plyParsedRows = 0;

    const worker = new Worker(
        URL.createObjectURL(
            new Blob(["const FORMAT = " + JSON.stringify(FORMAT) + ";(", createWorker.toString(), ")(self)"], {
                type: "application/javascript",
            }),
        ),
    );

    const canvas = document.getElementById("canvas");
    const fps = document.getElementById("fps");
    const camid = document.getElementById("camid");

    let projectionMatrix;

    const gl = canvas.getContext("webgl2", {
        antialias: FORMAT === "sg",
    });

    const vertexShader = gl.createShader(gl.VERTEX_SHADER);
    gl.shaderSource(vertexShader, vertexShaderSource);
    gl.compileShader(vertexShader);
    if (!gl.getShaderParameter(vertexShader, gl.COMPILE_STATUS))
        console.error(gl.getShaderInfoLog(vertexShader));

    const fragmentShader = gl.createShader(gl.FRAGMENT_SHADER);
    gl.shaderSource(fragmentShader, fragmentShaderSource);
    gl.compileShader(fragmentShader);
    if (!gl.getShaderParameter(fragmentShader, gl.COMPILE_STATUS))
        console.error(gl.getShaderInfoLog(fragmentShader));

    const program = gl.createProgram();
    gl.attachShader(program, vertexShader);
    gl.attachShader(program, fragmentShader);
    gl.linkProgram(program);
    gl.useProgram(program);

    if (!gl.getProgramParameter(program, gl.LINK_STATUS))
        console.error(gl.getProgramInfoLog(program));

    gl.disable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    gl.blendFuncSeparate(
        gl.ONE_MINUS_DST_ALPHA,
        gl.ONE,
        gl.ONE_MINUS_DST_ALPHA,
        gl.ONE,
    );
    gl.blendEquationSeparate(gl.FUNC_ADD, gl.FUNC_ADD);

    const u_projection = gl.getUniformLocation(program, "projection");
    const u_viewport = gl.getUniformLocation(program, "viewport");
    const u_focal = gl.getUniformLocation(program, "focal");
    const u_view = gl.getUniformLocation(program, "view");
    const u_camPos = gl.getUniformLocation(program, "camPos");

    // positions
    const triangleVertices = new Float32Array([-2, -2, 2, -2, 2, 2, -2, 2]);
    const vertexBuffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vertexBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, triangleVertices, gl.STATIC_DRAW);
    const a_position = gl.getAttribLocation(program, "position");
    gl.enableVertexAttribArray(a_position);
    gl.bindBuffer(gl.ARRAY_BUFFER, vertexBuffer);
    gl.vertexAttribPointer(a_position, 2, gl.FLOAT, false, 0, 0);

    var mainTexture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, mainTexture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32UI, 1, 1, 0, gl.RGBA_INTEGER, gl.UNSIGNED_INT, new Uint32Array([0, 0, 0, 0]));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    var u_textureLocation = gl.getUniformLocation(program, "u_texture");
    gl.uniform1i(u_textureLocation, 0);

    var shTexture = null, texture_sg = null, texture_index = null;
    if (FORMAT === "sg") {
        texture_sg = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, texture_sg);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 1, 1, 0, gl.RGBA, gl.FLOAT, new Float32Array([0, 0, 0, 0]));
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.uniform1i(gl.getUniformLocation(program, "u_texture_sg"), 1);

        texture_index = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, texture_index);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32UI, 1, 1, 0, gl.RGBA_INTEGER, gl.UNSIGNED_INT, new Uint32Array([0, 0, 0, 0]));
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.uniform1i(gl.getUniformLocation(program, "u_texture_index"), 2);
    } else {
        shTexture = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, shTexture);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 1, 1, 0, gl.RGBA, gl.FLOAT, new Float32Array([0, 0, 0, 0]));
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.uniform1i(gl.getUniformLocation(program, "u_sh_texture"), 1);
    }

    const indexBuffer = gl.createBuffer();
    const a_index = gl.getAttribLocation(program, "index");
    gl.enableVertexAttribArray(a_index);
    gl.bindBuffer(gl.ARRAY_BUFFER, indexBuffer);
    gl.vertexAttribIPointer(a_index, 1, gl.INT, false, 0, 0);
    gl.vertexAttribDivisor(a_index, 1);

    const resize = () => {
        gl.uniform2fv(u_focal, new Float32Array([camera.fx, camera.fy]));

        projectionMatrix = getProjectionMatrix(
            camera.fx,
            camera.fy,
            innerWidth,
            innerHeight,
        );

        gl.uniform2fv(u_viewport, new Float32Array([innerWidth, innerHeight]));

        gl.canvas.width = Math.round(innerWidth / downsample);
        gl.canvas.height = Math.round(innerHeight / downsample);
        gl.viewport(0, 0, gl.canvas.width, gl.canvas.height);

        gl.uniformMatrix4fv(u_projection, false, projectionMatrix);
    };

    window.addEventListener("resize", resize);
    resize();

    worker.onmessage = (e) => {
        if (e.data.buffer) {
            splatData = new Uint8Array(e.data.buffer);
            if (e.data.save) {
                const blob = new Blob([splatData.buffer], {
                    type: "application/octet-stream",
                });
                const link = document.createElement("a");
                link.download = "model.splat";
                link.href = URL.createObjectURL(blob);
                document.body.appendChild(link);
                link.click();
            }
        } else if (e.data.texdata) {
            const { texdata, texwidth, texheight } = e.data;
            gl.activeTexture(gl.TEXTURE0);
            gl.bindTexture(gl.TEXTURE_2D, mainTexture);
            gl.texParameteri(
                gl.TEXTURE_2D,
                gl.TEXTURE_WRAP_S,
                gl.CLAMP_TO_EDGE,
            );
            gl.texParameteri(
                gl.TEXTURE_2D,
                gl.TEXTURE_WRAP_T,
                gl.CLAMP_TO_EDGE,
            );
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);

            gl.texImage2D(
                gl.TEXTURE_2D,
                0,
                gl.RGBA32UI,
                texwidth,
                texheight,
                0,
                gl.RGBA_INTEGER,
                gl.UNSIGNED_INT,
                texdata,
            );
        } else if (FORMAT === "sh" && e.data.texdata_sh) {
            const { texdata_sh, texwidth_sh, texheight_sh } = e.data;
            gl.activeTexture(gl.TEXTURE1);
            gl.bindTexture(gl.TEXTURE_2D, shTexture);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, texwidth_sh, texheight_sh, 0, gl.RGBA, gl.FLOAT, texdata_sh);
        } else if (FORMAT === "sg" && e.data.texdata_sg) {
            const { texdata_sg, texwidth_sg, texheight_sg } = e.data;
            gl.activeTexture(gl.TEXTURE1);
            gl.bindTexture(gl.TEXTURE_2D, texture_sg);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, texwidth_sg, texheight_sg, 0, gl.RGBA, gl.FLOAT, texdata_sg);
        } else if (FORMAT === "sg" && e.data.texdata_index) {
            const { texdata_index, texwidth_index, texheight_index } = e.data;
            gl.activeTexture(gl.TEXTURE2);
            gl.bindTexture(gl.TEXTURE_2D, texture_index);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32UI, texwidth_index, texheight_index, 0, gl.RGBA_INTEGER, gl.UNSIGNED_INT, texdata_index);
        } else if (e.data.depthIndex) {
            const { depthIndex, viewProj } = e.data;
            gl.bindBuffer(gl.ARRAY_BUFFER, indexBuffer);
            gl.bufferData(gl.ARRAY_BUFFER, depthIndex, gl.DYNAMIC_DRAW);
            vertexCount = e.data.vertexCount;
        } else if (e.data.plyLayout) {
            plyExpectedRows = e.data.plyLayout.total;
            const wanted = plyExpectedRows > 500000 ? 1 : 1 / devicePixelRatio;
            if (wanted !== downsample) {
                downsample = wanted;
                resize();
            }
        } else if (e.data.plyParsed) {
            plyParsedRows = e.data.plyParsed;
        } else if (e.data.plyError) {
            console.error("PLY load failed:", e.data.plyError);
            document.getElementById("message").innerText = "PLY load failed: " + e.data.plyError;
        }
    };

    let activeKeys = [];
    let currentCameraIndex = 0;

    window.addEventListener("keydown", (e) => {
        carousel = false;
        if (!activeKeys.includes(e.code)) activeKeys.push(e.code);
        if (/\d/.test(e.key)) {
            currentCameraIndex = parseInt(e.key);
            camera = cameras[currentCameraIndex];
            viewMatrix = getViewMatrix(camera);
        }
        if (["-", "_"].includes(e.key)) {
            currentCameraIndex =
                (currentCameraIndex + cameras.length - 1) % cameras.length;
            viewMatrix = getViewMatrix(cameras[currentCameraIndex]);
        }
        if (["+", "="].includes(e.key)) {
            currentCameraIndex = (currentCameraIndex + 1) % cameras.length;
            viewMatrix = getViewMatrix(cameras[currentCameraIndex]);
        }
        camid.innerText = "cam  " + currentCameraIndex;
        if (e.code == "KeyV") {
            location.hash =
                "#" +
                JSON.stringify(
                    viewMatrix.map((k) => Math.round(k * 100) / 100),
                );
            camid.innerText = "";
        } else if (e.code === "KeyP") {
            carousel = true;
            camid.innerText = "";
        }
    });
    window.addEventListener("keyup", (e) => {
        activeKeys = activeKeys.filter((k) => k !== e.code);
    });
    window.addEventListener("blur", () => {
        activeKeys = [];
    });

    window.addEventListener(
        "wheel",
        (e) => {
            carousel = false;
            e.preventDefault();
            const lineHeight = 10;
            const scale =
                e.deltaMode == 1
                    ? lineHeight
                    : e.deltaMode == 2
                        ? innerHeight
                        : 1;
            let inv = invert4(viewMatrix);
            if (e.shiftKey) {
                inv = translate4(
                    inv,
                    (e.deltaX * scale) / innerWidth,
                    (e.deltaY * scale) / innerHeight,
                    0,
                );
            } else if (e.ctrlKey || e.metaKey) {
                inv = translate4(
                    inv,
                    0,
                    0,
                    (-10 * (e.deltaY * scale)) / innerHeight,
                );
            } else {
                let d = 4;
                inv = translate4(inv, 0, 0, d);
                inv = rotate4(inv, -(e.deltaX * scale) / innerWidth, 0, 1, 0);
                inv = rotate4(inv, (e.deltaY * scale) / innerHeight, 1, 0, 0);
                inv = translate4(inv, 0, 0, -d);
            }

            viewMatrix = invert4(inv);
        },
        { passive: false },
    );

    let startX, startY, down;
    canvas.addEventListener("mousedown", (e) => {
        carousel = false;
        e.preventDefault();
        startX = e.clientX;
        startY = e.clientY;
        down = e.ctrlKey || e.metaKey ? 2 : 1;
    });
    canvas.addEventListener("contextmenu", (e) => {
        carousel = false;
        e.preventDefault();
        startX = e.clientX;
        startY = e.clientY;
        down = 2;
    });

    canvas.addEventListener("mousemove", (e) => {
        e.preventDefault();
        if (down == 1) {
            let inv = invert4(viewMatrix);
            let dx = (5 * (e.clientX - startX)) / innerWidth;
            let dy = (5 * (e.clientY - startY)) / innerHeight;
            let d = 4;

            inv = translate4(inv, 0, 0, d);
            inv = rotate4(inv, dx, 0, 1, 0);
            inv = rotate4(inv, -dy, 1, 0, 0);
            inv = translate4(inv, 0, 0, -d);
            viewMatrix = invert4(inv);

            startX = e.clientX;
            startY = e.clientY;
        } else if (down == 2) {
            let inv = invert4(viewMatrix);
            inv = translate4(
                inv,
                (-10 * (e.clientX - startX)) / innerWidth,
                0,
                (10 * (e.clientY - startY)) / innerHeight,
            );
            viewMatrix = invert4(inv);

            startX = e.clientX;
            startY = e.clientY;
        }
    });
    canvas.addEventListener("mouseup", (e) => {
        e.preventDefault();
        down = false;
        startX = 0;
        startY = 0;
    });

    let altX = 0,
        altY = 0;
    canvas.addEventListener(
        "touchstart",
        (e) => {
            e.preventDefault();
            if (e.touches.length === 1) {
                carousel = false;
                startX = e.touches[0].clientX;
                startY = e.touches[0].clientY;
                down = 1;
            } else if (e.touches.length === 2) {
                carousel = false;
                startX = e.touches[0].clientX;
                altX = e.touches[1].clientX;
                startY = e.touches[0].clientY;
                altY = e.touches[1].clientY;
                down = 1;
            }
        },
        { passive: false },
    );
    canvas.addEventListener(
        "touchmove",
        (e) => {
            e.preventDefault();
            if (e.touches.length === 1 && down) {
                let inv = invert4(viewMatrix);
                let dx = (4 * (e.touches[0].clientX - startX)) / innerWidth;
                let dy = (4 * (e.touches[0].clientY - startY)) / innerHeight;

                let d = 4;
                inv = translate4(inv, 0, 0, d);
                inv = rotate4(inv, dx, 0, 1, 0);
                inv = rotate4(inv, -dy, 1, 0, 0);
                inv = translate4(inv, 0, 0, -d);

                viewMatrix = invert4(inv);

                startX = e.touches[0].clientX;
                startY = e.touches[0].clientY;
            } else if (e.touches.length === 2) {
                const dtheta =
                    Math.atan2(startY - altY, startX - altX) -
                    Math.atan2(
                        e.touches[0].clientY - e.touches[1].clientY,
                        e.touches[0].clientX - e.touches[1].clientX,
                    );
                const dscale =
                    Math.hypot(startX - altX, startY - altY) /
                    Math.hypot(
                        e.touches[0].clientX - e.touches[1].clientX,
                        e.touches[0].clientY - e.touches[1].clientY,
                    );
                const dx =
                    (e.touches[0].clientX +
                        e.touches[1].clientX -
                        (startX + altX)) /
                    2;
                const dy =
                    (e.touches[0].clientY +
                        e.touches[1].clientY -
                        (startY + altY)) /
                    2;
                let inv = invert4(viewMatrix);
                inv = rotate4(inv, dtheta, 0, 0, 1);

                inv = translate4(inv, -dx / innerWidth, -dy / innerHeight, 0);

                inv = translate4(inv, 0, 0, 3 * (1 - dscale));

                viewMatrix = invert4(inv);

                startX = e.touches[0].clientX;
                altX = e.touches[1].clientX;
                startY = e.touches[0].clientY;
                altY = e.touches[1].clientY;
            }
        },
        { passive: false },
    );
    canvas.addEventListener(
        "touchend",
        (e) => {
            e.preventDefault();
            down = false;
            startX = 0;
            startY = 0;
        },
        { passive: false },
    );

    let jumpDelta = 0;
    let vertexCount = 0;

    let lastFrame = 0;
    let avgFps = 0;
    let start = 0;

    let gLastFrame = window.performance.now();
    let oldMilliseconds = 1000;
    let smoothFps = 60.0;

    window.addEventListener("gamepadconnected", (e) => {
        const gp = navigator.getGamepads()[e.gamepad.index];
        console.log(
            `Gamepad connected at index ${gp.index}: ${gp.id}. It has ${gp.buttons.length} buttons and ${gp.axes.length} axes.`,
        );
    });
    window.addEventListener("gamepaddisconnected", (e) => {
        console.log("Gamepad disconnected");
    });

    let leftGamepadTrigger, rightGamepadTrigger;

    const frame = (now) => {
        let inv = invert4(viewMatrix);
        let shiftKey =
            activeKeys.includes("Shift") ||
            activeKeys.includes("ShiftLeft") ||
            activeKeys.includes("ShiftRight");

        if (activeKeys.includes("ArrowUp")) {
            if (shiftKey) {
                inv = translate4(inv, 0, -0.03, 0);
            } else {
                inv = translate4(inv, 0, 0, 0.1);
            }
        }
        if (activeKeys.includes("ArrowDown")) {
            if (shiftKey) {
                inv = translate4(inv, 0, 0.03, 0);
            } else {
                inv = translate4(inv, 0, 0, -0.1);
            }
        }
        if (activeKeys.includes("ArrowLeft"))
            inv = translate4(inv, -0.03, 0, 0);
        if (activeKeys.includes("ArrowRight"))
            inv = translate4(inv, 0.03, 0, 0);
        if (activeKeys.includes("KeyA")) inv = rotate4(inv, -0.01, 0, 1, 0);
        if (activeKeys.includes("KeyD")) inv = rotate4(inv, 0.01, 0, 1, 0);
        if (activeKeys.includes("KeyQ")) inv = rotate4(inv, 0.01, 0, 0, 1);
        if (activeKeys.includes("KeyE")) inv = rotate4(inv, -0.01, 0, 0, 1);
        if (activeKeys.includes("KeyW")) inv = rotate4(inv, 0.005, 1, 0, 0);
        if (activeKeys.includes("KeyS")) inv = rotate4(inv, -0.005, 1, 0, 0);

        const gamepads = navigator.getGamepads ? navigator.getGamepads() : [];
        let isJumping = activeKeys.includes("Space");
        for (let gamepad of gamepads) {
            if (!gamepad) continue;

            const axisThreshold = 0.1;
            const moveSpeed = 0.06;
            const rotateSpeed = 0.02;

            if (Math.abs(gamepad.axes[0]) > axisThreshold) {
                inv = translate4(inv, moveSpeed * gamepad.axes[0], 0, 0);
                carousel = false;
            }
            if (Math.abs(gamepad.axes[1]) > axisThreshold) {
                inv = translate4(inv, 0, 0, -moveSpeed * gamepad.axes[1]);
                carousel = false;
            }
            if (gamepad.buttons[12].pressed || gamepad.buttons[13].pressed) {
                inv = translate4(
                    inv,
                    0,
                    -moveSpeed *
                    (gamepad.buttons[12].pressed -
                        gamepad.buttons[13].pressed),
                    0,
                );
                carousel = false;
            }

            if (gamepad.buttons[14].pressed || gamepad.buttons[15].pressed) {
                inv = translate4(
                    inv,
                    -moveSpeed *
                    (gamepad.buttons[14].pressed -
                        gamepad.buttons[15].pressed),
                    0,
                    0,
                );
                carousel = false;
            }

            if (Math.abs(gamepad.axes[2]) > axisThreshold) {
                inv = rotate4(inv, rotateSpeed * gamepad.axes[2], 0, 1, 0);
                carousel = false;
            }
            if (Math.abs(gamepad.axes[3]) > axisThreshold) {
                inv = rotate4(inv, -rotateSpeed * gamepad.axes[3], 1, 0, 0);
                carousel = false;
            }

            let tiltAxis = gamepad.buttons[6].value - gamepad.buttons[7].value;
            if (Math.abs(tiltAxis) > axisThreshold) {
                inv = rotate4(inv, rotateSpeed * tiltAxis, 0, 0, 1);
                carousel = false;
            }
            if (gamepad.buttons[4].pressed && !leftGamepadTrigger) {
                camera =
                    cameras[(cameras.indexOf(camera) + 1) % cameras.length];
                inv = invert4(getViewMatrix(camera));
                carousel = false;
            }
            if (gamepad.buttons[5].pressed && !rightGamepadTrigger) {
                camera =
                    cameras[
                    (cameras.indexOf(camera) + cameras.length - 1) %
                    cameras.length
                    ];
                inv = invert4(getViewMatrix(camera));
                carousel = false;
            }
            leftGamepadTrigger = gamepad.buttons[4].pressed;
            rightGamepadTrigger = gamepad.buttons[5].pressed;
            if (gamepad.buttons[0].pressed) {
                isJumping = true;
                carousel = false;
            }
            if (gamepad.buttons[3].pressed) {
                carousel = true;
            }
        }

        if (
            ["KeyJ", "KeyK", "KeyL", "KeyI"].some((k) => activeKeys.includes(k))
        ) {
            let d = 4;
            inv = translate4(inv, 0, 0, d);
            inv = rotate4(
                inv,
                activeKeys.includes("KeyJ")
                    ? -0.05
                    : activeKeys.includes("KeyL")
                        ? 0.05
                        : 0,
                0,
                1,
                0,
            );
            inv = rotate4(
                inv,
                activeKeys.includes("KeyI")
                    ? 0.05
                    : activeKeys.includes("KeyK")
                        ? -0.05
                        : 0,
                1,
                0,
                0,
            );
            inv = translate4(inv, 0, 0, -d);
        }

        viewMatrix = invert4(inv);

        if (carousel) {
            let inv = invert4(defaultViewMatrix);

            const t = Math.sin((Date.now() - start) / 5000);
            inv = translate4(inv, 2.5 * t, 0, 6 * (1 - Math.cos(t)));
            inv = rotate4(inv, -0.6 * t, 0, 1, 0);

            viewMatrix = invert4(inv);
        }

        if (isJumping) {
            jumpDelta = Math.min(1, jumpDelta + 0.05);
        } else {
            jumpDelta = Math.max(0, jumpDelta - 0.05);
        }

        let inv2 = invert4(viewMatrix);
        inv2 = translate4(inv2, 0, -jumpDelta, 0);
        inv2 = rotate4(inv2, -0.1 * jumpDelta, 1, 0, 0);
        let actualViewMatrix = invert4(inv2);

        const cameraPos = [
            actualViewMatrix[12],
            actualViewMatrix[13],
            actualViewMatrix[14]
        ];

        gl.uniform3fv(u_camPos, cameraPos);

        const viewProj = multiply4(projectionMatrix, actualViewMatrix);
        worker.postMessage({ view: viewProj });

        const currentFps = 1000 / (now - lastFrame) || 0;
        avgFps = avgFps * 0.9 + currentFps * 0.1;

        let currentFrame = window.performance.now();
        let milliseconds = currentFrame - gLastFrame;
        let smoothMilliseconds = oldMilliseconds * 0.995 + milliseconds * 0.005;
        smoothFps = 1000 / smoothMilliseconds;
        gLastFrame = currentFrame;
        oldMilliseconds = smoothMilliseconds;

        if (vertexCount > 0) {
            document.getElementById("spinner").style.display = "none";
            gl.uniformMatrix4fv(u_view, false, actualViewMatrix);
            gl.clear(gl.COLOR_BUFFER_BIT);

            gl.activeTexture(gl.TEXTURE0);
            gl.bindTexture(gl.TEXTURE_2D, mainTexture);
            if (FORMAT === "sg") {
                gl.activeTexture(gl.TEXTURE1);
                gl.bindTexture(gl.TEXTURE_2D, texture_sg);
                gl.activeTexture(gl.TEXTURE2);
                gl.bindTexture(gl.TEXTURE_2D, texture_index);
            } else {
                gl.activeTexture(gl.TEXTURE1);
                gl.bindTexture(gl.TEXTURE_2D, shTexture);
            }

            gl.drawArraysInstanced(gl.TRIANGLE_FAN, 0, 4, vertexCount);
        } else {
            gl.clear(gl.COLOR_BUFFER_BIT);
            document.getElementById("spinner").style.display = "";
            start = Date.now() + 2000;
        }
        const progress = plyExpectedRows
            ? (100 * plyParsedRows) / plyExpectedRows
            : (100 * vertexCount) / (splatData.length / rowLength);
        if (progress < 100) {
            document.getElementById("progress").style.width = progress + "%";
        } else {
            document.getElementById("progress").style.display = "none";
        }
        fps.innerText = smoothFps.toFixed(1) + " fps";
        window.parent?.postMessage(
            {
                type: "gaussian-viewer-stats",
                status: "running",
                fps: smoothFps.toFixed(1),
                vertices: vertexCount,
                progress: `${Math.round(progress)}%`,
            },
            "*",
        );
        if (isNaN(currentCameraIndex)) {
            camid.innerText = "";
        }
        lastFrame = now;
        requestAnimationFrame(frame);
    };

    frame();

    // Debug/testing handle: lets tests and the console read loader state.
    window.__viewerState = {
        format: FORMAT,
        get expectedRows() { return plyExpectedRows; },
        get parsedRows() { return plyParsedRows; },
        get vertices() { return vertexCount; },
        get contentBytes() { return splatData.length; },
    };

    const isPly = (data) =>
        data.length >= 4 &&
        data[0] == 112 &&
        data[1] == 108 &&
        data[2] == 121 &&
        (data[3] == 10 || data[3] == 13);

    const selectFile = (file) => {
        const fr = new FileReader();
        if (/\.json$/i.test(file.name)) {
            fr.onload = () => {
                cameras = JSON.parse(fr.result);
                viewMatrix = getViewMatrix(cameras[0]);
                projectionMatrix = getProjectionMatrix(
                    camera.fx / downsample,
                    camera.fy / downsample,
                    canvas.width,
                    canvas.height,
                );
                gl.uniformMatrix4fv(u_projection, false, projectionMatrix);

                console.log("Loaded Cameras");
            };
            fr.readAsText(file);
        } else {
            stopLoading = true;
            fr.onload = () => {
                splatData = new Uint8Array(fr.result);
                console.log("Loaded", Math.floor(splatData.length / rowLength));

                if (isPly(splatData)) {
                    worker.postMessage({ plyRaw: splatData.buffer }, [splatData.buffer]);
                    worker.postMessage({ plyDone: true, save: true });
                } else {
                    const completeBytes = Math.floor(splatData.byteLength / rowLength) * rowLength;
                    worker.postMessage({
                        buffer: splatData.buffer.slice(0, completeBytes),
                        vertexCount: Math.floor(completeBytes / rowLength),
                    });
                }
            };
            fr.readAsArrayBuffer(file);
        }
    };

    window.addEventListener("hashchange", (e) => {
        try {
            viewMatrix = JSON.parse(decodeURIComponent(location.hash.slice(1)));
            carousel = false;
        } catch (err) { }
    });

    const preventDefault = (e) => {
        e.preventDefault();
        e.stopPropagation();
    };
    document.addEventListener("dragenter", preventDefault);
    document.addEventListener("dragover", preventDefault);
    document.addEventListener("dragleave", preventDefault);
    document.addEventListener("drop", (e) => {
        e.preventDefault();
        e.stopPropagation();
        selectFile(e.dataTransfer.files[0]);
    });

    let lastVertexCount = -1;
    let stopLoading = false;
    let firstChunkChecked = false;
    let isPlyFile = false;

    while (true) {
        const { done, value } = await reader.read();
        if (done || stopLoading) break;

        if (!firstChunkChecked) {
            firstChunkChecked = true;
            isPlyFile = isPly(value);
        }

        if (isPlyFile) {
            // Stream raw PLY bytes to the worker; it parses rows incrementally.
            const copy = value.slice().buffer;
            worker.postMessage({ plyRaw: copy }, [copy]);
        } else {
            // .splat layout: feed complete rows as they arrive.
            if (bytesRead + value.length > splatData.length) {
                let cap = Math.max(splatData.length * 2, bytesRead + value.length);
                const bigger = new Uint8Array(cap);
                bigger.set(splatData.subarray(0, bytesRead), 0);
                splatData = bigger;
            }
            splatData.set(value, bytesRead);
            bytesRead += value.length;

            if (vertexCount > lastVertexCount) {
                const completeBytes = Math.floor(bytesRead / rowLength) * rowLength;
                worker.postMessage({
                    buffer: splatData.buffer.slice(0, completeBytes),
                    vertexCount: Math.floor(completeBytes / rowLength),
                });
                lastVertexCount = vertexCount;
            }
        }
    }
    if (!stopLoading) {
        if (isPlyFile) {
            worker.postMessage({ plyDone: true, save: false });
        } else {
            const completeBytes = Math.floor(bytesRead / rowLength) * rowLength;
            worker.postMessage({
                buffer: splatData.buffer.slice(0, completeBytes),
                vertexCount: Math.floor(completeBytes / rowLength),
            });
        }
    }
}

main().catch((err) => {
    document.getElementById("spinner").style.display = "none";
    document.getElementById("message").innerText = err.toString();
    window.parent?.postMessage(
        {
            type: "gaussian-viewer-stats",
            status: "error",
            fps: "-",
            vertices: "-",
            progress: "-",
            error: err.toString(),
        },
        "*",
    );
});
