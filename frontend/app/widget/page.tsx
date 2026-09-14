import { ChatExperience } from "@/components/chat-experience";

type WidgetParameters = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function safeHttpUrl(value: string | undefined, fallback: string): string {
  if (!value) return fallback;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.toString() : fallback;
  } catch {
    return fallback;
  }
}

export default async function WidgetPage({
  searchParams,
}: {
  searchParams: Promise<WidgetParameters>;
}) {
  const parameters = await searchParams;
  const defaultApi = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
  const parentOrigin = first(parameters.parentOrigin);
  return (
    <ChatExperience
      apiBaseUrl={safeHttpUrl(first(parameters.apiBaseUrl), defaultApi)}
      assistantKey={
        first(parameters.assistantKey) ??
        process.env.NEXT_PUBLIC_WIDGET_ASSISTANT_KEY ??
        "topmed-local-demo"
      }
      locale={first(parameters.locale) ?? "en-US"}
      mode="widget"
      parentOrigin={parentOrigin}
    />
  );
}
