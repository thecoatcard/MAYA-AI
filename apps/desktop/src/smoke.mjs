// In-app checks for `npm run smoke` (MAYA_DESKTOP_SMOKE=1). Runs inside the hidden window.

/**
 * @param {import('electron').BrowserWindow} win
 * @returns {Promise<{ name: string, ok: boolean, detail: string }[]>}
 */
export async function runSmokeChecks(win) {
  const run = (/** @type {string} */ code) => win.webContents.executeJavaScript(code, true);
  const results = [];
  const check = async (/** @type {string} */ name, /** @type {() => Promise<[boolean, string]>} */ fn) => {
    try {
      const [ok, detail] = await fn();
      results.push({ name, ok, detail });
    } catch (error) {
      results.push({ name, ok: false, detail: String(error?.message ?? error) });
    }
  };

  await check("page title", async () => {
    const title = await run("document.title");
    return [typeof title === "string" && title.includes("MAYA"), `title=${JSON.stringify(title)}`];
  });
  await check("preload bridge", async () => {
    const info = await run("JSON.stringify(window.maya ?? null)");
    return [JSON.parse(info)?.isDesktop === true, info];
  });
  await check("bootstrap status via app origin", async () => {
    const body = await run(
      "fetch('/api/v1/auth/bootstrap/status').then(async r => JSON.stringify({status: r.status, body: await r.json()}))",
    );
    const parsed = JSON.parse(body);
    return [parsed.status === 200 && parsed.body?.available === true, body];
  });
  await check("GET / returns exported HTML", async () => {
    const info = await run(
      "fetch('/').then(async r => JSON.stringify({status: r.status, type: r.headers.get('content-type'), html: (await r.text()).toLowerCase().includes('<html'), csp: !!r.headers.get('content-security-policy')}))",
    );
    const parsed = JSON.parse(info);
    return [parsed.status === 200 && parsed.html, info];
  });
  return results;
}
