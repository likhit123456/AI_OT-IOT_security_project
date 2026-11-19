export const TELEMETRY_TIMEOUT_MS = 3500;

export function telemetryExpired(lastSuccess: number, now = performance.now()): boolean {
  return lastSuccess <= 0 || now - lastSuccess > TELEMETRY_TIMEOUT_MS;
}
