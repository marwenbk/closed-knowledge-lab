import { mkdir, readFile, writeFile } from "node:fs/promises";

const appUrl = process.env.TOPMED_APP_URL?.replace(/\/$/, "");
const assistantKey = process.env.TOPMED_WIDGET_ASSISTANT_KEY;

const parsedAppUrl = appUrl ? new URL(appUrl) : null;
const safeAppUrl =
  parsedAppUrl?.protocol === "https:" ||
  ["127.0.0.1", "localhost"].includes(parsedAppUrl?.hostname ?? "");

if (!safeAppUrl || !assistantKey) {
  throw new Error("TOPMED_APP_URL and TOPMED_WIDGET_ASSISTANT_KEY are required");
}

const template = await readFile(new URL("./render-host.html", import.meta.url), "utf8");
const output = template
  .replaceAll("__TOPMED_APP_URL__", appUrl)
  .replaceAll("__TOPMED_ASSISTANT_KEY__", assistantKey);

await mkdir(new URL("./dist", import.meta.url), { recursive: true });
await writeFile(new URL("./dist/index.html", import.meta.url), output, "utf8");
