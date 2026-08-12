import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm, symlink } from "node:fs/promises";
import { createServer, request as httpRequest } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const bypassHeader = "x-ksi-cctv-dev-auth-token";

function isolatedEnv(overrides = {}) {
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (key.startsWith("KSI_CCTV_") || key.startsWith("CCTV_CAM_")) delete env[key];
  }
  delete env.NODE_ENV;
  delete env.PORT;
  return {
    ...env,
    CI: "1",
    NEXT_TELEMETRY_DISABLED: "1",
    KSI_CCTV_ENGINE_URL: "http://127.0.0.1:9",
    ...overrides,
  };
}

async function unusedPort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert(address && typeof address === "object");
  await new Promise((resolve) => server.close(resolve));
  return address.port;
}

function startProcess(args, cwd, env) {
  const child = spawn(process.execPath, args, {
    cwd,
    env,
    detached: process.platform !== "win32",
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  const capture = (chunk) => {
    output = `${output}${chunk}`.slice(-16_384);
  };
  child.stdout.on("data", capture);
  child.stderr.on("data", capture);
  child.on("error", (error) => capture(String(error)));
  return { child, output: () => output };
}

async function stopProcess(processState) {
  const child = processState?.child;
  if (!child || child.exitCode !== null || child.signalCode !== null) return;

  const exited = new Promise((resolve) => child.once("exit", resolve));
  const sendSignal = (signal) => {
    try {
      if (process.platform === "win32") child.kill(signal);
      else process.kill(-child.pid, signal);
    } catch {
      // The process may have exited between the state check and signal.
    }
  };
  sendSignal("SIGTERM");
  await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 3_000))]);
  if (child.exitCode === null && child.signalCode === null) {
    sendSignal("SIGKILL");
    await exited;
  }
}

function request(hostname, port, pathname, headers = {}, timeout = 10_000) {
  return new Promise((resolve, reject) => {
    const req = httpRequest({ hostname, port, path: pathname, headers }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => resolve({
        status: response.statusCode,
        headers: response.headers,
        body: Buffer.concat(chunks).toString("utf8"),
      }));
    });
    req.setTimeout(timeout, () => req.destroy(new Error("request timed out")));
    req.on("error", reject);
    req.end();
  });
}

