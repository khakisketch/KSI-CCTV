import { expect, test, type Page } from "@playwright/test";

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("비밀번호").fill("e2e-web-password");
  await page.getByRole("button", { name: "로그인" }).click();
  await expect(page).toHaveURL(/\/$/);
}

test.beforeEach(async ({ request }) => {
  await request.post("http://127.0.0.1:4177/__requests/reset");
});

test("proxies authorized HLS and denies disabled cameras before the gateway", async ({ page, request }) => {
  await login(page);
  const result = await page.evaluate(async () => {
    const stream = await fetch("/api/cameras/cam-hls/stream");
    const info = await stream.json();
    const playlist = await fetch(info.hls);
    const segment = await fetch("/api/cameras/cam-hls/hls/segment.ts");
    return {
      streamStatus: stream.status,
      hls: info.hls,
      playlistStatus: playlist.status,
      playlist: await playlist.text(),
      playlistType: playlist.headers.get("content-type"),
      segmentStatus: segment.status,
      segmentBytes: [...new Uint8Array(await segment.arrayBuffer())],
      segmentType: segment.headers.get("content-type"),
    };
  });

  expect(result).toEqual({
    streamStatus: 200,
    hls: "/api/cameras/cam-hls/hls/index.m3u8",
    playlistStatus: 200,
    playlist: "#EXTM3U\n#EXTINF:1,\nsegment.ts\n",
    playlistType: "application/vnd.apple.mpegurl",
    segmentStatus: 200,
    segmentBytes: [0x47, 0x40, 0x00],
    segmentType: "video/mp2t",
  });
  const gatewayRequests = await (await request.get("http://127.0.0.1:4177/__requests")).json();
  expect(gatewayRequests).toEqual([
    { path: "/cam-hls/index.m3u8", cookie: "" },
    { path: "/cam-hls/index.m3u8?cookieCheck=1", cookie: "cookieCheck=private" },
    { path: "/cam-hls/segment.ts", cookie: "cookieCheck=private; mtxSession=authorized" },
  ]);
  const response = await page.evaluate(async () => {
    const result = await fetch("/api/cameras/cam-disabled/hls/index.m3u8");
    return { status: result.status, body: await result.json() };
  });

  expect(response).toEqual({ status: 409, body: { detail: "비활성 카메라는 재생할 수 없습니다." } });
  expect(await (await request.get("http://127.0.0.1:4177/__requests")).json()).toEqual(gatewayRequests);
});
