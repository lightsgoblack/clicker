# Clicker facts service

A single Vercel function, `POST /api/facts`, that turns "what's playing" into five rare-but-true facts via Claude. Colin pays for it, so friends get the feature with no setup. Live at `https://clicker-facts.vercel.app/api/facts`.

## Guardrails

1. **Hard cap, set in the Anthropic console (do this first).** The API key must belong to a workspace with a monthly spend limit. Anthropic stops the key when that limit is reached, no matter what this code does.
2. **Its own meter.** `MONTHLY_BUDGET_USD` (default 2). The function refuses once its running total reaches it. Without Redis the meter is per-instance and best-effort, which is why step 1 is the one that matters.
3. **Per device.** `PER_DEVICE_DAILY` (default 25) lookups per IP per day.
4. **Cache.** The same show asked again within a week costs nothing.

The function keeps nothing about who asked. Vercel's request logs hold IPs for their usual retention.

## One-time setup (Colin)

1. In the Anthropic console, create a **new workspace** named "Clicker facts", set its **monthly spend limit to $2**, and create an **API key inside that workspace**.
2. From this folder, add the key to Vercel and redeploy:

```bash
cd ~/Developer/clicker/facts-service && vercel env add ANTHROPIC_API_KEY production
```

```bash
cd ~/Developer/clicker/facts-service && vercel --prod --yes
```

3. Check it: `curl https://clicker-facts.vercel.app/api/facts` should say `"configured":true`.

Optional, for a meter that survives restarts: add an Upstash Redis store to the Vercel project (Storage tab, free tier). The function picks up `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` (or the `KV_REST_API_*` names) automatically.

## Cost per lookup

| Model (`FACTS_MODEL`) | About | Lookups per $2 |
|---|---|---|
| `claude-opus-5` (default) | 2 to 3 cents | about 80 |
| `claude-sonnet-5` | 1 cent | about 200 |
| `claude-haiku-4-5` | half a cent | about 400 |

Change it with `vercel env add FACTS_MODEL production`, then redeploy. Each response logs its exact cost in the Vercel function logs.
