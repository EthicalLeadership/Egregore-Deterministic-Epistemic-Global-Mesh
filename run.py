#!/usr/bin/env python3
"""
Full-Perspective Interface - Zero Dependencies
Run with: python3 run.py
Open browser to http://localhost:8000
"""

import http.server
import socketserver
import json
import threading
import time
import webbrowser
import os

PORT = int(os.environ.get("EGREGORE_PORT", "8090"))

# Backend state (pure Python, no framework)
cross_section_enabled = False

# --------------------------------------------------------------
# The entire frontend is embedded here. No external files.
# Raw HTML, CSS, and Vanilla JS with custom WebGL + matrix math.
# --------------------------------------------------------------
HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Full-Perspective Interface (Zero-Dep)</title>
    <style>
        body { margin: 0; overflow: hidden; background: #0a0a1a; color: #fff; font-family: monospace; }
        #ui { position: absolute; top: 20px; left: 20px; z-index: 10; pointer-events: none; }
        #ui > * { pointer-events: auto; }
        #zoom-label { font-size: 14px; opacity: 0.8; margin-bottom: 8px; display: block; }
        #clip-btn { background: rgba(30, 30, 50, 0.8); color: #fff; border: 1px solid #4a6a8a; padding: 8px 16px; cursor: pointer; border-radius: 4px; }
        #clip-btn:hover { background: rgba(60, 80, 120, 0.8); }
        #status { position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%); font-size: 12px; opacity: 0.5; }
    </style>
</head>
<body>
    <div id="ui">
        <span id="zoom-label">Zoom: <span id="zoom-level">planet</span></span>
        <button id="clip-btn">Toggle Cross-Section</button>
    </div>
    <div id="status">ZERO DEPENDENCIES • WebGL 2.0 • Custom Engine</div>
    <canvas id="gl-canvas"></canvas>

    <script>
    "use strict";

    // --------------------------------------------------------------
    // 1. CUSTOM MATH LIBRARY (Matrices & Vectors)
    // --------------------------------------------------------------
    const M = {
        identity: function(out) {
            out[0]=1; out[1]=0; out[2]=0; out[3]=0;
            out[4]=0; out[5]=1; out[6]=0; out[7]=0;
            out[8]=0; out[9]=0; out[10]=1; out[11]=0;
            out[12]=0; out[13]=0; out[14]=0; out[15]=1;
            return out;
        },
        perspective: function(out, fovy, aspect, near, far) {
            const f = 1.0 / Math.tan(fovy / 2.0);
            out[0] = f / aspect; out[1] = 0; out[2] = 0; out[3] = 0;
            out[4] = 0; out[5] = f; out[6] = 0; out[7] = 0;
            out[8] = 0; out[9] = 0; out[10] = (far + near) / (near - far); out[11] = -1;
            out[12] = 0; out[13] = 0; out[14] = (2 * far * near) / (near - far); out[15] = 0;
            return out;
        },
        lookAt: function(out, eye, center, up) {
            let z0 = eye[0] - center[0], z1 = eye[1] - center[1], z2 = eye[2] - center[2];
            const len = 1 / Math.sqrt(z0*z0 + z1*z1 + z2*z2);
            z0 *= len; z1 *= len; z2 *= len;
            let x0 = up[1]*z2 - up[2]*z1, x1 = up[2]*z0 - up[0]*z2, x2 = up[0]*z1 - up[1]*z0;
            const len2 = 1 / Math.sqrt(x0*x0 + x1*x1 + x2*x2);
            x0 *= len2; x1 *= len2; x2 *= len2;
            const y0 = z1*x2 - z2*x1, y1 = z2*x0 - z0*x2, y2 = z0*x1 - z1*x0;
            out[0] = x0; out[1] = y0; out[2] = z0; out[3] = 0;
            out[4] = x1; out[5] = y1; out[6] = z1; out[7] = 0;
            out[8] = x2; out[9] = y2; out[10] = z2; out[11] = 0;
            out[12] = -(x0*eye[0] + x1*eye[1] + x2*eye[2]);
            out[13] = -(y0*eye[0] + y1*eye[1] + y2*eye[2]);
            out[14] = -(z0*eye[0] + z1*eye[1] + z2*eye[2]);
            out[15] = 1;
            return out;
        },
        multiply: function(out, a, b) {
            // Column-major: out[i + 4j] = sum_k a[i + 4k] * b[k + 4j]
            const a00=a[0],a01=a[1],a02=a[2],a03=a[3],a04=a[4],a05=a[5],a06=a[6],a07=a[7],
                  a08=a[8],a09=a[9],a10=a[10],a11=a[11],a12=a[12],a13=a[13],a14=a[14],a15=a[15];
            const b00=b[0],b01=b[1],b02=b[2],b03=b[3],b04=b[4],b05=b[5],b06=b[6],b07=b[7],
                  b08=b[8],b09=b[9],b10=b[10],b11=b[11],b12=b[12],b13=b[13],b14=b[14],b15=b[15];
            out[0]=a00*b00+a04*b01+a08*b02+a12*b03; out[1]=a01*b00+a05*b01+a09*b02+a13*b03;
            out[2]=a02*b00+a06*b01+a10*b02+a14*b03; out[3]=a03*b00+a07*b01+a11*b02+a15*b03;
            out[4]=a00*b04+a04*b05+a08*b06+a12*b07; out[5]=a01*b04+a05*b05+a09*b06+a13*b07;
            out[6]=a02*b04+a06*b05+a10*b06+a14*b07; out[7]=a03*b04+a07*b05+a11*b06+a15*b07;
            out[8]=a00*b08+a04*b09+a08*b10+a12*b11; out[9]=a01*b08+a05*b09+a09*b10+a13*b11;
            out[10]=a02*b08+a06*b09+a10*b10+a14*b11; out[11]=a03*b08+a07*b09+a11*b10+a15*b11;
            out[12]=a00*b12+a04*b13+a08*b14+a12*b15; out[13]=a01*b12+a05*b13+a09*b14+a13*b15;
            out[14]=a02*b12+a06*b13+a10*b14+a14*b15; out[15]=a03*b12+a07*b13+a11*b14+a15*b15;
            return out;
        },
        translate: function(out, a, v) {
            out[0]=a[0]; out[1]=a[1]; out[2]=a[2]; out[3]=a[3];
            out[4]=a[4]; out[5]=a[5]; out[6]=a[6]; out[7]=a[7];
            out[8]=a[8]; out[9]=a[9]; out[10]=a[10]; out[11]=a[11];
            out[12]=a[0]*v[0]+a[4]*v[1]+a[8]*v[2]+a[12];
            out[13]=a[1]*v[0]+a[5]*v[1]+a[9]*v[2]+a[13];
            out[14]=a[2]*v[0]+a[6]*v[1]+a[10]*v[2]+a[14];
            out[15]=a[15];
            return out;
        }
    };

    // --------------------------------------------------------------
    // 2. CUSTOM STATE MACHINE (Hysteresis LOD)
    // --------------------------------------------------------------
    const viewportMachine = {
        state: 'planet', // planet | region | city | building
        _lastDistance: Infinity,
        // Hysteresis thresholds (distance from camera to origin)
        thresholds: {
            planet: 8.0,
            region: 4.0,
            city: 2.0,
            building: 1.2
        },
        dispatch: function(distance) {
            // 2.5x hysteresis band: don't switch until well past threshold
            const current = this.state;
            let next = current;
            if (distance > this.thresholds.planet * 1.5) next = 'planet';
            else if (distance > this.thresholds.region * 1.5) next = 'region';
            else if (distance > this.thresholds.city * 1.5) next = 'city';
            else next = 'building';

            if (next !== current) {
                this.state = next;
                document.getElementById('zoom-level').textContent = next;
                console.log('Zoom State:', next);
            }
        }
    };

    // --------------------------------------------------------------
    // 3. RAW WEBGL ENGINE
    // --------------------------------------------------------------
    const canvas = document.getElementById('gl-canvas');
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (!gl) {
        document.body.innerHTML = '<h1 style="color:red;text-align:center;margin-top:20px;">WebGL not supported in this browser.</h1>';
        throw new Error('WebGL not supported');
    }

    function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
        gl.viewport(0, 0, canvas.width, canvas.height);
    }
    window.addEventListener('resize', resize);
    resize();

    // --- Shader Compilation ---
    function createShader(gl, src, type) {
        const shader = gl.createShader(type);
        gl.shaderSource(shader, src);
        gl.compileShader(shader);
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
            console.error(gl.getShaderInfoLog(shader));
            gl.deleteShader(shader);
            return null;
        }
        return shader;
    }

    function createProgram(gl, vSrc, fSrc) {
        const vs = createShader(gl, vSrc, gl.VERTEX_SHADER);
        const fs = createShader(gl, fSrc, gl.FRAGMENT_SHADER);
        const prog = gl.createProgram();
        gl.attachShader(prog, vs);
        gl.attachShader(prog, fs);
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
            console.error(gl.getProgramInfoLog(prog));
            return null;
        }
        return prog;
    }

    // --- Vertex Shader ---
    const VERT_SRC = `
        attribute vec3 a_position;
        attribute vec3 a_normal;
        attribute vec2 a_uv;
        uniform mat4 u_mvp;
        uniform mat4 u_model;
        varying vec3 v_normal;
        varying vec2 v_uv;
        varying vec3 v_world_pos;
        void main() {
            vec4 worldPos = u_model * vec4(a_position, 1.0);
            v_world_pos = worldPos.xyz;
            v_normal = normalize(mat3(u_model) * a_normal);
            v_uv = a_uv;
            gl_Position = u_mvp * vec4(a_position, 1.0);
        }
    `;

    // --- Fragment Shader ---
    const FRAG_SRC = `
        precision highp float;
        varying vec3 v_normal;
        varying vec2 v_uv;
        varying vec3 v_world_pos;
        uniform vec3 u_light_dir;
        uniform bool u_cross_enabled;
        uniform float u_cross_height;
        void main() {
            if (u_cross_enabled && v_world_pos.y < u_cross_height) discard;
            float diff = max(dot(v_normal, u_light_dir), 0.0);
            // Procedural "globe" coloring (latitude/longitude pattern)
            float lat = v_uv.y;
            float lon = v_uv.x;
            vec3 base = vec3(0.1, 0.3, 0.6);
            // Add a simple continent-like checker
            float noise = sin(lat * 12.0) * cos(lon * 12.0);
            float land = step(0.0, noise);
            vec3 color = mix(base, vec3(0.2, 0.6, 0.2), land * 0.8);
            // Add a slight grid
            float grid = max(step(0.98, fract(lat * 16.0)), step(0.98, fract(lon * 16.0)));
            color = mix(color, vec3(0.8, 0.8, 0.8), grid * 0.1);
            gl_FragColor = vec4(color * (0.3 + 0.7 * diff), 1.0);
        }
    `;

    const program = createProgram(gl, VERT_SRC, FRAG_SRC);
    if (!program) throw new Error('Shader program failed');

    const loc = {
        pos: gl.getAttribLocation(program, 'a_position'),
        norm: gl.getAttribLocation(program, 'a_normal'),
        uv: gl.getAttribLocation(program, 'a_uv'),
        mvp: gl.getUniformLocation(program, 'u_mvp'),
        model: gl.getUniformLocation(program, 'u_model'),
        light: gl.getUniformLocation(program, 'u_light_dir'),
        crossEnable: gl.getUniformLocation(program, 'u_cross_enabled'),
        crossHeight: gl.getUniformLocation(program, 'u_cross_height')
    };

    // --- Sphere Geometry (Procedural) ---
    function createSphere(radius, wSeg, hSeg) {
        const verts = [], norms = [], uvs = [], idx = [];
        for (let y = 0; y <= hSeg; y++) {
            const v = y / hSeg;
            const theta = v * Math.PI;
            for (let x = 0; x <= wSeg; x++) {
                const u = x / wSeg;
                const phi = u * 2 * Math.PI;
                const xp = radius * Math.sin(theta) * Math.cos(phi);
                const yp = radius * Math.cos(theta);
                const zp = radius * Math.sin(theta) * Math.sin(phi);
                verts.push(xp, yp, zp);
                const len = Math.sqrt(xp*xp + yp*yp + zp*zp);
                norms.push(xp/len, yp/len, zp/len);
                uvs.push(u, v);
            }
        }
        for (let y = 0; y < hSeg; y++) {
            for (let x = 0; x < wSeg; x++) {
                const a = y * (wSeg+1) + x;
                const b = y * (wSeg+1) + x + 1;
                const c = (y+1) * (wSeg+1) + x;
                const d = (y+1) * (wSeg+1) + x + 1;
                idx.push(a, b, c);
                idx.push(b, d, c);
            }
        }
        return { vertices: new Float32Array(verts), normals: new Float32Array(norms), uvs: new Float32Array(uvs), indices: new Uint16Array(idx) };
    }

    const sphere = createSphere(1.0, 48, 32);

    // --- Buffers ---
    const vbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    // Interleave: position (3) + normal (3) + uv (2) = 8 floats per vertex
    const interleaved = new Float32Array(sphere.vertices.length + sphere.normals.length + sphere.uvs.length);
    let off = 0;
    for (let i = 0; i < sphere.vertices.length / 3; i++) {
        interleaved[off++] = sphere.vertices[i*3];
        interleaved[off++] = sphere.vertices[i*3+1];
        interleaved[off++] = sphere.vertices[i*3+2];
        interleaved[off++] = sphere.normals[i*3];
        interleaved[off++] = sphere.normals[i*3+1];
        interleaved[off++] = sphere.normals[i*3+2];
        interleaved[off++] = sphere.uvs[i*2];
        interleaved[off++] = sphere.uvs[i*2+1];
    }
    gl.bufferData(gl.ARRAY_BUFFER, interleaved, gl.STATIC_DRAW);

    const ibo = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, sphere.indices, gl.STATIC_DRAW);

    // --- Attribute setup ---
    const stride = 8 * 4; // 8 floats * 4 bytes
    gl.enableVertexAttribArray(loc.pos);
    gl.vertexAttribPointer(loc.pos, 3, gl.FLOAT, false, stride, 0);
    gl.enableVertexAttribArray(loc.norm);
    gl.vertexAttribPointer(loc.norm, 3, gl.FLOAT, false, stride, 3*4);
    gl.enableVertexAttribArray(loc.uv);
    gl.vertexAttribPointer(loc.uv, 2, gl.FLOAT, false, stride, 6*4);

    // --- Matrices & Camera ---
    const proj = new Float32Array(16);
    const view = new Float32Array(16);
    const model = new Float32Array(16);
    const mvp = new Float32Array(16);
    const temp = new Float32Array(16);

    let radius = 5.0;
    let theta = 0.5; // pitch
    let phi = 0.0;   // yaw
    let crossEnabled = false;
    let crossHeight = 0.0;

    // --- Mouse & Keyboard Controls ---
    let isDragging = false;
    let prevX = 0, prevY = 0;
    canvas.addEventListener('mousedown', (e) => { isDragging = true; prevX = e.clientX; prevY = e.clientY; });
    window.addEventListener('mousemove', (e) => {
        if (!isDragging) return;
        const dx = e.clientX - prevX;
        const dy = e.clientY - prevY;
        phi += dx * 0.01;
        theta += dy * 0.01;
        theta = Math.max(0.1, Math.min(Math.PI - 0.1, theta));
        prevX = e.clientX; prevY = e.clientY;
    });
    window.addEventListener('mouseup', () => { isDragging = false; });
    canvas.addEventListener('wheel', (e) => {
        e.preventDefault();
        radius *= e.deltaY > 0 ? 1.1 : 0.9;
        radius = Math.max(1.1, Math.min(20, radius));
    }, { passive: false });

    // --- Cross-Section Button ---
    document.getElementById('clip-btn').addEventListener('click', async () => {
        const resp = await fetch('/api/cross-section', { method: 'POST' });
        const json = await resp.json();
        crossEnabled = json.enabled;
        console.log('Cross-section enabled:', crossEnabled);
    });

    // --- Render Loop ---
    function animate() {
        resize();

        // Update camera
        const eye = [
            radius * Math.sin(theta) * Math.sin(phi),
            radius * Math.cos(theta),
            radius * Math.sin(theta) * Math.cos(phi)
        ];
        const center = [0, 0, 0];
        const up = [0, 1, 0];

        M.perspective(proj, 45 * Math.PI/180, canvas.width/canvas.height, 0.1, 100);
        M.lookAt(view, eye, center, up);
        M.identity(model);
        // Rotate globe slowly if not dragging? Let's keep it static, user controls it.
        // M.translate(model, model, [0, 0, 0]); // already at origin

        M.multiply(mvp, proj, view);
        M.multiply(mvp, mvp, model);

        gl.useProgram(program);
        gl.uniformMatrix4fv(loc.mvp, false, mvp);
        gl.uniformMatrix4fv(loc.model, false, model);
        gl.uniform3f(loc.light, 0.5, 0.8, 0.6);
        gl.uniform1i(loc.crossEnable, crossEnabled ? 1 : 0);
        gl.uniform1f(loc.crossHeight, crossHeight);

        gl.clearColor(0.04, 0.04, 0.1, 1);
        gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
        gl.enable(gl.DEPTH_TEST);

        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
        gl.drawElements(gl.TRIANGLES, sphere.indices.length, gl.UNSIGNED_SHORT, 0);

        // LOD dispatch based on camera distance
        const dist = Math.sqrt(eye[0]*eye[0] + eye[1]*eye[1] + eye[2]*eye[2]);
        viewportMachine.dispatch(dist);

        requestAnimationFrame(animate);
    }

    // --- Initial fetch of state ---
    fetch('/api/state').then(r => r.json()).then(data => {
        crossEnabled = data.cross_section;
        document.getElementById('zoom-level').textContent = viewportMachine.state;
    });

    animate();
    </script>
