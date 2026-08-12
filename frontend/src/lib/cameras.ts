export type CameraZonePoint = { x: number; y: number };
export type CameraPolygonPurpose = "restricted" | "loitering";

export type CameraZone = {
  id: string;
  type: "polygon" | "line";
  points: CameraZonePoint[];
  direction?: "any" | "positive" | "negative";
};

/** Never reflect a camera password when an upstream diagnostic includes request data. */
export function redactCameraMessage(message: string, ...secrets: string[]): string {
  return secrets.filter(Boolean).reduce((redacted, secret) => redacted.split(secret).join("[redacted]"), message);
}
