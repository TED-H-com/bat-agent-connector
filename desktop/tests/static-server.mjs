import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
const root = new URL("../../src/bat_agent_connector/dashboard/", import.meta.url);
const port = Number(process.env.BATC_UI_TEST_PORT || process.env.BATC_TEST_PORT || 1421);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid fixture port");
createServer(async (req, res) => {
  const path = new URL(req.url, "http://127.0.0.1").pathname;
  const file = path === "/dashboard/" ? "index.html" : path.slice("/dashboard/".length);
  const source = /^\/src\/[a-z0-9/_-]+\.(js|css)$/.test(path);
  const componentTest = path === '/ui-components.html';
  if (!source && !componentTest && !["index.html", "app.js", "app.css", "i18n.js"].includes(file)) { res.writeHead(404).end(); return; }
  try {
    const body = await readFile(source ? new URL('..' + path, import.meta.url)
      : componentTest ? new URL('./ui-components.html', import.meta.url) : new URL(file, root));
    res.writeHead(200, { "Content-Type": file.endsWith("html") ? "text/html" : file.endsWith("css") ? "text/css" : "text/javascript",
      "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'" });
    res.end(body);
  } catch { res.writeHead(404).end(); }
}).listen(port, "127.0.0.1");
