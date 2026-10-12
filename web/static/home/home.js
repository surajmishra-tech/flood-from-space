// Home page intro: the Earth from space, a radar ping on the Trishuli valley, a wall of flood water,
// then the title and GO. It only shows pictures; no flood analysis runs on this page.
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

// The 3D library loaded, so the still-picture fallback is not needed (textures may still take a while)
clearTimeout(window.__fdsFallback);

const $ = (id) => document.getElementById(id);
const qs = new URLSearchParams(location.search);
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const FREEZE = qs.has("t") ? parseFloat(qs.get("t")) : null;  // ?t=7.2 holds one moment (to check a frame)
const END = 11.4;      // everything is on screen
const CUT = 7.95;      // the camera cuts to the title shot here, hidden under the water
const AUTO_GO = 12.8;  // then the dashboard opens by itself (GO opens it sooner)

let T = FREEZE ?? (reduced ? END : 0);

// ---------- small helpers ----------
const clamp01 = (x) => Math.min(1, Math.max(0, x));
const seg = (t, a, b) => clamp01((t - a) / (b - a));
const lerp = (a, b, k) => a + (b - a) * k;
const ease = (k) => k * k * (3 - 2 * k);
const easeOut = (k) => 1 - (1 - k) ** 3;
const easeInOut = (k) => (k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2);
const D2R = Math.PI / 180;
const Y = new THREE.Vector3(0, 1, 0);
// Direction of a place on the globe, in the Earth's own frame (matches the texture layout of THREE.SphereGeometry)
const geo = (lat, lon, r = 1) => new THREE.Vector3(
  Math.cos(lat * D2R) * Math.cos(lon * D2R), Math.sin(lat * D2R), -Math.cos(lat * D2R) * Math.sin(lon * D2R)).multiplyScalar(r);
function rng(seed) { return () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

// ---------- the story's geometry ----------
const TARGET = geo(28.21, 85.36);  // Trishuli valley, Syabrubesi to Rasuwagadhi
const EAST = new THREE.Vector3(-Math.sin(85.36 * D2R), 0, -Math.cos(85.36 * D2R));
const NORTH = new THREE.Vector3().crossVectors(TARGET, EAST);
// Morning sun 20 degrees above the valley, from the east-south-east: long shadows across the Himalaya
const SUN = TARGET.clone().multiplyScalar(Math.sin(20 * D2R))
  .addScaledVector(EAST, Math.cos(20 * D2R) * Math.cos(15 * D2R))
  .addScaledVector(NORTH, -Math.cos(20 * D2R) * Math.sin(15 * D2R)).normalize();
const SPIN0 = -1.6;                                              // the Earth turns this much while we fly in
const C0 = SUN.clone().negate().addScaledVector(Y, 0.06).normalize();  // start in the Earth's shadow: an eclipse, then sunrise at ~2.2 s
const D0 = 4.3;
const C_END = geo(17.5, 81.0).normalize();                       // close-up: high above India, looking north at Nepal
const D_END = 1.3;
const LOOK_END = geo(31.0, 86.2);
const F_DIR = geo(-14, 79.5).normalize();                        // title shot: the Earth sinking into the flood
const F_DIST = 3.0;

// ---------- renderer ----------
const canvas = $("gl");
let renderer;
try {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: "high-performance" });
} catch (e) {
  document.documentElement.classList.add("nogl");
  throw e;
}
const small = Math.max(innerWidth, innerHeight) < 900 || matchMedia("(pointer: coarse)").matches;
let pixelRatio = Math.min(devicePixelRatio || 1, small ? 2 : 2);
renderer.setPixelRatio(pixelRatio);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
const maxTex = renderer.capabilities.maxTextureSize;
const aniso = Math.min(8, renderer.capabilities.getMaxAnisotropy());

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(38, innerWidth / innerHeight, 0.004, 4000);

// ---------- textures ----------
const tier = small || maxTex < 4096 ? "2k" : "4k";
const files = {  // about 5.8 MB in all; the shading and cloud pictures look the same at 2k from orbit
  day: `earth_day_${tier}.jpg`, relief: "earth_relief_2k.jpg", aux: "earth_aux_2k.jpg",
  pday: small ? "nepal_day_1k.jpg" : "nepal_day.jpg", prelief: "nepal_relief_1k.jpg",
};
let loaded = 0;
const total = Object.keys(files).length;
function setLoad(pct, step) {
  $("loadArc").style.strokeDashoffset = String(553 * (1 - pct / 100));
  $("loadPct").textContent = `${Math.round(pct)}%`;
  if (step) $("loadStep").textContent = step;
}
function loadTex(name, srgb, repeat) {
  return new Promise((resolve, reject) => {
    new THREE.TextureLoader().load(`/static/home/${name}`, (t) => {
      t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
      t.anisotropy = aniso;
      if (repeat) t.wrapS = THREE.RepeatWrapping;
      loaded += 1;
      setLoad((loaded / total) * 70);
      resolve(t);
    }, undefined, reject);
  });
}

// ---------- shaders ----------
const NOISE = /* glsl */ `
float hash12(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * .1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
vec2 hash22(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * vec3(.1031, .1030, .0973)); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.xx + p3.yz) * p3.zy); }
uniform sampler2D tNoise;
float vnoise(vec2 p) { vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f); return textureLod(tNoise, (i + f + 0.5) / 256.0, 0.0).r; }
float fbm(vec2 p) { float v = 0.0, a = 0.5; for (int i = 0; i < 4; i++) { v += a * vnoise(p); p = p * 2.02 + vec2(13.7, 7.1); a *= 0.5; } return v * 1.0667; }
`;

const NOISE3 = /* glsl */ `
uniform sampler2D tNoise;
float n3(vec3 x) { vec3 i = floor(x), f = fract(x); f = f * f * (3.0 - 2.0 * f);
  vec2 rg = textureLod(tNoise, (i.xy + vec2(37.0, 17.0) * i.z + f.xy + 0.5) / 256.0, 0.0).rg;
  return mix(rg.x, rg.y, f.z); }
`;

const GLOBE_VERT = /* glsl */ `
varying vec2 vUv;
varying vec3 vN;
void main() {
  vUv = uv;
  vN = normalize(position);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

const EARTH_FRAG = /* glsl */ `
uniform sampler2D tDay, tRelief, tAux, tPDay, tPRelief;
uniform vec4 uPatch;
uniform float uBump, uPing, uPingAmp, uReveal, uCloudShift, uSunI;
uniform vec3 uSunObj, uCamObj, uTarget;
varying vec2 vUv;
varying vec3 vN;
${NOISE3}
vec2 slopeOf(vec2 e) { vec2 c = (e - 0.5) * 2.0; return sign(c) * c * c * 2.0; }

