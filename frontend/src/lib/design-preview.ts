export function designPreviewEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return env.NODE_ENV === "development" && env.KSI_CCTV_ENABLE_DESIGN_PREVIEW === "1";
}
