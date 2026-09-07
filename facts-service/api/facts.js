// Clicker facts service. One endpoint, POST /api/facts, that turns "what's playing"
// into five rare-but-true facts via Claude, paid for by the person who deployed it.
//
// Guardrails, in order of strength:
//   1. The Anthropic workspace this key belongs to has a hard monthly spend limit (set in the console).
//   2. MONTHLY_BUDGET_USD here: the function stops answering once its own meter reaches it.
//   3. PER_DEVICE_DAILY: per-IP daily cap.
//   4. Cache: the same show asked twice costs nothing the second time.
// Storage: Upstash Redis via REST if UPSTASH_REDIS_REST_URL/TOKEN (or KV_REST_API_URL/TOKEN)
// are set, otherwise per-instance memory (best effort; the console limit is the real cap).

import Anthropic from "@anthropic-ai/sdk";
import { createHash } from "node:crypto";

const MODEL = process.env.FACTS_MODEL || "claude-opus-5";
const BUDGET = parseFloat(process.env.MONTHLY_BUDGET_USD || "2");
const PER_DEVICE_DAILY = parseInt(process.env.PER_DEVICE_DAILY || "25", 10);
const PRICES = { // USD per million tokens: [input, output]
  "claude-opus-5": [5, 25], "claude-opus-4-8": [5, 25], "claude-sonnet-5": [2, 10], "claude-haiku-4-5": [1, 5],
};
const SYSTEM =
  "You write trivia for a living-room TV remote app. The user tells you what is on screen " +
  "(a show, film, video, song, or channel). Reply with 5 genuinely surprising, specific, " +
  "little-known facts about that work or its people that you are highly confident are TRUE " +
  "and documented (production stories, casting near-misses, real-world consequences, records, " +
  "odd coincidences). No spoilers for plot twists. One or two sentences each, plain text, no markdown. " +
  "If you do not actually know this work well enough to be sure, return exactly one item that says so " +
  "instead of inventing anything. Output ONLY a JSON array of strings.";

// ---- tiny key-value layer -------------------------------------------------
const R_URL = process.env.UPSTASH_REDIS_REST_URL || process.env.KV_REST_API_URL;
const R_TOKEN = process.env.UPSTASH_REDIS_REST_TOKEN || process.env.KV_REST_API_TOKEN;
const mem = new Map();
async function redis(cmd) {
  const r = await fetch(R_URL, { method: "POST", headers: { Authorization: `Bearer ${R_TOKEN}`, "Content-Type": "application/json" }, body: JSON.stringify(cmd) });
  const j = await r.json();
  if (j.error) throw new Error("redis: " + j.error);
  return j.result;
}
async function kvGet(k) {
  if (R_URL) return redis(["GET", k]);
  const e = mem.get(k); if (!e) return null;
  if (e.exp < Date.now()) { mem.delete(k); return null; }
  return e.v;
}
async function kvSet(k, v, ttl) {
  if (R_URL) return redis(["SET", k, String(v), "EX", String(ttl)]);
  mem.set(k, { v: String(v), exp: Date.now() + ttl * 1000 });
}
async function kvIncr(k, ttl) {
  if (R_URL) { const v = await redis(["INCR", k]); await redis(["EXPIRE", k, String(ttl), "NX"]); return Number(v); }
  const v = Number((await kvGet(k)) || 0) + 1; await kvSet(k, v, ttl); return v;
}
async function kvIncrByFloat(k, by, ttl) {
  if (R_URL) { const v = await redis(["INCRBYFLOAT", k, String(by)]); await redis(["EXPIRE", k, String(ttl), "NX"]); return parseFloat(v); }
  const v = parseFloat((await kvGet(k)) || "0") + by; await kvSet(k, v, ttl); return v;
}
const monthKey = () => "spend:" + new Date().toISOString().slice(0, 7);
const dayKey = (ip) => "ip:" + ip + ":" + new Date().toISOString().slice(0, 10);