void main() {
  vec3 n0 = normalize(vN);
  float lat = vUv.y * 180.0 - 90.0;
  float lon = vUv.x * 360.0 - 180.0;
  vec3 albedo = texture2D(tDay, vUv).rgb;
  vec3 rel = texture2D(tRelief, vUv).rgb;
  vec2 slope = slopeOf(rel.rg);
  float water = smoothstep(0.35, 0.75, rel.b);

  // sharper pictures over Nepal for the close-up
  vec2 pu = vec2((lon - uPatch.x) / (uPatch.y - uPatch.x), (lat - uPatch.z) / (uPatch.w - uPatch.z));
  if (pu.x > 0.0 && pu.x < 1.0 && pu.y > 0.0 && pu.y < 1.0) {
    vec2 edge = min(pu, 1.0 - pu) * vec2(uPatch.y - uPatch.x, uPatch.w - uPatch.z);
    float k = smoothstep(0.0, 1.2, min(edge.x, edge.y));
    albedo = mix(albedo, texture2D(tPDay, pu).rgb, k);
    slope = mix(slope, slopeOf(texture2D(tPRelief, pu).rg), k);
  }
  slope *= smoothstep(86.0, 80.0, abs(lat));

  vec3 E = normalize(vec3(n0.z, 0.0, -n0.x) + vec3(1e-6, 0.0, 0.0));
  vec3 Nn = cross(n0, E);
  vec3 nrm = normalize(n0 - uBump * (slope.x * E + slope.y * Nn));
  vec3 L = normalize(uSunObj);
  vec3 V = normalize(uCamObj - n0);
  float mu0 = dot(n0, L);
  float lit = smoothstep(-0.10, 0.12, mu0);
  float ndl = dot(nrm, L);
  float diff = mix(max(ndl, 0.0), 0.5 + 0.5 * ndl, 0.16) * lit;

  // cloud shadows (the clouds float a little above the ground)
  vec2 sunT = vec2(dot(L, E), dot(L, Nn));
  float cl = max(cos(radians(lat)), 0.08);
  vec2 suv = vUv + vec2(uCloudShift + sunT.x * 0.0035 / cl, sunT.y * 0.007);
  float ang = acos(clamp(dot(n0, uTarget), -1.0, 1.0));
  float reveal = 1.0;
  if (uReveal > 0.0 && ang < uReveal * 1.6) {
    float rag = (n3(n0 * 60.0) - 0.5) * 0.6 + (n3(n0 * 160.0) - 0.5) * 0.25;
    reveal = smoothstep(uReveal * 0.5, uReveal, ang + rag * uReveal);
  }
  float shadow = smoothstep(0.12, 0.85, texture2D(tAux, suv).r) * reveal;

  vec3 sunCol = mix(vec3(1.0, 0.58, 0.32), vec3(1.0, 0.97, 0.92), smoothstep(0.0, 0.35, mu0));
  vec3 col = albedo * diff * sunCol * uSunI * (1.0 - 0.5 * shadow);

  // sunlight glinting on the sea
  vec3 H = normalize(L + V);
  float nh = max(dot(n0, H), 0.0);
  float fres = 0.02 + 0.98 * pow(1.0 - max(dot(n0, V), 0.0), 5.0);
  col += water * lit * (1.0 - shadow) * sunCol * (pow(nh, 220.0) * 7.0 + pow(nh, 26.0) * 0.16) * (0.35 + fres);

  // city lights on the night side
  float lights = smoothstep(0.2, 0.95, texture2D(tAux, vUv).g);
  col += vec3(1.0, 0.64, 0.3) * lights * lights * (1.0 - smoothstep(-0.22, 0.04, mu0)) * 1.5;

  // air: blue haze toward the edge of the globe, warm near sunrise
  float rim = pow(1.0 - max(dot(n0, V), 0.0), 2.4);
  vec3 sky = mix(vec3(1.0, 0.45, 0.2), vec3(0.3, 0.55, 1.0), smoothstep(-0.05, 0.4, mu0));
  col = mix(col, sky * 1.2 * smoothstep(-0.25, 0.25, mu0), rim * 0.7);
  col += vec3(0.12, 0.22, 0.45) * 0.04 * lit;

  // radar ping on the Trishuli valley
  if (uPingAmp > 0.0) {
    float km = ang * 6371.0;
    float rings = 0.0;
    for (int i = 0; i < 3; i++) {
      float t = uPing - float(i) * 0.55;
      if (t > 0.0) {
        float r = t * 150.0;
        rings += smoothstep(4.0 + r * 0.03, 0.0, abs(km - r)) * exp(-t * 0.85);
      }
    }
    float core = exp(-km / 7.0) + 0.3 * exp(-km / 30.0);
    col += uPingAmp * vec3(1.0, 0.5, 0.15) * (rings * 2.6 + core * 3.5);
  }
  gl_FragColor = vec4(col, 1.0);
}`;

const CLOUD_FRAG = /* glsl */ `
uniform sampler2D tAux;
uniform vec3 uSunObj, uCamObj, uTarget;
uniform float uCloudShift, uReveal;
varying vec2 vUv;
varying vec3 vN;
${NOISE3}
void main() {
  vec3 n0 = normalize(vN);
  float c = smoothstep(0.12, 0.85, texture2D(tAux, vUv + vec2(uCloudShift, 0.0)).r);
  // fine detail only when we are close (far away it would flicker)
  float near = smoothstep(2.2, 1.45, length(uCamObj));
  if (near > 0.0 && c > 0.0) {
    float detail = n3(n0 * 420.0) * 0.6 + n3(n0 * 1100.0) * 0.4;
    c = mix(c, clamp(c * (0.35 + 1.3 * detail) - 0.05, 0.0, 1.0), near);
  }
  float ang = acos(clamp(dot(n0, uTarget), -1.0, 1.0));
  if (uReveal > 0.0 && ang < uReveal * 1.6) {  // the radar sees through the clouds
    float rag = (n3(n0 * 60.0) - 0.5) * 0.6 + (n3(n0 * 160.0) - 0.5) * 0.25;
    c *= smoothstep(uReveal * 0.5, uReveal, ang + rag * uReveal);
  }
  vec3 L = normalize(uSunObj);
  vec3 V = normalize(uCamObj - n0 * 1.006);
  float mu0 = dot(n0, L);
  float lit = smoothstep(-0.12, 0.3, mu0);
  vec3 sunCol = mix(vec3(1.0, 0.55, 0.3), vec3(1.0), smoothstep(0.0, 0.35, mu0));
  float rim = pow(1.0 - max(dot(n0, V), 0.0), 3.0);
  vec3 col = sunCol * lit * 1.5 + vec3(0.02, 0.03, 0.05);
  gl_FragColor = vec4(col, clamp(c * (0.85 + rim * 0.3), 0.0, 1.0));
}`;

const ATMO_VERT = /* glsl */ `
varying vec3 vPosW;
void main() {
  vec4 w = modelMatrix * vec4(position, 1.0);
  vPosW = w.xyz;
  gl_Position = projectionMatrix * viewMatrix * w;
}`;

const ATMO_FRAG = /* glsl */ `
uniform vec3 uSunW;
varying vec3 vPosW;
void main() {
  vec3 ro = cameraPosition;
  vec3 rd = normalize(vPosW - ro);
  vec3 pc = ro + rd * max(dot(-ro, rd), 0.0);   // closest point of this view ray to the Earth's centre
  float h = length(pc);
  float alt = max(h - 1.0, 0.0);
  float g = (exp(-alt / 0.011) + 0.3 * exp(-alt / 0.04)) * smoothstep(0.15, 0.08, alt);
  vec3 L = normalize(uSunW);
  float mu = dot(pc / max(h, 1e-4), L);
  vec3 col = vec3(0.28, 0.52, 1.0) * smoothstep(-0.25, 0.3, mu) * 1.3 + vec3(1.0, 0.42, 0.16) * exp(-mu * mu * 30.0) * 0.9;
  col += vec3(1.0, 0.72, 0.48) * pow(max(dot(rd, L), 0.0), 14.0) * 1.5 * smoothstep(-0.35, 0.05, mu);
  gl_FragColor = vec4(col * g, 1.0);
}`;

const STAR_VERT = /* glsl */ `
attribute float aSize;
attribute vec3 aColor;
uniform float uPR, uTime;
varying vec3 vColor;
void main() {
  vColor = aColor * (0.86 + 0.14 * sin(uTime * (0.7 + aSize) + position.x * 0.37));
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = aSize * uPR;
}`;
const STAR_FRAG = /* glsl */ `
varying vec3 vColor;
void main() {
  float a = smoothstep(0.5, 0.0, length(gl_PointCoord - 0.5));
  gl_FragColor = vec4(vColor * a * a, 1.0);
}`;

const SKY_FRAG = /* glsl */ `
varying vec3 vDir;
${NOISE3}
void main() {
  vec3 d = normalize(vDir);
  float b = dot(d, normalize(vec3(0.35, 0.82, -0.45)));
  float n = n3(d * 5.0) * 0.5 + n3(d * 11.0) * 0.3 + n3(d * 23.0) * 0.2;
  float dust = smoothstep(0.42, 0.72, n3(d * 7.0 + 3.0)) * exp(-b * b * 110.0);
  vec3 col = vec3(0.5, 0.56, 0.75) * exp(-b * b * 26.0) * (0.35 + n) * 0.045 * (1.0 - dust * 0.85);
  col += vec3(0.7, 0.5, 0.36) * exp(-b * b * 70.0) * n * 0.012;
  gl_FragColor = vec4(col, 1.0);
}`;
const SKY_VERT = /* glsl */ `
varying vec3 vDir;
void main() { vDir = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;

const QUAD_VERT = /* glsl */ `
varying vec2 vUv;
void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }`;

const TITLE_FRAG = /* glsl */ `
uniform sampler2D tTitle;
uniform float uAlpha, uSweep, uRise;
varying vec2 vUv;
void main() {
  vec4 t = texture2D(tTitle, vUv + vec2(0.0, -0.035 * uRise));
  float band = vUv.x + vUv.y * 0.45 - uSweep;
  float glint = exp(-band * band * 700.0);
  gl_FragColor = vec4(t.rgb * (1.7 + glint * 5.0), t.a * uAlpha);
}`;

// The flood water: a churning wall, then under water, then a calm flood that mirrors the sky
const WATER_FRAG = /* glsl */ `
uniform sampler2D tDiffuse;
uniform float uTime, uLevel, uCalm, uFoam, uSpray, uSlant, uUnder, uShake, uBtnGlow, uAspect;
uniform vec2 uBtn;
varying vec2 vUv;
${NOISE}
float caustics(vec2 uv, float t) {
  vec2 p = mod(uv * 6.28318, 6.28318) - 250.0;
  vec2 i = p;
  float c = 1.0, inten = 0.005;
  for (int n = 0; n < 4; n++) {
    float tt = t * (1.0 - (3.5 / float(n + 1)));
    i = p + vec2(cos(tt - i.x) + sin(tt + i.y), sin(tt - i.y) + cos(tt + i.x));
    c += 1.0 / length(vec2(p.x / (sin(i.x + tt) / inten), p.y / (cos(i.y + tt) / inten)));
  }
  c /= 4.0;
  c = 1.17 - pow(c, 1.4);
  return pow(abs(c), 8.0);
}
float surfaceAt(float x) {
  float churn = 1.0 - uCalm, t = uTime;
  float h = uLevel + uSlant * (x - 0.5);
  if (churn > 0.001) h += churn * (0.06 * (fbm(vec2(x * 2.6 - t * 0.9, t * 0.7)) - 0.5) + 0.016 * sin(x * 13.0 + t * 7.0) + 0.01 * sin(x * 29.0 - t * 9.0));
  h += uCalm * (0.0032 * sin(x * 37.0 + t * 1.6) + 0.0022 * sin(x * 71.0 - t * 2.1));
  return h;
}
void main() {
  vec2 uv = vUv + uShake * (vec2(vnoise(vec2(uTime * 37.0, 1.0)), vnoise(vec2(1.0, uTime * 41.0))) - 0.5) * 0.02;
  vec3 col = texture2D(tDiffuse, uv).rgb;
  if (uLevel < -0.2 && uSpray < 0.001) { gl_FragColor = vec4(col, 1.0); return; }   // no water on screen yet
  vec2 p = vec2(uv.x * uAspect, uv.y);
  float h = surfaceAt(uv.x);
  float d = h - uv.y;                       // > 0: under the water surface
  if (d < -0.12 && uSpray < 0.001) { gl_FragColor = vec4(col, 1.0); return; }      // well above the water
  float churn = 1.0 - uCalm;

  if (d > 0.0) {
    vec3 under = vec3(0.0), calm = vec3(0.0);
    if (churn > 0.001) {
    // churning wall / under water: the world behind is bent and tinted
    vec2 q = p * vec2(2.4, 3.2);
    vec2 g = vec2(fbm(q + vec2(0.0, uTime * 1.4)), fbm(q + vec2(5.2, -uTime * 1.1))) - 0.5;
    float streak = fbm(vec2(p.x * 9.0, p.y * 1.5 + uTime * 3.0));         // water pouring down the wave face
    vec2 bend = (g * 0.06 + vec2(0.0, (streak - 0.5) * 0.012 * churn * (1.0 - uUnder))) * smoothstep(0.0, 0.06, d);
    vec3 refr = textureLod(tDiffuse, uv + bend, 0.0).rgb;
    float depth = clamp(d * 1.35, 0.0, 1.0);
    vec3 face = mix(vec3(0.02, 0.105, 0.11), vec3(0.002, 0.014, 0.02), depth);
    under = mix(refr * vec3(0.2, 0.55, 0.58), face, 0.55 + 0.35 * depth);
    vec3 wn = normalize(vec3(g * 4.0 + vec2(0.0, (streak - 0.5) * 2.0), 1.0));
    under += vec3(0.75, 0.95, 1.0) * pow(max(dot(wn, normalize(vec3(0.25, 0.75, 0.6))), 0.0), 40.0) * 0.9 * churn * (1.0 - depth * 0.7);
    // whitewater: dense just under the crest, streaks pouring down the face
    float fine = fbm(vec2(p.x * 38.0, p.y * 6.0 + uTime * 3.4));
    float streaks = smoothstep(0.56, 0.72, fbm(vec2(p.x * 14.0 + g.x * 2.0, p.y * 3.0 + uTime * 2.6))) * (0.45 + 0.55 * fine);
    float dense = smoothstep(0.5, 0.62, fbm(p * vec2(18.0, 10.0) + vec2(0.0, uTime * 2.2) + g * 2.0)) * (0.4 + 0.6 * fine);
    float faceFoam = (dense * smoothstep(0.07, 0.0, d) + streaks * smoothstep(0.26, 0.02, d) * 0.5) * uFoam * churn;
    under = mix(under, vec3(0.88, 0.96, 0.98), clamp(faceFoam, 0.0, 1.0) * 0.85);
    if (uUnder > 0.001) under += vec3(0.3, 0.8, 0.82) * caustics(p * 0.9 + g * 0.3, uTime * 0.8) * (1.0 - depth) * 0.45 * uUnder;
    vec2 sp = uv - vec2(0.5 + 0.2 * sin(uTime * 0.2), 1.25);
    float ang = atan(sp.x, -sp.y);
    float shafts = pow(0.5 + 0.5 * sin(ang * 26.0 + uTime * 0.6) * sin(ang * 41.0 - uTime * 0.45), 3.0) * smoothstep(1.3, 0.3, length(sp));
    under += vec3(0.25, 0.75, 0.8) * shafts * uUnder * 0.35;
    float bub = 0.0;
    for (int k = 0; k < 2; k++) {
      float fk = float(k);
      vec2 bq = p * (7.0 + fk * 5.0) - vec2(0.0, uTime * (1.2 + fk * 0.9));
      vec2 id = floor(bq), f = fract(bq) - 0.5;
      vec2 r2 = hash22(id + fk * 17.0);
      if (r2.x > 0.9) {
        float rad = 0.04 + 0.16 * r2.y * r2.y;
        float dd = length(f - (r2 - 0.5) * 0.5);
        bub += smoothstep(rad, rad - 0.035, dd) * (0.25 + 0.75 * smoothstep(rad - 0.06, rad, dd));
      }
    }
    under += vec3(0.6, 0.9, 0.95) * bub * churn * 0.5 * smoothstep(0.02, 0.15, d);
    }

    if (uCalm > 0.001) {
    // calm flood water: a rippled mirror of the sky, stretched like real reflections
    vec2 rip = vec2(fbm(vec2(p.x * 7.0, d * 34.0 - uTime * 0.7)), fbm(vec2(p.x * 7.0 + 4.0, d * 34.0 - uTime * 0.55))) - 0.5;
    float amp = 0.002 + d * 0.09;
    vec2 muv = vec2(uv.x + rip.x * amp * 0.5, 2.0 * h - uv.y + rip.y * amp);
    vec3 refl = vec3(0.0);
    for (int i = 0; i < 5; i++) refl += textureLod(tDiffuse, muv + vec2(0.0, float(i) * (0.0025 + d * 0.012)), 0.0).rgb;
    refl /= 5.0;
    float fres = mix(0.78, 0.1, smoothstep(0.0, 0.28, d));
    calm = mix(vec3(0.003, 0.016, 0.022), refl * vec3(0.72, 0.86, 0.95), fres);
    calm += pow(vnoise(vec2(p.x * 160.0, d * 420.0 - uTime * 1.5)), 24.0) * refl * 5.0 * smoothstep(0.0, 0.03, d);
    float bx = (uv.x - uBtn.x) * uAspect, by = uBtn.y - uv.y;
    calm += uBtnGlow * vec3(0.2, 0.85, 0.95) * exp(-abs(bx) * (22.0 + 60.0 * abs(rip.x))) * smoothstep(-0.02, 0.06, by) * exp(-max(by, 0.0) * 3.0) * 0.6;
    }
    col = mix(under, calm, uCalm);
  }

  // a sharp, broken crest of foam
  if (uFoam > 0.001 && abs(d) < 0.08) {
    float n1 = fbm(vec2(p.x * 9.0 - uTime * 2.5, uv.y * 22.0 + uTime * 1.5));
    float cd = d + 0.004 - (n1 - 0.5) * 0.05;
    float crestTex = smoothstep(0.42, 0.6, fbm(p * vec2(34.0, 18.0) + vec2(-uTime * 2.0, uTime * 3.0)));
    float crest = smoothstep(-0.004, 0.0, cd) * smoothstep(0.04, 0.0, cd) * (0.4 + 0.6 * crestTex);  // sharp top, foam below
    col = mix(col, vec3(1.4, 1.55, 1.6), clamp(crest * uFoam, 0.0, 1.0));
  }
  col += uCalm * vec3(0.6, 0.85, 0.95) * 0.22 * smoothstep(0.003, 0.0, abs(d));

  if (d < 0.0 && uSpray > 0.001) {
    // spray thrown up by the wave
    float up = -d;
    vec2 sq = vec2(p.x * 26.0, (uv.y - h) * 16.0 - uTime * 6.0);
    vec2 id = floor(sq), f = fract(sq) - 0.5;
    vec2 r2 = hash22(id);
    float drop = r2.x > 0.35 ? smoothstep(0.04 + 0.07 * r2.y, 0.0, length((f - (r2 - 0.5) * 0.6) * vec2(1.0, 0.33))) : 0.0;
    vec2 mq = vec2(p.x * 70.0, (uv.y - h) * 45.0 - uTime * 9.0);
    vec2 mr = hash22(floor(mq));
    drop += mr.x > 0.6 ? smoothstep(0.12, 0.0, length(fract(mq) - 0.5 - (mr - 0.5) * 0.5)) * 0.8 : 0.0;   // fine spray
    col += vec3(1.8, 2.0, 2.1) * drop * exp(-up * 16.0) * uSpray;
    col = mix(col, vec3(0.45, 0.6, 0.65), exp(-up * 30.0) * 0.18 * uSpray);
  }
  if (d < 0.0) col = mix(col, vec3(0.05, 0.11, 0.14), uCalm * 0.3 * exp(d * 70.0));   // mist over the calm water
  gl_FragColor = vec4(col, 1.0);
}`;

// Lens: drops on the glass, sun flare, vignette, film grain, cinema bars
const FINAL_FRAG = /* glsl */ `
uniform sampler2D tDiffuse;
uniform float uTime, uFade, uBars, uDrops, uWet, uAspect;
uniform vec3 uSun;
varying vec2 vUv;
${NOISE}
void main() {
  vec2 uv = vUv;
  vec2 off = vec2(0.0);
  float rim = 0.0, hl = 0.0;
  if (uDrops > 0.001) {
    vec2 p = vec2(uv.x * uAspect, uv.y);
    for (int k = 0; k < 2; k++) {
      float fk = float(k), sc = 7.0 + fk * 8.0;
      vec2 q = p * sc;
      float slide = hash12(vec2(floor(q.x), 7.0 + fk));
      q.y += uTime * (0.12 + slide * 0.45) * step(0.62, slide);
      vec2 id = floor(q), f = fract(q) - 0.5;
      vec2 r2 = hash22(id + fk * 31.0);
      if (r2.x < 0.86 + fk * 0.05) continue;
      float rad = 0.07 + 0.14 * hash12(id + 3.7);
      vec2 dv = (f - (r2 - 0.5) * 0.5) / rad;
      float dd = dot(dv, dv);
      if (dd < 1.0) {
        off -= dv * (0.008 + 0.006 * fk) * uDrops;
        rim += smoothstep(0.6, 1.0, dd) * 0.7;
        hl += smoothstep(0.3, 0.0, length(dv - vec2(-0.3, 0.42)));
      }
    }
  }
  vec2 suv = uv + off;
  vec2 cc = suv - 0.5;
  float ca = 0.006 * dot(cc, cc);
  vec3 col = vec3(texture2D(tDiffuse, suv + cc * ca).r, texture2D(tDiffuse, suv).g, texture2D(tDiffuse, suv - cc * ca).b);
  if (uWet > 0.001) {
    vec3 b = texture2D(tDiffuse, suv + vec2(0.005, 0.0)).rgb + texture2D(tDiffuse, suv - vec2(0.005, 0.0)).rgb
           + texture2D(tDiffuse, suv + vec2(0.0, 0.007)).rgb + texture2D(tDiffuse, suv - vec2(0.0, 0.007)).rgb;
    col = mix(col, b * 0.25, uWet);
  }
  col *= 1.0 - rim * 0.3 * uDrops;
  col += hl * 0.22 * uDrops;

  if (uSun.z > 0.001) {
    vec2 sd = (uv - uSun.xy) * vec2(uAspect, 1.0);
    float r = length(sd);
    vec3 fl = vec3(1.0, 0.86, 0.7) * (exp(-r * 9.0) * 0.5 + exp(-r * 45.0) * 1.1);
    fl += vec3(0.45, 0.68, 1.0) * exp(-abs(sd.y) * 150.0) * exp(-abs(sd.x) * 1.2) * 0.85;
    for (int i = 1; i <= 3; i++) {
      vec2 gp = uSun.xy + (vec2(0.5) - uSun.xy) * (0.6 + float(i) * 0.5);
      float gr = length((uv - gp) * vec2(uAspect, 1.0)), sz = 0.02 + 0.03 * float(i);
      fl += vec3(0.3, 0.55, 0.9) * smoothstep(sz, sz * 0.55, gr) * 0.06 * uSun.z;
    }
    col += fl * uSun.z;
  }
  vec2 vc = (uv - 0.5) * vec2(uAspect * 0.62, 1.0);
  col *= 1.0 - dot(vc, vc) * 0.5;
  col += (hash12(uv * vec2(1931.0, 1087.0) + fract(uTime * 13.7) * 211.0) - 0.5) * 0.03;
  if (uv.y < uBars || uv.y > 1.0 - uBars) col = vec3(0.0);
  gl_FragColor = vec4(col * uFade, 1.0);
}`;

// ---------- noise picture ----------
function makeNoise() {
  const N = 256, rand = rng(1234), r = new Uint8Array(N * N), data = new Uint8Array(N * N * 4);
  for (let i = 0; i < N * N; i++) r[i] = Math.floor(rand() * 256);
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      const k = (y * N + x) * 4;
      data[k] = r[y * N + x];
      data[k + 1] = r[((y + 17) & 255) * N + ((x + 37) & 255)];  // one step further in z, for the 3D noise
      data[k + 2] = Math.floor(rand() * 256);
      data[k + 3] = 255;
    }
  }
  const t = new THREE.DataTexture(data, N, N, THREE.RGBAFormat);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.magFilter = t.minFilter = THREE.LinearFilter;
  t.generateMipmaps = false;
  t.needsUpdate = true;
  return t;
}
const noise = { value: makeNoise() };

