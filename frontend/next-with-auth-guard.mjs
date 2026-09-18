import { spawn } from "node:child_process";
import { randomBytes, timingSafeEqual } from "node:crypto";
import { chmod, lstat, mkdtemp, readlink, rm, symlink, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const mode = process.argv[2];
const args = process.argv.slice(3);
const bypass = process.env.KSI_CCTV_DEV_AUTH_BYPASS === "1";
const root = path.dirname(fileURLToPath(import.meta.url));
const bypassHeader = "x-ksi-cctv-dev-auth-token";
const browserCookie = "ksi_cctv_dev_auth";
const bootstrapPath = "/_ksi/dev-auth";

function optionValue(shortName, longName) {
  let value;
  for (let index = 0; index < args.length; index += 1) {
    const item = args[index];
    if (item === shortName || item === longName) {
      value = args[index + 1];
      index += 1;
    } else if (item.startsWith(`${longName}=`)) {
      value = item.slice(longName.length + 1);
    } else if (item.startsWith(shortName) && item.length > shortName.length) {
      value = item.slice(shortName.length).replace(/^=/, "");
    }
  }
  return value;
}

function normalizedLoopbackHost(value) {
  const host = String(value ?? "").trim().toLowerCase().replace(/^\[|\]$/g, "");
  if (host === "localhost") return "127.0.0.1";
  if (host === "127.0.0.1" || host === "::1") return host;
  return undefined;
}

function isLoopbackAddress(value) {
  const address = String(value ?? "").trim().toLowerCase();
  return address === "127.0.0.1" || address === "::1" || address === "::ffff:127.0.0.1";
}

function secretsEqual(left, right) {
  const leftBuffer = Buffer.from(String(left ?? ""));
  const rightBuffer = Buffer.from(String(right ?? ""));
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
}

function cookieValue(header, name) {
  const value = Array.isArray(header) ? header.join(";") : String(header ?? "");
  for (const item of value.split(";")) {
    const separator = item.indexOf("=");
    if (separator >= 0 && item.slice(0, separator).trim() === name) {
      return item.slice(separator + 1).trim();
    }
  }
  return undefined;
}

function withoutCookie(header, name) {
  const value = Array.isArray(header) ? header.join(";") : String(header ?? "");
  return value
    .split(";")
    .filter((item) => {
      const separator = item.indexOf("=");
      return separator < 0 || item.slice(0, separator).trim() !== name;
    })
    .map((item) => item.trim())
    .filter(Boolean)
    .join("; ");
}

function removeRawHeader(request, name) {
  for (let index = request.rawHeaders.length - 2; index >= 0; index -= 2) {
    if (request.rawHeaders[index].toLowerCase() === name) {
      request.rawHeaders.splice(index, 2);
    }
  }
}

function refuse(message) {
  console.error(message);
  process.exit(78);
}

function parseProtectedDevArgs() {
  const options = { hostname: undefined, port: undefined, webpack: false };
  const setValue = (name, value) => {
    if (!value || options[name] !== undefined) {
      refuse(`Invalid or duplicate ${name} option for protected local development.`);
    }
    options[name] = value;
  };

  for (let index = 0; index < args.length; index += 1) {
    const item = args[index];
    if (item === "-H" || item === "--hostname") {
      setValue("hostname", args[index + 1]);
      index += 1;
    } else if (item.startsWith("--hostname=")) {
      setValue("hostname", item.slice("--hostname=".length));
    } else if (item.startsWith("-H") && item.length > 2) {
      setValue("hostname", item.slice(2).replace(/^=/, ""));
    } else if (item === "-p" || item === "--port") {
      setValue("port", args[index + 1]);
      index += 1;
    } else if (item.startsWith("--port=")) {
      setValue("port", item.slice("--port=".length));
    } else if (item.startsWith("-p") && item.length > 2) {
      setValue("port", item.slice(2).replace(/^=/, ""));
    } else if (item === "--webpack") {
      options.webpack = true;
    } else if (item !== "--turbopack" && item !== "--turbo") {
      refuse(`Unsupported option for protected local development: ${item}`);
    }
  }
  return options;
}

async function startProtectedDevServer() {
  if (process.env.KSI_CCTV_WEB_DEV !== "1") {
    refuse("KSI_CCTV_DEV_AUTH_BYPASS requires KSI_CCTV_WEB_DEV=1.");
  }

  const options = parseProtectedDevArgs();
  const requestedHost = options.hostname
    ?? process.env.KSI_CCTV_WEB_BIND_HOST
    ?? "127.0.0.1";
  const bindHost = normalizedLoopbackHost(requestedHost);
  if (!bindHost) {
    refuse("KSI_CCTV_DEV_AUTH_BYPASS is allowed only on a loopback bind.");
  }

  const portText = String(options.port ?? process.env.PORT ?? "3000");
  const port = Number(portText);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    refuse(`Invalid port for protected local development: ${portText}`);
  }

  process.env.NODE_ENV = "development";
  process.env.KSI_CCTV_WEB_BIND_HOST = bindHost;
  process.env.KSI_CCTV_DEV_AUTH_BYPASS_TOKEN = randomBytes(32).toString("hex");
  const bootstrapToken = randomBytes(32).toString("hex");
  const browserToken = randomBytes(32).toString("hex");

  const { default: next } = await import("next");
  const app = next({
    dev: true,
    dir: root,
    hostname: bindHost,
    port,
    turbopack: !options.webpack,
    webpack: options.webpack,
  });
  await app.prepare();

  const handleRequest = app.getRequestHandler();
  const handleUpgrade = app.getUpgradeHandler();
  let bootstrapAvailable = true;
  let bootstrapDirectory;
  const removeBootstrapArtifact = async () => {
    if (!bootstrapDirectory) return;
    const directory = bootstrapDirectory;
    bootstrapDirectory = undefined;
    await rm(directory, { recursive: true, force: true });
  };
  const attestBrowserRequest = (request) => {
    removeRawHeader(request, bypassHeader);
    delete request.headers[bypassHeader];
    if (!secretsEqual(cookieValue(request.headers.cookie, browserCookie), browserToken)) {
      return false;
    }
    const remainingCookies = withoutCookie(request.headers.cookie, browserCookie);
    removeRawHeader(request, "cookie");
    if (remainingCookies) {
      request.headers.cookie = remainingCookies;
      request.rawHeaders.push("Cookie", remainingCookies);
    } else {
      delete request.headers.cookie;
    }
    request.headers[bypassHeader] = process.env.KSI_CCTV_DEV_AUTH_BYPASS_TOKEN;
    return true;
  };
  const server = createServer((request, response) => {
    if (!isLoopbackAddress(request.socket.remoteAddress)) {
      response.writeHead(403, {
        "Cache-Control": "no-store",
        "Content-Type": "text/plain; charset=utf-8",
      });
      response.end("Loopback access only.\n");
      return;
    }
    const requestUrl = new URL(request.url ?? "/", "http://localhost");
    if (requestUrl.pathname === bootstrapPath) {
      const valid = request.method === "GET"
        && bootstrapAvailable
        && secretsEqual(requestUrl.searchParams.get("token"), bootstrapToken);
      if (!valid) {
        response.writeHead(404, { "Cache-Control": "no-store" });
        response.end("Not Found\n");
        return;
      }
      bootstrapAvailable = false;
      response.writeHead(303, {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "Set-Cookie": `${browserCookie}=${browserToken}; Path=/; HttpOnly; SameSite=Strict`,
        Location: "/",
      });
      response.end();
      void removeBootstrapArtifact();
      return;
    }
    attestBrowserRequest(request);
    handleRequest(request, response).catch((error) => {
      console.error("Protected local development request failed:", error);
      if (!response.headersSent) response.statusCode = 500;
      response.end("Internal Server Error\n");
    });
  });
  server.on("upgrade", (request, socket, head) => {
    if (
      !isLoopbackAddress(request.socket.remoteAddress)
      || !attestBrowserRequest(request)
    ) {
      socket.destroy();
      return;
    }
    handleUpgrade(request, socket, head).catch((error) => {
      console.error("Protected local development upgrade failed:", error);
      socket.destroy();
    });
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, bindHost, () => {
      server.off("error", reject);
      resolve();
    });
  });
  console.log(`Protected local development server ready on http://${bindHost}:${port}`);

  const displayHost = bindHost.includes(":") ? `[${bindHost}]` : bindHost;
  const bootstrapUrl = `http://${displayHost}:${port}${bootstrapPath}?token=${bootstrapToken}`;
  bootstrapDirectory = await mkdtemp(path.join(tmpdir(), "ksi-cctv-dev-auth-"));
  await chmod(bootstrapDirectory, 0o700);
  const bootstrapFile = path.join(bootstrapDirectory, "open-url");
  await writeFile(bootstrapFile, `${bootstrapUrl}\n`, { encoding: "utf8", flag: "wx", mode: 0o600 });
  console.log(`Opening the local browser for development authentication.`);
  console.log(`Fallback one-time URL file (mode 0600): ${bootstrapFile}`);
  const browserCommand = process.env.KSI_CCTV_DEV_AUTH_BROWSER
    ?? (process.platform === "darwin" ? "open" : "xdg-open");
  const browserEnv = {};
  for (const name of [
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
  ]) {
    if (process.env[name] !== undefined) browserEnv[name] = process.env[name];
  }
  const browserProcess = spawn(browserCommand, [bootstrapUrl], {
    detached: true,
    env: browserEnv,
    stdio: "ignore",
  });
  browserProcess.on("error", () => {
    console.error("Could not open a local browser; use the protected fallback URL file.");
  });
  browserProcess.unref();

  let closing = false;
  const close = async () => {
    if (closing) return;
    closing = true;
    server.close();
    server.closeAllConnections();
    await app.close();
    await removeBootstrapArtifact();
  };
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, () => {
      close().finally(() => process.exit(0));
    });
  }
}

