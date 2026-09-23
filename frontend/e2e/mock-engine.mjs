import { createServer } from "node:http";

const port = Number(process.env.MOCK_ENGINE_PORT ?? "9100");

function initialState() {
  return {
    cameraRevision: 7,
    zoneConflictPending: true,
    cameras: [
    {
      id: "cam-1",
      name: "로비 카메라",
      location: "로비",
      enabled: false,
      managed: true,
      stream_url: "rtsp://redacted",
      enabled_events: ["person"],
      zones: [],
      detection_type: "general",
    },
    ],
    models: [
    { name: "uploaded-detector", type: "onnx", source: "uploaded", enabled: false },
    {
      name: "open-vocab-default",
      type: "open_vocab",
      source: "builtin",
      enabled: true,
      classes: ["사람"],
    },
    ],
    commissioning: {
    revision: 3,
    site: {
      name: "E2E 검증 현장",
      location: "서울",
      timezone: "Asia/Seoul",
      installer: "설치 담당자",
      expected_camera_count: 1,
    },
    completed_at: null,
    ready_to_complete: true,
    progress: { passed: 4, total: 4, percent: 100 },
    checks: [{ id: "engine", label: "엔진 상태", status: "pass", summary: "정상" }],
    cameras: [
      {
        id: "cam-1",
        name: "로비 카메라",
        location: "로비",
        enabled: true,
        health_state: "receiving",
        fps: 12.5,
        enabled_events: ["person"],
        zone_count: 0,
        zone_warnings: [],
        ai_events: ["person"],
        ready: true,
        checks: {
          live_verified: true,
          reconnect_verified: true,
          detection_verified: true,
          alert_verified: true,
          notes: "",
        },
      },
    ],
    },
    incidents: [
    {
      event_id: "evt-2",
      camera_id: "cam-1",
      category: "safety",
      source_event_type: "fire",
      source_event_alert_level: "critical",
      status: "new",
      updated_at: "2026-01-01T10:01:00Z",
      actions: [],
    },
    ],
  };
}

let state = initialState();

const events = [
  {
    event_id: "evt-1",
    camera_id: "cam-1",
    event_type: "person",
    alert_level: "high",
    description: "출입구 사람 감지",
    confidence: 0.91,
    timestamp: "2026-01-01T10:00:00Z",
  },
  {
    event_id: "evt-2",
    camera_id: "cam-1",
    event_type: "fire",
    alert_level: "critical",
    description: "창고 화재 감지",
    confidence: 0.88,
    timestamp: "2026-01-01T10:01:00Z",
  },
];

const preflightChecks = [
  {
    id: "stream",
    label: "카메라 스트림",
    status: "fail",
    summary: "카메라가 프레임을 보내지 않습니다.",
    remediation: "RTSP 주소와 네트워크 연결을 확인하세요.",
  },
  {
    id: "gpu",
    label: "GPU 실행 공급자",
    status: "warning",
    summary: "CPU 실행 공급자를 사용 중입니다.",
    remediation: "GPU overlay와 CUDA 공급자를 확인하세요.",
  },
  {
    id: "storage",
    label: "저장소 여유 공간",
    status: "pass",
    summary: "저장소 여유 공간이 충분합니다.",
  },
];

const auditEntries = [
  {
    timestamp: "2026-01-01T10:00:00Z",
    event: "camera.updated",
    source: "installer",
    camera_id: "cam-1",
    revision: 8,
  },
  {
    timestamp: "2026-01-01T10:01:00Z",
    event: "pipeline.started",
    source: "system",
    options: { mode: "policy" },
  },
];

function send(response, status, body) {
  response.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  response.end(JSON.stringify(body));
}

function sendBinary(response, status, body, contentType) {
  response.writeHead(status, { "Content-Type": contentType, "Cache-Control": "no-store" });
  response.end(body);
}

async function jsonBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    return {};
  }
}

async function boundedBody(request, limit = 1024 * 1024) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > limit) return null;
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