// ---------- scene objects ----------
const U = {
  earth: null, clouds: null, atmo: null, stars: null,
};
function buildScene(tex) {
  const meta = tex.meta.patch;
  const earthMat = new THREE.ShaderMaterial({
    vertexShader: GLOBE_VERT, fragmentShader: EARTH_FRAG,
    uniforms: {
      tDay: { value: tex.day }, tRelief: { value: tex.relief }, tAux: { value: tex.aux },
      tPDay: { value: tex.pday }, tPRelief: { value: tex.prelief },
      uPatch: { value: new THREE.Vector4(meta.lon0, meta.lon1, meta.lat0, meta.lat1) },
      uBump: { value: 1.2 }, uPing: { value: -1 }, uPingAmp: { value: 0 }, uReveal: { value: 0 },
      uCloudShift: { value: 0 }, uSunI: { value: 1.9 }, tNoise: noise,
      uSunObj: { value: new THREE.Vector3() }, uCamObj: { value: new THREE.Vector3() }, uTarget: { value: TARGET.clone() },
    },
  });
  const earth = new THREE.Mesh(new THREE.SphereGeometry(1, 256, 128), earthMat);
  scene.add(earth);

  const cloudMat = new THREE.ShaderMaterial({
    vertexShader: GLOBE_VERT, fragmentShader: CLOUD_FRAG, transparent: true, depthWrite: false,
    uniforms: {
      tAux: { value: tex.aux }, tNoise: noise, uSunObj: earthMat.uniforms.uSunObj, uCamObj: earthMat.uniforms.uCamObj,
      uTarget: earthMat.uniforms.uTarget, uCloudShift: earthMat.uniforms.uCloudShift, uReveal: earthMat.uniforms.uReveal,
    },
  });
  const clouds = new THREE.Mesh(new THREE.SphereGeometry(1.006, 192, 96), cloudMat);
  clouds.renderOrder = 1;
  earth.add(clouds);

  const atmo = new THREE.Mesh(new THREE.SphereGeometry(1.16, 160, 80), new THREE.ShaderMaterial({
    vertexShader: ATMO_VERT, fragmentShader: ATMO_FRAG, side: THREE.BackSide, transparent: true,
    blending: THREE.AdditiveBlending, depthWrite: false, uniforms: { uSunW: { value: SUN.clone() } },
  }));
  atmo.renderOrder = 2;
  scene.add(atmo);

  // stars: a fixed seed, so every visit shows the same sky
  const rand = rng(20260826), n = 5200;
  const pos = new Float32Array(n * 3), col = new Float32Array(n * 3), size = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const u = rand() * 2 - 1, th = rand() * Math.PI * 2, r = Math.sqrt(1 - u * u);
    pos.set([r * Math.cos(th) * 900, u * 900, r * Math.sin(th) * 900], i * 3);
    const m = rand() ** 7, t = rand();
    const c = t < 0.14 ? [1.0, 0.8, 0.62] : t < 0.34 ? [0.76, 0.86, 1.0] : [1, 1, 1];
    const b = 0.22 + m * 5.5;
    col.set([c[0] * b, c[1] * b, c[2] * b], i * 3);
    size[i] = 1.1 + m * 3.4;
  }
  const sg = new THREE.BufferGeometry();
  sg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  sg.setAttribute("aColor", new THREE.BufferAttribute(col, 3));
  sg.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
  const stars = new THREE.Points(sg, new THREE.ShaderMaterial({
    vertexShader: STAR_VERT, fragmentShader: STAR_FRAG, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uPR: { value: pixelRatio }, uTime: { value: 0 } },
  }));
  stars.renderOrder = -1;
  scene.add(stars);
  const sky = new THREE.Mesh(new THREE.SphereGeometry(1500, 48, 24), new THREE.ShaderMaterial({
    vertexShader: SKY_VERT, fragmentShader: SKY_FRAG, side: THREE.BackSide, depthWrite: false, uniforms: { tNoise: noise },
  }));
  sky.renderOrder = -2;
  scene.add(sky);

  Object.assign(U, { earth, clouds, atmo, stars, sky, earthMat });
}

