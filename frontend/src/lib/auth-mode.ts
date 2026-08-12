export type DevelopmentAuthBypassState =
  | { status: "disabled" }
  | { status: "active" }
  | { status: "invalid"; reason: string };

export const DEVELOPMENT_AUTH_BYPASS_HEADER = "x-ksi-cctv-dev-auth-token";

const BYPASS_TOKEN_PATTERN = /^[a-f0-9]{64}$/;

export function isLoopbackHost(value: string | undefined): boolean {
  const host = (value ?? "").trim().toLowerCase().replace(/^\[|\]$/g, "");
  return host === "127.0.0.1" || host === "::1" || host === "localhost";
}

export function isProtectedDevelopmentLauncher(argv: readonly string[] = process.argv): boolean {
  const entrypoint = (argv[1] ?? "").replaceAll("\\", "/").split("/").pop();
  return entrypoint === "next-with-auth-guard.mjs" && argv[2] === "dev";
}

export function developmentAuthBypassState(
  env: Record<string, string | undefined> = process.env,
  argv: readonly string[] = process.argv,
): DevelopmentAuthBypassState {
  const requested = env.KSI_CCTV_DEV_AUTH_BYPASS === "1";
  if (!requested) return { status: "disabled" };
  if (env.NODE_ENV !== "development") {
    return { status: "invalid", reason: "개발 인증 생략은 next dev에서만 사용할 수 있습니다." };
  }
  if (env.KSI_CCTV_WEB_DEV !== "1") {
    return { status: "invalid", reason: "개발 인증 생략은 로컬 개발 launcher에서만 사용할 수 있습니다." };
  }
  if (!isProtectedDevelopmentLauncher(argv)) {
    return { status: "invalid", reason: "개발 인증 생략은 보호된 로컬 launcher 프로세스에서만 사용할 수 있습니다." };
  }
  if (!isLoopbackHost(env.KSI_CCTV_WEB_BIND_HOST)) {
    return { status: "invalid", reason: "개발 인증 생략은 loopback bind에서만 사용할 수 있습니다." };
  }
  if (!BYPASS_TOKEN_PATTERN.test(env.KSI_CCTV_DEV_AUTH_BYPASS_TOKEN ?? "")) {
    return { status: "invalid", reason: "개발 인증 생략은 보호된 로컬 launcher를 통해서만 사용할 수 있습니다." };
  }
  return { status: "active" };
}