function commissioningReport() {
  return {
    ...state.commissioning,
    completed_at: state.commissioning.completed_at ?? "2026-01-01T10:01:00Z",
    generated_at: "2026-01-01T10:02:00Z",
    ready: true,
    handoff: state.commissioning.handoff ?? {},
  };
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", `http://127.0.0.1:${port}`);
  const { pathname } = url;
  const method = request.method ?? "GET";

  if (method === "POST" && pathname === "/__reset") {
    state = initialState();
    send(response, 200, { reset: true });
    return;
  }

  if (pathname === "/api/events/stream") {
    response.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
    response.end(": e2e mock connected\n\n");
    return;
  }

  if (method === "GET" && pathname === "/api/cameras") {
    send(response, 200, { count: state.cameras.length, cameras: state.cameras, revision: state.cameraRevision });
    return;
  }
  if (method === "GET" && pathname === "/api/cameras/cam-1") {
    send(response, 200, { camera: state.cameras[0], revision: state.cameraRevision });
    return;
  }
  if (method === "GET" && pathname === "/api/cameras/cam-hls") {
    send(response, 200, { camera: { id: "cam-hls", enabled: true } });
    return;
  }
  if (method === "GET" && pathname === "/api/cameras/cam-disabled") {
    send(response, 200, { camera: { id: "cam-disabled", enabled: false } });
    return;
  }
  if (method === "POST" && pathname === "/api/cameras") {
    send(response, 201, { applied: true, apply_error: null, camera: { id: "cam-new" } });
    return;
  }
  if (method === "GET" && pathname === "/api/camera-profiles") {
    send(response, 200, { cameras: { "cam-1": { domain: "general", enabled_events: ["person"] } } });
    return;
  }
  if (method === "GET" && pathname === "/api/policies") {
    send(response, 200, { profiles: { general: [{ name: "person", label: "사람", enabled: true, alert_level: "high" }] } });
    return;
  }
  if (method === "GET" && pathname === "/api/event-catalog") {
    send(response, 200, {
      count: 1,
      catalog: [{ name: "person", label: "사람", selectable: true }],
      profiles: { general: { label: "일반", default_events: ["person"] } },
    });
    return;
  }
  if (method === "POST" && pathname === "/api/cameras/test") {
    send(response, 200, { ok: true, connected: true, diagnostics: { codec: "h264", first_frame_ms: 80 } });
    return;
  }
  if (method === "POST" && /^\/api\/cameras\/cam-1\/(test|apply|enable|disable)$/.test(pathname)) {
    const action = pathname.split("/").at(-1);
    send(response, 200, action === "test"
      ? { ok: true, connected: true, code: "h264" }
      : { applied: true, apply_error: null });
    return;
  }
  if (method === "GET" && pathname === "/api/cameras/telemetry") {
    send(response, 200, {
      cameras: {
        "cam-1": { connected: false, fps: 0, dropped_frames: 0, is_stale: true, health_state: "disconnected" },
      },
      generated_at: new Date().toISOString(),
      telemetry_status: "ok",
    });
    return;
  }
  if (method === "GET" && pathname === "/api/events") {
    send(response, 200, { count: events.length, events });
    return;
  }
  if (method === "GET" && pathname === "/api/events/evt-2") {
    send(response, 200, { event: events.find((event) => event.event_id === "evt-2") });
    return;
  }
  if (method === "GET" && pathname === "/api/preflight") {
    send(response, 200, { checks: preflightChecks });
    return;
  }
  if (method === "GET" && pathname === "/api/audit") {
    send(response, 200, { count: auditEntries.length, entries: auditEntries });
    return;
  }
  if (method === "GET" && pathname === "/api/incidents") {
    const unresolved = url.searchParams.get("unresolved") === "true";
    const incidents = unresolved ? state.incidents.filter((incident) => incident.status !== "resolved") : state.incidents;
    send(response, 200, { available: true, count: incidents.length, incidents });
    return;
  }
  if (method === "GET" && /^\/api\/incidents\/[^/]+$/.test(pathname)) {
    const eventId = decodeURIComponent(pathname.split("/").at(-1));
    const incident = state.incidents.find((item) => item.event_id === eventId);
    if (!incident) {
      send(response, 404, { detail: "incident not found" });
      return;
    }
    send(response, 200, { incident, actions: incident.actions });
    return;
  }
  if (method === "POST" && /^\/api\/incidents\/[^/]+\/actions$/.test(pathname)) {
    const eventId = decodeURIComponent(pathname.split("/")[3]);
    const incident = state.incidents.find((item) => item.event_id === eventId);
    if (!incident) {
      send(response, 404, { detail: "incident not found" });
      return;
    }
    const body = await jsonBody(request);
    const previous_status = incident.status;
    if (!["acknowledged", "in_progress", "resolved"].includes(body.status)) {
      send(response, 422, { detail: "invalid incident status" });
      return;
    }
    if (body.status === "resolved" && !["confirmed", "false_positive", "duplicate", "test"].includes(body.disposition)) {
      send(response, 422, { detail: "resolution disposition is required" });
      return;
    }
    incident.status = body.status;
    incident.updated_at = `2026-01-01T10:0${2 + incident.actions.length}:00Z`;
    if (body.status === "resolved") incident.disposition = body.disposition;
    incident.actions.push({ previous_status, status: body.status, disposition: body.status === "resolved" ? body.disposition : undefined, note: body.note ?? "", created_at: incident.updated_at });
    send(response, 200, incident);
    return;
  }
  if (method === "GET" && pathname === "/api/observations") {
    send(response, 200, { connected: true, count: 0, observations: [] });
    return;
  }
  if (method === "GET" && pathname === "/api/control/status") {
    send(response, 200, { connected: true, running: false });
    return;
  }
  if (method === "GET" && pathname === "/api/models") {
    send(response, 200, { count: state.models.length, models: state.models });
    return;
  }
  if (method === "POST" && pathname === "/api/models/upload") {
    const contentType = request.headers["content-type"] ?? "";
    const body = await boundedBody(request);
    if (!contentType.startsWith("multipart/form-data;") || !body) {
      send(response, 400, { detail: "invalid multipart upload" });
      return;
    }
    const payload = body.toString("utf8");
    const match = payload.match(/name="model_name"\r\n\r\n([^\r\n]+)/);
    const name = match?.[1] || "uploaded-model";
    state.models.push({ name, type: "onnx", source: "uploaded", enabled: false });
    send(response, 201, { profile_name: name });
    return;
  }
  if (method === "PUT" && /^\/api\/models\/[^/]+\/enabled$/.test(pathname)) {
    const body = await jsonBody(request);
    const name = decodeURIComponent(pathname.split("/")[3]);
    const model = state.models.find((item) => item.name === name);
    if (model) model.enabled = Boolean(body.enabled);
    send(response, 200, { enabled: Boolean(body.enabled) });
    return;
  }
  if (method === "POST" && /^\/api\/models\/[^/]+\/prompts$/.test(pathname)) {
    const body = await jsonBody(request);
    const name = decodeURIComponent(pathname.split("/")[3]);
    const model = state.models.find((item) => item.name === name);
    if (model && Array.isArray(body.classes)) model.classes = body.classes;
    send(response, 200, { classes: Array.isArray(body.classes) ? body.classes : [] });
    return;
  }
  if (method === "GET" && pathname === "/api/commissioning") {
    send(response, 200, state.commissioning);
    return;
  }
  if (method === "GET" && pathname === "/api/config") {
    send(response, 200, { system: { site_name: "E2E 검증 현장" } });
    return;
  }
  if (method === "GET" && pathname === "/api/alert-preferences") {
    send(response, 200, { email_alert_level_threshold: "high", per_event_email_enabled: { person: true } });
    return;
  }
  if (method === "PUT" && pathname === "/api/alert-preferences") {
    send(response, 200, await jsonBody(request));
    return;
  }
  if (method === "GET" && pathname === "/api/alerts/status") {
    send(response, 200, { available: true, email_configured: false });
    return;
  }
  if (method === "GET" && pathname === "/api/webhooks") {
    send(response, 200, { count: 0, endpoints: [] });
    return;
  }
  if (method === "GET" && pathname === "/api/webhooks/channels") {
    send(response, 200, { channels: {} });
    return;
  }
  if (method === "GET" && pathname === "/api/support/bundle") {
    sendBinary(response, 200, Buffer.from("PK\x03\x04e2e-support-bundle"), "application/zip");
    return;
  }
  if (method === "GET" && pathname === "/api/cameras/cam-1/snapshot") {
    sendBinary(response, 200, Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL7QgAAAABJRU5ErkJggg==", "base64"), "image/png");
    return;
  }
  if (method === "PUT" && pathname === "/api/cameras/cam-1/zones") {
    const body = await jsonBody(request);
    if (state.zoneConflictPending) {
      state.zoneConflictPending = false;
      state.cameraRevision += 1;
      send(response, 409, { detail: "stale camera revision" });
      return;
    }
    if (body.expected_revision !== state.cameraRevision || !Array.isArray(body.zones)) {
      send(response, 409, { detail: "stale camera revision" });
      return;
    }
    state.cameras[0].zones = body.zones;
    state.cameraRevision += 1;
    send(response, 200, { revision: state.cameraRevision });
    return;
  }
  if (method === "PUT" && pathname === "/api/commissioning/cameras/cam-1") {
    const body = await jsonBody(request);
    state.commissioning.cameras[0].checks = {
      ...state.commissioning.cameras[0].checks,
      ...body,
    };
    state.commissioning.revision += 1;
    send(response, 200, state.commissioning);
    return;
  }
  if (method === "POST" && pathname === "/api/commissioning/complete") {
    state.commissioning.completed_at = "2026-01-01T10:01:00Z";
    state.commissioning.revision += 1;
    send(response, 200, state.commissioning);
    return;
  }
  if (method === "GET" && pathname === "/api/commissioning/report") {
    send(response, 200, commissioningReport());
    return;
  }
  if (method === "PUT" && pathname === "/api/commissioning/handoff") {
    const body = await jsonBody(request);
    state.commissioning.revision += 1;
    state.commissioning.handoff = {
      customer_name: body.customer_name,
      notes: body.notes,
      accepted_at: "2026-01-01T10:03:00Z",
    };
    send(response, 200, { revision: state.commissioning.revision, handoff: state.commissioning.handoff });
    return;
  }

  send(response, 200, {});
});

server.listen(port, "127.0.0.1", () => {
  console.log(`E2E mock engine listening on ${port}`);
});