// ---------- title (drawn in WebGL so the water can bend and mirror it) ----------
const titleCanvas = document.createElement("canvas");
const titleTex = new THREE.CanvasTexture(titleCanvas);
titleTex.colorSpace = THREE.SRGBColorSpace;
const titleMat = new THREE.ShaderMaterial({
  vertexShader: QUAD_VERT, fragmentShader: TITLE_FRAG, transparent: true, depthTest: false, depthWrite: false,
  uniforms: { tTitle: { value: titleTex }, uAlpha: { value: 0 }, uSweep: { value: -1 }, uRise: { value: 1 } },
});
const titleScene = new THREE.Scene();
titleScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), titleMat));
const flatCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

let L = null;  // current layout
function layout() {
  const w = innerWidth, h = innerHeight, portrait = w / h < 0.85;
  const water = portrait ? 0.34 : 0.32;                // calm water line, as a share of the height from the bottom
  return {
    w, h, portrait, water,
    limb: water + (portrait ? 0.13 : 0.17),            // top edge of the Earth
    s1: Math.min(w * (portrait ? 0.12 : 0.074), h * 0.125),
    y1: h * (portrait ? 0.19 : 0.17),                  // top of the first title line
    goY: h * (1 - water * 0.6),
    bars: w / h > 1.15 ? Math.max(0.045, (1 - (w / h) / 2.39) / 2) : 0.05,
  };
}

