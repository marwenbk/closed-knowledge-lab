import { ChatExperience } from "@/components/chat-experience";

export default function ChatPage() {
  return (
    <div className="flex min-h-dvh items-center justify-center p-3 sm:p-6">
      <ChatExperience
        apiBaseUrl={process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"}
        assistantKey={
          process.env.NEXT_PUBLIC_WIDGET_ASSISTANT_KEY ?? "topmed-local-demo"
        }
        mode="page"
      />
    </div>
  );
}
