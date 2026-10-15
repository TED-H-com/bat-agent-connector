import { createServer } from "node:http";
import { readFile } from "node:fs/promises";

const root = new URL("../../src/bat_agent_connector/dashboard/", import.meta.url);
const protoRoot = new URL("../dist-prototype/v1.3/", import.meta.url);
const demoRoot = new URL("../dist-demo/", import.meta.url);

const port = Number(process.env.BATC_UI_TEST_PORT || process.env.BATC_TEST_PORT || 1421);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid fixture port");
createServer(async (req, res) => {
  const path = new URL(req.url, "http://127.0.0.1").pathname;
  const file = path === "/dashboard/" ? "index.html" : path.slice("/dashboard/".length);
  const protoFile =
    path === "/prototype/v1.3/" || path === "/prototype/v1.3/index.html"
      ? "index.html"
      : path.startsWith("/prototype/v1.3/")
        ? path.slice("/prototype/v1.3/".length)
        : null;
  const isProto = Boolean(protoFile && ["index.html", "app.js", "app.css"].includes(protoFile));
  const demoFile = path === '/demo/' ? 'index.html' : path.startsWith('/demo/') ? path.slice('/demo/'.length) : null;
  const isDemo = Boolean(demoFile && ['index.html', 'app.js', 'app.css'].includes(demoFile));
  const source = /^\/src\/[a-z0-9/_-]+\.(js|css)$/.test(path);
  const componentTest = path === "/ui-components.html";
  if (!isDemo && !isProto && !source && !componentTest && !["index.html", "app.js", "app.css", "i18n.js"].includes(file)) {
    res.writeHead(404).end();
    return;
  }
  const targetName = isDemo ? demoFile : isProto ? protoFile : componentTest ? "ui-components.html" : source ? path : file;
  try {
    const body = await readFile(
      isDemo ? new URL(demoFile, demoRoot) : isProto
        ? new URL(protoFile, protoRoot)
        : source
          ? new URL(".." + path, import.meta.url)
          : componentTest
            ? new URL("./ui-components.html", import.meta.url)
            : new URL(file, root),
    );
    res.writeHead(200, {
      "Content-Type": targetName.endsWith("html")
        ? "text/html"
        : targetName.endsWith("css")
          ? "text/css"
          : "text/javascript",
      "Content-Security-Policy":
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
    });
    res.end(body);
  } catch {
    res.writeHead(404).end();
  }
}).listen(port, "127.0.0.1");