function drawTitle() {
  const pr = renderer.getPixelRatio();
  const W = Math.round(L.w * pr), H = Math.round(L.h * pr);
  if (titleCanvas.width !== W || titleCanvas.height !== H) titleTex.dispose();  // a new size needs a new GPU texture
  titleCanvas.width = W;
  titleCanvas.height = H;
  const g = titleCanvas.getContext("2d");
  g.clearRect(0, 0, W, H);
  const fam = 'Jost, Futura, "Century Gothic", "Trebuchet MS", sans-serif';
  const widthOf = (text, weight, size, tr) => {
    g.font = `${weight} ${size}px ${fam}`;
    return [...text].reduce((a, ch) => a + g.measureText(ch).width, 0) + size * tr * (text.length - 1);
  };
  const tracked = (text, weight, size, tr, y, fill) => {
    g.font = `${weight} ${size}px ${fam}`;
    const ws = [...text].map((ch) => g.measureText(ch).width);
    let x = W / 2 - (ws.reduce((a, b) => a + b, 0) + size * tr * (text.length - 1)) / 2;
    g.fillStyle = fill;
    [...text].forEach((ch, i) => { g.fillText(ch, x, y); x += ws[i] + size * tr; });
    return x;
  };
  let s1 = L.s1 * pr;
  const w1 = widthOf("FLOOD DAMAGE", 600, s1, 0.12);
  if (w1 > W * 0.9) s1 *= (W * 0.9) / w1;
  const s2 = s1 * 0.42;
  const base1 = L.y1 * pr + s1 * 0.72;
  const base2 = base1 + s1 * 0.74;

  g.shadowColor = "rgba(0, 6, 14, .8)";
  g.shadowBlur = s1 * 0.28;
  g.shadowOffsetY = s1 * 0.04;
  const grad = g.createLinearGradient(0, base1 - s1 * 0.72, 0, base1);
  grad.addColorStop(0, "#ffffff");
  grad.addColorStop(0.55, "#eaf5ff");
  grad.addColorStop(1, "#9cc0db");
  tracked("FLOOD DAMAGE", 600, s1, 0.12, base1, grad);
  g.shadowBlur = s2 * 0.4;
  tracked("FROM SPACE", 300, s2, 0.62, base2, "#d8ebf9");
  // amber hairlines either side of "FROM SPACE"
  const w2 = widthOf("FROM SPACE", 300, s2, 0.62);
  g.shadowBlur = 0;
  g.fillStyle = "rgba(255, 180, 94, .85)";
  const len = Math.min(s1 * 1.1, (W - w2) * 0.2), gap = s2 * 0.9, ly = base2 - s2 * 0.36;
  g.fillRect(W / 2 - w2 / 2 - gap - len, ly, len, Math.max(1, pr));
  g.fillRect(W / 2 + w2 / 2 + gap - s2 * 0.62 * 0.5, ly, len, Math.max(1, pr));
  titleTex.needsUpdate = true;

  const css = document.documentElement.style;
  css.setProperty("--kick-y", `${(L.y1 * pr - s1 * 0.05) / pr - 40}px`);
  css.setProperty("--tag-y", `${(base2 + s2 * 0.95) / pr}px`);
  css.setProperty("--go-y", `${L.goY}px`);
}

