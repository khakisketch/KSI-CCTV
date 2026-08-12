import { createServer } from "node:http";

const port = Number(process.env.MOCK_MEDIAMTX_PORT ?? "9101");
const requests = [];

function mediaResponse(response, status, headers, body = "") {
  response.writeHead(status, { "Cache-Control": "no-store", ...headers });
  response.end(body);
}

const server = createServer((request, response) => {
  const url = new URL(request.url ?? "/", `http://127.0.0.1:${port}`);
  if (url.pathname === "/health") return mediaResponse(response, 200, { "Content-Type": "text/plain" }, "ok");
  if (url.pathname === "/__requests") return mediaResponse(response, 200, { "Content-Type": "application/json" }, JSON.stringify(requests));
  if (url.pathname === "/__requests/reset" && request.method === "POST") {
    requests.length = 0;
    return mediaResponse(response, 204, {});
  }

  const cookie = request.headers.cookie ?? "";
  requests.push({ path: `${url.pathname}${url.search}`, cookie });
  if (!cookie.includes("cookieCheck=private")) {
    return mediaResponse(response, 302, {
      Location: `${url.pathname}?cookieCheck=1`,
      "Set-Cookie": "cookieCheck=private; Path=/; HttpOnly",
    });
  }
  if (url.pathname === "/cam-hls/index.m3u8") {
    return mediaResponse(response, 200, {
      "Content-Type": "application/vnd.apple.mpegurl",
      "Set-Cookie": "mtxSession=authorized; Path=/; HttpOnly",
    }, "#EXTM3U\n#EXTINF:1,\nsegment.ts\n");
  }
  if (url.pathname === "/cam-hls/segment.ts" && cookie.includes("mtxSession=authorized")) {
    return mediaResponse(response, 200, { "Content-Type": "video/mp2t" }, Buffer.from([0x47, 0x40, 0x00]));
  }
  return mediaResponse(response, 401, { "Content-Type": "text/plain" }, "unauthorized");
});

server.listen(port, "127.0.0.1", () => {
  console.log(`E2E mock MediaMTX listening on ${port}`);
});