</body>
</html>
"""

# --------------------------------------------------------------
# 4. PYTHON STANDARD LIBRARY HTTP SERVER (no dependencies)
# --------------------------------------------------------------
class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode('utf-8'))
        elif self.path == '/api/state':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'cross_section': cross_section_enabled}).encode('utf-8'))
        else:
            self.send_error(404)

    def do_POST(self):
        global cross_section_enabled
        if self.path == '/api/cross-section':
            cross_section_enabled = not cross_section_enabled
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'enabled': cross_section_enabled}).encode('utf-8'))
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        # Quiet logging
        pass

def run_server():
    with socketserver.TCPServer(("", PORT), CustomHandler) as httpd:
        print(f"🌍 Server running at http://localhost:{PORT}")
        print("📂 Open this URL in your browser (Firefox/Chrome).")
        print("🚀 No external dependencies loaded. 100% custom code.")
        httpd.serve_forever()

def main() -> None:
    # Start the HTTP server first, then give it a moment to bind
    # before opening the browser; otherwise the page can load
    # before the port is listening (blank window).
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    if os.environ.get("EGREGORE_NO_BROWSER") != "1":
        time.sleep(2.0)
        webbrowser.open(f'http://localhost:{PORT}')
    server_thread.join()


if __name__ == '__main__':
    main()