async function ensureStandaloneAssetLink(linkPath, target) {
  try {
    const stat = await lstat(linkPath);
    if (stat.isSymbolicLink()) {
      if (await readlink(linkPath) !== target) {
        throw new Error(`Refusing to replace unexpected standalone asset path: ${linkPath}`);
      }
      return;
    }
    if (stat.isDirectory()) {
      return;
    }
    throw new Error(`Refusing to replace unexpected standalone asset path: ${linkPath}`);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    await symlink(target, linkPath, "dir");
  }
}

async function standaloneLaunch() {
  const standaloneRoot = path.join(root, ".next", "standalone");
  const serverPath = path.join(standaloneRoot, "server.js");
  try {
    await lstat(serverPath);
  } catch {
    console.error("Standalone build not found. Run npm run build first.");
    process.exit(1);
  }
  await ensureStandaloneAssetLink(
    path.join(standaloneRoot, ".next", "static"),
    "../../static",
  );
  await ensureStandaloneAssetLink(path.join(standaloneRoot, "public"), "../../public");

  const bindHost = optionValue("-H", "--hostname")
    ?? process.env.KSI_CCTV_WEB_BIND_HOST
    ?? "0.0.0.0";
  const port = optionValue("-p", "--port") ?? process.env.PORT ?? "3000";
  return spawn(process.execPath, [serverPath], {
    cwd: standaloneRoot,
    env: {
      ...process.env,
      HOSTNAME: String(bindHost),
      KSI_CCTV_WEB_BIND_HOST: String(bindHost),
      NODE_ENV: "production",
      PORT: String(port),
    },
    stdio: "inherit",
  });
}