// ---- handler --------------------------------------------------------------
export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  const spent = parseFloat((await kvGet(monthKey()).catch(() => "0")) || "0");
  if (req.method === "GET") {
    return res.status(200).json({ ok: true, configured: !!process.env.ANTHROPIC_API_KEY, model: MODEL, budget: BUDGET,
      spent: +spent.toFixed(4), durable: !!R_URL, perDeviceDaily: PER_DEVICE_DAILY });
  }
  if (req.method !== "POST") return res.status(405).json({ ok: false, error: "POST only" });
  if (!req.headers["x-clicker-client"]) return res.status(400).json({ ok: false, error: "This endpoint is for the Clicker app." });
  if (!process.env.ANTHROPIC_API_KEY) return res.status(503).json({ ok: false, code: "unconfigured", error: "The facts service has no API key yet." });

  const body = typeof req.body === "object" && req.body ? req.body : {};
  const ctx = {};
  for (const k of ["title", "series", "artist", "app", "season", "episode", "type"]) {
    const v = String(body[k] ?? "").trim().slice(0, 200);
    if (v) ctx[k] = v;
  }
  if (!ctx.title && !ctx.series && !ctx.artist) return res.status(400).json({ ok: false, error: "Nothing to look up." });

  const key = "facts:" + createHash("sha1").update(JSON.stringify(ctx, Object.keys(ctx).sort())).digest("hex");
  if (!body.fresh) {
    const hit = await kvGet(key).catch(() => null);
    if (hit) return res.status(200).json({ ok: true, facts: JSON.parse(hit), cached: true, spent: +spent.toFixed(4), budget: BUDGET });
  }
  if (spent >= BUDGET) return res.status(429).json({ ok: false, code: "budget", error: "Colin's monthly facts budget is used up. Add your own key in Settings, or wait for next month.", spent: +spent.toFixed(4), budget: BUDGET });
  const ip = String(req.headers["x-forwarded-for"] || "").split(",")[0].trim() || "unknown";
  const n = await kvIncr(dayKey(ip), 36 * 3600).catch(() => 0);
  if (n > PER_DEVICE_DAILY) return res.status(429).json({ ok: false, code: "rate", error: "That is plenty of facts for one day. Try again tomorrow, or add your own key in Settings." });

  const client = new Anthropic();
  const desc = Object.entries(ctx).map(([k, v]) => `${k}: ${v}`).join(", ");
  let response;
  try {
    response = await client.messages.create({
      model: MODEL,
      max_tokens: 1500,
      system: SYSTEM,
      output_config: { effort: "low" },
      messages: [{ role: "user", content: `On screen right now: ${desc}` }],
    });
  } catch (error) {
    if (error instanceof Anthropic.AuthenticationError) return res.status(503).json({ ok: false, code: "unconfigured", error: "The facts service key is invalid." });
    if (error instanceof Anthropic.RateLimitError) return res.status(503).json({ ok: false, code: "busy", error: "Claude is busy right now. Try again in a minute." });
    if (error instanceof Anthropic.APIError) return res.status(502).json({ ok: false, error: `Claude error ${error.status}: ${error.message}` });
    return res.status(502).json({ ok: false, error: "Could not reach Claude." });
  }
  if (response.stop_reason === "refusal") return res.status(200).json({ ok: false, error: "Claude declined to write facts for this one." });
  const text = response.content.filter((b) => b.type === "text").map((b) => b.text).join("").trim();
  let facts;
  try { const m = text.match(/\[[\s\S]*\]/); facts = JSON.parse(m ? m[0] : text).map((x) => String(x).trim()).filter(Boolean); }
  catch { facts = text.split("\n").map((l) => l.replace(/^[-•*\d.)\s]+/, "").trim()).filter(Boolean); }
  facts = facts.slice(0, 6);

  const [pin, pout] = PRICES[MODEL] || [5, 25];
  const u = response.usage || {};
  const cost = ((u.input_tokens || 0) * pin + (u.output_tokens || 0) * pout + (u.cache_read_input_tokens || 0) * pin * 0.1) / 1e6;
  const total = await kvIncrByFloat(monthKey(), cost, 40 * 86400).catch(() => spent + cost);
  await kvSet(key, JSON.stringify(facts), 7 * 86400).catch(() => {});
  console.log(JSON.stringify({ event: "facts", model: MODEL, in: u.input_tokens, out: u.output_tokens, cost: +cost.toFixed(5), spent: +total.toFixed(4), ip }));
  return res.status(200).json({ ok: true, facts, cached: false, cost: +cost.toFixed(5), spent: +total.toFixed(4), budget: BUDGET, model: MODEL });
}
