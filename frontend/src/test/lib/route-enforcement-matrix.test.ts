import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { minRoleForPath, ROLE_RANK } from "@/lib/roles";

const here = path.dirname(fileURLToPath(import.meta.url));
const apiDir = path.resolve(here, "../../app/api");

const PUBLIC_PREFIXES = ["/api/auth/"];
const PUBLIC_EXACT = new Set(["/api/auth"]);
const MIN_MUTATION_ROUTES = 20;
const MUTATION_METHODS = ["POST", "PUT", "PATCH", "DELETE"] as const;

function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:"'\\])\/\/.*$/gm, "$1");
}

function collectRouteFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...collectRouteFiles(full));
    } else if (entry === "route.ts") {
      out.push(full);
    }
  }
  return out.sort();
}

function samplePathFor(file: string): string {
  const rel = path.relative(apiDir, file);
  const dir = path.dirname(rel);
  const segments = dir === "." ? [] : dir.split(path.sep);
  const mapped = segments.map((seg) => {
    if (/^\[\.\.\..+\]$/.test(seg)) return "1";
    if (/^\[.+\]$/.test(seg)) return "1";
    return seg;
  });
  return `/api/${mapped.join("/")}`.replace(/\/+$/, "") || "/api";
}

function isPublic(pathname: string): boolean {
  if (PUBLIC_EXACT.has(pathname)) return true;
  return PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function exportedMethods(stripped: string): string[] {
  const found = new Set<string>();
  const re = /export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\b/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(stripped)) !== null) {
    found.add(match[1]);
  }
  return [...found].sort();
}

describe("route enforcement matrix (proxy <-> requireRole)", () => {
  it("requires requireRole + operator-or-higher proxy minimum on every mutation route", () => {
    const files = collectRouteFiles(apiDir);
    expect(files.length).toBeGreaterThan(0);
    const mutationFiles = files.filter((file) => {
      const stripped = stripComments(readFileSync(file, "utf8"));
      return exportedMethods(stripped).some((method) =>
        (MUTATION_METHODS as readonly string[]).includes(method),
      );
    });
    const guarded = mutationFiles.filter((file) => !isPublic(samplePathFor(file)));

    expect(guarded.length).toBeGreaterThanOrEqual(MIN_MUTATION_ROUTES);

    for (const file of guarded) {
      const rel = path.relative(apiDir, file);
      const source = readFileSync(file, "utf8");
      const stripped = stripComments(source);
      expect(
        stripped.includes("requireRole("),
        `${rel}: mutation route must call requireRole (proxy is only the first gate)`,
      ).toBe(true);

      const methods = exportedMethods(stripped).filter((method) =>
        (MUTATION_METHODS as readonly string[]).includes(method),
      );
      expect(methods.length).toBeGreaterThan(0);
      for (const method of methods) {
        const pathname = samplePathFor(file);
        const minimum = minRoleForPath(pathname, method);
        expect(
          minimum !== null && ROLE_RANK[minimum] >= ROLE_RANK.operator,
          `${rel} [${method}] ${pathname} must require operator or higher (got ${minimum})`,
        ).toBe(true);
      }
    }
  });

  it("ignores requireRole mentions inside comments when detecting the gate", () => {
    const stripped = stripComments(`// requireRole(req, "admin")\n/* requireRole( */\nexport async function GET() {}`);
    expect(stripped.includes("requireRole(")).toBe(false);
    const exportSrc = stripComments(`// export async function POST() {}\nexport async function GET() {}`);
    expect(exportedMethods(exportSrc)).toEqual(["GET"]);
  });
});