if (bypass) {
  if (mode !== "dev") {
    refuse("KSI_CCTV_DEV_AUTH_BYPASS is allowed only with next dev.");
  }
  await startProtectedDevServer();
} else {
  const defaultHost = mode === "dev" ? "localhost" : "0.0.0.0";
  const bindHost = optionValue("-H", "--hostname")
    ?? process.env.KSI_CCTV_WEB_BIND_HOST
    ?? defaultHost;
  const childEnv = { ...process.env, KSI_CCTV_WEB_BIND_HOST: String(bindHost) };
  if (mode === "dev") childEnv.NODE_ENV = "development";
  const child = mode === "start"
    ? await standaloneLaunch()
    : spawn(
      process.execPath,
      [path.join(root, "node_modules", "next", "dist", "bin", "next"), mode, ...args],
      { cwd: root, env: childEnv, stdio: "inherit" },
    );

  const signalHandlers = new Map();
  for (const signal of ["SIGINT", "SIGTERM"]) {
    const handler = () => child.kill(signal);
    signalHandlers.set(signal, handler);
    process.once(signal, handler);
  }
  child.on("exit", (code, signal) => {
    for (const [name, handler] of signalHandlers) process.off(name, handler);
    if (signal) process.kill(process.pid, signal);
    else process.exit(code ?? 1);
  });
}