async function waitForResponse(processState, port, pathname, headers = {}) {
  const deadline = Date.now() + 60_000;
  let lastError;
  while (Date.now() < deadline) {
    if (processState.child.exitCode !== null || processState.child.signalCode !== null) {
      throw new Error(`development server exited early:\n${processState.output()}`);
    }
    try {
      return await request("127.0.0.1", port, pathname, headers);
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  throw new Error(`development server did not respond: ${lastError}\n${processState.output()}`);
}

async function waitForBootstrapUrl(processState) {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const match = processState.output().match(/Fallback one-time URL file \(mode 0600\): (.+)/);
    if (match) return (await readFile(match[1].trim(), "utf8")).trim();
    if (processState.child.exitCode !== null || processState.child.signalCode !== null) {
      throw new Error(`development server exited before bootstrap:\n${processState.output()}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`development bootstrap was not published:\n${processState.output()}`);
}

async function startRelay(targetPort) {
  const relay = createServer((incoming, outgoing) => {
    const headers = { ...incoming.headers };
    delete headers.cookie;
    headers.host = `127.0.0.1:${targetPort}`;
    headers[bypassHeader] = "9".repeat(64);
    const upstream = httpRequest({
      hostname: "127.0.0.1",
      port: targetPort,
      path: incoming.url,
      method: incoming.method,
      headers,
    }, (response) => {
      outgoing.writeHead(response.statusCode ?? 502, response.headers);
      response.pipe(outgoing);
    });
    upstream.on("error", () => {
      outgoing.writeHead(502);
      outgoing.end();
    });
    incoming.pipe(upstream);
  });
  await new Promise((resolve, reject) => {
    relay.once("error", reject);
    relay.listen(0, "127.0.0.1", resolve);
  });
  const address = relay.address();
  assert(address && typeof address === "object");
  return { relay, port: address.port };
}

test("development auth bypass has a process-enforced loopback boundary", { timeout: 180_000 }, async () => {
  const fixture = await mkdtemp(path.join(tmpdir(), "ksi-cctv-dev-auth-"));
  let running;
  let relay;
  try {
    await cp(frontendRoot, fixture, {
      recursive: true,
      filter(source) {
        const relative = path.relative(frontendRoot, source);
        const topLevel = relative.split(path.sep)[0];
        return topLevel !== "node_modules"
          && topLevel !== ".next"
          && !topLevel.startsWith(".env");
      },
    });
    await symlink(path.join(frontendRoot, "node_modules"), path.join(fixture, "node_modules"), "dir");

    const wrapper = path.join(fixture, "next-with-auth-guard.mjs");
    const bypassEnv = isolatedEnv({
      KSI_CCTV_WEB_DEV: "1",
      KSI_CCTV_DEV_AUTH_BYPASS: "1",
      KSI_CCTV_DEV_AUTH_BROWSER: "/bin/true",
    });
    const refusalCases = [
      { args: [wrapper, "dev", "-H0.0.0.0"], env: bypassEnv },
      { args: [wrapper, "dev", "-H", "127.0.0.1", "-H0.0.0.0"], env: bypassEnv },
      { args: [wrapper, "dev", "-H127.0.0.1"], env: isolatedEnv({ KSI_CCTV_DEV_AUTH_BYPASS: "1" }) },
      { args: [wrapper, "build"], env: bypassEnv },
      { args: [wrapper, "start"], env: bypassEnv },
    ];
    for (const refusal of refusalCases) {
      const result = spawnSync(process.execPath, refusal.args, {
        cwd: fixture,
        env: refusal.env,
        encoding: "utf8",
        timeout: 10_000,
      });
      assert.equal(result.status, 78, result.stderr || result.stdout);
    }
    const dockerRefusal = spawnSync("sh", [path.join(fixture, "docker-entrypoint.sh")], {
      cwd: fixture,
      env: bypassEnv,
      encoding: "utf8",
      timeout: 10_000,
    });
    assert.equal(dockerRefusal.status, 78, dockerRefusal.stderr || dockerRefusal.stdout);

    const protectedPort = await unusedPort();
    running = startProcess(
      [wrapper, "dev", "--webpack", "-H127.0.0.1", `-p${protectedPort}`],
      fixture,
      bypassEnv,
    );
    const bootstrapUrl = new URL(await waitForBootstrapUrl(running));

    const unbootstrapped = await waitForResponse(running, protectedPort, "/", {
      Host: "untrusted.example",
      [bypassHeader]: "f".repeat(64),
    });
    assert.equal(unbootstrapped.status, 503, running.output());

    const bootstrapResponse = await request(
      "127.0.0.1",
      protectedPort,
      `${bootstrapUrl.pathname}${bootstrapUrl.search}`,
      { Host: `127.0.0.1:${protectedPort}` },
    );
    assert.equal(bootstrapResponse.status, 303, running.output());
    const setCookie = bootstrapResponse.headers["set-cookie"];
    assert(Array.isArray(setCookie) && setCookie.length === 1);
    assert.match(setCookie[0], /; Path=\/; HttpOnly; SameSite=Strict$/);
    assert.doesNotMatch(setCookie[0], /; Domain=/i);
    const browserCookie = setCookie[0].split(";", 1)[0];

    const protectedResponse = await waitForResponse(running, protectedPort, "/", {
      Host: `127.0.0.1:${protectedPort}`,
      Cookie: browserCookie,
      [bypassHeader]: "f".repeat(64),
    });
    assert.equal(protectedResponse.status, 200, running.output());
    assert.match(protectedResponse.body, /개발 모드.*로그인 비활성화.*loopback 전용/s);

    const replay = await request(
      "127.0.0.1",
      protectedPort,
      `${bootstrapUrl.pathname}${bootstrapUrl.search}`,
    );
    assert.equal(replay.status, 404);

    const relayState = await startRelay(protectedPort);
    relay = relayState.relay;
    const relayedResponse = await request("127.0.0.1", relayState.port, "/");
    assert.equal(relayedResponse.status, 503, running.output());
    await new Promise((resolve) => relay.close(resolve));
    relay = undefined;

    await assert.rejects(
      request("127.0.0.2", protectedPort, "/", {}, 1_000),
      /ECONNREFUSED|EHOSTUNREACH|request timed out/,
    );
    await stopProcess(running);
    running = undefined;

    const directPort = await unusedPort();
    const nextBin = path.join(fixture, "node_modules", "next", "dist", "bin", "next");
    running = startProcess(
      [nextBin, "dev", "--webpack", "-H", "127.0.0.1", "-p", String(directPort)],
      fixture,
      isolatedEnv({
        KSI_CCTV_WEB_DEV: "1",
        KSI_CCTV_DEV_AUTH_BYPASS: "1",
        KSI_CCTV_DEV_AUTH_BYPASS_TOKEN: "e".repeat(64),
        KSI_CCTV_WEB_BIND_HOST: "127.0.0.1",
      }),
    );
    const directResponse = await waitForResponse(running, directPort, "/cameras", {
      Host: `localhost:${directPort}`,
      [bypassHeader]: "e".repeat(64),
    });
    assert.equal(directResponse.status, 503, running.output());
    assert.match(directResponse.body, /보호된 로컬 launcher/);
  } finally {
    if (relay) await new Promise((resolve) => relay.close(resolve));
    await stopProcess(running);
    await rm(fixture, { recursive: true, force: true });
  }
});