// ---------- post-processing ----------
let composer, waterPass, finalPass, bloom, outputPass;
function buildComposer() {
  const rt = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType });
  composer = new EffectComposer(renderer, rt);
  composer.addPass(new RenderPass(scene, camera));
  const tp = new RenderPass(titleScene, flatCam);
  tp.clear = false;
  composer.addPass(tp);
  waterPass = new ShaderPass(new THREE.ShaderMaterial({
    vertexShader: QUAD_VERT, fragmentShader: WATER_FRAG,
    uniforms: {
      tDiffuse: { value: null }, tNoise: noise, uTime: { value: 0 }, uLevel: { value: -1 }, uCalm: { value: 0 }, uFoam: { value: 0 },
      uSpray: { value: 0 }, uSlant: { value: 0 }, uUnder: { value: 0 }, uShake: { value: 0 }, uBtnGlow: { value: 0 },
      uAspect: { value: 1 }, uBtn: { value: new THREE.Vector2(0.5, 0.16) },
    },
  }));
  composer.addPass(waterPass);
  bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.75, 0.6, 1.85);
  composer.addPass(bloom);
  outputPass = new OutputPass();
  composer.addPass(outputPass);
  finalPass = new ShaderPass(new THREE.ShaderMaterial({
    vertexShader: QUAD_VERT, fragmentShader: FINAL_FRAG,
    uniforms: {
      tDiffuse: { value: null }, tNoise: noise, uTime: { value: 0 }, uFade: { value: 0 }, uBars: { value: 0 }, uDrops: { value: 0 },
      uWet: { value: 0 }, uAspect: { value: 1 }, uSun: { value: new THREE.Vector3() },
    },
  }));
  composer.addPass(finalPass);
}

function fit() {
  L = layout();
  renderer.setPixelRatio(pixelRatio);
  renderer.setSize(L.w, L.h, false);
  composer.setPixelRatio(pixelRatio);
  composer.setSize(L.w, L.h);
  camera.aspect = L.w / L.h;
  camera.fov = L.portrait ? 50 : 38;
  camera.updateProjectionMatrix();
  waterPass.uniforms.uAspect.value = finalPass.uniforms.uAspect.value = L.w / L.h;
  waterPass.uniforms.uBtn.value.set(0.5, L.water * 0.6);
  U.stars.material.uniforms.uPR.value = pixelRatio;
  drawTitle();
}

// ---------- camera path ----------
const v1 = new THREE.Vector3(), v2 = new THREE.Vector3(), v3 = new THREE.Vector3();
function slerpDir(a, b, k, out) {
  const th = Math.acos(THREE.MathUtils.clamp(a.dot(b), -1, 1));
  if (th < 1e-5) return out.copy(a);
  const s = Math.sin(th);
  return out.copy(a).multiplyScalar(Math.sin((1 - k) * th) / s).addScaledVector(b, Math.sin(k * th) / s);
}
const spinAt = (t) => (t < CUT ? SPIN0 * (1 - easeOut(seg(t, 0, 6.6))) : 0.0015 * (t - CUT));
function placeCamera(t) {
  camera.up.copy(Y);
  if (t < CUT) {
    const dir = slerpDir(C0, C_END, easeInOut(seg(t, 0, 6.9)), v1);
    const dist = lerp(D0, D_END, easeInOut(seg(t, 0.2, 6.9))) * (1 - 0.012 * seg(t, 6.9, CUT));
    camera.position.copy(dir).multiplyScalar(dist);
    camera.lookAt(v2.set(0, 0, 0).lerp(LOOK_END, easeInOut(seg(t, 1.2, 6.9))));
  } else {
    // the title shot: the top of the Earth just above the flood water
    const d = F_DIST - 0.06 * Math.min((t - CUT) / 40, 1);
    camera.position.copy(F_DIR).multiplyScalar(d);
    camera.lookAt(0, 0, 0);
    const alpha = Math.asin(1 / d), yTop = 2 * L.limb - 1;
    camera.rotateX(alpha - Math.atan(yTop * Math.tan((camera.fov / 2) * D2R)));
  }
  camera.updateMatrixWorld();
}

// ---------- heads-up display ----------
const hud = $("hud"), target = $("target"), readout = $("readout");
let frameNo = 0;
function updateHud(t, spin) {
  hud.classList.toggle("on", t > 0.8 && !leaving);
  hud.classList.toggle("ended", t > 9.3);
  $("controls").classList.toggle("ended", t > 9.3);
  if (frameNo % 4 === 0 && t < 9.3) {
    const c = v1.copy(camera.position).applyAxisAngle(Y, -spin);
    const lat = Math.asin(c.y / c.length()) / D2R, lon = Math.atan2(-c.z, c.x) / D2R;
    const alt = Math.round((c.length() - 1) * 6371);
    readout.textContent = `LAT ${Math.abs(lat).toFixed(2)}° ${lat >= 0 ? "N" : "S"} · LON ${Math.abs(lon).toFixed(2)}° ${lon >= 0 ? "E" : "W"} · ALT ${alt.toLocaleString("en-US")} KM`;
  }
  const w = v2.copy(TARGET).applyAxisAngle(Y, spin);
  const facing = v3.copy(camera.position).sub(w).normalize().dot(w) > 0.15;
  const p = w.clone().project(camera);
  const show = facing && Math.abs(p.x) < 0.9 && Math.abs(p.y) < 0.9 && t > 5.1 && t < 6.6 && !leaving;
  target.classList.toggle("on", show);
  if (show) {
    target.style.setProperty("--tx", `${((p.x + 1) / 2) * L.w}px`);
    target.style.setProperty("--ty", `${((1 - p.y) / 2) * L.h}px`);
  }
}
function showUi(t) {
  const on = (sel, at) => document.querySelector(sel).classList.toggle("show", t >= at && !leaving);
  on(".kicker", 9.4);
  on(".tagline", 9.9);
  on("#go", 10.3);
  on(".go-caption", 10.7);
  on("#credits", 10.9);
  $("skip").classList.toggle("gone", t >= 10.3);
  // the ring around GO fills up until the dashboard opens
  $("countRing").style.strokeDashoffset = String(352 * (1 - seg(t, 10.3, AUTO_GO)));
}

// ---------- the Sun's lens flare ----------
function sunFlare() {
  const cp = camera.position;
  const ang = Math.acos(THREE.MathUtils.clamp(v1.copy(cp).negate().normalize().dot(SUN), -1, 1));
  const m = ang - Math.asin(Math.min(1, 1 / cp.length()));   // > 0: the Sun has come out from behind the Earth
  const p = v2.copy(cp).addScaledVector(SUN, 100).project(camera);
  if (p.z > 1) return [0, 0, 0];
  const vis = 1 - THREE.MathUtils.smoothstep(Math.max(Math.abs(p.x), Math.abs(p.y)), 0.9, 1.5);
  const k = THREE.MathUtils.smoothstep(m, -0.004, 0.02) * (0.55 + 2.6 * Math.exp(-Math.max(m, 0) * 30));
  return [p.x * 0.5 + 0.5, p.y * 0.5 + 0.5, k * vis * 0.9];
}

