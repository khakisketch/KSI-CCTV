type LimitResult = { allowed: true } | { allowed: false; retryAfterSeconds: number };

export class LoginRateLimiter {
  private readonly sources = new Map<string, number[]>();
  private global: number[] = [];

  constructor(
    private readonly windowMs = 60_000,
    private readonly sourceLimit = 5,
    private readonly globalLimit = 30,
    private readonly maxSources = 1024,
  ) {}

  consume(source: string, now = Date.now()): LimitResult {
    const cutoff = now - this.windowMs;
    this.global = this.global.filter((timestamp) => timestamp > cutoff);
    for (const [key, timestamps] of this.sources) {
      const active = timestamps.filter((timestamp) => timestamp > cutoff);
      if (active.length) this.sources.set(key, active);
      else this.sources.delete(key);
    }
    if (!this.sources.has(source) && this.sources.size >= this.maxSources) {
      source = "overflow";
      if (!this.sources.has(source)) {
        const oldestKey = this.sources.keys().next().value;
        if (oldestKey) this.sources.delete(oldestKey);
      }
    }
    const sourceAttempts = (this.sources.get(source) ?? []).filter(
      (timestamp) => timestamp > cutoff,
    );
    this.sources.set(source, sourceAttempts);

    if (sourceAttempts.length >= this.sourceLimit || this.global.length >= this.globalLimit) {
      const blockedUntil = Math.max(
        sourceAttempts.length >= this.sourceLimit
          ? sourceAttempts[0] + this.windowMs
          : 0,
        this.global.length >= this.globalLimit
          ? this.global[0] + this.windowMs
          : 0,
      );
      return {
        allowed: false,
        retryAfterSeconds: Math.max(1, Math.ceil((blockedUntil - now) / 1000)),
      };
    }

    sourceAttempts.push(now);
    this.global.push(now);
    return { allowed: true };
  }

  check(source: string, now = Date.now()): LimitResult {
    const cutoff = now - this.windowMs;
    this.global = this.global.filter((timestamp) => timestamp > cutoff);
    for (const [key, timestamps] of this.sources) {
      const active = timestamps.filter((timestamp) => timestamp > cutoff);
      if (active.length) this.sources.set(key, active);
      else this.sources.delete(key);
    }

    const trackedSource = !this.sources.has(source)
      && this.sources.size >= this.maxSources
      && this.sources.has("overflow")
      ? "overflow"
      : source;
    const sourceAttempts = this.sources.get(trackedSource) ?? [];
    if (sourceAttempts.length >= this.sourceLimit || this.global.length >= this.globalLimit) {
      const blockedUntil = Math.max(
        sourceAttempts.length >= this.sourceLimit
          ? sourceAttempts[0] + this.windowMs
          : 0,
        this.global.length >= this.globalLimit
          ? this.global[0] + this.windowMs
          : 0,
      );
      return {
        allowed: false,
        retryAfterSeconds: Math.max(1, Math.ceil((blockedUntil - now) / 1000)),
      };
    }

    return { allowed: true };
  }

  trackedSourceCount(): number {
    return this.sources.size;
  }
}

export const loginRateLimiter = new LoginRateLimiter();

export function loginSource(headers: Headers, trustProxy = false): string {
  if (!trustProxy) return "direct";
  const forwarded = headers.get("x-real-ip") ?? headers.get("x-forwarded-for")?.split(",")[0];
  return (forwarded?.trim().slice(0, 128) || "proxy-unknown").replace(/[^A-Za-z0-9:._-]/g, "_");
}