// ---------- sound (made in the browser; off until the viewer turns it on) ----------
class Score {
  constructor() { this.ctx = null; this.bus = null; }
  build() {
    const ctx = (this.ctx = new (window.AudioContext || window.webkitAudioContext)());
    const nb = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
    const d = nb.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    this.noiseBuf = nb;
    const comp = ctx.createDynamicsCompressor();
    comp.connect(ctx.destination);
    this.master = ctx.createGain();
    this.master.gain.value = 0;
    this.master.connect(comp);
    this.lp = ctx.createBiquadFilter();
    this.lp.type = "lowpass";
    this.lp.frequency.value = 18000;
    this.lp.connect(this.master);
    this.verb = ctx.createConvolver();
    const n = ctx.sampleRate * 3.2, ib = ctx.createBuffer(2, n, ctx.sampleRate);
    for (let c = 0; c < 2; c++) { const x = ib.getChannelData(c); for (let i = 0; i < n; i++) x[i] = (Math.random() * 2 - 1) * (1 - i / n) ** 3; }
    this.verb.buffer = ib;
    const vg = ctx.createGain();
    vg.gain.value = 0.4;
    this.verb.connect(vg);
    vg.connect(this.lp);
    // low drone
    this.drone = ctx.createGain();
    this.drone.gain.value = 0;
    const df = ctx.createBiquadFilter();
    df.type = "lowpass";
    df.frequency.value = 260;
    df.connect(this.drone);
    this.drone.connect(this.lp);
    [[55, "sine", 0.5], [82.41, "triangle", 0.2], [110.3, "sine", 0.1]].forEach(([hz, type, gain]) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = type; o.frequency.value = hz; g.gain.value = gain;
      o.connect(g); g.connect(df); o.start();
    });
    // water lapping
    const ns = this.noise(), nf = ctx.createBiquadFilter();
    nf.type = "lowpass"; nf.frequency.value = 420;
    this.bed = ctx.createGain(); this.bed.gain.value = 0;
    ns.connect(nf); nf.connect(this.bed); this.bed.connect(this.lp); ns.start();
    const lfo = ctx.createOscillator(), lg = ctx.createGain();
    lfo.frequency.value = 0.18; lg.gain.value = 0.02;
    lfo.connect(lg); lg.connect(this.bed.gain); lfo.start();
  }
  noise() { const s = this.ctx.createBufferSource(); s.buffer = this.noiseBuf; s.loop = true; return s; }
  env(g, at, peak, attack, release) {
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(peak, at + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, at + attack + release);
  }
  tone(at, type, f0, f1, glide, peak, attack, release, verb = 0) {
    const o = this.ctx.createOscillator(), g = this.ctx.createGain();
    o.type = type;
    o.frequency.setValueAtTime(f0, at);
    if (f1 !== f0) o.frequency.exponentialRampToValueAtTime(f1, at + glide);
    this.env(g, at, peak, attack, release);
    o.connect(g); g.connect(this.bus);
    if (verb) { const s = this.ctx.createGain(); s.gain.value = verb; g.connect(s); s.connect(this.verb); }
    o.start(at); o.stop(at + attack + release + 0.05);
  }
  hiss(at, type, f0, f1, glide, q, peak, attack, release) {
    const s = this.noise(), f = this.ctx.createBiquadFilter(), g = this.ctx.createGain();
    f.type = type; f.Q.value = q;
    f.frequency.setValueAtTime(f0, at);
    f.frequency.exponentialRampToValueAtTime(f1, at + glide);
    this.env(g, at, peak, attack, release);
    s.connect(f); f.connect(g); g.connect(this.bus);
    s.start(at); s.stop(at + attack + release + 0.05);
  }
  plan(t) {
    const ctx = this.ctx, now = ctx.currentTime + 0.05;
    if (this.bus) this.bus.disconnect();
    this.bus = ctx.createGain();
    this.bus.connect(this.lp);
    const at = (x) => now + Math.max(0, x - t);
    const future = (x) => x >= t - 0.02;
    this.drone.gain.cancelScheduledValues(now);
    this.drone.gain.setTargetAtTime(t < 9.5 ? 0.16 : 0.07, now, 0.6);
    if (t < 9.5) this.drone.gain.setTargetAtTime(0.07, at(9.5), 1.5);
    this.bed.gain.cancelScheduledValues(now);
    this.bed.gain.setTargetAtTime(t >= 9 ? 0.05 : 0, now, 0.4);
    if (t < 9) this.bed.gain.setTargetAtTime(0.05, at(9), 1.0);
    this.lp.frequency.cancelScheduledValues(now);
    this.lp.frequency.setTargetAtTime(t > 6.9 && t < 8.9 ? 380 : 18000, now, 0.08);
    [4.6, 5.15, 5.7].filter(future).forEach((x) => this.tone(at(x), "sine", 1250, 980, 0.9, 0.2, 0.008, 1.3, 0.6));
    if (future(5.4)) this.hiss(at(5.4), "bandpass", 250, 2600, 1.4, 4, 0.22, 1.3, 0.15);
    if (future(6.8)) {
      this.tone(at(6.8), "sine", 74, 30, 1.3, 0.9, 0.01, 2.2);
      this.hiss(at(6.8), "lowpass", 9000, 350, 1.8, 0.7, 0.65, 0.02, 2.0);
      this.lp.frequency.setTargetAtTime(380, at(6.9), 0.06);
    }
    for (let i = 0; i < 18; i++) {
      const x = 7.0 + ((i * 0.61803) % 1) * 1.8;
      if (future(x)) this.tone(at(x), "sine", 260 + (i % 5) * 60, 1100, 0.07, 0.05, 0.005, 0.08);
    }
    if (future(8.9)) {
      this.lp.frequency.setTargetAtTime(18000, at(8.9), 0.25);
      this.hiss(at(8.9), "highpass", 1500, 1500, 0.1, 0.7, 0.5, 0.01, 1.0);
      for (let i = 0; i < 10; i++) this.tone(at(8.95 + ((i * 0.381966) % 1) * 1.8), "sine", 1800 + (i % 4) * 400, 1800 + (i % 4) * 400, 0.05, 0.035, 0.003, 0.06);
    }
    if (future(9.5)) [196, 293.66, 392, 587.33].forEach((hz, i) => this.tone(at(9.5 + i * 0.06), "sine", hz, hz, 0.1, 0.05, 0.6, 5, 1));
  }
  async on(t) {
    if (!this.ctx) this.build();
    await this.ctx.resume();
    this.master.gain.cancelScheduledValues(this.ctx.currentTime);
    this.master.gain.setTargetAtTime(0.85, this.ctx.currentTime, 0.3);
    this.plan(t);
  }
  off() {
    if (!this.ctx) return;
    this.master.gain.setTargetAtTime(0, this.ctx.currentTime, 0.12);
    if (this.bus) { this.bus.disconnect(); this.bus = null; }
  }
  dive() {
    if (!this.ctx || !this.bus) return;
    const at = this.ctx.currentTime + 0.02;
    this.hiss(at, "bandpass", 2500, 180, 0.8, 2, 0.4, 0.05, 0.9);
    this.tone(at + 0.35, "sine", 70, 32, 0.8, 0.6, 0.01, 1.2);
  }
}
const score = new Score();
let soundOn = false;
$("sound").addEventListener("click", async () => {
  soundOn = !soundOn;
  $("sound").setAttribute("aria-pressed", String(soundOn));
  $("sound").textContent = soundOn ? "Sound on" : "Sound off";
  if (soundOn) await score.on(T); else score.off();
});

// ---------- GO, skip and replay ----------
const go = $("go");
let leaving = false, leaveT = 0;
function leave() {  // the water rises over everything, then the dashboard opens
  if (leaving) return;
  leaving = true;
  leaveT = clockNow();
  mark(`intro over: story ${T.toFixed(2)} s, ${slow.n} long frames lost ${slow.lost.toFixed(2)} s (worst ${(slow.worst * 1000).toFixed(0)} ms)`);
  if (soundOn) score.dive();
  setTimeout(() => { location.href = go.href; }, reduced ? 0 : 950);
}
go.addEventListener("click", (e) => {
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button === 1) return;  // opening in a new tab: no animation
  e.preventDefault();
  leave();
});
addEventListener("pageshow", (e) => { if (e.persisted) { leaving = false; T = 0; } });
function jump(t) {
  T = t;
  if (soundOn) score.plan(T);
}
$("skip").addEventListener("click", () => jump(END));
addEventListener("keydown", (e) => { if (e.key === "Escape" && T < END) jump(END); });

// The dashboard opens with the main Trishuli result if this copy has it
fetch("/api/runs").then((r) => r.json()).then((runs) => {
  const done = runs.filter((r) => r.status === "done");
  const pick = done.find((r) => r.run_id === "fb7507902568")
    || done.find((r) => /trishuli/i.test(r.name || "") && r.flood_date === "2026-08-26")
    || done.find((r) => r.flood_date === "2026-08-26") || done[0];
  if (pick) go.href = `/app?run=${encodeURIComponent(pick.run_id)}`;
}).catch(() => {});

// ---------- the story, frame by frame ----------
const t0 = performance.now();
const clockNow = () => (performance.now() - t0) / 1000;
let last = performance.now(), slowFrames = 0, measured = 0;

const slow = { n: 0, lost: 0, worst: 0, startReal: 0 };
function frame(now) {
  const raw = (now - last) / 1000;
  const dt = Math.min(0.05, raw);
  last = now;
  if (raw > 0.05) { slow.n += 1; slow.lost += raw - 0.05; slow.worst = Math.max(slow.worst, raw); if (perf) console.log(`[perf] long frame ${(raw * 1000).toFixed(0)} ms at story ${T.toFixed(2)} s`); }
  if (FREEZE === null) T += dt;
  const t = T;
  const time = FREEZE === null ? clockNow() : t;
  frameNo += 1;

  // adapt the quality on slow computers
  if (FREEZE === null && measured < 40) {
    measured += 1;
    if (dt > 1 / 40) slowFrames += 1;
    if (measured === 40 && slowFrames > 20 && pixelRatio > 0.6) {
      pixelRatio = Math.max(0.6, pixelRatio * 0.75);
      fit();
      measured = 0;
      slowFrames = 0;
    }
  }

  const spin = spinAt(t);
  U.earth.rotation.y = spin;
  placeCamera(t);
  const eu = U.earthMat.uniforms;
  eu.uSunObj.value.copy(SUN).applyAxisAngle(Y, -spin);
  eu.uCamObj.value.copy(camera.position).applyAxisAngle(Y, -spin);
  eu.uCloudShift.value = 0.0006 * time;
  eu.uReveal.value = 0.13 * ease(seg(t, 4.2, 6.2));
  eu.uPing.value = t >= 4.6 ? (t - 4.6) % 3.4 : -1;
  eu.uPingAmp.value = ease(seg(t, 4.6, 4.9)) * (t > CUT ? 0.75 : 1);
  eu.uBump.value = t < CUT ? lerp(1.2, 2.6, ease(seg(t, 3.5, 6.6))) : 1.6;
  U.stars.material.uniforms.uTime.value = time;

  // the title rises out of the water and a glint runs across it now and then
  titleMat.uniforms.uAlpha.value = qs.has("clean") ? 0 : ease(seg(t, 8.1, 9.4));
  titleMat.uniforms.uRise.value = 1 - easeOut(seg(t, 8.1, 9.8));
  const sw = (t - 9.5) % 9;
  titleMat.uniforms.uSweep.value = t > 9.5 && sw < 1.2 ? lerp(-0.3, 1.6, sw / 1.2) : -2;

  // the flood water
  const w = waterPass.uniforms;
  const rise = seg(t, 6.55, 7.5), surface = seg(t, 8.75, 9.95);
  let level = t < 8.75 ? lerp(-0.25, 1.3, rise * rise) : lerp(1.3, L.water, easeOut(surface));
  let calm = ease(seg(t, 8.7, 10.1));
  let foam = t < 6.55 ? 0 : t < 8.75 ? 1 : 0.55 * (1 - ease(seg(t, 8.9, 9.7)));
  let fade = ease(seg(t, 0, 1.8));
  if (leaving) {
    const k = seg(clockNow() - leaveT, 0, 0.85);
    level = lerp(L.water, 1.35, k * k);
    calm *= 1 - 0.7 * k;
    foam = Math.max(foam, k);
    fade *= 1 - 0.55 * ease(seg(k, 0.55, 1));
  }
  w.uTime.value = time;
  w.uLevel.value = level;
  w.uCalm.value = calm;
  w.uFoam.value = foam;
  w.uSpray.value = t > 6.45 && t < 7.65 ? 1 : t >= 8.75 ? 0.3 * (1 - seg(t, 8.9, 9.4)) : 0;
  w.uSlant.value = lerp(0.18, 0, rise);
  w.uUnder.value = ease(seg(t, 7.35, 7.7)) * (1 - ease(seg(t, 8.75, 9.3)));
  w.uShake.value = seg(t, 6.2, 6.9) * (1 - seg(t, 7.4, 8.4));
  w.uBtnGlow.value = ease(seg(t, 10.3, 11.2)) * (go.matches(":hover, :focus-visible") ? 1.6 : 1);

  const f = finalPass.uniforms;
  f.uTime.value = time;
  f.uFade.value = fade;
  const bars = L.bars * (1 - ease(seg(t, 9.3, 10.3)));
  f.uBars.value = bars;
  document.documentElement.style.setProperty("--bar", `${(bars * L.h).toFixed(1)}px`);
  f.uDrops.value = t > 8.95 && !reduced ? 1 - ease(seg(t, 9.6, 11.2)) : 0;
  f.uWet.value = t > 8.9 ? 0.5 * (1 - ease(seg(t, 8.9, 9.5))) : 0;
  const s = t < CUT ? sunFlare() : [0, 0, 0];
  f.uSun.value.set(s[0], s[1], s[2] * fade);

  updateHud(t, spin);
  showUi(t);
  if (t >= AUTO_GO && FREEZE === null) leave();

  composer.render(dt);
  requestAnimationFrame(frame);
}

// ---------- start ----------
async function precompile() {
  if (renderer.compileAsync) {
    try {
      const quad = new THREE.PlaneGeometry(2, 2);
      const sceneOf = (mats) => { const sc = new THREE.Scene(); mats.forEach((m) => sc.add(new THREE.Mesh(quad, m))); return sc; };
      renderer.setRenderTarget(composer.renderTarget1);          // the 3D scene and most effects draw into off-screen pictures
      await renderer.compileAsync(scene, camera);
      setLoad(82);
      await renderer.compileAsync(titleScene, flatCam);
      await renderer.compileAsync(sceneOf([waterPass.material, ...bloom.separableBlurMaterials, bloom.compositeMaterial,
        bloom.materialHighPassFilter, bloom.blendMaterial]), flatCam);
      setLoad(92);
      renderer.setRenderTarget(null);                            // the last effect draws to the screen
      await renderer.compileAsync(sceneOf([finalPass.material]), flatCam);
    } catch { /* anything left is compiled on the first frame */ }
    renderer.setRenderTarget(null);
  }
  composer.render(0);
  setLoad(97);
}

// ?perf shows timings in the console; ?off=bloom,water,... switches parts off to find what is slow
const perf = qs.has("perf");
const mark = (label) => { if (perf) console.log(`[perf] ${label} at ${(performance.now() / 1000).toFixed(2)} s`); };
function switchOff(list) {
  const off = new Set(list.split(",").filter(Boolean));
  if (off.has("bloom")) bloom.enabled = false;
  if (off.has("water")) waterPass.enabled = false;
  if (off.has("final")) finalPass.enabled = false;
  if (off.has("sky")) U.sky.visible = false;
  if (off.has("stars")) U.stars.visible = false;
  if (off.has("clouds")) U.clouds.visible = false;
  if (off.has("atmo")) U.atmo.visible = false;
  if (off.has("earth")) U.earth.material.visible = false;
}

async function start() {
  mark("script running");
  setLoad(4, "Loading the Earth from Sentinel-2");
  const metaP = fetch("/static/home/textures.json").then((r) => r.json());
  const [day, relief, aux, pday, prelief, meta] = await Promise.all([
    loadTex(files.day, true), loadTex(files.relief, false), loadTex(files.aux, false, true),
    loadTex(files.pday, true), loadTex(files.prelief, false), metaP,
  ]);
  mark("pictures loaded");
  setLoad(72, "Preparing the flood scene");
  $("loader").classList.add("preparing");
  buildScene({ day, relief, aux, pday, prelief, meta });
  buildComposer();
  if (qs.has("off")) switchOff(qs.get("off"));
  const fonts = Promise.all([document.fonts.load('600 80px "Jost"'), document.fonts.load('300 80px "Jost"')]);
  await Promise.race([fonts, new Promise((r) => setTimeout(r, 2500))]).catch(() => {});
  fit();
  addEventListener("resize", fit);
  document.fonts.ready.then(() => drawTitle());
  // compile every shader now, behind the loading bar, so the film does not stutter later
  await precompile();
  mark("shaders ready");
  setLoad(100, "Ready");
  $("loader").classList.remove("preparing");
  await new Promise((r) => setTimeout(r, 350));
  $("loader").classList.add("done");
  if (qs.has("clean")) document.body.classList.add("clean");
  last = performance.now();
  requestAnimationFrame(frame);
}
start().catch((e) => {
  console.error(e);
  document.documentElement.classList.add("nogl");
});
